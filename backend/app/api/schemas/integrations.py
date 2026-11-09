from typing import Literal

from pydantic import BaseModel, Field, SecretStr

from app.api.schemas.common import ApiModel, UtcDatetime


class IntegrationEntry(BaseModel):
    name: str
    category: str
    status: Literal["CONNECTED", "CONFIGURED", "SIMULATED", "NOT_CONFIGURED"]
    label: str
    detail: str


class IntegrationCounts(BaseModel):
    total: int
    connected: int
    configured: int
    simulated: int
    not_configured: int


class IntegrationStatus(BaseModel):
    integrations: list[IntegrationEntry]
    counts: IntegrationCounts


class RazorpayIntegrationOut(ApiModel):
    """The merchant's payment configuration. Secrets are never returned, only whether they are set."""

    source: Literal["merchant", "legacy-environment", "simulated"]
    provider: str
    mode: Literal["test", "live"] | None
    key_id_masked: str | None
    has_webhook_secret: bool
    account_id: str | None
    enabled: bool
    validated_at: UtcDatetime | None
    last_error: str | None
    live_payments_allowed: bool
    webhook_path: str = "/webhooks/razorpay"


class RazorpayCredentialsIn(BaseModel):
    key_id: str = Field(min_length=10, max_length=64, pattern=r"^rzp_(test|live)_[A-Za-z0-9]+$")
    key_secret: SecretStr = Field(min_length=8, max_length=128)
    account_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    webhook_secret: SecretStr | None = Field(None, min_length=8, max_length=256)
    verify: bool = Field(True, description="Call Razorpay with the keys before reporting success")


class IntegrationToggle(BaseModel):
    enabled: bool
