"""Integration status and the merchant's own Razorpay credentials (admins only)."""

from dataclasses import asdict

from fastapi import APIRouter

from app.api import mcp
from app.api.dependencies import AdminUser, CurrentUser, DbSession
from app.api.schemas.integrations import (
    IntegrationStatus,
    IntegrationToggle,
    RazorpayCredentialsIn,
    RazorpayIntegrationOut,
)
from app.application import integrations, payment_integrations
from app.application.payment_integrations import IntegrationView
from app.core.config import settings

router = APIRouter(tags=["integrations"])
NO_CREDENTIALS = {404: {"description": "No credentials configured for this merchant"}}


def _out(view: IntegrationView) -> RazorpayIntegrationOut:
    return RazorpayIntegrationOut(**asdict(view), live_payments_allowed=settings.ALLOW_LIVE_PAYMENTS)


@router.get("/integrations/status")
def integration_status(session: DbSession, user: CurrentUser) -> IntegrationStatus:
    return IntegrationStatus.model_validate(
        integrations.integration_status(session, user.organization_id, mcp_tool_count=len(mcp.TOOLS))
    )


@router.get("/integrations/razorpay")
def get_razorpay(session: DbSession, user: AdminUser) -> RazorpayIntegrationOut:
    return _out(payment_integrations.describe(session, user.organization_id))


@router.put(
    "/integrations/razorpay",
    responses={400: {"description": "Invalid or live keys"}, 409: {"description": "Account used by another merchant"}},
)
def save_razorpay(payload: RazorpayCredentialsIn, session: DbSession, user: AdminUser) -> RazorpayIntegrationOut:
    """Store or rotate the merchant's Razorpay keys. Secrets are encrypted and never returned."""
    view = payment_integrations.save_razorpay(
        session,
        user,
        key_id=payload.key_id,
        key_secret=payload.key_secret.get_secret_value(),
        account_id=payload.account_id,
        webhook_secret=payload.webhook_secret.get_secret_value() if payload.webhook_secret else None,
        validate=payload.verify,
    )
    return _out(view)


@router.post("/integrations/razorpay/validate", responses=NO_CREDENTIALS)
def validate_razorpay(session: DbSession, user: AdminUser) -> RazorpayIntegrationOut:
    """Call Razorpay with the stored keys and record the result."""
    return _out(payment_integrations.validate_razorpay(session, user))


@router.patch("/integrations/razorpay", responses=NO_CREDENTIALS)
def toggle_razorpay(payload: IntegrationToggle, session: DbSession, user: AdminUser) -> RazorpayIntegrationOut:
    """Disable to fall back to the simulator without deleting the keys; enable to resume."""
    return _out(payment_integrations.set_enabled(session, user, payload.enabled))
