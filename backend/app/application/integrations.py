"""Truthful report of which integrations are live and which are simulated."""

import os
from typing import Any

from sqlalchemy.orm import Session

from app.application import payment_integrations
from app.application.payment_integrations import IntegrationView
from app.core.config import settings

# CONNECTED: live and verified; CONFIGURED: set up, not checked from here; SIMULATED: a stand-in runs.
CONNECTED, CONFIGURED, SIMULATED, NOT_CONFIGURED = "CONNECTED", "CONFIGURED", "SIMULATED", "NOT_CONFIGURED"


def _entry(name: str, category: str, status: str, label: str, detail: str) -> dict[str, str]:
    return {"name": name, "category": category, "status": status, "label": label, "detail": detail}


def _razorpay(view: IntegrationView) -> dict[str, str]:
    if view.source == "simulated":
        return _entry(
            "Razorpay",
            "Payment",
            SIMULATED,
            "Simulated",
            "Simulated Razorpay provider; add this merchant's test keys under Integrations",
        )
    origin = "merchant keys" if view.source == "merchant" else "environment keys"
    if not view.enabled:
        return _entry("Razorpay", "Payment", NOT_CONFIGURED, "Disabled", f"Razorpay {origin} are disabled")
    if view.last_error:
        return _entry("Razorpay", "Payment", NOT_CONFIGURED, "Check failed", view.last_error)
    return _entry("Razorpay", "Payment", CONNECTED, "Connected", f"Razorpay API with {view.mode}-mode {origin}")


def _temporal() -> dict[str, str]:
    if settings.TEMPORAL_ENABLED:
        return _entry(
            "Temporal", "Workflows", CONNECTED, "Enabled", f"Durable workflows dispatched to {settings.TEMPORAL_HOST}"
        )
    return _entry(
        "Temporal",
        "Workflows",
        SIMULATED,
        "In-process",
        "In-process runner (set TEMPORAL_ENABLED=true for durable workflows)",
    )


def _webhooks(view: IntegrationView) -> dict[str, str]:
    if view.has_webhook_secret:
        return _entry(
            "RecoverX Webhooks",
            "Webhooks",
            CONNECTED,
            "Active",
            "POST /webhooks/razorpay with HMAC-SHA256 verification",
        )
    return _entry(
        "RecoverX Webhooks",
        "Webhooks",
        NOT_CONFIGURED,
        "No secret",
        "Add the webhook secret from the Razorpay dashboard; unsigned events are rejected",
    )


def _advisor() -> dict[str, str]:
    if not settings.uses_llm_advisor:
        return _entry(
            "LLM provider",
            "Decisioning",
            NOT_CONFIGURED,
            "Rules only",
            "No LLM configured (LLM_PROVIDER=rules): RecoverX's deterministic rules run as a LangGraph workflow. "
            "Set LLM_PROVIDER=anthropic to let Claude diagnose and recommend; policy still decides",
        )
    if not (settings.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return _entry(
            "LLM provider",
            "Decisioning",
            NOT_CONFIGURED,
            "No credentials",
            "LLM_PROVIDER=anthropic but no ANTHROPIC_API_KEY is set; every case will fail safe to human review",
        )
    return _entry(
        "LLM provider",
        "Decisioning",
        CONFIGURED,
        "Claude",
        f"{settings.LLM_MODEL} (effort {settings.LLM_EFFORT}) recommends inside the LangGraph advisor; "
        "invalid or missing answers fail safe to human review, and policy decides",
    )


def _sentry() -> dict[str, str]:
    if settings.SENTRY_DSN:
        return _entry("Sentry", "Observability", CONFIGURED, "Reporting", "Errors are reported to Sentry")
    return _entry("Sentry", "Observability", NOT_CONFIGURED, "Off", "Set SENTRY_DSN to report errors to Sentry")


def integration_status(session: Session, organization_id: str, mcp_tool_count: int) -> dict[str, Any]:
    view = payment_integrations.describe(session, organization_id)
    integrations = [
        _razorpay(view),
        _webhooks(view),
        _entry(
            "RecoverX MCP Server",
            "MCP",
            CONNECTED,
            "Active",
            f"{mcp_tool_count} read and write tools behind the policy boundary",
        ),
        _entry(
            "WhatsApp",
            "Communication",
            SIMULATED,
            "Simulated",
            "Simulated WhatsApp channel; messages are recorded, not sent",
        ),
        _entry(
            "Email", "Communication", SIMULATED, "Simulated", "Simulated email channel; messages are recorded, not sent"
        ),
        _temporal(),
        _advisor(),
        _sentry(),
    ]
    statuses = [item["status"] for item in integrations]
    return {
        "integrations": integrations,
        "counts": {
            "total": len(integrations),
            "connected": statuses.count(CONNECTED),
            "configured": statuses.count(CONFIGURED),
            "simulated": statuses.count(SIMULATED),
            "not_configured": statuses.count(NOT_CONFIGURED),
        },
    }
