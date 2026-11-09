"""Simulation center ("failure lab"): drive real state transitions through the simulated provider.

Every scenario changes persisted state through the same code paths production
uses; nothing here fabricates analytics.
"""

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import container
from app.application.advice import advisor_context
from app.application.audit import record_audit
from app.application.case_state import ensure_open, escalate_case
from app.application.cases import executable_actions, stop_open_case
from app.application.ports import SignedWebhook
from app.application.recovery_actions import ToolGateway, evaluate_case_policy, tool_gateway
from app.application.webhooks import WebhookOutcome, ingest_simulated_webhook
from app.domain.constants import (
    CONTACTABLE_CASE_STATUSES,
    LINK_ACTIONS,
    ActionType,
    CaseStatus,
    PolicyDecision,
)
from app.domain.decisions import INVALID_AI_OUTPUT
from app.domain.events import PAISE_PER_RUPEE
from app.domain.exceptions import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PolicyViolationError,
    ProviderError,
)
from app.domain.policy import PolicyEngine
from app.infrastructure.database.models import (
    AgentDecision,
    Communication,
    Customer,
    Escalation,
    PaymentLink,
    RecoveryAction,
    RecoveryCase,
    User,
)
from app.infrastructure.database.repositories import (
    find_case,
    find_customer,
    get_case,
    get_or_create_policy,
    get_organization,
    get_payment_link,
)

# Canonical event type -> Razorpay webhook event name.
SIMULATED_EVENT_NAMES = {
    "PAYMENT_FAILURE": "payment.failed",
    "PAYMENT_CAPTURED": "payment.captured",
    "PAYMENT_LINK_PAID": "payment_link.paid",
    "PAYMENT_LINK_ABANDONED": "payment_link.abandoned",
    "INVOICE_OVERDUE": "invoice.overdue",
    "INVOICE_PAID": "invoice.paid",
    "SUBSCRIPTION_HALTED": "subscription.halted",
    "SUBSCRIPTION_CHARGED": "subscription.charged",
    "CHECKOUT_ABANDONED": "checkout.abandoned",
}

# Amount below the confidence floor used to force a low-confidence decision.
_LOW_CONFIDENCE_MARGIN = 0.20


@dataclass(frozen=True)
class OptOutResult:
    customer: Customer
    cases_stopped: int


def _require_case(session: Session, user: User, case_id: str | None) -> RecoveryCase:
    if not case_id:
        raise InvalidRequestError("case_id is required")
    return get_case(session, user.organization_id, case_id)


def signed_payment_webhook(case: RecoveryCase, link: PaymentLink | None, account_id: str | None) -> SignedWebhook:
    """The callback Razorpay would send when the customer pays this case."""
    amount = case.amount * PAISE_PER_RUPEE
    notes = {"case_id": case.id, "customer_id": case.customer_id}
    payment = {"id": f"pay_{uuid4().hex[:14]}", "amount": amount, "currency": case.currency, "status": "captured"}
    return container.payment_simulator().sign_webhook(
        "payment_link.paid" if link else "payment.captured",
        {
            "id": link.provider_link_id if link else f"pay_{case.id}",
            "amount": amount,
            "currency": case.currency,
            "status": "paid" if link else "captured",
            "notes": notes,
        },
        account_id=account_id,
        related={"payment": {**payment, "notes": notes}} if link else None,
    )


def simulate_payment(session: Session, user: User, case_id: str | None) -> RecoveryCase:
    """The customer pays: a signed success webhook resolves the case."""
    case = _require_case(session, user, case_id)
    customer = session.get(Customer, case.customer_id)
    if not customer:
        raise ConflictError("Customer record missing for payment case")
    organization = get_organization(session, user.organization_id)
    webhook = signed_payment_webhook(case, get_payment_link(session, case.id), organization.razorpay_account_id)
    ingest_simulated_webhook(session, organization, customer, webhook, actor="customer_simulator")
    session.commit()
    return case


def simulate_event(
    session: Session,
    user: User,
    event_type: str,
    customer_reference: str | None,
    amount: int,
    currency: str,
    event_id: str | None,
) -> WebhookOutcome:
    """A revenue event arrives from the simulated provider and runs the full pipeline."""
    organization = get_organization(session, user.organization_id)
    customer = find_customer(session, organization.id, customer_reference) if customer_reference else None
    if not customer:
        raise NotFoundError("Customer not found for this organization")
    event_id = event_id or f"sim_{uuid4().hex[:10]}"
    webhook = container.payment_simulator().sign_webhook(
        SIMULATED_EVENT_NAMES.get(event_type, event_type),
        {
            "id": event_id,
            "customer_id": customer.razorpay_customer_id,
            "amount": amount * PAISE_PER_RUPEE,
            "currency": currency,
            "notes": {"customer_id": customer.razorpay_customer_id},
        },
        account_id=organization.razorpay_account_id,
        event_id=event_id,
    )
    outcome = ingest_simulated_webhook(session, organization, customer, webhook, actor=user.email)
    session.commit()
    return outcome


@dataclass(frozen=True)
class TimeoutOutcome:
    case: RecoveryCase
    action: RecoveryAction
    failed_attempts: int
    messages_sent: int


class _TimingOutProvider:
    """Stands in for Razorpay while it is not answering: every call times out."""

    def __getattr__(self, name: str):  # noqa: ANN204
        def timed_out(*_args: object, **_kwargs: object) -> None:
            raise ProviderError(f"Razorpay did not answer {name} in time (simulated timeout)")

        return timed_out


def simulate_provider_retry(session: Session, user: User, case_id: str | None) -> TimeoutOutcome:
    """Razorpay times out while RecoverX issues a payment link, then the action is retried.

    The first attempt runs through the real Tool Gateway against a provider that times out: the
    action is recorded as FAILED and nothing reaches the customer. The retry uses the same
    idempotency key, so it completes that action instead of creating a second one, and the
    customer receives exactly one link and one message.
    """
    case = _require_case(session, user, case_id)
    ensure_open(case)
    if evaluate_case_policy(session, case).decision != PolicyDecision.AUTO_APPROVE:
        raise ConflictError("Choose a case policy lets RecoverX act on automatically (not one awaiting review)")
    action_type = _link_action_for(session, case)
    gateway = tool_gateway()
    # Force a provider call: an open link would simply be reused.
    gateway.cancel_open_link(session, case)
    messages_before = _messages(session, case.id)

    timing_out = ToolGateway(lambda _session, _organization: _TimingOutProvider(), gateway.messaging)
    try:
        timing_out.execute_recovery_action(session, case, action_type, actor=user.email)
    except ProviderError:
        pass  # recorded by the gateway as a FAILED action with an action.failed audit event
    else:  # pragma: no cover - the stand-in provider always times out
        raise ConflictError("The simulated timeout did not happen")
    if _messages(session, case.id) != messages_before:  # pragma: no cover - a failed action must not contact anyone
        raise ConflictError("A message was sent although the provider timed out")

    action = gateway.execute_recovery_action(session, case, action_type, actor=user.email)
    record_audit(
        session,
        case.organization_id,
        "failure_lab.provider_timeout",
        {"action_id": action.id, "action_type": action_type, "retried": True, "status": action.status},
        actor=user.email,
        case_id=case.id,
    )
    session.commit()
    return TimeoutOutcome(case, action, failed_attempts=1, messages_sent=_messages(session, case.id) - messages_before)


def _link_action_for(session: Session, case: RecoveryCase) -> str:
    allowed = executable_actions(session, case)
    for choice in (case.recommended_action, ActionType.PAYMENT_LINK, ActionType.REMINDER):
        if choice in allowed and choice in LINK_ACTIONS:
            return choice
    link_actions = [action for action in allowed if action in LINK_ACTIONS]
    if not link_actions:
        raise ConflictError("Merchant policy allows no payment-link action for this case")
    return link_actions[0]


def _messages(session: Session, case_id: str) -> int:
    return session.scalar(select(func.count(Communication.id)).where(Communication.case_id == case_id)) or 0


def opt_out_customer(session: Session, user: User, customer_reference: str | None, case_id: str | None) -> OptOutResult:
    """Stop all automated contact for a customer, identified directly or through one of their cases."""
    customer = None
    if customer_reference:
        customer = find_customer(session, user.organization_id, customer_reference)
    elif case_id:
        case = find_case(session, user.organization_id, case_id)
        customer = session.get(Customer, case.customer_id) if case else None
        customer = customer or find_customer(session, user.organization_id, case_id)
    if not customer:
        raise NotFoundError("Customer not found")

    customer.opted_out = True
    active_cases = session.scalars(
        select(RecoveryCase).where(
            RecoveryCase.customer_id == customer.id,
            RecoveryCase.organization_id == user.organization_id,
            RecoveryCase.status.in_(CONTACTABLE_CASE_STATUSES),
        )
    ).all()
    for case in active_cases:
        case.status = CaseStatus.STOPPED
        record_audit(
            session,
            case.organization_id,
            "customer.opted_out",
            {"customer_id": customer.id},
            actor=user.email,
            case_id=case.id,
        )
    session.commit()
    return OptOutResult(customer, len(active_cases))


def simulate_low_confidence(session: Session, user: User, case_id: str | None) -> RecoveryCase:
    """The advisor answers with confidence below the merchant's floor; the policy engine must send it to a person.

    Only the advisor's confidence is simulated. The real policy evaluation decides and escalates.
    """
    case = _require_case(session, user, case_id)
    ensure_open(case)
    policy = get_or_create_policy(session, case.organization_id)
    case.ai_confidence = max(0.0, policy.min_ai_confidence - _LOW_CONFIDENCE_MARGIN)
    record_audit(
        session,
        case.organization_id,
        "failure_lab.low_confidence",
        {"ai_confidence": case.ai_confidence, "min_ai_confidence": policy.min_ai_confidence},
        actor=user.email,
        case_id=case.id,
    )
    _apply_policy(session, case, user.email)
    session.commit()
    return case


def simulate_max_attempts(session: Session, user: User, case_id: str | None) -> RecoveryCase:
    """The customer has been contacted the maximum number of times; the next reminder must be refused.

    Only the contact count is simulated. The Tool Gateway and the policy engine decide, and
    the refusal stops the case exactly as it would in production.
    """
    case = _require_case(session, user, case_id)
    ensure_open(case)
    policy = get_or_create_policy(session, case.organization_id)
    case.contact_attempts = max(case.contact_attempts, policy.max_contact_attempts)
    record_audit(
        session,
        case.organization_id,
        "failure_lab.max_attempts_reached",
        {"contact_attempts": case.contact_attempts, "max_contact_attempts": policy.max_contact_attempts},
        actor=user.email,
        case_id=case.id,
    )
    try:
        tool_gateway().execute_recovery_action(session, case, ActionType.REMINDER, actor=user.email)
    except PolicyViolationError as refusal:
        evaluation = PolicyEngine.evaluate(policy, case, session.get(Customer, case.customer_id))
        case.policy_decision = evaluation.decision
        record_audit(
            session,
            case.organization_id,
            "policy.evaluated",
            {"decision": evaluation.decision, "reasons": evaluation.reasons, "violations": evaluation.rule_violations},
            actor=user.email,
            case_id=case.id,
        )
        stop_open_case(session, case, user.email, str(refusal))
    else:  # pragma: no cover - would mean the contact limit is not enforced
        raise ConflictError("Policy allowed a contact beyond the maximum")
    session.commit()
    return case


def simulate_invalid_ai(session: Session, user: User, case_id: str | None) -> RecoveryCase:
    """The advisor model returns unusable output. The real advisor graph must reject it, retry once,
    fail safe to human review, and the policy engine must escalate: nothing may be executed."""
    case = _require_case(session, user, case_id)
    ensure_open(case)
    advisor = container.malformed_advisor()
    decision = advisor.advise(advisor_context(session, case))
    session.add(
        AgentDecision(
            recovery_case_id=case.id,
            diagnosis=decision.diagnosis,
            root_cause=decision.root_cause,
            recommended_action=decision.recommended_action,
            recovery_probability=case.risk_assessment.recovery_probability if case.risk_assessment else 0.0,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            model=advisor.model_name,
        )
    )
    case.recommended_action = decision.recommended_action
    case.ai_confidence = decision.confidence
    record_audit(
        session,
        case.organization_id,
        "ai.invalid_output_safe_failure",
        {"model": advisor.model_name, "detail": decision.reasoning, "fallback": PolicyDecision.HUMAN_REVIEW},
        actor=user.email,
        case_id=case.id,
    )
    _apply_policy(session, case, user.email, extra_reason=INVALID_AI_OUTPUT if decision.is_safe_failure else None)
    session.commit()
    return case


def _apply_policy(session: Session, case: RecoveryCase, actor: str, extra_reason: str | None = None) -> None:
    """Re-evaluate merchant policy for the case and act on the result, as ingestion does."""
    evaluation = evaluate_case_policy(session, case)
    case.policy_decision = evaluation.decision
    record_audit(
        session,
        case.organization_id,
        "policy.evaluated",
        {"decision": evaluation.decision, "reasons": evaluation.reasons, "violations": evaluation.rule_violations},
        actor=actor,
        case_id=case.id,
    )
    if evaluation.decision == PolicyDecision.STOP:
        stop_open_case(session, case, actor, "; ".join(evaluation.reasons))
    elif evaluation.decision == PolicyDecision.HUMAN_REVIEW:
        reasons = ([extra_reason] if extra_reason else []) + evaluation.rule_violations
        escalate_case(session, case, reason=",".join(reasons) or "POLICY_REVIEW_REQUIRED", actor=actor)


def _failure_lab_escalation(session: Session, case: RecoveryCase, reason: str, actor: str) -> None:
    if not session.scalar(select(Escalation).where(Escalation.case_id == case.id)):
        session.add(Escalation(case_id=case.id, reason=reason))
    record_audit(
        session, case.organization_id, "failure_lab.escalated", {"reason": reason}, actor=actor, case_id=case.id
    )
