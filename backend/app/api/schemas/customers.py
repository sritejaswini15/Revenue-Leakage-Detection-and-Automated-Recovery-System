from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas.common import ApiModel, UtcDatetime
from app.application.customers import CustomerDetail, CustomerView


class CustomerStatsOut(ApiModel):
    """Combined figures, and the merchant profile and observed payments they were computed from."""

    reliability_pct: float
    successful_payments: int
    failed_payments: int
    profile_reliability_pct: float
    profile_successful: int
    profile_failed: int
    observed_successful: int
    observed_failed: int
    observed_captured_amount: int
    last_payment_at: UtcDatetime | None


class CustomerOut(ApiModel):
    id: str
    razorpay_customer_id: str
    name: str
    email: str
    phone: str
    lifetime_value: int
    # Combined profile and observed figures; see `stats` for the breakdown.
    successful_payments: int
    failed_payments: int
    payment_reliability_pct: float
    preferred_channel: str
    preferred_language: str
    historical_recovery_rate: float
    previous_recovery_attempts: int = 0
    best_historical_recovery_action: str | None = None
    opted_out: bool
    created_at: UtcDatetime
    stats: CustomerStatsOut

    @classmethod
    def of(cls, view: CustomerView) -> "CustomerOut":
        return cls._build(view)

    @classmethod
    def _build(cls, view: CustomerView, **extra: object) -> "CustomerOut":
        customer, stats = view.customer, view.stats
        fields = {name: getattr(customer, name) for name in CustomerOut.model_fields if hasattr(customer, name)}
        return cls.model_validate(
            {
                **fields,
                "successful_payments": stats.successful_payments,
                "failed_payments": stats.failed_payments,
                "payment_reliability_pct": stats.reliability_pct,
                "stats": CustomerStatsOut.model_validate(stats),
                **extra,
            }
        )


class CustomerList(BaseModel):
    items: list[CustomerOut]
    total: int
    limit: int
    offset: int


class CustomerPaymentOut(ApiModel):
    id: str
    provider_payment_id: str
    amount: int
    currency: str
    status: str
    method: str | None
    failure_reason: str | None
    origin: str
    recovery_case_id: str | None
    created_at: UtcDatetime


class CustomerCaseOut(ApiModel):
    id: str
    status: str
    amount: int
    recovered_amount: int
    currency: str
    created_at: UtcDatetime


class CustomerDetailOut(CustomerOut):
    payments: list[CustomerPaymentOut]
    cases: list[CustomerCaseOut]

    @classmethod
    def of_detail(cls, detail: CustomerDetail) -> "CustomerDetailOut":
        return cls._build(
            detail.view,
            payments=[CustomerPaymentOut.model_validate(payment) for payment in detail.payments],
            cases=[CustomerCaseOut.model_validate(case) for case in detail.cases],
        )


class CustomerImportIn(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000, description="UTF-8 CSV text with a header row")
    commit: bool = Field(False, description="false previews the import; true writes it")


class CustomerSyncIn(BaseModel):
    commit: bool = False


class ImportRowOut(ApiModel):
    line: int
    action: Literal["create", "update", "duplicate", "invalid"]
    name: str
    email: str
    phone: str
    razorpay_customer_id: str
    errors: list[str]


class ImportSummaryOut(ApiModel):
    source: Literal["csv", "razorpay"]
    committed: bool
    total_rows: int
    created: int
    updated: int
    duplicates: int
    invalid: int
    ignored_columns: list[str]
    rows: list[ImportRowOut]
