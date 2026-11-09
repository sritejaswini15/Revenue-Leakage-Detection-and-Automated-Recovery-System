"""Revenue event ingestion: the core RecoverX pipeline.

    normalize -> persist event -> mirror payment state ->
      success event:  resolve the matching open case
      leak event:     risk -> AI recommendation -> policy -> act / escalate / stop

Callers own the transaction (this module only flushes).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import container
from app.application.audit import record_audit
from app.application.case_state import escalate_case, mark_recovered
from app.application.customers import combined_profile
from app.application.recovery_actions import tool_gateway
from app.application.workflows import runner
from app.domain.constants import (
    EXECUTABLE_ACTIONS,
    OPEN_CASE_STATUSES,
    WAIT_ACTIONS,
    CaseStatus,
    PaymentLinkStatus,
    PolicyDecision,
)
from app.domain.decisions import INVALID_AI_OUTPUT, RecoveryContext
from app.domain.events import PAYMENT_LINK_LIFECYCLE_STATUS, CanonicalRevenueEvent, normalize_event
from app.domain.exceptions import PolicyViolationError
from app.domain.policy import PolicyEngine
from app.domain.risk import RiskEngine
from app.infrastructure.database.models import (
    AgentDecision,
    Customer,
    Organization,
    Payment,
    PaymentAttempt,
    PaymentLink,
    RecoveryCase,
    RevenueEvent,
    RiskAssessment,
)
from app.infrastructure.database.repositories import get_or_create_policy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionResult:
    event: RevenueEvent
    case: RecoveryCase | None
    duplicate: bool


def process_revenue_event(
    session: Session,
    organization: Organization,
    customer: Customer,
    raw_body: dict[str, Any],
    dedupe_key: str,
    actor: str = "webhook",
) -> IngestionResult:
    existing = session.scalar(select(RevenueEvent).where(RevenueEvent.dedupe_key == dedupe_key))
    if existing:
        case = session.scalar(select(RecoveryCase).where(RecoveryCase.revenue_event_id == existing.id))
        return IngestionResult(existing, case, duplicate=True)

    canonical = normalize_event(raw_body)
    event = RevenueEvent(
        organization_id=organization.id,
        event_type=canonical.event_type,
        source=canonical.source,
        source_entity=canonical.source_entity,
        source_id=canonical.source_id,
        customer_id=customer.id,
        amount=canonical.amount,
        currency=canonical.currency,
        raw_payload=raw_body,
        dedupe_key=dedupe_key,
    )
    session.add(event)
    session.flush()
    payment = _mirror_payment_state(session, organization, customer, canonical, event)

    link = _recoverx_link(session, organization, canonical.source_id)
    if link and canonical.event_type in PAYMENT_LINK_LIFECYCLE_STATUS:
        # A link RecoverX issued expired, was cancelled or abandoned: update it, never open a second case.
        new_status = PAYMENT_LINK_LIFECYCLE_STATUS[canonical.event_type]
        if new_status and link.status != PaymentLinkStatus.PAID:
            link.status = new_status
        case = session.get(RecoveryCase, link.case_id)
        record_audit(
            session,
            organization.id,
            f"payment_link.{canonical.event_type.removeprefix('PAYMENT_LINK_').lower()}",
            {"link_id": link.provider_link_id},
            actor=actor,
            case_id=link.case_id,
            correlation_id=dedupe_key,
        )
        session.flush()
        return IngestionResult(event, case, duplicate=False)

    if canonical.is_informational:
        session.flush()
        return IngestionResult(event, None, duplicate=False)

    if canonical.is_payment_success:
        case = _find_case_resolved_by(session, organization, customer, canonical)
        _link_payment(payment, case)
        if case and case.status != CaseStatus.RECOVERED:
            mark_recovered(
                session,
                case,
                canonical.amount or case.amount,
                event_type=canonical.event_type,
                actor=actor,
                correlation_id=dedupe_key,
            )
        elif case and canonical.source_entity == "payment_link" and canonical.amount > case.recovered_amount:
            # Link events carry the cumulative amount paid; never add, so a replayed or reordered
            # event can't count the same money twice.
            previous = case.recovered_amount
            case.recovered_amount = canonical.amount
            record_audit(
                session,
                organization.id,
                "payment.recovered_amount_updated",
                {"previous": previous, "recovered_amount": canonical.amount, "event_type": canonical.event_type},
                actor=actor,
                case_id=case.id,
                correlation_id=dedupe_key,
            )
        session.flush()
        return IngestionResult(event, case, duplicate=False)

    open_case = _open_case_for_source(session, organization, canonical.source_id)
    if open_case:
        _link_payment(payment, open_case)
        # Further leak events for the same payment, invoice or subscription join its open case.
        record_audit(
            session,
            organization.id,
            "revenue_event.linked",
            {"event_type": canonical.event_type, "amount": canonical.amount},
            actor=actor,
            case_id=open_case.id,
            correlation_id=dedupe_key,
        )
        session.flush()
        return IngestionResult(event, open_case, duplicate=False)

    case = _open_recovery_case(session, organization, customer, canonical, event, raw_body, actor, dedupe_key)
    _link_payment(payment, case)
    return IngestionResult(event, case, duplicate=False)


def _link_payment(payment: Payment | None, case: RecoveryCase | None) -> None:
    if payment is not None and case is not None and payment.recovery_case_id is None:
        payment.recovery_case_id = case.id


def _recoverx_link(session: Session, organization: Organization, source_id: str) -> PaymentLink | None:
    return session.scalar(
        select(PaymentLink)
        .join(RecoveryCase, RecoveryCase.id == PaymentLink.case_id)
        .where(
            RecoveryCase.organization_id == organization.id,
            or_(PaymentLink.provider_link_id == source_id, PaymentLink.id == source_id),
        )
    )


def _open_case_for_source(
    session: Session, organization: Organization, source_id: str, statuses: Iterable[str] = OPEN_CASE_STATUSES
) -> RecoveryCase | None:
    return session.scalar(
        select(RecoveryCase)
        .join(RevenueEvent, RevenueEvent.id == RecoveryCase.revenue_event_id)
        .where(
            RecoveryCase.organization_id == organization.id,
            RecoveryCase.status.in_(statuses),
            RevenueEvent.source_id == source_id,
        )
        .order_by(RecoveryCase.created_at.desc())
        .limit(1)
    )


def _mirror_payment_state(
    session: Session,
    organization: Organization,
    customer: Customer,
    canonical: CanonicalRevenueEvent,
    event: RevenueEvent,
) -> Payment | None:
    """Keep a local copy of provider payment state: retries act on it and customer stats count it.

    One row per provider payment, updated to its latest status, so a payment that failed and was
    later captured counts once, as a success.
    """
    if canonical.source_entity == "payment":
        payment_id, outcome = canonical.source_id, canonical.event_type
    elif canonical.is_payment_success and canonical.metadata.get("payment_id"):
        payment_id, outcome = canonical.metadata["payment_id"], "PAYMENT_CAPTURED"
    else:
        return None
    payment = session.scalar(select(Payment).where(Payment.provider_payment_id == payment_id))
    if payment is not None and payment.organization_id != organization.id:
        # Payment ids are global; another merchant's event must never touch this record.
        logger.warning("Ignoring payment %s: it belongs to another organization", payment_id)
        return None
    if payment is None:
        payment = Payment(
            organization_id=organization.id,
            customer_id=customer.id,
            provider_payment_id=payment_id,
            amount=canonical.amount,
            currency=canonical.currency,
            status="CREATED",
            method=canonical.metadata.get("method"),
            origin="webhook",
        )
        session.add(payment)
        session.flush()

    if outcome == "PAYMENT_CAPTURED":
        payment.status, payment.failure_reason, attempt_status = "CAPTURED", None, "SUCCESS"
    elif outcome == "PAYMENT_AUTHORIZED":
        if payment.status != "CAPTURED":
            payment.status = "AUTHORIZED"
        return payment
    elif outcome == "PAYMENT_FAILURE":
        if payment.status == "CAPTURED":  # a late, out-of-order failure never undoes a capture
            return payment
        payment.status = "FAILED"
        payment.failure_reason = canonical.metadata.get("error_description") or "payment_failed"
        attempt_status = "FAILED"
    else:
        return payment
    session.add(
        PaymentAttempt(
            payment_id=payment.id,
            provider_attempt_id=f"webhook_{event.id}",
            status=attempt_status,
            failure_reason=payment.failure_reason,
        )
    )
    return payment


def _find_case_resolved_by(
    session: Session, organization: Organization, customer: Customer, canonical: CanonicalRevenueEvent
) -> RecoveryCase | None:
    """Match a payment-success event to a case: by payment link, then source payment, then notes."""
    link = _recoverx_link(session, organization, canonical.source_id)
    if link:
        link.status = PaymentLinkStatus.PAID
        return session.get(RecoveryCase, link.case_id)

    # Money that arrives after a case expired or was stopped is still recovered revenue.
    case = _open_case_for_source(session, organization, canonical.source_id) or _open_case_for_source(
        session, organization, canonical.source_id, statuses=_CLOSED_UNPAID
    )
    if case is None and canonical.source_entity == "payment_link":
        # A link paid in parts reports a running total: the later event raises the recorded amount.
        case = _open_case_for_source(session, organization, canonical.source_id, statuses=(CaseStatus.RECOVERED,))
    if case or not canonical.case_reference:
        return case
    return session.scalar(
        select(RecoveryCase).where(
            RecoveryCase.organization_id == organization.id,
            RecoveryCase.status.in_(OPEN_CASE_STATUSES | _CLOSED_UNPAID),
            RecoveryCase.id == canonical.case_reference,
            RecoveryCase.customer_id == customer.id,
        )
    )


# Closed without payment; a payment that arrives later still recovers them.
_CLOSED_UNPAID = frozenset({CaseStatus.EXPIRED, CaseStatus.STOPPED})


def _open_recovery_case(
    session: Session,
    organization: Organization,
    customer: Customer,
    canonical: CanonicalRevenueEvent,
    event: RevenueEvent,
    raw_body: dict[str, Any],
    actor: str,
    dedupe_key: str,
) -> RecoveryCase:
    policy = get_or_create_policy(session, organization.id)
    overdue_days = int(raw_body.get("overdue_days", 0))
    repeat_failures = int(raw_body.get("repeat_failures", 0))
    # The customer's record as of just before this event: profile plus every other observed payment.
    profile = combined_profile(
        session, customer, exclude_payment_id=canonical.source_id if canonical.source_entity == "payment" else None
    )

    risk = RiskEngine.evaluate(
        amount=canonical.amount,
        customer=profile,
        overdue_days=overdue_days,
        repeat_failures=repeat_failures,
        event_type=canonical.event_type,
    )
    assessment = RiskAssessment(
        risk_score=risk.risk_score,
        recovery_probability=risk.recovery_probability,
        expected_recovery_value=risk.expected_recovery_value,
        model_version=risk.model_version,
        factors=risk.factors,
    )
    session.add(assessment)
    session.flush()

    advisor = container.recovery_advisor()
    decision = advisor.advise(
        RecoveryContext(
            event_type=canonical.event_type,
            amount=canonical.amount,
            currency=canonical.currency,
            customer_name=customer.name,
            customer_reliability=profile.payment_reliability_pct,
            successful_payments=profile.successful_payments,
            failed_payments=profile.failed_payments,
            preferred_channel=customer.preferred_channel,
            preferred_language=customer.preferred_language,
            overdue_days=overdue_days,
            repeat_failures=repeat_failures,
            historical_recovery_rate=profile.historical_recovery_rate,
            previous_recovery_attempts=customer.previous_recovery_attempts,
            best_historical_recovery_action=customer.best_historical_recovery_action,
            lifetime_value=customer.lifetime_value,
            risk_score=risk.risk_score,
            recovery_probability=risk.recovery_probability,
            expected_recovery_value=risk.expected_recovery_value,
        )
    )

    case = RecoveryCase(
        id=f"RC-{uuid4().hex[:8].upper()}",
        organization_id=organization.id,
        revenue_event_id=event.id,
        customer_id=customer.id,
        risk_assessment_id=assessment.id,
        status=CaseStatus.ACTIONABLE,
        amount=canonical.amount,
        currency=canonical.currency,
        recommended_action=decision.recommended_action,
        policy_decision=PolicyDecision.PENDING,
        ai_confidence=decision.confidence,
        failure_reason=decision.root_cause,
    )
    session.add(case)
    session.flush()

    evaluation = PolicyEngine.evaluate(
        policy, case, profile, ai_confidence=decision.confidence, overdue_days=overdue_days
    )
    case.policy_decision = evaluation.decision
    session.add(
        AgentDecision(
            recovery_case_id=case.id,
            diagnosis=decision.diagnosis,
            root_cause=decision.root_cause,
            recommended_action=decision.recommended_action,
            recovery_probability=risk.recovery_probability,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            model=advisor.model_name,
        )
    )

    def audit(event_name: str, payload: dict[str, Any]) -> None:
        record_audit(
            session, organization.id, event_name, payload, actor=actor, case_id=case.id, correlation_id=dedupe_key
        )

    audit("revenue_event.received", {"event_type": canonical.event_type, "amount": canonical.amount})
    audit(
        "risk.assessed",
        {
            "risk_score": risk.risk_score,
            "expected_recovery_value": risk.expected_recovery_value,
            "recovery_probability": risk.recovery_probability,
        },
    )
    audit(
        "agent.decision.created",
        {
            "root_cause": decision.root_cause,
            "action": decision.recommended_action,
            "confidence": decision.confidence,
            "model": advisor.model_name,
        },
    )
    if decision.is_safe_failure:
        audit("ai.invalid_output_safe_failure", {"model": advisor.model_name, "detail": decision.reasoning})
    audit(
        "policy.evaluated",
        {"decision": evaluation.decision, "reasons": evaluation.reasons, "violations": evaluation.rule_violations},
    )

    if evaluation.decision == PolicyDecision.AUTO_APPROVE and case.recommended_action in WAIT_ACTIONS:
        # The advisor expects the money without contact: nothing runs; a payment or expiry closes the case.
        audit("workflow.monitoring", {"action": case.recommended_action, "reasoning": decision.reasoning})
    elif evaluation.decision == PolicyDecision.AUTO_APPROVE and case.recommended_action not in EXECUTABLE_ACTIONS:
        # ESCALATE_TO_HUMAN, STOP or PROMISE_TO_PAY: nothing automatic can carry it out, so a person decides.
        escalate_case(session, case, reason="ADVISOR_RECOMMENDS_REVIEW", actor=actor, notes=decision.reasoning)
    elif evaluation.decision == PolicyDecision.AUTO_APPROVE:
        try:
            tool_gateway().execute_recovery_action(
                session, case, case.recommended_action, actor=actor, trace_id=dedupe_key
            )
        except PolicyViolationError as refusal:
            # e.g. the merchant switched this action off: the event still becomes a case, for a person.
            escalate_case(session, case, reason="ACTION_NOT_ALLOWED", actor=actor, notes=str(refusal))
        else:
            runner.trigger_recovery_workflow(session, case.id)
    elif evaluation.decision == PolicyDecision.HUMAN_REVIEW:
        reasons = ([INVALID_AI_OUTPUT] if decision.is_safe_failure else []) + evaluation.rule_violations
        escalate_case(session, case, reason=",".join(reasons) or "POLICY_REVIEW_REQUIRED", actor=actor)
    else:
        case.status = CaseStatus.STOPPED
        audit("workflow.stopped", {"reasons": evaluation.reasons})

    session.flush()
    return case
