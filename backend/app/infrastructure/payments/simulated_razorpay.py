"""Simulated Razorpay: stands in for the payment provider in development and demos.

It mirrors Razorpay's observable behavior: payment links with short URLs,
payment retries recorded as attempts, and HMAC-signed webhook envelopes with
amounts in paise.
"""

import json
import secrets
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.application.ports import PaymentLinkResult, PaymentResult, ProviderCustomer, SignedWebhook
from app.core.config import settings
from app.core.security import hmac_sha256_hex
from app.core.time import utc_now
from app.domain.exceptions import InvalidRequestError, NotFoundError
from app.infrastructure.database.models import Customer, Payment, PaymentAttempt

# Simulated retries succeed for customers above this payment reliability.
_RETRY_SUCCESS_RELIABILITY_PCT = 70.0
SIMULATED_ACCOUNT_ID = "acc_simulated_razorpay"


class SimulatedRazorpayProvider:
    def __init__(self, webhook_secret: str | None = None) -> None:
        self.webhook_secret = webhook_secret or settings.RAZORPAY_WEBHOOK_SECRET

    def create_payment_link(self, case_id: str, amount: int, description: str) -> PaymentLinkResult:  # noqa: ARG002
        public_token = secrets.token_urlsafe(22)
        return PaymentLinkResult(
            provider_link_id=f"plink_{uuid4().hex[:12]}",
            status="OPEN",
            short_url=f"https://rzp.io/i/{public_token}",
            public_token=public_token,
        )

    def cancel_payment_link(self, provider_link_id: str) -> None:
        """Simulated links have no provider-side state to cancel."""

    def list_customers(self, limit: int) -> list[ProviderCustomer]:  # noqa: ARG002
        raise InvalidRequestError(
            "Customer sync reads from Razorpay: add this merchant's Razorpay test keys under Integrations first"
        )

    def retry_payment(self, session: Session, payment_id: str) -> PaymentResult:
        payment = session.scalar(
            select(Payment).where(or_(Payment.id == payment_id, Payment.provider_payment_id == payment_id))
        )
        if not payment:
            raise NotFoundError(f"Payment {payment_id} not found")
        if payment.status == "CAPTURED":  # like Razorpay, never capture twice
            return PaymentResult(payment.provider_payment_id, payment.amount, payment.currency, payment.status)

        customer = session.get(Customer, payment.customer_id)
        reliability = customer.payment_reliability_pct if customer else _RETRY_SUCCESS_RELIABILITY_PCT
        succeeded = reliability > _RETRY_SUCCESS_RELIABILITY_PCT
        payment.status = "CAPTURED" if succeeded else "FAILED"
        failure_reason = None if succeeded else "insufficient_funds"
        session.add(
            PaymentAttempt(
                payment_id=payment.id,
                provider_attempt_id=f"att_{uuid4().hex[:12]}",
                status="SUCCESS" if succeeded else "FAILED",
                failure_reason=failure_reason,
            )
        )
        session.flush()
        return PaymentResult(
            payment.provider_payment_id, payment.amount, payment.currency, payment.status, failure_reason
        )

    def sign_webhook(
        self,
        event: str,
        entity: dict[str, Any],
        account_id: str | None = None,
        event_id: str | None = None,
        related: dict[str, dict[str, Any]] | None = None,
    ) -> SignedWebhook:
        """Build a Razorpay-shaped webhook envelope and its HMAC-SHA256 signature.

        ``entity["amount"]`` must already be in paise. ``related`` adds the other entities
        Razorpay attaches, e.g. the payment inside a ``payment_link.paid`` event.
        """
        event_id = event_id or f"evt_{uuid4().hex[:16]}"
        entity_name = event.split(".", 1)[0]
        payload = {entity_name: {"entity": entity}} | {
            name: {"entity": value} for name, value in (related or {}).items()
        }
        body = {
            "id": event_id,
            "entity": "event",
            "account_id": account_id or SIMULATED_ACCOUNT_ID,
            "event": event,
            "contains": list(payload),
            "payload": payload,
            "created_at": int(utc_now().timestamp()),
        }
        raw_body = json.dumps(body, sort_keys=True).encode()
        return SignedWebhook(event_id, raw_body, hmac_sha256_hex(self.webhook_secret, raw_body))
