"""Composition root: chooses the infrastructure implementations behind each application port.

Which provider acts for a given merchant is decided in application/payment_integrations.py;
this module only builds the implementations.
"""

from functools import cache
from typing import TYPE_CHECKING

from app.application.ports import MessagingProvider, PaymentProvider, RecoveryAdvisor
from app.core.config import settings

if TYPE_CHECKING:
    from app.infrastructure.payments.razorpay import RazorpayProvider
    from app.infrastructure.payments.simulated_razorpay import SimulatedRazorpayProvider


@cache
def simulated_payment_provider() -> "SimulatedRazorpayProvider":
    """The simulator: development, demos, and merchants without their own credentials."""
    from app.infrastructure.payments.simulated_razorpay import SimulatedRazorpayProvider

    return SimulatedRazorpayProvider()


def payment_simulator() -> "SimulatedRazorpayProvider":
    """The simulation center always uses the simulated provider, whatever PAYMENT_PROVIDER is."""
    return simulated_payment_provider()


@cache
def payment_provider() -> PaymentProvider:
    """The legacy environment-wide provider (PAYMENT_PROVIDER, RAZORPAY_KEY_ID/SECRET)."""
    if settings.uses_live_razorpay:
        return razorpay_provider(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    return simulated_payment_provider()


def razorpay_provider(key_id: str, key_secret: str) -> "RazorpayProvider":
    from app.infrastructure.payments.razorpay import RazorpayProvider

    return RazorpayProvider(key_id, key_secret)


@cache
def messaging_providers() -> dict[str, MessagingProvider]:
    """Keyed by the customer's preferred channel. Only simulated channels exist today."""
    from app.infrastructure.messaging.simulated import SimulatedEmailProvider, SimulatedWhatsAppProvider

    return {"whatsapp": SimulatedWhatsAppProvider(), "email": SimulatedEmailProvider()}


@cache
def recovery_advisor() -> RecoveryAdvisor:
    """The LangGraph advisor with RecoverX's rules, or with Claude when LLM_PROVIDER=anthropic."""
    from app.infrastructure.ai.recovery_graph import LangGraphRecoveryAdvisor

    if settings.uses_llm_advisor:
        from app.infrastructure.ai.claude_reasoner import ClaudeReasoner

        return LangGraphRecoveryAdvisor(
            ClaudeReasoner(
                model=settings.LLM_MODEL,
                effort=settings.LLM_EFFORT,
                refusal_fallback=settings.LLM_REFUSAL_FALLBACK,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_retries=settings.LLM_MAX_RETRIES,
            )
        )
    return LangGraphRecoveryAdvisor()


@cache
def malformed_advisor() -> RecoveryAdvisor:
    """The simulation center's misbehaving advisor: the same graph, fed output that must be rejected."""
    from app.infrastructure.ai.recovery_graph import LangGraphRecoveryAdvisor, MalformedOutputReasoner

    return LangGraphRecoveryAdvisor(MalformedOutputReasoner())
