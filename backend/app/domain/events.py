"""Normalization of provider events into the canonical RecoverX revenue event.

Two input shapes are accepted:

* Razorpay webhook envelopes (``{"event": "payment.failed", "payload": {...}}``).
  Razorpay amounts are in paise and are converted to whole rupees here.
* Internal canonical dicts (``{"event_type": "PAYMENT_FAILURE", ...}``) whose
  amounts are already in rupees.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.exceptions import InvalidRequestError

PAISE_PER_RUPEE = 100

RAZORPAY_EVENT_TYPES = {
    "payment.failed": "PAYMENT_FAILURE",
    "payment.authorized": "PAYMENT_AUTHORIZED",
    "payment.captured": "PAYMENT_CAPTURED",
    "payment_link.paid": "PAYMENT_LINK_PAID",
    "payment_link.partially_paid": "PAYMENT_LINK_PAID",
    "payment_link.expired": "PAYMENT_LINK_EXPIRED",
    "payment_link.cancelled": "PAYMENT_LINK_CANCELLED",
    "payment_link.abandoned": "PAYMENT_LINK_ABANDONED",
    "invoice.paid": "INVOICE_PAID",
    "invoice.partially_paid": "INVOICE_OVERDUE",
    "invoice.expired": "INVOICE_OVERDUE",
    "invoice.overdue": "INVOICE_OVERDUE",
    "subscription.charged": "SUBSCRIPTION_CHARGED",
    "subscription.halted": "SUBSCRIPTION_HALTED",
    "subscription.cancelled": "SUBSCRIPTION_CANCELLED",
    "subscription.pending": "SUBSCRIPTION_PENDING",
    "checkout.abandoned": "CHECKOUT_ABANDONED",
}

# Money arrived: may resolve an open case.
PAYMENT_SUCCESS_EVENT_TYPES = frozenset(
    {"PAYMENT_CAPTURED", "PAYMENT_LINK_PAID", "INVOICE_PAID", "SUBSCRIPTION_CHARGED"}
)

# Recorded for the audit trail but neither revenue at risk nor a recovery:
# an authorization is not yet captured money, and a cancellation is a deliberate choice.
INFORMATIONAL_EVENT_TYPES = frozenset({"PAYMENT_AUTHORIZED", "PAYMENT_LINK_CANCELLED", "SUBSCRIPTION_CANCELLED"})

# Lifecycle events that, for a link RecoverX itself issued, update that link instead of opening a case.
PAYMENT_LINK_LIFECYCLE_STATUS = {
    "PAYMENT_LINK_EXPIRED": "EXPIRED",
    "PAYMENT_LINK_CANCELLED": "CANCELLED",
    "PAYMENT_LINK_ABANDONED": None,
}

# Razorpay entity names, in lookup order when the event name does not identify one.
_ENTITY_NAMES = ("payment", "payment_link", "invoice", "subscription", "checkout")


class CanonicalRevenueEvent(BaseModel):
    event_type: str
    customer_id: str
    amount: int
    currency: str = "INR"
    source: str = "razorpay"
    source_entity: str = "payment"
    source_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_payment_success(self) -> bool:
        return self.event_type in PAYMENT_SUCCESS_EVENT_TYPES

    @property
    def is_informational(self) -> bool:
        return self.event_type in INFORMATIONAL_EVENT_TYPES

    @property
    def case_reference(self) -> str | None:
        """RecoverX case id carried in provider notes, if any."""
        return case_reference(self.metadata.get("notes"))


def case_reference(notes: dict[str, Any] | None) -> str | None:
    notes = notes or {}
    return notes.get("recoverx_case_id") or notes.get("case_id")


def razorpay_entity(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return ``(entity_name, entity)`` for the primary entity of a webhook envelope.

    The entity named by the event (``payment_link.paid`` -> ``payment_link``)
    takes precedence, because Razorpay also attaches related entities.
    """
    payload = body.get("payload") or {}
    preferred = str(body.get("event", "")).split(".", 1)[0]
    for name in (preferred, *_ENTITY_NAMES):
        if name in payload:
            return name, (payload[name] or {}).get("entity") or {}
    return "payment", {}


def _whole_number(value: Any) -> int:  # noqa: ANN401 - untrusted JSON
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"Amount must be a number, got {value!r}") from exc


def normalize_event(raw_body: dict[str, Any]) -> CanonicalRevenueEvent:
    if "event_type" in raw_body and not ("payment" in raw_body and "contains" in raw_body):
        return _normalize_canonical(raw_body)
    return _normalize_razorpay(raw_body)


def _normalize_canonical(raw_body: dict[str, Any]) -> CanonicalRevenueEvent:
    event_type = raw_body.get("event_type", "PAYMENT_FAILURE")
    return CanonicalRevenueEvent(
        event_type=RAZORPAY_EVENT_TYPES.get(event_type, event_type),
        customer_id=raw_body.get("customer_id", "cus_default"),
        amount=_whole_number(raw_body.get("amount", 0)),
        currency=raw_body.get("currency", "INR"),
        source=raw_body.get("source", "simulated_razorpay"),
        source_entity=raw_body.get("source_entity", "payment"),
        source_id=raw_body.get("id") or raw_body.get("source_id", "src_unknown"),
        timestamp=raw_body.get("timestamp") or datetime.now(UTC).isoformat(),
        metadata=raw_body.get("metadata", {}),
    )


def _normalize_razorpay(raw_body: dict[str, Any]) -> CanonicalRevenueEvent:
    raw_event = raw_body.get("event")
    if raw_event not in RAZORPAY_EVENT_TYPES:
        raise InvalidRequestError(f"Unsupported Razorpay event: {raw_event or 'missing'}")

    source_entity, entity = razorpay_entity(raw_body)
    # A paid link reports the total in `amount` and what was collected in `amount_paid`.
    paid = entity.get("amount_paid") if source_entity == "payment_link" else None
    amount = _whole_number(paid or entity.get("amount", raw_body.get("amount", 0))) // PAISE_PER_RUPEE
    if amount <= 0:
        raise InvalidRequestError("Webhook amount must be at least one rupee (100 paise)")

    notes = entity.get("notes") or {}
    # Links, invoices and subscriptions embed the payment that settled them.
    embedded_payment = ((raw_body.get("payload") or {}).get("payment") or {}).get("entity") or {}
    return CanonicalRevenueEvent(
        event_type=RAZORPAY_EVENT_TYPES[raw_event],
        customer_id=entity.get("customer_id") or notes.get("customer_id") or raw_body.get("customer_id", "cus_default"),
        amount=amount,
        currency=entity.get("currency", "INR"),
        source="razorpay",
        source_entity=source_entity,
        source_id=entity.get("id") or raw_body.get("id", f"src_{raw_event}"),
        metadata={
            "raw_event": raw_event,
            "method": entity.get("method"),
            "error_code": entity.get("error_code"),
            "error_description": entity.get("error_description"),
            "notes": notes,
            "payment_id": entity.get("id") if source_entity == "payment" else embedded_payment.get("id"),
        },
    )
