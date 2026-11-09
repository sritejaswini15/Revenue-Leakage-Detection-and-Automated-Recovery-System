from pydantic import BaseModel, Field

from app.api.schemas.common import UtcDatetime


class Funnel(BaseModel):
    risk_cases: int
    eligible: int
    actions: int
    successful: int


class PaymentMetrics(BaseModel):
    captured: int
    captured_amount: int
    failed: int
    failed_amount: int
    authorized: int
    success_rate: float


class RecoveryOverview(BaseModel):
    revenue_at_risk: int
    revenue_targeted: int
    revenue_recovered: int
    recovery_rate: float
    expected_recovery_value: int
    cases: int
    successful_recoveries: int
    escalations: int
    stopped_cases: int
    auto_approved: int
    action_failures: int
    average_ai_confidence: float
    average_recovery_time_hours: float
    funnel: Funnel
    payments: PaymentMetrics
    window_days: int | None
    window_start: UtcDatetime | None
    mode: str


class StrategyPerformance(BaseModel):
    strategy: str
    cases: int
    attempts: int
    success: int
    recovery_rate: float
    recovered: int
    average_recovery_time: float


class TrendPoint(BaseModel):
    date: str
    detected: int
    recovered: int
    cases: int


class ExperimentVariant(BaseModel):
    variant: str
    attempts: int
    recoveries: int
    recovery_rate: float
    revenue_recovered: int


class ExperimentOut(BaseModel):
    id: str
    name: str
    segment: str
    status: str
    variants: list[ExperimentVariant]


class EvaluationSummary(BaseModel):
    cases_processed: int
    advisor_model: str = Field("langgraph-v1", description="The advisor that was evaluated")
    revenue_at_risk: int
    revenue_targeted: int
    revenue_recovered: int
    recovery_rate: float
    average_recovery_probability: float
    average_ai_confidence: float
    successful_recoveries: int
    escalations: int
    false_escalations: int
    policy_violations: int
    invalid_ai_decisions: int
    action_failures: int
    average_recovery_time_hours: float
    evaluation_status: str


class LatestEvaluation(BaseModel):
    summary: EvaluationSummary
    run_by: str
    run_at: UtcDatetime
