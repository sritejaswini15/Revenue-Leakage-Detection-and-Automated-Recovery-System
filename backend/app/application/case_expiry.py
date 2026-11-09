"""Close recovery cases that stayed open too long without payment.

Rules (documented in docs/ARCHITECTURE.md):
* Expirable: NEW, ACTIONABLE, WAITING_FOR_PAYMENT and PROMISE_TO_PAY cases opened more
  than the merchant's ``case_expiry_days`` ago (policy, default 7).
* Never expired: recovered, stopped or already expired cases, and escalated cases,
  which wait for a person.
* Deferred while a promise to pay is due in the future, a link is already paid, or an
  authorized payment is awaiting capture.
* On expiry the unpaid link is cancelled so it cannot be reused. A payment that still
  arrives later is recorded as a recovery; money is never ignored.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.application.learning import learn_from_outcome
from app.application.recovery_actions import tool_gateway
from app.core.time import as_utc, utc_now
from app.domain.constants import DEFAULT_POLICY, CaseStatus, PaymentLinkStatus, PromiseStatus
from app.infrastructure.database.models import Payment, Policy, PromiseToPay, RecoveryCase, RevenueEvent
from app.infrastructure.database.repositories import get_payment_link

logger = logging.getLogger(__name__)

EXPIRABLE_STATUSES = (
    CaseStatus.NEW,
    CaseStatus.ACTIONABLE,
    CaseStatus.WAITING_FOR_PAYMENT,
    CaseStatus.PROMISE_TO_PAY,
)


def expire_due_cases(session: Session, now: datetime | None = None) -> int:
    """Expire every eligible case; each case commits on its own. Returns how many expired."""
    now = now or utc_now()
    expiry_days = dict(session.execute(select(Policy.organization_id, Policy.case_expiry_days)).all())
    candidates = session.execute(
        select(RecoveryCase.id, RecoveryCase.organization_id, RecoveryCase.created_at).where(
            RecoveryCase.status.in_(EXPIRABLE_STATUSES)
        )
    ).all()
    expired = 0
    for case_id, organization_id, created_at in candidates:
        days = expiry_days.get(organization_id, DEFAULT_POLICY.case_expiry_days)
        if as_utc(created_at) > now - timedelta(days=days):
            continue
        case = session.get(RecoveryCase, case_id)
        reason = _deferral_reason(session, case, now)
        if reason:
            logger.debug("Not expiring %s: %s", case_id, reason)
            continue
        _expire(session, case, days)
        session.commit()
        expired += 1
    return expired


def _deferral_reason(session: Session, case: RecoveryCase, now: datetime) -> str | None:
    promise_dates = session.scalars(
        select(PromiseToPay.promised_date).where(
            PromiseToPay.case_id == case.id, PromiseToPay.status == PromiseStatus.PENDING
        )
    ).all()
    if any(as_utc(date) > now for date in promise_dates):
        return "a promise to pay is still due"
    link = get_payment_link(session, case.id)
    if link and link.status == PaymentLinkStatus.PAID:
        return "the payment link is paid"
    awaiting_capture = session.scalar(
        select(Payment.id)
        .join(RevenueEvent, RevenueEvent.source_id == Payment.provider_payment_id)
        .where(
            RevenueEvent.id == case.revenue_event_id,
            Payment.organization_id == case.organization_id,
            Payment.status == "AUTHORIZED",
        )
    )
    if awaiting_capture:
        return "a payment is authorized and awaiting capture"
    return None


def _expire(session: Session, case: RecoveryCase, expiry_days: int) -> None:
    link = tool_gateway().cancel_open_link(session, case)
    previous = case.status
    case.status = CaseStatus.EXPIRED
    learn_from_outcome(session, case, recovered=False, previous_status=previous)
    record_audit(
        session,
        case.organization_id,
        "case.expired",
        {
            "previous_status": previous,
            "expiry_days": expiry_days,
            "payment_link_cancelled": link.provider_link_id if link else None,
        },
        actor="case_expiry",
        case_id=case.id,
    )
