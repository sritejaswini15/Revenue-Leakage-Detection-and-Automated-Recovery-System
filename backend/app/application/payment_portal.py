"""Customer self-service payment page, addressed by an opaque, expiring link token.

The token is never stored; only its SHA-256 hash is. No authentication is required.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.case_state import record_promise
from app.application.simulation import signed_payment_webhook
from app.application.webhooks import ingest_simulated_webhook
from app.application.workflows import runner
from app.core.security import sha256_hex
from app.core.time import as_utc, utc_now
from app.domain.constants import CLOSED_LINK_STATUSES, TERMINAL_CASE_STATUSES, PaymentLinkStatus
from app.domain.exceptions import ConflictError, InvalidRequestError, NotFoundError
from app.infrastructure.database.models import Customer, Organization, PaymentLink, PromiseToPay, RecoveryCase

ACTOR = "customer_self_service"
DEFAULT_PROMISE_DAYS = 7


@dataclass(frozen=True)
class PaymentRequest:
    link: PaymentLink
    case: RecoveryCase
    customer: Customer | None


def get_payment_request(session: Session, token: str) -> PaymentRequest:
    link, case = _resolve(session, token)
    return PaymentRequest(link, case, session.get(Customer, case.customer_id))


def pay(session: Session, token: str) -> RecoveryCase:
    link, case = _resolve_payable(session, token)
    organization, customer = _parties(session, case)
    webhook = signed_payment_webhook(case, link, organization.razorpay_account_id)
    ingest_simulated_webhook(session, organization, customer, webhook, actor=ACTOR)
    session.commit()
    return case


def promise_to_pay(session: Session, token: str, promise_date: str | None) -> PromiseToPay:
    _, case = _resolve_payable(session, token)
    _parties(session, case)
    promise = record_promise(session, case, case.amount, _parse_promise_date(promise_date), actor=ACTOR)
    session.commit()
    runner.schedule_promise_check(promise.id)
    return promise


def _resolve(session: Session, token: str) -> tuple[PaymentLink, RecoveryCase]:
    link = session.scalar(select(PaymentLink).where(PaymentLink.public_token_hash == sha256_hex(token)))
    if link and link.expires_at and as_utc(link.expires_at) <= utc_now():
        link.status = PaymentLinkStatus.EXPIRED
        session.commit()
    if not link or link.status in CLOSED_LINK_STATUSES:
        raise NotFoundError("Payment link is invalid or expired")
    case = session.get(RecoveryCase, link.case_id)
    if not case:
        raise NotFoundError("Payment request not found")
    return link, case


def _resolve_payable(session: Session, token: str) -> tuple[PaymentLink, RecoveryCase]:
    link, case = _resolve(session, token)
    if case.status in TERMINAL_CASE_STATUSES:
        raise ConflictError(f"Case is already in terminal state: {case.status}")
    return link, case


def _parties(session: Session, case: RecoveryCase) -> tuple[Organization, Customer]:
    customer = session.get(Customer, case.customer_id)
    if not customer:
        raise ConflictError("Customer record missing for payment case")
    return session.get(Organization, case.organization_id), customer


def _parse_promise_date(value: str | None) -> datetime:
    if not value:
        return (utc_now() + timedelta(days=DEFAULT_PROMISE_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        parsed = datetime.fromisoformat(value if "T" in value else f"{value}T00:00:00+00:00")
    except ValueError as exc:
        raise InvalidRequestError("promise_date must be an ISO date (YYYY-MM-DD)") from exc
    if as_utc(parsed).date() < utc_now().date():
        raise InvalidRequestError("The promised date must be today or later")
    return as_utc(parsed)
