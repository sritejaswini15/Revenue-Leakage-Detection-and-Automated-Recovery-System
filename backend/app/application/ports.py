"""Interfaces the application layer depends on; infrastructure provides the implementations.

``retry_payment`` receives the database session because the simulated provider
models Razorpay's payment state in the local database.
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.domain.decisions import RecoveryContext, RecoveryDecision


@dataclass(frozen=True)
class PaymentResult:
    provider_payment_id: str
    amount: int
    currency: str
    status: str
    failure_reason: str | None = None


@dataclass(frozen=True)
class PaymentLinkResult:
    provider_link_id: str
    status: str
    short_url: str | None = None
    # Opaque token for the RecoverX-hosted payment page (simulated provider only).
    public_token: str | None = None


@dataclass(frozen=True)
class SignedWebhook:
    """A provider callback as received on the wire: raw body plus HMAC signature."""

    event_id: str
    raw_body: bytes
    signature: str


@dataclass(frozen=True)
class ProviderCustomer:
    id: str
    name: str | None
    email: str | None
    contact: str | None


@dataclass(frozen=True)
class MessageResult:
    message_id: str
    channel: str
    recipient: str
    status: str


class PaymentProvider(Protocol):
    def create_payment_link(self, case_id: str, amount: int, description: str) -> PaymentLinkResult: ...

    def cancel_payment_link(self, provider_link_id: str) -> None: ...

    def retry_payment(self, session: Session, payment_id: str) -> PaymentResult: ...

    def list_customers(self, limit: int) -> list[ProviderCustomer]: ...


class MessagingProvider(Protocol):
    channel: str

    def send_message(
        self, session: Session, case_id: str, customer_id: str, recipient: str, content: str
    ) -> MessageResult: ...


class RecoveryAdvisor(Protocol):
    """Produces a validated recovery recommendation. It never executes actions."""

    model_name: str

    def advise(self, context: RecoveryContext) -> RecoveryDecision: ...
