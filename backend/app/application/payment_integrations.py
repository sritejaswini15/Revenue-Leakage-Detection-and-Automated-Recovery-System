"""Per-merchant payment-provider configuration.

Each merchant stores its own Razorpay test (or, if allowed, live) keys and webhook
secret. Secrets are encrypted at rest and never returned by the API. The
environment-wide RAZORPAY_* settings are a legacy single-merchant configuration and
apply only to the primary merchant, never to every merchant.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import container
from app.application.audit import record_audit
from app.application.ports import PaymentProvider
from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, mask
from app.core.time import utc_now
from app.domain.exceptions import ConflictError, InvalidRequestError, NotFoundError, ProviderError
from app.infrastructure.database.models import Organization, PaymentIntegration, User

logger = logging.getLogger(__name__)

TEST_KEY_PREFIX, LIVE_KEY_PREFIX = "rzp_test_", "rzp_live_"


@dataclass(frozen=True)
class IntegrationView:
    """What a merchant may see about its configuration: never a secret."""

    source: str  # "merchant", "legacy-environment" or "simulated"
    provider: str
    mode: str | None
    key_id_masked: str | None
    has_webhook_secret: bool
    account_id: str | None
    enabled: bool
    validated_at: datetime | None
    last_error: str | None


def _integration(session: Session, organization_id: str) -> PaymentIntegration | None:
    return session.scalar(select(PaymentIntegration).where(PaymentIntegration.organization_id == organization_id))


def is_legacy_primary(session: Session, organization: Organization) -> bool:
    """The merchant the environment-wide configuration belongs to."""
    if settings.BOOTSTRAP_RAZORPAY_ACCOUNT_ID:
        return organization.razorpay_account_id == settings.BOOTSTRAP_RAZORPAY_ACCOUNT_ID
    return (session.scalar(select(func.count(Organization.id))) or 0) == 1


def describe(session: Session, organization_id: str) -> IntegrationView:
    organization = session.get(Organization, organization_id)
    integration = _integration(session, organization_id)
    if integration:
        return IntegrationView(
            source="merchant",
            provider=integration.provider,
            mode="live" if integration.key_id.startswith(LIVE_KEY_PREFIX) else "test",
            key_id_masked=mask(integration.key_id, 8),
            has_webhook_secret=integration.webhook_secret_encrypted is not None,
            account_id=organization.razorpay_account_id,
            enabled=integration.enabled,
            validated_at=integration.validated_at,
            last_error=integration.last_error,
        )
    legacy = is_legacy_primary(session, organization) and settings.uses_live_razorpay and bool(settings.RAZORPAY_KEY_ID)
    return IntegrationView(
        source="legacy-environment" if legacy else "simulated",
        provider="razorpay" if legacy else "simulated",
        mode=("live" if settings.RAZORPAY_KEY_ID.startswith(LIVE_KEY_PREFIX) else "test") if legacy else None,
        key_id_masked=mask(settings.RAZORPAY_KEY_ID, 8) if legacy else None,
        has_webhook_secret=bool(organization.webhook_secret) or is_legacy_primary(session, organization),
        account_id=organization.razorpay_account_id,
        enabled=legacy,
        validated_at=None,
        last_error=None,
    )


def save_razorpay(
    session: Session,
    user: User,
    key_id: str,
    key_secret: str,
    account_id: str,
    webhook_secret: str | None,
    validate: bool,
) -> IntegrationView:
    """Create or rotate the merchant's Razorpay credentials."""
    key_id, account_id = key_id.strip(), account_id.strip()
    if not key_id.startswith((TEST_KEY_PREFIX, LIVE_KEY_PREFIX)):
        raise InvalidRequestError("Razorpay key ids start with rzp_test_ or rzp_live_")
    if key_id.startswith(LIVE_KEY_PREFIX) and not settings.ALLOW_LIVE_PAYMENTS:
        raise InvalidRequestError("Live keys are disabled on this deployment; use rzp_test_ keys (ALLOW_LIVE_PAYMENTS)")
    if not key_secret.strip():
        raise InvalidRequestError("key_secret is required")
    taken = session.scalar(
        select(Organization.id).where(
            Organization.razorpay_account_id == account_id, Organization.id != user.organization_id
        )
    )
    if taken:
        raise ConflictError("This Razorpay account is already connected to another merchant")

    organization = session.get(Organization, user.organization_id)
    organization.razorpay_account_id = account_id
    integration = _integration(session, user.organization_id) or PaymentIntegration(
        organization_id=user.organization_id, provider="razorpay"
    )
    integration.key_id = key_id
    integration.key_secret_encrypted = encrypt_secret(key_secret.strip())
    if webhook_secret:
        integration.webhook_secret_encrypted = encrypt_secret(webhook_secret.strip())
    integration.enabled = True
    integration.validated_at = None
    integration.last_error = None
    session.add(integration)
    record_audit(
        session,
        user.organization_id,
        "integration.razorpay.saved",
        {"key_id": mask(key_id, 8), "account_id": account_id, "webhook_secret_rotated": bool(webhook_secret)},
        actor=user.email,
    )
    session.flush()
    if validate:
        _validate(session, integration)
    session.commit()
    return describe(session, user.organization_id)


def validate_razorpay(session: Session, user: User) -> IntegrationView:
    integration = _integration(session, user.organization_id)
    if not integration:
        raise NotFoundError("No Razorpay credentials are configured for this merchant")
    _validate(session, integration)
    record_audit(
        session,
        user.organization_id,
        "integration.razorpay.validated",
        {"ok": integration.last_error is None},
        actor=user.email,
    )
    session.commit()
    return describe(session, user.organization_id)


def set_enabled(session: Session, user: User, enabled: bool) -> IntegrationView:
    integration = _integration(session, user.organization_id)
    if not integration:
        raise NotFoundError("No Razorpay credentials are configured for this merchant")
    integration.enabled = enabled
    record_audit(
        session,
        user.organization_id,
        "integration.razorpay.enabled" if enabled else "integration.razorpay.disabled",
        {},
        actor=user.email,
    )
    session.commit()
    return describe(session, user.organization_id)


def _validate(session: Session, integration: PaymentIntegration) -> None:
    provider = container.razorpay_provider(integration.key_id, decrypt_secret(integration.key_secret_encrypted))
    try:
        provider.verify_credentials()
    except ProviderError as exc:
        integration.validated_at, integration.last_error = None, str(exc)[:255]
    else:
        integration.validated_at, integration.last_error = utc_now(), None
    session.flush()


def payment_provider_for(session: Session, organization_id: str) -> PaymentProvider:
    """The provider that acts for this merchant: its own keys, the legacy keys, or the simulator."""
    integration = _integration(session, organization_id)
    if integration and integration.enabled:
        return container.razorpay_provider(integration.key_id, decrypt_secret(integration.key_secret_encrypted))
    organization = session.get(Organization, organization_id)
    if settings.uses_live_razorpay and is_legacy_primary(session, organization):
        return container.payment_provider()
    return container.simulated_payment_provider()


def webhook_secret_for(session: Session, organization: Organization) -> str | None:
    """The secret that signs this merchant's webhooks; None means its webhooks are not accepted."""
    integration = _integration(session, organization.id)
    if integration and integration.webhook_secret_encrypted:
        return decrypt_secret(integration.webhook_secret_encrypted)
    if organization.webhook_secret:
        return organization.webhook_secret
    if is_legacy_primary(session, organization):
        return settings.RAZORPAY_WEBHOOK_SECRET
    return None


def processes_events(session: Session, organization_id: str) -> bool:
    """False while the merchant has disabled its Razorpay integration: events are acknowledged, not acted on."""
    integration = _integration(session, organization_id)
    return integration is None or integration.enabled
