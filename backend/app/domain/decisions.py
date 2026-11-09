"""Input and output contracts for the AI recovery advisor."""

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.domain.constants import ActionType

# Root cause recorded when no usable recommendation could be produced; the case goes to a person.
ADVISOR_OUTPUT_INVALID = "ADVISOR_OUTPUT_INVALID"
# Escalation reason for such cases (review queue: low confidence).
INVALID_AI_OUTPUT = "INVALID_AI_OUTPUT"


@dataclass(frozen=True)
class RecoveryContext:
    """Everything the advisor may consider. Amounts and scores are persisted values, never AI output."""

    event_type: str
    amount: int
    currency: str
    customer_name: str
    customer_reliability: float
    successful_payments: int
    failed_payments: int
    preferred_channel: str
    preferred_language: str
    overdue_days: int = 0
    repeat_failures: int = 0
    # The customer's recovery history: what has worked for them before.
    historical_recovery_rate: float = 0.0
    previous_recovery_attempts: int = 0
    best_historical_recovery_action: str | None = None
    lifetime_value: int = 0
    # The deterministic risk engine's figures for this case, when already computed.
    risk_score: int | None = None
    recovery_probability: float | None = None
    expected_recovery_value: int | None = None


class RecoveryDecision(BaseModel):
    """Validated advisor output. Anything outside this schema is rejected."""

    diagnosis: str = Field(..., min_length=1, description="What happened and why")
    root_cause: str = Field(..., min_length=1, max_length=160, description="Categorized root cause of the loss")
    recovery_probability: float = Field(..., ge=0.0, le=1.0, description="Advisor's estimate of recovery likelihood")
    recommended_action: ActionType = Field(..., description="Action from the controlled vocabulary")
    reasoning: str = Field(..., min_length=1, description="Why this action was selected")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Advisor confidence in the recommendation")
    communication_tone: str = Field(default="empathetic")
    suggested_language: str = Field(default="English")

    @property
    def is_safe_failure(self) -> bool:
        return self.root_cause == ADVISOR_OUTPUT_INVALID


def safe_failure_decision(problem: str, language: str = "English") -> RecoveryDecision:
    """What the advisor returns when it cannot produce a valid recommendation: nothing runs, a person decides.

    Zero confidence is below any merchant's threshold, so policy always routes the case to human review.
    """
    return RecoveryDecision(
        diagnosis="The recovery advisor could not produce a valid recommendation.",
        root_cause=ADVISOR_OUTPUT_INVALID,
        recovery_probability=0.0,
        recommended_action=ActionType.ESCALATE_TO_HUMAN,
        reasoning=f"Safe failure to human review: {problem}"[:2000],
        confidence=0.0,
        suggested_language=language,
    )
