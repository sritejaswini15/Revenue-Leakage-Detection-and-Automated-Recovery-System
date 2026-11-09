"""Read-only views of the business entities the domain rules operate on.

The SQLAlchemy models in ``app.infrastructure.database.models`` satisfy these
protocols structurally, so domain logic never imports the ORM.
"""

from typing import Protocol


class CustomerProfile(Protocol):
    payment_reliability_pct: float
    historical_recovery_rate: float
    successful_payments: int
    failed_payments: int
    opted_out: bool


class CaseState(Protocol):
    status: str
    amount: int
    contact_attempts: int
    ai_confidence: float


class PolicyRules(Protocol):
    max_auto_action_amount: int
    human_approval_above_amount: int
    max_contact_attempts: int
    escalate_after_days: int
    min_ai_confidence: float
