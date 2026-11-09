from typing import Literal

from pydantic import BaseModel

from app.api.schemas.common import ApiModel, UtcDatetime
from app.application.transactions import TransactionView


class PublicPaymentLink(BaseModel):
    id: str
    short_url: str | None
    amount: int
    status: str


class PublicPaymentRequest(BaseModel):
    """What an unauthenticated customer may see; deliberately excludes contact details."""

    case_id: str
    customer_name: str
    amount: int
    currency: str
    status: str
    recovered_amount: int
    event_type: str
    failure_reason: str
    recommended_action: str
    payment_link: PublicPaymentLink


class CustomerPaymentAction(BaseModel):
    action: Literal["pay", "promise"] = "pay"
    promise_date: str | None = None


class CustomerPaymentResult(BaseModel):
    case_id: str
    status: str
    recovered_amount: int | None = None
    promise_id: str | None = None
    promised_date: str | None = None
    message: str | None = None


class TransactionOut(BaseModel):
    id: str
    provider_payment_id: str
    amount: int
    currency: str
    status: str
    method: str | None
    failure_reason: str | None
    origin: str
    customer_id: str
    customer_name: str | None
    recovery_case_id: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @classmethod
    def of(cls, view: TransactionView) -> "TransactionOut":
        payment = view.payment
        fields = {name: getattr(payment, name) for name in cls.model_fields if hasattr(payment, name)}
        return cls.model_validate({**fields, "customer_name": view.customer_name})


class TransactionList(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class PaymentAttemptOut(ApiModel):
    provider_attempt_id: str | None
    status: str
    failure_reason: str | None
    created_at: UtcDatetime


class LinkedCaseOut(ApiModel):
    id: str
    status: str
    amount: int
    recovered_amount: int


class TransactionDetailOut(TransactionOut):
    attempts: list[PaymentAttemptOut]
    case: LinkedCaseOut | None
