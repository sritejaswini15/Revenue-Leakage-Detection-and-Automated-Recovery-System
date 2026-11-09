"""Tool Gateway: the only path from a recovery decision to an external action.

Every call re-evaluates merchant policy, enforces idempotency, routes through
the configured payment and messaging providers, and writes an audit trail.
"""

import logging
from collections.abc import Callable
from datetime import timedelta
from functools import cache
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import container
from app.application import payment_integrations
from app.application.audit import record_audit
from app.application.case_state import mark_recovered
from app.application.ports import MessagingProvider, PaymentProvider, PaymentResult
from app.core.config import settings
from app.core.security import sha256_hex
from app.core.time import as_utc, utc_now
from app.domain.constants import (
    CLOSED_LINK_STATUSES,
    LINK_ACTIONS,
    PAYMENT_LINK_VARIANTS,
    ActionStatus,
    ActionType,
    CaseStatus,
    PaymentLinkStatus,
    PolicyDecision,
)
from app.domain.exceptions import (
    ConflictError,
    NotFoundError,
    NotRetryableError,
    PolicyViolationError,
    ProviderError,
)
from app.domain.messages import compose_recovery_message
from app.domain.policy import PolicyEngine, PolicyEvaluationResult, contact_channel, message_language
from app.infrastructure.database.models import Customer, Payment, PaymentLink, RecoveryAction, RecoveryCase
from app.infrastructure.database.repositories import get_or_create_policy, get_payment_link

logger = logging.getLogger(__name__)


ProviderResolver = Callable[[Session, str], PaymentProvider]


def evaluate_case_policy(
    session: Session, case: RecoveryCase, customer: Customer | None = None
) -> PolicyEvaluationResult:
    """The merchant's current policy applied to the case as it stands.

    The Tool Gateway authorizes every action with this, and the case page and MCP show the
    same result, so what an operator reads is what the gateway will decide.
    """
    customer = customer or session.get(Customer, case.customer_id)
    factors = (case.risk_assessment.factors or {}) if case.risk_assessment else {}
    return PolicyEngine.evaluate(
        get_or_create_policy(session, case.organization_id),
        case,
        customer,
        overdue_days=int(factors.get("overdue_days") or 0),
    )


class ToolGateway:
    def __init__(self, resolve_provider: ProviderResolver, messaging: dict[str, MessagingProvider]) -> None:
        # Resolved per action so every merchant acts through its own credentials.
        self.resolve_provider = resolve_provider
        self.messaging = messaging

    def execute_recovery_action(
        self,
        session: Session,
        case: RecoveryCase,
        action_type: str,
        actor: str = "system",
        trace_id: str | None = None,
        organization_id: str | None = None,
        human_approved_by: str | None = None,
    ) -> RecoveryAction:
        if organization_id and case.organization_id != organization_id:
            raise NotFoundError("Recovery case not found")
        action_type = action_type.upper()
        customer = session.get(Customer, case.customer_id)
        try:
            self._authorize(session, case, customer, action_type, human_approved_by)
        except PolicyViolationError as refusal:
            record_audit(
                session,
                case.organization_id,
                "policy.rejected",
                {"action_type": action_type, "reason": str(refusal), "approved_by": human_approved_by},
                actor=actor,
                case_id=case.id,
                correlation_id=trace_id,
            )
            raise

        idempotency_key = f"action:{case.id}:{action_type}:{case.contact_attempts}"
        action = session.scalar(select(RecoveryAction).where(RecoveryAction.idempotency_key == idempotency_key))
        if action and action.status == ActionStatus.EXECUTED:
            return action
        trace = trace_id or str(uuid4())
        if action is None:
            action = RecoveryAction(
                recovery_case_id=case.id,
                action_type=action_type,
                status=ActionStatus.PENDING,
                idempotency_key=idempotency_key,
                trace_id=trace,
                payload={
                    "amount": case.amount,
                    "action_type": action_type,
                    **({"approved_by": human_approved_by} if human_approved_by else {}),
                },
            )
            session.add(action)
            session.flush()

        try:
            self._dispatch(session, case, customer, action, actor, trace)
        except Exception as exc:
            logger.warning("Recovery action %s failed for case %s: %s", action_type, case.id, exc)
            action.status = ActionStatus.FAILED
            action.payload = {**action.payload, "error": str(exc)}
            record_audit(
                session,
                case.organization_id,
                "action.failed",
                {"action_id": action.id, "error": str(exc)},
                actor=actor,
                case_id=case.id,
                correlation_id=trace,
            )
            raise

        session.flush()
        return action

    def _authorize(
        self,
        session: Session,
        case: RecoveryCase,
        customer: Customer | None,
        action_type: str,
        human_approved_by: str | None,
    ) -> None:
        policy = get_or_create_policy(session, case.organization_id)
        allowed = {value.strip().upper() for value in policy.allowed_actions.split(",") if value.strip()}
        is_link_variant = action_type in PAYMENT_LINK_VARIANTS and ActionType.PAYMENT_LINK in allowed
        if action_type not in allowed and not is_link_variant:
            raise PolicyViolationError(f"Action {action_type} is prohibited by merchant policy")

        decision = evaluate_case_policy(session, case, customer).decision
        if decision != PolicyDecision.AUTO_APPROVE and not human_approved_by:
            raise PolicyViolationError(f"Action requires policy decision {decision}")
        if decision == PolicyDecision.STOP:
            raise PolicyViolationError("Action is stopped by policy and cannot be approved")
        # Link actions message the customer, and a declined retry falls back to one.
        sends_message = action_type in LINK_ACTIONS or action_type == ActionType.PAYMENT_RETRY
        if sends_message and not contact_channel(
            customer.preferred_channel if customer else None, policy.preferred_channels
        ):
            raise PolicyViolationError("Merchant policy permits no contact channel")

    def _dispatch(
        self,
        session: Session,
        case: RecoveryCase,
        customer: Customer | None,
        action: RecoveryAction,
        actor: str,
        trace: str,
    ) -> None:
        def audit(event_name: str, payload: dict) -> None:
            record_audit(
                session, case.organization_id, event_name, payload, actor=actor, case_id=case.id, correlation_id=trace
            )

        if action.action_type == ActionType.PAYMENT_RETRY:
            result = self._retry(session, case)
            if result:
                audit("payment.retried", {"payment_id": result.provider_payment_id, "status": result.status})
            if result and result.status == "CAPTURED":
                action.status = ActionStatus.EXECUTED
                case.action_executed = True
                mark_recovered(
                    session, case, result.amount, event_type="PAYMENT_RETRY", actor=actor, correlation_id=trace
                )
                audit(
                    "action.executed", {"action_id": action.id, "action_type": action.action_type, "status": "EXECUTED"}
                )
                return
            # The payment cannot be retried or was declined again: let the customer pay another way.
            action.payload = {
                **action.payload,
                "fallback": "PAYMENT_LINK",
                "retry_status": result.status if result else None,
            }
        elif action.action_type not in LINK_ACTIONS:
            raise PolicyViolationError(f"Action {action.action_type} cannot be executed automatically")

        link, public_url = self._ensure_payment_link(session, case)
        action.payload = {**action.payload, "payment_url": public_url, "provider_link_id": link.provider_link_id}
        audit("payment_link.created", {"link_id": link.provider_link_id, "amount": case.amount})

        # The merchant's policy decides which channels and languages are allowed; within that,
        # the customer's own preference wins.
        policy = get_or_create_policy(session, case.organization_id)
        channel = contact_channel(customer.preferred_channel if customer else None, policy.preferred_channels)
        if channel is None:  # checked in _authorize; kept so a policy edit mid-action can't slip through
            raise PolicyViolationError("Merchant policy permits no contact channel")
        language = message_language(customer.preferred_language if customer else None, policy.supported_languages)
        provider = self.messaging[channel]
        recipient = (customer.phone if channel == "whatsapp" else customer.email) if customer else ""
        content = compose_recovery_message(
            customer_name=customer.name if customer else "Customer",
            amount=case.amount,
            language=language,
            link_url=link.short_url,
        )
        message = provider.send_message(session, case.id, case.customer_id, recipient, content)
        audit(
            "message.sent",
            {"channel": channel, "language": language, "message_id": message.message_id, "recipient": recipient},
        )

        action.status = ActionStatus.EXECUTED
        case.action_executed = True
        case.status = CaseStatus.WAITING_FOR_PAYMENT
        case.contact_attempts += 1
        audit("action.executed", {"action_id": action.id, "action_type": action.action_type, "status": "EXECUTED"})

    def _retry(self, session: Session, case: RecoveryCase) -> PaymentResult | None:
        """Retry the case's own payment; None when there is no retryable payment."""
        payment = session.scalar(
            select(Payment).where(
                Payment.organization_id == case.organization_id,
                Payment.provider_payment_id == case.revenue_event.source_id,
            )
        )
        if payment is None:
            return None
        try:
            return self.resolve_provider(session, case.organization_id).retry_payment(session, payment.id)
        except NotRetryableError as exc:
            logger.info("Payment %s is not retryable for case %s: %s", payment.provider_payment_id, case.id, exc)
            return None

    def cancel_open_link(self, session: Session, case: RecoveryCase) -> PaymentLink | None:
        """Close the case's unpaid link so it can never be reused. Provider errors are logged, not raised."""
        link = get_payment_link(session, case.id)
        if not link or link.status in CLOSED_LINK_STATUSES:
            return None
        try:
            self.resolve_provider(session, case.organization_id).cancel_payment_link(link.provider_link_id)
        except ProviderError as exc:
            logger.warning("Could not cancel provider link %s for case %s: %s", link.provider_link_id, case.id, exc)
        link.status = PaymentLinkStatus.CANCELLED
        session.flush()
        return link

    def _ensure_payment_link(self, session: Session, case: RecoveryCase) -> tuple[PaymentLink, str | None]:
        """Reuse the case's open link, or issue a new one if it has expired or been cancelled.

        A case has a single link row; issuing a new link replaces its provider details.
        The public URL is only known when a link is issued.
        """
        link = get_payment_link(session, case.id)
        if link and link.status == PaymentLinkStatus.PAID:
            raise ConflictError("The payment link for this case has already been paid")
        expired = link and link.expires_at and as_utc(link.expires_at) <= utc_now()
        if link and link.status not in CLOSED_LINK_STATUSES and not expired:
            return link, None

        provider = self.resolve_provider(session, case.organization_id)
        result = provider.create_payment_link(case.id, case.amount, f"Payment for {case.id}")
        if link is None:
            link = PaymentLink(id=f"pl_{uuid4().hex[:12]}", case_id=case.id)
            session.add(link)
        link.provider_link_id = result.provider_link_id
        link.amount = case.amount
        link.status = result.status
        link.short_url = result.short_url
        link.public_token_hash = sha256_hex(result.public_token) if result.public_token else None
        link.expires_at = utc_now() + timedelta(hours=settings.PAYMENT_LINK_TTL_HOURS)
        session.flush()
        return link, f"/pay/{result.public_token}" if result.public_token else None


@cache
def tool_gateway() -> ToolGateway:
    return ToolGateway(payment_integrations.payment_provider_for, container.messaging_providers())
