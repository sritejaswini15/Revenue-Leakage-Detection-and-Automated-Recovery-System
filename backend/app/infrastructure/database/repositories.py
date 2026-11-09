"""Tenant-scoped lookups shared by the application services."""

from collections.abc import Collection
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.domain.constants import DEFAULT_POLICY
from app.domain.exceptions import NotFoundError
from app.infrastructure.database.models import Customer, Organization, Payment, PaymentLink, Policy, RecoveryCase


def find_case(session: Session, organization_id: str, case_id: str) -> RecoveryCase | None:
    return session.scalar(
        select(RecoveryCase).where(RecoveryCase.id == case_id, RecoveryCase.organization_id == organization_id)
    )


def get_case(session: Session, organization_id: str, case_id: str) -> RecoveryCase:
    case = find_case(session, organization_id, case_id)
    if not case:
        raise NotFoundError("Recovery case not found")
    return case


def namespaced_reference(organization_id: str, reference: str) -> str:
    """How a customer reference is stored when it cannot be used as is (see customers.customer_key)."""
    return f"{organization_id[:8]}:{reference}"[:80]


def find_customer(session: Session, organization_id: str, identifier: str) -> Customer | None:
    """Look a customer up by RecoverX id or Razorpay customer id, stored as is or namespaced."""
    return session.scalar(
        select(Customer)
        .where(
            Customer.organization_id == organization_id,
            or_(
                Customer.id == identifier,
                Customer.razorpay_customer_id.in_([identifier, namespaced_reference(organization_id, identifier)]),
            ),
        )
        .limit(1)
    )


def get_organization(session: Session, organization_id: str) -> Organization:
    organization = session.get(Organization, organization_id)
    if not organization:
        raise NotFoundError("Organization not found")
    return organization


def get_payment_link(session: Session, case_id: str) -> PaymentLink | None:
    return session.scalar(select(PaymentLink).where(PaymentLink.case_id == case_id))


def get_or_create_policy(session: Session, organization_id: str) -> Policy:
    policy = session.scalar(select(Policy).where(Policy.organization_id == organization_id))
    if not policy:
        policy = Policy(organization_id=organization_id, **asdict(DEFAULT_POLICY))
        session.add(policy)
        session.flush()
    return policy


@dataclass(frozen=True)
class PaymentTotals:
    successful: int = 0
    failed: int = 0
    captured_amount: int = 0
    last_payment_at: datetime | None = None


def observed_payment_totals(
    session: Session,
    organization_id: str,
    customer_ids: Collection[str],
    exclude_provider_payment_id: str | None = None,
) -> dict[str, PaymentTotals]:
    """Per customer: payments RecoverX observed (not seeded history), by each payment's final status."""
    if not customer_ids:
        return {}
    captured = Payment.status == "CAPTURED"
    query = (
        select(
            Payment.customer_id,
            func.sum(case((captured, 1), else_=0)),
            func.sum(case((Payment.status == "FAILED", 1), else_=0)),
            func.sum(case((captured, Payment.amount), else_=0)),
            func.max(Payment.created_at),
        )
        .where(
            Payment.organization_id == organization_id,
            Payment.customer_id.in_(customer_ids),
            Payment.origin != "seed",
        )
        .group_by(Payment.customer_id)
    )
    if exclude_provider_payment_id:
        query = query.where(Payment.provider_payment_id != exclude_provider_payment_id)
    rows = session.execute(query).all()
    return {
        customer_id: PaymentTotals(int(ok or 0), int(failed or 0), int(amount or 0), last)
        for customer_id, ok, failed, amount, last in rows
    }
