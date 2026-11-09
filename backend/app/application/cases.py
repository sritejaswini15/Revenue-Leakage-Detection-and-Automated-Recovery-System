"""Recovery case queries and operator commands."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import ColumnElement, desc, false, func, or_, select
from sqlalchemy.orm import Session, contains_eager, selectinload

from app.application.audit import record_audit
from app.application.case_state import ensure_open, escalate_case, record_promise
from app.application.recovery_actions import evaluate_case_policy, tool_gateway
from app.application.workflows import runner
from app.core.time import as_utc, utc_now
from app.domain.constants import (
    CLOSED_LINK_STATUSES,
    EXECUTABLE_ACTIONS,
    EXECUTING_RESOLUTIONS,
    OPEN_CASE_STATUSES,
    PAYMENT_LINK_VARIANTS,
    ActionType,
    CaseStatus,
    PaymentLinkStatus,
    Resolution,
)
from app.domain.exceptions import (
    ConflictError,
    ExpiredError,
    InvalidRequestError,
    NotFoundError,
    PolicyViolationError,
    ProviderError,
)
from app.domain.policy import PolicyEvaluationResult, escalation_queue
from app.domain.risk import RiskReason, explain_risk
from app.infrastructure.database.models import (
    AgentDecision,
    Communication,
    Customer,
    Escalation,
    Payment,
    PaymentAttempt,
    PaymentLink,
    PromiseToPay,
    RecoveryAction,
    RecoveryCase,
    RevenueEvent,
    RiskAssessment,
    User,
)
from app.infrastructure.database.repositories import get_case, get_or_create_policy, get_payment_link

MAX_PAGE_SIZE = 500


@dataclass(frozen=True)
class CaseFilters:
    status: str | None = None
    risk: str | None = None  # HIGH (>=75), MEDIUM (50-74), LOW (<50)
    revenue_type: str | None = None
    query: str | None = None
    limit: int = 100


@dataclass(frozen=True)
class CaseDetail:
    case: RecoveryCase
    decision: AgentDecision | None
    payment_link: PaymentLink | None
    payment_url: str | None
    communications: list[Communication] = field(default_factory=list)
    escalation: Escalation | None = None
    promises: list[PromiseToPay] = field(default_factory=list)
    actions: list[RecoveryAction] = field(default_factory=list)
    payment_attempts: list[tuple[Payment, PaymentAttempt]] = field(default_factory=list)
    executable_actions: list[str] = field(default_factory=list)
    # The merchant's current policy applied to the case now: what the Tool Gateway would decide.
    policy: PolicyEvaluationResult | None = None
    risk_reasons: list[RiskReason] = field(default_factory=list)


def _risk_condition(tier: str) -> ColumnElement[bool]:
    score = RiskAssessment.risk_score
    conditions = {"HIGH": score >= 75, "MEDIUM": score.between(50, 74), "LOW": score < 50}
    return conditions.get(tier, false())


def list_cases(session: Session, organization_id: str, filters: CaseFilters) -> tuple[list[RecoveryCase], int]:
    """Cases ordered by expected recovery value, highest first."""
    conditions: list[ColumnElement[bool]] = [RecoveryCase.organization_id == organization_id]
    if filters.status and filters.status.upper() == "OPEN":  # still being worked: not recovered, stopped or expired
        conditions.append(RecoveryCase.status.in_(OPEN_CASE_STATUSES))
    elif filters.status and filters.status.upper() != "ALL":
        conditions.append(RecoveryCase.status == filters.status.upper())
    if filters.risk and filters.risk.upper() != "ALL":
        conditions.append(_risk_condition(filters.risk.upper()))
    if filters.revenue_type and filters.revenue_type.upper() != "ALL":
        conditions.append(RevenueEvent.event_type == filters.revenue_type.upper())
    if filters.query:
        needle = filters.query.lower()
        conditions.append(
            or_(
                func.lower(Customer.name).contains(needle, autoescape=True),
                func.lower(RecoveryCase.id).contains(needle, autoescape=True),
            )
        )

    joined = (
        select(RecoveryCase)
        .join(RecoveryCase.risk_assessment)
        .join(RecoveryCase.revenue_event)
        .join(RecoveryCase.customer)
        .where(*conditions)
    )
    total = session.scalar(select(func.count()).select_from(joined.subquery())) or 0
    cases = session.scalars(
        joined.options(
            contains_eager(RecoveryCase.risk_assessment),
            contains_eager(RecoveryCase.revenue_event),
            contains_eager(RecoveryCase.customer),
        )
        .order_by(desc(RiskAssessment.expected_recovery_value), desc(RecoveryCase.created_at))
        .limit(max(1, min(filters.limit, MAX_PAGE_SIZE)))
    ).all()
    return list(cases), total


def get_case_detail(session: Session, organization_id: str, case_id: str) -> CaseDetail:
    case = get_case(session, organization_id, case_id)
    actions = session.scalars(
        select(RecoveryAction)
        .where(RecoveryAction.recovery_case_id == case.id)
        .order_by(desc(RecoveryAction.created_at))
    ).all()
    # The customer page URL is only known when the link is created; it lives on that action.
    payment_url = next((a.payload["payment_url"] for a in actions if (a.payload or {}).get("payment_url")), None)
    return CaseDetail(
        case=case,
        decision=session.scalar(
            select(AgentDecision)
            .where(AgentDecision.recovery_case_id == case.id)
            .order_by(desc(AgentDecision.created_at))
            .limit(1)
        ),
        payment_link=get_payment_link(session, case.id),
        payment_url=payment_url,
        communications=list(session.scalars(select(Communication).where(Communication.case_id == case.id))),
        escalation=session.scalar(select(Escalation).where(Escalation.case_id == case.id)),
        promises=list(session.scalars(select(PromiseToPay).where(PromiseToPay.case_id == case.id))),
        actions=list(actions),
        payment_attempts=[
            (payment, attempt)
            for payment, attempt in session.execute(
                select(Payment, PaymentAttempt)
                .join(PaymentAttempt, PaymentAttempt.payment_id == Payment.id)
                .where(Payment.organization_id == organization_id, Payment.recovery_case_id == case.id)
                .order_by(desc(PaymentAttempt.created_at))
            ).all()
        ],
        executable_actions=executable_actions(session, case),
        policy=current_policy(session, case),
        risk_reasons=explain_risk(case.risk_assessment.factors or {}) if case.risk_assessment else [],
    )


def current_policy(session: Session, case: RecoveryCase) -> PolicyEvaluationResult:
    """The merchant's policy applied to the case now: exactly what the Tool Gateway will decide."""
    return evaluate_case_policy(session, case)


def executable_actions(session: Session, case: RecoveryCase) -> list[str]:
    """Actions that may run on this open case: executable and allowed by policy.

    An operator runs them with execute; on an escalated case only the reviewer can, with EDIT.
    """
    if case.status not in OPEN_CASE_STATUSES:
        return []
    policy = get_or_create_policy(session, case.organization_id)
    allowed = {value.strip().upper() for value in policy.allowed_actions.split(",") if value.strip()}
    if ActionType.PAYMENT_LINK in allowed:
        allowed |= PAYMENT_LINK_VARIANTS
    return [action for action in EXECUTABLE_ACTIONS if action in allowed]


@dataclass(frozen=True)
class EscalatedCase:
    case: RecoveryCase
    reason: str
    queue: str
    escalated_at: datetime | None


def list_escalated_cases(session: Session, organization_id: str) -> list[EscalatedCase]:
    """The human review queue, most recoverable value first, each case in its review queue."""
    cases = session.scalars(
        select(RecoveryCase)
        .join(RecoveryCase.risk_assessment)
        .where(RecoveryCase.organization_id == organization_id, RecoveryCase.status == CaseStatus.ESCALATED)
        .options(
            contains_eager(RecoveryCase.risk_assessment),
            selectinload(RecoveryCase.escalation),
            selectinload(RecoveryCase.customer),
            selectinload(RecoveryCase.revenue_event),
        )
        .order_by(desc(RiskAssessment.expected_recovery_value), desc(RecoveryCase.created_at))
    ).all()
    escalated = []
    for case in cases:
        # Demo cases can be escalated without an escalation row: fall back to what policy says now.
        reason = case.escalation.reason if case.escalation else ",".join(current_policy(session, case).rule_violations)
        reason = reason or "POLICY_REVIEW_REQUIRED"
        escalated.append(
            EscalatedCase(
                case=case,
                reason=reason,
                queue=escalation_queue(reason),
                escalated_at=case.escalation.created_at if case.escalation else None,
            )
        )
    return escalated


def execute_case(session: Session, user: User, case_id: str, action_type: str | None = None) -> RecoveryAction:
    """Run the recommended action, or one the operator chose; the policy is checked either way."""
    case = get_case(session, user.organization_id, case_id)
    if case.status == CaseStatus.ESCALATED:
        raise ConflictError("This case is waiting for a reviewer; resolve the escalation instead")
    chosen = action_type or case.recommended_action
    if action_type and action_type != case.recommended_action:
        record_audit(
            session,
            case.organization_id,
            "action.override",
            {"recommended": case.recommended_action, "chosen": action_type},
            actor=user.email,
            case_id=case.id,
        )
    try:
        action = tool_gateway().execute_recovery_action(session, case, chosen, actor=user.email)
    except (ProviderError, PolicyViolationError):
        session.commit()  # keep the FAILED action or the policy refusal in the audit trail
        raise
    session.commit()
    return action


def stop_case(
    session: Session, user: User, case_id: str, reason: str = "Operator manually stopped case"
) -> RecoveryCase:
    case = get_case(session, user.organization_id, case_id)
    stop_open_case(session, case, user.email, reason)
    session.commit()
    return case


def stop_open_case(session: Session, case: RecoveryCase, actor: str, reason: str) -> None:
    """Close an open case and its unpaid link, so the customer can no longer pay a case nobody tracks."""
    ensure_open(case)
    link = tool_gateway().cancel_open_link(session, case)
    case.status = CaseStatus.STOPPED
    record_audit(
        session,
        case.organization_id,
        "workflow.stopped",
        {"reason": reason, "cancelled_link": link.provider_link_id if link else None},
        actor=actor,
        case_id=case.id,
    )


def escalate(session: Session, user: User, case_id: str, notes: str | None) -> RecoveryCase:
    """An operator hands an open case to a human reviewer."""
    case = get_case(session, user.organization_id, case_id)
    if case.status == CaseStatus.ESCALATED:  # keep the original reason, and its review queue
        raise ConflictError("This case is already waiting for a reviewer")
    escalate_case(session, case, reason="OPERATOR_REQUEST", actor=user.email, notes=notes)
    session.commit()
    return case


def get_open_payment_link(session: Session, organization_id: str, case_id: str) -> tuple[RecoveryCase, PaymentLink]:
    case = get_case(session, organization_id, case_id)
    link = get_payment_link(session, case.id)
    if not link:
        raise NotFoundError("No payment link found for this case")
    if link.status in CLOSED_LINK_STATUSES:
        raise ConflictError(f"Payment link is in terminal state: {link.status}")
    if link.expires_at and as_utc(link.expires_at) <= utc_now():
        link.status = PaymentLinkStatus.EXPIRED
        session.commit()
        raise ExpiredError("Payment link has expired")
    return case, link


def resolve_escalation(
    session: Session,
    user: User,
    case_id: str,
    resolution: str,
    notes: str | None,
    action_type: str | None = None,
) -> RecoveryCase:
    """A person's decision on a case awaiting review.

    APPROVE runs the recommended action, EDIT runs the action the reviewer chose and
    CONTACT sends personalised outreach; all three still pass the Tool Gateway, which
    refuses anything policy stops outright (opt-out, contact limit). REJECT and CLOSE
    stop the case. A closed case cannot be resolved, so its outcome is never rewritten.
    """
    try:
        decision = Resolution(resolution.strip().upper())
    except ValueError as exc:
        allowed = ", ".join(Resolution)
        raise InvalidRequestError(f"Unknown resolution {resolution!r}; use one of {allowed}") from exc
    case = get_case(session, user.organization_id, case_id)
    ensure_open(case)
    escalation = session.scalar(select(Escalation).where(Escalation.case_id == case.id))
    if not escalation:
        escalation = escalate_case(
            session, case, reason=case.policy_decision or "HUMAN_REVIEW", actor=user.email, assigned_to_user_id=user.id
        )

    chosen: str | None = None
    if decision in EXECUTING_RESOLUTIONS:
        chosen = _reviewed_action(case, decision, action_type)
        if chosen != case.recommended_action:
            record_audit(
                session,
                case.organization_id,
                "action.override",
                {"recommended": case.recommended_action, "chosen": chosen, "resolution": decision},
                actor=user.email,
                case_id=case.id,
            )
        # The case stays ESCALATED until the gateway succeeds, so its policy re-check sees the real state.
        try:
            tool_gateway().execute_recovery_action(
                session, case, chosen, actor=user.email, human_approved_by=user.email
            )
        except ProviderError:
            session.commit()  # keep the FAILED action and its audit record; the escalation stays open
            raise
        runner.trigger_recovery_workflow(session, case.id)
    else:
        stop_open_case(session, case, user.email, f"Reviewer resolution {decision}")

    escalation.resolution = decision
    escalation.resolution_notes = notes
    record_audit(
        session,
        user.organization_id,
        "escalation.resolved",
        {"resolution": decision, "action": chosen, "notes": notes},
        actor=user.email,
        case_id=case.id,
    )
    session.commit()
    return case


def _reviewed_action(case: RecoveryCase, decision: Resolution, action_type: str | None) -> str:
    if decision == Resolution.CONTACT:
        return ActionType.PERSONALIZED_OUTREACH
    if decision == Resolution.EDIT:
        if not action_type:
            raise InvalidRequestError("EDIT needs the action_type to run instead")
        chosen = action_type.strip().upper()
    else:
        chosen = case.recommended_action
    if chosen not in EXECUTABLE_ACTIONS:
        hint = " Choose an executable action with EDIT." if decision == Resolution.APPROVE else ""
        raise ConflictError(f"{chosen} cannot be executed.{hint}")
    return chosen


def create_promise(
    session: Session, user: User, case_id: str, amount: int, promised_date: datetime, notes: str | None
) -> PromiseToPay:
    case = get_case(session, user.organization_id, case_id)
    promise = record_promise(session, case, amount, promised_date, actor=user.email, notes=notes)
    session.commit()
    runner.schedule_promise_check(promise.id)
    return promise


def promise_on_behalf_of_customer(
    session: Session, user: User, case_id: str, amount: int, promised_date: datetime, notes: str | None
) -> PromiseToPay:
    """An operator records a promise the customer made (by phone, email…): the date must be ahead."""
    if as_utc(promised_date).date() < utc_now().date():
        raise InvalidRequestError("The promised date must be today or later")
    return create_promise(session, user, case_id, amount, promised_date, notes)


def evaluate_promise(session: Session, organization_id: str, promise_id: str) -> dict:
    """Check a promise now: close the case if paid, escalate if the date has passed."""
    promise = session.scalar(
        select(PromiseToPay)
        .join(RecoveryCase, RecoveryCase.id == PromiseToPay.case_id)
        .where(PromiseToPay.id == promise_id, RecoveryCase.organization_id == organization_id)
    )
    if not promise:
        raise NotFoundError("Promise not found")
    result = runner.trigger_promise_workflow(session, promise_id)
    session.commit()
    return result
