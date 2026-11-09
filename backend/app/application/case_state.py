"""Case state transitions shared by several use cases. Callers own the transaction."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.application.learning import learn_from_outcome
from app.domain.constants import TERMINAL_CASE_STATUSES, CaseStatus, PromiseStatus
from app.domain.exceptions import ConflictError, InvalidRequestError
from app.infrastructure.database.models import Escalation, PromiseToPay, RecoveryCase


def ensure_open(case: RecoveryCase) -> None:
    """A recovered, stopped or expired case keeps its outcome: no transition may rewrite it."""
    if case.status in TERMINAL_CASE_STATUSES:
        raise ConflictError(f"Case {case.id} is already {case.status}")


def escalate_case(
    session: Session,
    case: RecoveryCase,
    reason: str,
    actor: str = "system",
    assigned_to_user_id: str | None = None,
    notes: str | None = None,
) -> Escalation:
    ensure_open(case)
    escalation = session.scalar(select(Escalation).where(Escalation.case_id == case.id))
    if escalation:
        escalation.reason = reason
        escalation.resolution_notes = notes
    else:
        escalation = Escalation(
            case_id=case.id, reason=reason, assigned_to_user_id=assigned_to_user_id, resolution_notes=notes
        )
        session.add(escalation)

    case.status = CaseStatus.ESCALATED
    record_audit(
        session,
        case.organization_id,
        "escalation.created",
        {"reason": reason, "assigned_to": assigned_to_user_id},
        actor=actor,
        case_id=case.id,
    )
    session.flush()
    return escalation


def record_promise(
    session: Session,
    case: RecoveryCase,
    amount: int,
    promised_date: datetime,
    actor: str = "system",
    notes: str | None = None,
) -> PromiseToPay:
    ensure_open(case)
    if amount <= 0 or amount > case.amount:
        raise InvalidRequestError(f"A promise must be for between ₹1 and the ₹{case.amount:,} owed")
    # A new promise replaces any earlier one still pending: only the latest date is checked.
    earlier = session.scalars(
        select(PromiseToPay).where(PromiseToPay.case_id == case.id, PromiseToPay.status == PromiseStatus.PENDING)
    ).all()
    for previous in earlier:
        previous.status = PromiseStatus.SUPERSEDED
        record_audit(
            session,
            case.organization_id,
            "promise.superseded",
            {"promise_id": previous.id, "promised_date": previous.promised_date.isoformat()},
            actor=actor,
            case_id=case.id,
        )
    promise = PromiseToPay(
        case_id=case.id,
        customer_id=case.customer_id,
        amount=amount,
        promised_date=promised_date,
        notes=notes,
        status=PromiseStatus.PENDING,
    )
    session.add(promise)
    case.status = CaseStatus.PROMISE_TO_PAY
    record_audit(
        session,
        case.organization_id,
        "promise.created",
        {"amount": amount, "promised_date": promised_date.isoformat()},
        actor=actor,
        case_id=case.id,
    )
    session.flush()
    return promise


def mark_recovered(
    session: Session, case: RecoveryCase, amount: int, event_type: str, actor: str, correlation_id: str
) -> None:
    previous = case.status
    case.status = CaseStatus.RECOVERED
    case.recovered_amount = amount
    record_audit(
        session,
        case.organization_id,
        "payment.recovered",
        {"amount": amount, "event_type": event_type},
        actor=actor,
        case_id=case.id,
        correlation_id=correlation_id,
    )
    record_audit(
        session,
        case.organization_id,
        "workflow.closed",
        {"resolution": "SUCCESS", "recovered_amount": amount},
        actor=actor,
        case_id=case.id,
        correlation_id=correlation_id,
    )
    learn_from_outcome(session, case, recovered=True, previous_status=previous)
