"""Payments RecoverX knows about: observed through webhooks, captured by retries, or demo history."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.domain.exceptions import NotFoundError
from app.infrastructure.database.models import Customer, Payment, PaymentAttempt, RecoveryCase

PAYMENT_STATUSES = ("CREATED", "AUTHORIZED", "CAPTURED", "FAILED")


@dataclass(frozen=True)
class TransactionView:
    payment: Payment
    customer_name: str | None


@dataclass(frozen=True)
class TransactionDetail:
    view: TransactionView
    attempts: list[PaymentAttempt]
    case: RecoveryCase | None


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def list_transactions(
    session: Session,
    organization_id: str,
    *,
    search: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    case_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TransactionView], int]:
    """Newest first. Dates are whole UTC days, both ends inclusive."""
    query = (
        select(Payment, Customer.name)
        .outerjoin(Customer, Customer.id == Payment.customer_id)
        .where(Payment.organization_id == organization_id)
    )
    if search and search.strip():
        needle = search.strip().lower()
        query = query.where(
            or_(
                func.lower(Payment.provider_payment_id).contains(needle, autoescape=True),
                func.lower(Customer.name).contains(needle, autoescape=True),
                func.lower(Customer.email).contains(needle, autoescape=True),
                func.lower(func.coalesce(Payment.recovery_case_id, "")).contains(needle, autoescape=True),
            )
        )
    if status:
        query = query.where(Payment.status == status)
    if case_id:
        query = query.where(Payment.recovery_case_id == case_id)
    if date_from:
        query = query.where(Payment.created_at >= _day_start(date_from))
    if date_to:
        query = query.where(Payment.created_at < _day_start(date_to + timedelta(days=1)))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(query.order_by(desc(Payment.created_at), Payment.id).limit(limit).offset(offset)).all()
    return [TransactionView(payment, name) for payment, name in rows], total


def get_transaction(session: Session, organization_id: str, identifier: str) -> TransactionDetail:
    """By RecoverX id or Razorpay payment id."""
    row = session.execute(
        select(Payment, Customer.name)
        .outerjoin(Customer, Customer.id == Payment.customer_id)
        .where(
            Payment.organization_id == organization_id,
            or_(Payment.id == identifier, Payment.provider_payment_id == identifier),
        )
    ).first()
    if not row:
        raise NotFoundError("Transaction not found")
    payment, customer_name = row
    attempts = session.scalars(
        select(PaymentAttempt).where(PaymentAttempt.payment_id == payment.id).order_by(PaymentAttempt.created_at)
    ).all()
    case = session.get(RecoveryCase, payment.recovery_case_id) if payment.recovery_case_id else None
    return TransactionDetail(TransactionView(payment, customer_name), list(attempts), case)
