from pydantic import BaseModel, Field

from app.api.schemas.common import ApiModel, UtcDatetime
from app.application.cases import CaseDetail
from app.infrastructure.database.models import RecoveryCase


class CaseSummary(BaseModel):
    id: str
    customer_id: str
    customer: str
    customer_phone: str
    customer_email: str
    customer_reliability: float
    preferred_channel: str
    preferred_language: str
    event_type: str
    amount: int
    currency: str
    risk_score: int
    recovery_probability: float
    expected_recovery_value: int
    ai_confidence: float
    policy_decision: str
    recommended_action: str
    status: str
    contact_attempts: int
    recovered_amount: int
    action_executed: bool
    failure_reason: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @classmethod
    def from_case(cls, case: RecoveryCase) -> "CaseSummary":
        customer, risk = case.customer, case.risk_assessment
        return cls(
            id=case.id,
            customer_id=case.customer_id,
            customer=customer.name,
            customer_phone=customer.phone,
            customer_email=customer.email,
            customer_reliability=customer.payment_reliability_pct,
            preferred_channel=customer.preferred_channel,
            preferred_language=customer.preferred_language,
            event_type=case.revenue_event.event_type,
            amount=case.amount,
            currency=case.currency,
            risk_score=risk.risk_score,
            recovery_probability=risk.recovery_probability,
            expected_recovery_value=risk.expected_recovery_value,
            ai_confidence=case.ai_confidence,
            policy_decision=case.policy_decision,
            recommended_action=case.recommended_action,
            status=case.status,
            contact_attempts=case.contact_attempts,
            recovered_amount=case.recovered_amount,
            action_executed=case.action_executed,
            failure_reason=case.failure_reason,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )


class CaseList(BaseModel):
    items: list[CaseSummary]
    total: int


class EscalationItem(CaseSummary):
    escalation_reason: str = Field(description="Reason code(s), comma-separated")
    queue: str = Field(
        description=(
            "HIGH_VALUE, LOW_CONFIDENCE, PROMISE_MISSED, MAX_ATTEMPTS, PROVIDER_FAILURE, CUSTOMER_REQUEST "
            "or SPECIAL_CASE"
        )
    )
    escalated_at: UtcDatetime | None


class EscalationList(BaseModel):
    items: list[EscalationItem]
    total: int = Field(description="Cases matching the requested queue (all queues by default)")
    limit: int
    offset: int
    queues: dict[str, int] = Field(description="Cases waiting in each review queue, whatever the filter")


class PaymentLinkOut(BaseModel):
    id: str
    provider_link_id: str | None
    amount: int
    status: str
    short_url: str | None
    payment_url: str | None = Field(None, description="RecoverX-hosted customer page (simulated provider only)")


class CommunicationOut(ApiModel):
    id: str
    channel: str
    direction: str
    content: str
    status: str
    created_at: UtcDatetime


class EscalationOut(ApiModel):
    id: str
    reason: str
    resolution: str | None
    resolution_notes: str | None


class PromiseOut(ApiModel):
    id: str
    amount: int
    promised_date: UtcDatetime
    status: str
    fulfilled: bool


class RecoveryActionOut(BaseModel):
    id: str
    action_type: str
    status: str
    created_at: UtcDatetime
    detail: str | None = Field(None, description="Why it failed or fell back, when it did")


class PaymentAttemptHistoryOut(BaseModel):
    provider_payment_id: str
    attempt_id: str | None
    status: str
    failure_reason: str | None
    created_at: UtcDatetime


def _action_detail(payload: dict | None) -> str | None:
    payload = payload or {}
    if payload.get("error"):
        return str(payload["error"])[:255]
    if payload.get("fallback"):
        status = payload.get("retry_status") or "not retryable"
        return f"Retry {status.lower()}; sent a payment link instead"
    return None


class PolicyEvaluationOut(BaseModel):
    decision: str = Field(description="AUTO_APPROVE, HUMAN_REVIEW or STOP")
    reasons: list[str]
    violations: list[str]
    max_amount_limit: int
    min_confidence_limit: float
    current_attempts: int
    max_attempts_limit: int


class RiskReasonOut(BaseModel):
    text: str
    effect: str = Field(description="positive, negative or neutral: how it moves the chance of recovery")


class CaseDetailOut(CaseSummary):
    advisor_model: str | None = Field(
        None, description="Producer of the diagnosis, e.g. langgraph-v1 or synthetic-seed"
    )
    diagnosis: str
    root_cause: str
    ai_reasoning: str
    payment_link: PaymentLinkOut | None
    communications: list[CommunicationOut]
    escalation: EscalationOut | None
    promises_to_pay: list[PromiseOut]
    actions: list[RecoveryActionOut]
    payment_attempts: list[PaymentAttemptHistoryOut]
    executable_actions: list[str]
    policy_evaluation: PolicyEvaluationOut | None = Field(
        None, description="The merchant's current policy applied now; the Tool Gateway re-checks it before acting"
    )
    risk_reasons: list[RiskReasonOut] = Field(default_factory=list, description="Why the case is prioritised")

    @classmethod
    def from_detail(cls, detail: CaseDetail) -> "CaseDetailOut":
        case, decision, link = detail.case, detail.decision, detail.payment_link
        payment_link = None
        if link:
            payment_link = PaymentLinkOut(
                id=link.id,
                provider_link_id=link.provider_link_id,
                amount=link.amount,
                status=link.status,
                short_url=link.short_url,
                payment_url=detail.payment_url,
            )
        return cls(
            **CaseSummary.from_case(case).model_dump(),
            advisor_model=decision.model if decision else None,
            diagnosis=decision.diagnosis if decision else "Payment issue diagnosed",
            root_cause=decision.root_cause if decision else case.failure_reason or "PAYMENT_METHOD_DECLINE",
            ai_reasoning=decision.reasoning if decision else "No advisor reasoning was recorded for this case",
            payment_link=payment_link,
            communications=[CommunicationOut.model_validate(item) for item in detail.communications],
            escalation=EscalationOut.model_validate(detail.escalation) if detail.escalation else None,
            promises_to_pay=[PromiseOut.model_validate(item) for item in detail.promises],
            actions=[
                RecoveryActionOut(
                    id=action.id,
                    action_type=action.action_type,
                    status=action.status,
                    created_at=action.created_at,
                    detail=_action_detail(action.payload),
                )
                for action in detail.actions
            ],
            payment_attempts=[
                PaymentAttemptHistoryOut(
                    provider_payment_id=payment.provider_payment_id,
                    attempt_id=attempt.provider_attempt_id,
                    status=attempt.status,
                    failure_reason=attempt.failure_reason,
                    created_at=attempt.created_at,
                )
                for payment, attempt in detail.payment_attempts
            ],
            executable_actions=detail.executable_actions,
            policy_evaluation=PolicyEvaluationOut(
                decision=detail.policy.decision,
                reasons=detail.policy.reasons,
                violations=detail.policy.rule_violations,
                max_amount_limit=detail.policy.max_amount_limit,
                min_confidence_limit=detail.policy.min_confidence_limit,
                current_attempts=detail.policy.current_attempts,
                max_attempts_limit=detail.policy.max_attempts_limit,
            )
            if detail.policy
            else None,
            risk_reasons=[RiskReasonOut(text=reason.text, effect=reason.effect) for reason in detail.risk_reasons],
        )


class ExecuteRequest(BaseModel):
    action_type: str | None = Field(
        None, description="One of the case's executable_actions; defaults to the recommended action"
    )


class CaseActionResult(BaseModel):
    case_id: str
    status: str
    action_id: str | None = None


class PaymentLinkStatusOut(BaseModel):
    case_id: str
    payment_link_id: str
    amount: int
    currency: str
    status: str
    short_url: str | None
    expires_at: UtcDatetime | None


class ResolutionRequest(BaseModel):
    resolution: str = Field(
        min_length=2,
        max_length=64,
        description=(
            "APPROVE runs the recommended action, EDIT runs `action_type`, CONTACT sends personalised outreach; "
            "REJECT and CLOSE stop the case"
        ),
    )
    action_type: str | None = Field(None, max_length=64, description="Required with EDIT: the action to run")
    notes: str | None = Field(None, max_length=2000)


class EscalateRequest(BaseModel):
    notes: str | None = Field(None, max_length=2000, description="What the reviewer should know")


class CasePromiseRequest(BaseModel):
    amount: int = Field(gt=0, description="Whole rupees, at most the amount owed")
    promised_date: UtcDatetime = Field(description="When the customer said they would pay; today or later")
    notes: str | None = Field(None, max_length=2000)


class ResolutionResult(BaseModel):
    case_id: str
    resolution: str | None
    status: str
