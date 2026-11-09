"""Batch evaluation: replay every case through the advisor and policy engine.

Measures decision validity, policy compliance and financial outcomes, and
records the summary as an ``evaluation.completed`` audit event.
"""

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import container
from app.application.advice import advisor_context
from app.application.audit import record_audit
from app.core.time import hours_between
from app.domain.constants import EXECUTING_RESOLUTIONS, ActionStatus, CaseStatus, PolicyDecision
from app.domain.policy import PolicyEngine
from app.infrastructure.database.models import AuditEvent, Escalation, RecoveryAction, RecoveryCase
from app.infrastructure.database.repositories import get_or_create_policy

logger = logging.getLogger(__name__)


def run_evaluation(session: Session, organization_id: str, actor: str = "batch_evaluator") -> dict[str, Any]:
    cases = session.scalars(
        select(RecoveryCase)
        .where(RecoveryCase.organization_id == organization_id)
        .options(
            selectinload(RecoveryCase.customer),
            selectinload(RecoveryCase.revenue_event),
            selectinload(RecoveryCase.risk_assessment),
        )
    ).all()
    if not cases:
        return {"error": "No cases available for evaluation. Please seed dataset first."}

    policy = get_or_create_policy(session, organization_id)
    advisor = container.recovery_advisor()
    invalid_decisions = policy_violations = false_escalations = 0
    probabilities: list[float] = []
    human_approved = set(
        session.scalars(
            select(Escalation.case_id)
            .join(RecoveryCase, RecoveryCase.id == Escalation.case_id)
            .where(RecoveryCase.organization_id == organization_id, Escalation.resolution.in_(EXECUTING_RESOLUTIONS))
        )
    )

    for case in cases:
        customer = case.customer
        try:
            decision = advisor.advise(advisor_context(session, case))
        except Exception:
            logger.exception("Advisor failed while evaluating case %s", case.id)
            invalid_decisions += 1
            continue
        if decision.is_safe_failure:  # the advisor answered, but nothing usable: it fell back to a person
            invalid_decisions += 1

        probabilities.append(decision.recovery_probability)
        # Judge the case on what policy decided with: the days overdue recorded at intake, not the
        # event's age, which would turn every older auto-approved case into a "violation".
        factors = (case.risk_assessment.factors or {}) if case.risk_assessment else {}
        expected = PolicyEngine.evaluate(
            policy,
            case,
            customer,
            ai_confidence=decision.confidence,
            overdue_days=int(factors.get("overdue_days") or 0),
        ).decision
        acted_while_open = case.action_executed and case.status not in (CaseStatus.RECOVERED, CaseStatus.STOPPED)
        # A person approving what policy sent to review is the policy working, not a violation.
        if acted_while_open and expected != PolicyDecision.AUTO_APPROVE and case.id not in human_approved:
            policy_violations += 1
        if case.status == CaseStatus.ESCALATED and expected == PolicyDecision.AUTO_APPROVE:
            false_escalations += 1

    at_risk = sum(case.amount for case in cases)
    recovered = sum(case.recovered_amount for case in cases)
    recovered_cases = [case for case in cases if case.status == CaseStatus.RECOVERED]
    recovery_hours = [hours_between(case.created_at, case.updated_at) for case in recovered_cases]
    action_failures = session.scalar(
        select(func.count(RecoveryAction.id))
        .join(RecoveryCase, RecoveryCase.id == RecoveryAction.recovery_case_id)
        .where(RecoveryCase.organization_id == organization_id, RecoveryAction.status == ActionStatus.FAILED)
    )

    summary = {
        "cases_processed": len(cases),
        "advisor_model": advisor.model_name,
        "revenue_at_risk": at_risk,
        "revenue_targeted": sum(
            case.risk_assessment.expected_recovery_value
            for case in cases
            if case.policy_decision != PolicyDecision.STOP
        ),
        "revenue_recovered": recovered,
        "recovery_rate": round(recovered / at_risk, 4) if at_risk else 0.0,
        "average_recovery_probability": round(sum(probabilities) / len(probabilities), 4) if probabilities else 0.0,
        "average_ai_confidence": round(sum(case.ai_confidence for case in cases) / len(cases), 4),
        "successful_recoveries": len(recovered_cases),
        "escalations": sum(1 for case in cases if case.status == CaseStatus.ESCALATED),
        "false_escalations": false_escalations,
        "policy_violations": policy_violations,
        "invalid_ai_decisions": invalid_decisions,
        "action_failures": action_failures or 0,
        "average_recovery_time_hours": round(sum(recovery_hours) / len(recovery_hours), 2) if recovery_hours else 0.0,
        "evaluation_status": "PASSED" if policy_violations == 0 and invalid_decisions == 0 else "FAILED",
    }
    record_audit(session, organization_id, "evaluation.completed", summary, actor=actor)
    session.commit()
    return summary


def latest_evaluation(session: Session, organization_id: str) -> tuple[dict[str, Any], str, Any] | None:
    """The most recent evaluation summary, who ran it and when; None if none has run yet."""
    event = session.scalar(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id, AuditEvent.event_name == "evaluation.completed")
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    return (event.payload, event.actor, event.created_at) if event else None
