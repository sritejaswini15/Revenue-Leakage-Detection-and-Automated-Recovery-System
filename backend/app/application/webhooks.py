"""Razorpay webhook receipt: merchant routing, signature verification, replay protection.

Simulated provider callbacks go through the same receipt path as real webhooks.

Response policy for real webhooks: authentication and routing failures are errors
(4xx). An authentic event that RecoverX cannot use (unsupported type, unknown
customer) is acknowledged with ``accepted=False`` and kept as a REJECTED receipt,
because Razorpay retries non-2xx deliveries and eventually disables the webhook.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.case_ingestion import process_revenue_event
from app.application.customers import customer_key
from app.application.payment_integrations import processes_events, webhook_secret_for
from app.application.ports import SignedWebhook
from app.core.config import settings
from app.core.security import constant_time_equals, hmac_sha256_hex
from app.core.time import utc_now
from app.domain.events import case_reference, normalize_event, razorpay_entity
from app.domain.exceptions import AuthenticationError, InvalidRequestError, NotFoundError, ProviderError
from app.infrastructure.database.models import Customer, Organization, RecoveryCase, RevenueEvent, WebhookReceipt
from app.infrastructure.database.repositories import find_case, find_customer

logger = logging.getLogger(__name__)

_ENTITY_NAMES = ("payment", "payment_link", "invoice", "subscription", "checkout")
# Keeps "razorpay:event:<id>" within the 80-character correlation columns.
MAX_EVENT_ID_LENGTH = 64


@dataclass(frozen=True)
class WebhookOutcome:
    dedupe_key: str
    duplicate: bool
    event: RevenueEvent | None
    case: RecoveryCase | None
    accepted: bool = True
    reason: str | None = None


def receive_razorpay_webhook(
    session: Session,
    raw_body: bytes,
    signature: str | None,
    account_id_header: str | None,
    event_id_header: str | None = None,
) -> WebhookOutcome:
    body = _parse_body(raw_body)

    # Resolve the merchant before checking the signature so a valid signature for
    # merchant A can never be applied to merchant B.
    account_id = account_id_header or body.get("account_id")
    if not account_id:
        raise InvalidRequestError("Razorpay account id required")
    organization = session.scalar(select(Organization).where(Organization.razorpay_account_id == account_id))
    if not organization:
        raise NotFoundError("Unknown Razorpay merchant account")

    secret = webhook_secret_for(session, organization)
    if not secret:
        raise AuthenticationError("No webhook signing secret is configured for this merchant")
    if not signature:
        raise AuthenticationError("Webhook signature required")
    if not constant_time_equals(hmac_sha256_hex(secret, raw_body), signature):
        logger.warning("Rejected webhook with invalid signature for account %s", account_id)
        raise AuthenticationError("Invalid webhook signature")

    provider_event_id = _provider_event_id(body, event_id_header)
    replay = _replayed_outcome(session, organization, provider_event_id)
    if replay:
        return replay
    receipt = _receipt(session, organization, provider_event_id, body)
    if not processes_events(session, organization.id):
        return _ignore(session, receipt, "Razorpay integration is disabled for this merchant")

    try:
        normalize_event(body)
    except InvalidRequestError as exc:
        return _ignore(session, receipt, str(exc))
    customer = _resolve_customer(session, organization, body)
    if customer is None:
        return _ignore(session, receipt, "No customer available for event")

    outcome = _process(session, organization, customer, body, receipt, actor="razorpay_webhook")
    session.commit()
    return outcome


def ingest_simulated_webhook(
    session: Session, organization: Organization, customer: Customer, webhook: SignedWebhook, actor: str
) -> WebhookOutcome:
    """Process a simulated provider callback exactly as a signed Razorpay webhook. Caller commits.

    Unlike real webhooks, invalid simulated events raise, so the operator sees the error.
    """
    expected = hmac_sha256_hex(settings.RAZORPAY_WEBHOOK_SECRET, webhook.raw_body)
    if not constant_time_equals(expected, webhook.signature):
        raise ProviderError("Simulated webhook signature invalid")
    body = _parse_body(webhook.raw_body)
    replay = _replayed_outcome(session, organization, webhook.event_id)
    if replay:
        return replay
    receipt = _receipt(session, organization, webhook.event_id, body)
    return _process(session, organization, customer, body, receipt, actor=actor)


def _replayed_outcome(session: Session, organization: Organization, provider_event_id: str) -> WebhookOutcome | None:
    receipt = _find_receipt(session, organization, provider_event_id)
    if not receipt or not receipt.revenue_event_id:
        return None
    case = session.scalar(select(RecoveryCase).where(RecoveryCase.revenue_event_id == receipt.revenue_event_id))
    event = session.get(RevenueEvent, receipt.revenue_event_id)
    return WebhookOutcome(receipt.dedupe_key, duplicate=True, event=event, case=case)


def _find_receipt(session: Session, organization: Organization, provider_event_id: str) -> WebhookReceipt | None:
    return session.scalar(
        select(WebhookReceipt).where(
            WebhookReceipt.organization_id == organization.id,
            WebhookReceipt.provider_event_id == provider_event_id,
        )
    )


def _receipt(session: Session, organization: Organization, provider_event_id: str, body: dict) -> WebhookReceipt:
    """The receipt for this event id; a previously rejected delivery is reprocessed."""
    receipt = _find_receipt(session, organization, provider_event_id)
    if receipt is None:
        receipt = WebhookReceipt(
            organization_id=organization.id,
            provider_event_id=provider_event_id,
            dedupe_key=f"razorpay:event:{provider_event_id}",
            raw_payload=body,
        )
        session.add(receipt)
        session.flush()
    return receipt


def _ignore(session: Session, receipt: WebhookReceipt, reason: str) -> WebhookOutcome:
    logger.info("Acknowledged but ignored webhook %s: %s", receipt.provider_event_id, reason)
    receipt.status = "REJECTED"
    receipt.rejection_reason = reason[:255]
    receipt.processed_at = utc_now()
    session.commit()
    return WebhookOutcome(receipt.dedupe_key, duplicate=False, event=None, case=None, accepted=False, reason=reason)


def _process(
    session: Session,
    organization: Organization,
    customer: Customer,
    body: dict[str, Any],
    receipt: WebhookReceipt,
    actor: str,
) -> WebhookOutcome:
    result = process_revenue_event(session, organization, customer, body, receipt.dedupe_key, actor=actor)
    receipt.status = "PROCESSED"
    receipt.rejection_reason = None
    receipt.revenue_event_id = result.event.id
    receipt.processed_at = utc_now()
    return WebhookOutcome(receipt.dedupe_key, duplicate=result.duplicate, event=result.event, case=result.case)


def _parse_body(raw_body: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRequestError("Invalid webhook JSON") from exc
    if not isinstance(body, dict):
        raise InvalidRequestError("Webhook body must be a JSON object")
    return body


def _entities(body: dict[str, Any]) -> list[dict[str, Any]]:
    payload = body.get("payload") or {}
    primary_name, primary = razorpay_entity(body)
    others = [(payload.get(name) or {}).get("entity") or {} for name in _ENTITY_NAMES if name != primary_name]
    return [primary, *others]


def _provider_event_id(body: dict[str, Any], header: str | None) -> str:
    """Razorpay's X-Razorpay-Event-Id header, else a body id, else a key derived from the entity."""
    event_id = header or body.get("id") or body.get("event_id")
    if not event_id:
        entity_id = next((entity["id"] for entity in _entities(body) if entity.get("id")), None)
        if entity_id:
            event_id = f"legacy:{body.get('event', 'unknown')}:{entity_id}"
    if not isinstance(event_id, str) or not event_id.strip():
        raise InvalidRequestError("Webhook event id required")
    if len(event_id) > MAX_EVENT_ID_LENGTH:
        raise InvalidRequestError(f"Webhook event id longer than {MAX_EVENT_ID_LENGTH} characters")
    return event_id


def _resolve_customer(session: Session, organization: Organization, body: dict[str, Any]) -> Customer | None:
    """Find the event's customer, or create one from the provider's details on first sight.

    Lookup order: a customer id on the event or its notes; the RecoverX case named in the
    notes (Payment Link callbacks carry no customer id); otherwise a new profile.
    """
    entities = _entities(body)
    references = [body.get("customer_id")] + [
        entity.get("customer_id") or (entity.get("notes") or {}).get("customer_id") for entity in entities
    ]
    for reference in filter(None, references):
        customer = find_customer(session, organization.id, reference)
        if customer:
            return customer
    for entity in entities:
        case_id = case_reference(entity.get("notes"))
        case = find_case(session, organization.id, case_id) if case_id else None
        if case:
            return session.get(Customer, case.customer_id)
    return _provision_customer(session, organization, entities)


def _provision_customer(
    session: Session, organization: Organization, entities: list[dict[str, Any]]
) -> Customer | None:
    """Create a neutral customer profile from a payment's customer id, email or contact.

    Safe because the event's signature has been verified for this merchant. Returns None when
    the event identifies nobody.
    """
    for entity in entities:
        details = entity.get("customer") or {}  # Payment Links nest the customer
        email = entity.get("email") or details.get("email") or ""
        phone = entity.get("contact") or details.get("contact") or ""
        reference = entity.get("customer_id") or (f"{email or phone}" if email or phone else None)
        if not reference:
            continue
        key = customer_key(session, organization.id, str(reference))
        existing = find_customer(session, organization.id, key)
        if existing:
            return existing
        customer = Customer(
            organization_id=organization.id,
            razorpay_customer_id=key,
            name=(details.get("name") or email or phone or str(reference))[:160],
            email=email[:255],
            phone=phone[:32],
            preferred_channel="whatsapp" if phone else "email",
            historical_recovery_rate=0.5,  # no history yet: neutral prior
        )
        session.add(customer)
        session.flush()
        # The key can be an email or phone number: log the internal id, never contact details.
        logger.info("Created customer %s for organization %s from a Razorpay event", customer.id, organization.id)
        return customer
    return None
