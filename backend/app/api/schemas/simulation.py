from pydantic import BaseModel, Field

from app.api.schemas.common import ApiModel, UtcDatetime


class SimulationRequest(BaseModel):
    case_id: str | None = None
    customer_id: str | None = None


class SimulatedPaymentResult(BaseModel):
    case_id: str
    status: str
    recovered_amount: int
    message: str


class RetryResult(BaseModel):
    case_id: str
    status: str = Field(description="The retried action's final status, e.g. EXECUTED")
    workflow_preserved: bool = Field(description="The case is still open and being worked")
    action_id: str
    action_type: str
    failed_attempts: int = Field(description="Attempts that timed out and were recorded as FAILED")
    messages_sent: int = Field(description="Messages the customer received across all attempts")


class OptOutResult(BaseModel):
    customer_id: str
    opted_out: bool
    cases_stopped: int


class LowConfidenceResult(BaseModel):
    case_id: str
    status: str
    ai_confidence: float


class MaxAttemptsResult(BaseModel):
    case_id: str
    status: str
    contact_attempts: int


class InvalidAiResult(BaseModel):
    case_id: str
    status: str
    fallback: str


class PromiseRequest(BaseModel):
    case_id: str
    amount: int = Field(gt=0)
    promised_date: UtcDatetime
    notes: str | None = None


class PromiseResult(ApiModel):
    id: str
    case_id: str
    amount: int
    promised_date: UtcDatetime
    status: str


class EventRequest(BaseModel):
    event_type: str = Field(description="Canonical type, e.g. PAYMENT_FAILURE or INVOICE_OVERDUE")
    customer_id: str | None = None
    amount: int = Field(0, ge=0, description="Whole rupees")
    currency: str = "INR"
    id: str | None = None


class EventResult(BaseModel):
    event_id: str | None
    case_id: str | None
    event_type: str | None
    status: str | None
    is_duplicate: bool
