"""Customer directory, payment statistics, CSV import and Razorpay customer sync.

Statistics combine the merchant's profile (imported or demo data) with the payments RecoverX
observed; see ``app.domain.reliability``. Imports and syncs share one pipeline: every row is
normalized and validated, matched to an existing customer of the same merchant (Razorpay id,
then email, then phone) and previewed before anything is written.
"""

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.application.payment_integrations import payment_provider_for
from app.domain.exceptions import InvalidRequestError, NotFoundError
from app.domain.reliability import CombinedProfile, ObservedPayments, combine
from app.infrastructure.database.models import Customer, Payment, RecoveryCase, User
from app.infrastructure.database.repositories import (
    PaymentTotals,
    find_customer,
    namespaced_reference,
    observed_payment_totals,
)

ContactFilter = Literal["all", "contactable", "opted_out"]
CustomerSort = Literal["value", "name", "recent"]

DEFAULT_RELIABILITY_PCT = 85.0  # a customer with no history; same default as webhook-created profiles
IMPORT_MAX_ROWS = 5000
PREVIEW_ROWS = 200
PROVIDER_ID_PREFIXES = ("cust_", "cus_")


@dataclass(frozen=True)
class CustomerStats:
    """Combined figures first, then the two sources they were computed from."""

    reliability_pct: float
    successful_payments: int
    failed_payments: int
    profile_reliability_pct: float
    profile_successful: int
    profile_failed: int
    observed_successful: int
    observed_failed: int
    observed_captured_amount: int
    last_payment_at: datetime | None


@dataclass(frozen=True)
class CustomerView:
    customer: Customer
    stats: CustomerStats


@dataclass(frozen=True)
class CustomerDetail:
    view: CustomerView
    payments: list[Payment]
    cases: list[RecoveryCase]


def _stats(customer: Customer, totals: PaymentTotals) -> CustomerStats:
    combined = combine(customer, ObservedPayments(totals.successful, totals.failed))
    return CustomerStats(
        reliability_pct=combined.payment_reliability_pct,
        successful_payments=combined.successful_payments,
        failed_payments=combined.failed_payments,
        profile_reliability_pct=customer.payment_reliability_pct,
        profile_successful=customer.successful_payments,
        profile_failed=customer.failed_payments,
        observed_successful=totals.successful,
        observed_failed=totals.failed,
        observed_captured_amount=totals.captured_amount,
        last_payment_at=totals.last_payment_at,
    )


def combined_profile(session: Session, customer: Customer, exclude_payment_id: str | None = None) -> CombinedProfile:
    """What the risk engine, advisor and policy see; ``exclude_payment_id`` is the payment being decided on."""
    totals = observed_payment_totals(
        session, customer.organization_id, [customer.id], exclude_provider_payment_id=exclude_payment_id
    ).get(customer.id, PaymentTotals())
    return combine(customer, ObservedPayments(totals.successful, totals.failed))


def list_customers(
    session: Session,
    organization_id: str,
    *,
    search: str | None = None,
    contact: ContactFilter = "all",
    sort: CustomerSort = "value",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CustomerView], int]:
    query = select(Customer).where(Customer.organization_id == organization_id)
    if search and search.strip():
        needle = search.strip().lower()
        query = query.where(
            or_(
                func.lower(Customer.name).contains(needle, autoescape=True),
                func.lower(Customer.email).contains(needle, autoescape=True),
                Customer.phone.contains(needle, autoescape=True),
                func.lower(Customer.razorpay_customer_id).contains(needle, autoescape=True),
            )
        )
    if contact == "opted_out":
        query = query.where(Customer.opted_out.is_(True))
    elif contact == "contactable":
        query = query.where(Customer.opted_out.is_(False))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    order = {
        "value": (desc(Customer.lifetime_value), Customer.id),
        "name": (Customer.name, Customer.id),
        "recent": (desc(Customer.created_at), Customer.id),
    }[sort]
    customers = session.scalars(query.order_by(*order).limit(limit).offset(offset)).all()
    totals = observed_payment_totals(session, organization_id, [customer.id for customer in customers])
    return [CustomerView(c, _stats(c, totals.get(c.id, PaymentTotals()))) for c in customers], total


def get_customer_detail(session: Session, organization_id: str, identifier: str) -> CustomerDetail:
    customer = find_customer(session, organization_id, identifier)
    if not customer:
        raise NotFoundError("Customer not found")
    totals = observed_payment_totals(session, organization_id, [customer.id]).get(customer.id, PaymentTotals())
    payments = session.scalars(
        select(Payment)
        .where(Payment.organization_id == organization_id, Payment.customer_id == customer.id)
        .order_by(desc(Payment.created_at))
        .limit(25)
    ).all()
    cases = session.scalars(
        select(RecoveryCase)
        .where(RecoveryCase.organization_id == organization_id, RecoveryCase.customer_id == customer.id)
        .order_by(desc(RecoveryCase.created_at))
        .limit(25)
    ).all()
    return CustomerDetail(CustomerView(customer, _stats(customer, totals)), list(payments), list(cases))


def customer_key(session: Session, organization_id: str, reference: str) -> str:
    """The stored ``razorpay_customer_id`` for a reference (Razorpay id, email or phone).

    The column is unique across merchants: a Razorpay id another merchant already uses, and
    any email or phone, is namespaced with the merchant so two merchants never collide.
    """
    taken = session.scalar(
        select(Customer.id).where(
            Customer.razorpay_customer_id == reference, Customer.organization_id != organization_id
        )
    )
    if taken or not reference.startswith(PROVIDER_ID_PREFIXES):
        return namespaced_reference(organization_id, reference)
    return reference[:80]


# --- import and sync -------------------------------------------------------------------------

_HEADER_ALIASES = {
    "name": ("name", "customer_name", "full_name"),
    "email": ("email", "email_address", "e_mail"),
    "phone": ("phone", "contact", "mobile", "phone_number"),
    "razorpay_customer_id": ("razorpay_customer_id", "customer_id", "razorpay_id"),
    "preferred_channel": ("preferred_channel", "channel"),
    "preferred_language": ("preferred_language", "language"),
    "lifetime_value": ("lifetime_value", "ltv"),
    "successful_payments": ("successful_payments",),
    "failed_payments": ("failed_payments",),
    "opted_out": ("opted_out", "opt_out", "do_not_contact"),
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9_\-.:]{3,80}$")
_CHANNELS = ("whatsapp", "email")
_TRUE, _FALSE = {"true", "yes", "y", "1"}, {"false", "no", "n", "0", ""}


ImportAction = Literal["create", "update", "duplicate", "invalid"]
IMPORT_ACTIONS: tuple[ImportAction, ...] = ("create", "update", "duplicate", "invalid")


@dataclass
class ImportRow:
    line: int
    action: ImportAction
    name: str = ""
    email: str = ""
    phone: str = ""
    razorpay_customer_id: str = ""
    errors: list[str] = field(default_factory=list)
    customer_id: str | None = None
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportSummary:
    source: str
    committed: bool
    total_rows: int
    created: int
    updated: int
    duplicates: int
    invalid: int
    ignored_columns: list[str]
    rows: list[ImportRow]


def import_csv(session: Session, user: User, text: str, commit: bool) -> ImportSummary:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not text.strip() or not reader.fieldnames:
        raise InvalidRequestError("The file is empty; the first line must be a header row")
    columns = {_header(name): name for name in reader.fieldnames if name}
    mapping = {
        target: columns[alias] for target, aliases in _HEADER_ALIASES.items() for alias in aliases if alias in columns
    }
    if "name" not in mapping:
        raise InvalidRequestError("A 'name' column is required")
    if not {"email", "phone", "razorpay_customer_id"} & mapping.keys():
        raise InvalidRequestError("Add an 'email', 'phone' or 'razorpay_customer_id' column to identify customers")
    mapped = set(mapping.values())
    ignored = sorted(original for original in columns.values() if original not in mapped)

    records = []
    for index, raw in enumerate(reader):
        if index >= IMPORT_MAX_ROWS:
            raise InvalidRequestError(f"Import at most {IMPORT_MAX_ROWS} rows at a time")
        records.append({target: (raw.get(source) or "").strip() for target, source in mapping.items()})
    return _run(session, user, "csv", records, commit, ignored, first_line=2)


def sync_from_razorpay(session: Session, user: User, commit: bool) -> ImportSummary:
    """Fetch the merchant's customers from Razorpay with its own keys and merge them."""
    provider = payment_provider_for(session, user.organization_id)
    records = [
        {
            "razorpay_customer_id": item.id,
            "name": item.name or item.email or item.contact or item.id,
            "email": item.email or "",
            "phone": item.contact or "",
        }
        for item in provider.list_customers(IMPORT_MAX_ROWS)
    ]
    return _run(session, user, "razorpay", records, commit, ignored=[], first_line=1)


def _header(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def _run(
    session: Session,
    user: User,
    source: str,
    records: Iterable[dict[str, str]],
    commit: bool,
    ignored: list[str],
    first_line: int,
) -> ImportSummary:
    rows = _plan(session, user.organization_id, records, first_line)
    counts = {action: sum(1 for row in rows if row.action == action) for action in IMPORT_ACTIONS}
    if commit:
        for row in rows:
            _apply(session, user.organization_id, row)
        record_audit(
            session,
            user.organization_id,
            f"customers.imported.{source}",
            {"rows": len(rows), **counts},
            actor=user.email,
        )
        session.commit()
    return ImportSummary(
        source=source,
        committed=commit,
        total_rows=len(rows),
        created=counts["create"],
        updated=counts["update"],
        duplicates=counts["duplicate"],
        invalid=counts["invalid"],
        ignored_columns=ignored,
        rows=[row for row in rows if row.action == "invalid"][:PREVIEW_ROWS]
        + [row for row in rows if row.action != "invalid"][:PREVIEW_ROWS],
    )


def _plan(
    session: Session, organization_id: str, records: Iterable[dict[str, str]], first_line: int
) -> list[ImportRow]:
    existing = session.execute(
        select(Customer.id, Customer.razorpay_customer_id, func.lower(Customer.email), Customer.phone).where(
            Customer.organization_id == organization_id
        )
    ).all()
    by_provider_id = {key: customer_id for customer_id, key, _, _ in existing}
    by_email = {email: customer_id for customer_id, _, email, _ in existing if email}
    by_phone = {phone: customer_id for customer_id, _, _, phone in existing if phone}
    seen: set[str] = set()

    rows = []
    for offset, record in enumerate(records):
        row = _normalize(first_line + offset, record)
        keys = {f"id:{row.razorpay_customer_id}", f"email:{row.email}", f"phone:{row.phone}"} - {
            "id:",
            "email:",
            "phone:",
        }
        if row.action != "invalid" and keys & seen:
            row.action = "duplicate"
            row.errors.append("Repeats an earlier row in this file")
        seen |= keys
        if row.action in ("invalid", "duplicate"):
            rows.append(row)
            continue
        row.customer_id = (
            (
                (
                    by_provider_id.get(row.razorpay_customer_id)
                    or by_provider_id.get(namespaced_reference(organization_id, row.razorpay_customer_id))
                )
                if row.razorpay_customer_id
                else None
            )
            or by_email.get(row.email)
            or by_phone.get(row.phone)
        )
        row.action = "update" if row.customer_id else "create"
        rows.append(row)
    return rows


def _normalize(line: int, record: dict[str, str]) -> ImportRow:
    row = ImportRow(line=line, action="create")
    errors = row.errors

    row.name = record.get("name", "")[:160]
    if not row.name:
        errors.append("name is required")
    email = record.get("email", "").lower()
    if email and (len(email) > 255 or not _EMAIL.match(email)):
        errors.append(f"email '{email[:60]}' is not a valid address")
    else:
        row.email = email
    phone = record.get("phone", "")
    if phone:
        try:
            row.phone = normalize_phone(phone)
        except ValueError as exc:
            errors.append(str(exc))
    provider_id = record.get("razorpay_customer_id", "")
    if provider_id and not _PROVIDER_ID.match(provider_id):
        errors.append("razorpay_customer_id may contain only letters, digits, '_', '-', '.' and ':'")
    else:
        row.razorpay_customer_id = provider_id
    if not (row.email or row.phone or row.razorpay_customer_id) and not errors:
        errors.append("each row needs an email, a phone or a razorpay_customer_id")

    values: dict[str, Any] = {}
    channel = record.get("preferred_channel", "").lower()
    if channel and channel not in _CHANNELS:
        errors.append("preferred_channel must be 'whatsapp' or 'email'")
    elif channel == "whatsapp" and not row.phone:
        errors.append("preferred_channel 'whatsapp' needs a phone number")
    elif channel == "email" and not row.email:
        errors.append("preferred_channel 'email' needs an email address")
    elif channel:
        values["preferred_channel"] = channel
    if record.get("preferred_language"):
        values["preferred_language"] = record["preferred_language"][:32]
    for name in ("lifetime_value", "successful_payments", "failed_payments"):
        if record.get(name):
            try:
                values[name] = _non_negative_int(record[name])
            except ValueError:
                errors.append(f"{name} must be a whole number of 0 or more")
    if record.get("opted_out") is not None:
        flag = record["opted_out"].lower()
        if flag in _TRUE:
            values["opted_out"] = True
        elif flag not in _FALSE:
            errors.append("opted_out must be yes/no, true/false or 1/0")
    row.values = values
    if errors:
        row.action = "invalid"
    return row


def normalize_phone(raw: str) -> str:
    """E.164: 10-digit Indian mobile numbers get +91; other numbers must carry their country code."""
    number = re.sub(r"[\s\-().]", "", raw)
    if number.startswith("00"):
        number = f"+{number[2:]}"
    if re.fullmatch(r"0?[6-9]\d{9}", number):
        return f"+91{number[-10:]}"
    if re.fullmatch(r"91[6-9]\d{9}", number):
        return f"+{number}"
    if re.fullmatch(r"\+\d{8,15}", number):
        return number
    raise ValueError(f"phone '{raw[:20]}' must be a 10-digit Indian mobile number or start with a country code (+)")


def _non_negative_int(raw: str) -> int:
    value = float(raw.replace(",", "").replace("₹", "").strip())
    if value < 0:
        raise ValueError(raw)
    return round(value)


def _apply(session: Session, organization_id: str, row: ImportRow) -> None:
    if row.action not in ("create", "update"):
        return
    values = dict(row.values)
    # Consent is one-way: an import can opt a customer out but never back in.
    if not values.get("opted_out"):
        values.pop("opted_out", None)
    successful, failed = values.get("successful_payments"), values.get("failed_payments")

    if row.action == "update":
        customer = session.get(Customer, row.customer_id)
        customer.name = row.name
        customer.email = row.email or customer.email
        customer.phone = row.phone or customer.phone
    else:
        reference = row.razorpay_customer_id or row.email or row.phone
        customer = Customer(
            organization_id=organization_id,
            razorpay_customer_id=customer_key(session, organization_id, reference),
            name=row.name,
            email=row.email,
            phone=row.phone,
            preferred_channel="whatsapp" if row.phone else "email",
            payment_reliability_pct=DEFAULT_RELIABILITY_PCT,
            historical_recovery_rate=0.5,  # no recovery history yet: neutral prior
            successful_payments=0,
            failed_payments=0,
        )
        session.add(customer)
    for name, value in values.items():
        setattr(customer, name, value)
    if successful is not None or failed is not None:
        total = customer.successful_payments + customer.failed_payments
        if total:
            customer.payment_reliability_pct = round(customer.successful_payments / total * 100, 1)
    session.flush()
