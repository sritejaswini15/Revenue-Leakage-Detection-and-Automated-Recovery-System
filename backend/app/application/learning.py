"""Learning from outcomes: every closed case updates the customer's recovery profile.

The profile (recovery rate, number of past recoveries, the action that most often recovered
them) feeds the next case: the risk engine's recovery probability, the advisor's choice of
strategy, and the customer page. Financial figures stay deterministic; only these inputs move.
Callers own the transaction.
"""

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.domain.constants import ActionStatus, CaseStatus
from app.infrastructure.database.models import Customer, RecoveryAction, RecoveryCase


def learn_from_outcome(session: Session, case: RecoveryCase, recovered: bool, previous_status: str | None) -> None:
    """Fold one closed case into its customer's profile. Call it as the case closes, once."""
    customer = session.get(Customer, case.customer_id)
    if customer is None:
        return
    attempts = customer.previous_recovery_attempts or 0
    rate = customer.historical_recovery_rate or 0.0
    if recovered and previous_status == CaseStatus.EXPIRED and attempts:
        # Already counted as a loss when it expired; the late payment makes it a recovery.
        rate = (rate * attempts + 1) / attempts
    else:
        rate = (rate * attempts + (1 if recovered else 0)) / (attempts + 1)
        attempts += 1
    customer.previous_recovery_attempts = attempts
    customer.historical_recovery_rate = round(min(1.0, max(0.0, rate)), 4)
    if recovered:
        customer.best_historical_recovery_action = (
            most_successful_action(session, customer.id) or customer.best_historical_recovery_action
        )
    record_audit(
        session,
        case.organization_id,
        "customer.profile_learned",
        {
            "customer_id": customer.id,
            "recovered": recovered,
            "historical_recovery_rate": customer.historical_recovery_rate,
            "previous_recovery_attempts": attempts,
            "best_historical_recovery_action": customer.best_historical_recovery_action,
        },
        actor="learning",
        case_id=case.id,
    )


def most_successful_action(session: Session, customer_id: str) -> str | None:
    """The executed action found in the most of this customer's recovered cases."""
    recovered_cases = func.count(func.distinct(RecoveryAction.recovery_case_id))
    row = session.execute(
        select(RecoveryAction.action_type, recovered_cases.label("recoveries"))
        .join(RecoveryCase, RecoveryCase.id == RecoveryAction.recovery_case_id)
        .where(
            RecoveryCase.customer_id == customer_id,
            RecoveryCase.status == CaseStatus.RECOVERED,
            RecoveryAction.status == ActionStatus.EXECUTED,
        )
        .group_by(RecoveryAction.action_type)
        .order_by(desc("recoveries"), RecoveryAction.action_type)
        .limit(1)
    ).first()
    return row.action_type if row else None
