"""Razorpay REST adapter (test or live keys).

Selected when ``PAYMENT_PROVIDER=razorpay``. Amounts cross the wire in paise;
RecoverX stores whole rupees. Calls are only made through the Tool Gateway.
"""

import base64
import json
import logging
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.application.ports import PaymentLinkResult, PaymentResult, ProviderCustomer
from app.core.config import settings
from app.core.time import utc_now
from app.domain.events import PAISE_PER_RUPEE
from app.domain.exceptions import NotFoundError, NotRetryableError, ProviderError
from app.infrastructure.database.models import Payment

logger = logging.getLogger(__name__)

_API_BASE_URL = "https://api.razorpay.com/v1"
_TIMEOUT_SECONDS = 20
_RETRYABLE_STATUSES = ("AUTHORIZED", "CREATED")
_REFERENCE_ID_MAX_LENGTH = 40
_PAGE_SIZE = 100  # Razorpay maximum for list endpoints


class RazorpayProvider:
    def __init__(self, key_id: str, key_secret: str) -> None:
        self.key_id = key_id
        self.key_secret = key_secret

    def create_payment_link(self, case_id: str, amount: int, description: str) -> PaymentLinkResult:
        expire_by = utc_now() + timedelta(hours=settings.PAYMENT_LINK_TTL_HOURS)
        response = self._request(
            "POST",
            "/payment_links",
            {
                "amount": amount * PAISE_PER_RUPEE,
                "currency": "INR",
                "accept_partial": False,
                "description": description or f"RecoverX recovery case {case_id}",
                # Razorpay requires a unique reference per link; a case can need a fresh link.
                "reference_id": f"{case_id[: _REFERENCE_ID_MAX_LENGTH - 9]}-{uuid4().hex[:8]}",
                "expire_by": int(expire_by.timestamp()),
                "notes": {"recoverx_case_id": case_id},
            },
        )
        return PaymentLinkResult(
            provider_link_id=response["id"],
            status=str(response.get("status", "created")).upper(),
            short_url=response.get("short_url"),
        )

    def retry_payment(self, session: Session, payment_id: str) -> PaymentResult:
        """Capture an authorized payment.

        Razorpay has no generic retry API. The provider state is re-read first so a
        failed payment is never turned into a blind duplicate charge.
        """
        payment = session.scalar(
            select(Payment).where(or_(Payment.id == payment_id, Payment.provider_payment_id == payment_id))
        )
        if not payment:
            raise NotFoundError(f"Payment {payment_id} not found")
        if payment.status == "CAPTURED":
            return PaymentResult(payment.provider_payment_id, payment.amount, payment.currency, payment.status)

        current = self.fetch_payment(payment.provider_payment_id)
        if not current or current.status not in _RETRYABLE_STATUSES:
            raise NotRetryableError(
                f"Razorpay payment {payment.provider_payment_id} cannot be retried from status "
                f"{current.status if current else 'UNKNOWN'}; send a payment link instead"
            )
        response = self._request(
            "POST",
            f"/payments/{payment.provider_payment_id}/capture",
            {"amount": payment.amount * PAISE_PER_RUPEE, "currency": payment.currency},
        )
        payment.status = str(response.get("status", "captured")).upper()
        session.flush()
        return PaymentResult(
            provider_payment_id=response.get("id", payment.provider_payment_id),
            amount=int(response.get("amount", payment.amount * PAISE_PER_RUPEE)) // PAISE_PER_RUPEE,
            currency=response.get("currency", payment.currency),
            status=payment.status,
        )

    def verify_credentials(self) -> None:
        """Make one authenticated read; raises ProviderError when the keys are rejected."""
        self._request("GET", "/payments?count=1")

    def cancel_payment_link(self, provider_link_id: str) -> None:
        self._request("POST", f"/payment_links/{provider_link_id}/cancel")

    def payment_link_status(self, provider_link_id: str) -> str:
        """Razorpay's current status for a link: CREATED, PARTIALLY_PAID, PAID, EXPIRED or CANCELLED."""
        return str(self._request("GET", f"/payment_links/{provider_link_id}").get("status", "")).upper()

    def list_customers(self, limit: int) -> list[ProviderCustomer]:
        """The account's customers, newest first, fetched a page at a time up to ``limit``."""
        customers: list[ProviderCustomer] = []
        while len(customers) < limit:
            page = self._request("GET", f"/customers?count={_PAGE_SIZE}&skip={len(customers)}").get("items") or []
            customers += [
                ProviderCustomer(item["id"], item.get("name"), item.get("email"), item.get("contact"))
                for item in page
                if item.get("id")
            ]
            if len(page) < _PAGE_SIZE:
                break
        return customers[:limit]

    def fetch_payment(self, provider_payment_id: str) -> PaymentResult | None:
        try:
            response = self._request("GET", f"/payments/{provider_payment_id}")
        except ProviderError:
            return None
        return PaymentResult(
            provider_payment_id=response["id"],
            amount=int(response.get("amount", 0)) // PAISE_PER_RUPEE,
            currency=response.get("currency", "INR"),
            status=str(response.get("status", "unknown")).upper(),
        )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.key_id or not self.key_secret:
            raise ProviderError("Razorpay credentials are not configured")
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        request = Request(  # noqa: S310 - fixed https base URL
            f"{_API_BASE_URL}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
                return json.loads(response.read().decode())
        except (HTTPError, URLError) as exc:
            logger.warning("Razorpay %s %s failed: %s", method, path, exc)
            raise ProviderError(f"Razorpay request failed: {exc}") from exc
