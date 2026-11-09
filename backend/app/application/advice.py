"""The advisor's view of an existing case, built from persisted records only."""

from sqlalchemy.orm import Session

from app.application.customers import combined_profile
from app.domain.decisions import RecoveryContext
from app.infrastructure.database.models import Customer, RecoveryCase


def advisor_context(session: Session, case: RecoveryCase) -> RecoveryContext:
    """What the advisor sees for an existing case: persisted amounts, scores and the customer's history."""
    customer = session.get(Customer, case.customer_id)
    profile = combined_profile(session, customer)
    risk = case.risk_assessment
    factors = (risk.factors or {}) if risk else {}
    return RecoveryContext(
        event_type=case.revenue_event.event_type if case.revenue_event else "PAYMENT_FAILURE",
        amount=case.amount,
        currency=case.currency,
        customer_name=customer.name,
        customer_reliability=profile.payment_reliability_pct,
        successful_payments=profile.successful_payments,
        failed_payments=profile.failed_payments,
        preferred_channel=customer.preferred_channel,
        preferred_language=customer.preferred_language,
        overdue_days=int(factors.get("overdue_days") or 0),
        repeat_failures=int(factors.get("repeat_failures") or 0),
        historical_recovery_rate=profile.historical_recovery_rate,
        previous_recovery_attempts=customer.previous_recovery_attempts,
        best_historical_recovery_action=customer.best_historical_recovery_action,
        lifetime_value=customer.lifetime_value,
        risk_score=risk.risk_score if risk else None,
        recovery_probability=risk.recovery_probability if risk else None,
        expected_recovery_value=risk.expected_recovery_value if risk else None,
    )
