"""Multi-day recovery follow-up.

Step semantics: after the first contact, wait two days and send a reminder;
after the second contact without payment, escalate to a human.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.application.case_state import escalate_case
from app.application.learning import learn_from_outcome
from app.application.recovery_actions import tool_gateway
from app.core.time import utc_now
from app.domain.constants import ActionType, CaseStatus, PaymentLinkStatus, PolicyDecision
from app.domain.exceptions import PolicyViolationError
from app.domain.policy import PolicyEngine
from app.infrastructure.database.models import Customer, RecoveryCase
from app.infrastructure.database.repositories import get_or_create_policy, get_payment_link

FOLLOW_UP_AFTER_DAYS = 2
ESCALATE_AFTER_DAYS = 3  # after the reminder
ACTOR = "workflow_engine"
_FINISHED_STATUSES = (CaseStatus.RECOVERED, CaseStatus.STOPPED, CaseStatus.ESCALATED)


class RecoveryWorkflow:
    def __init__(self, case_id: str, session: Session) -> None:
        self.case_id = case_id
        self.session = session

    def execute_step(self, simulated_fast_forward_days: int = 0) -> dict[str, Any]:
        case = self.session.get(RecoveryCase, self.case_id)
        if not case:
            return {"error": "Case not found", "status": "UNKNOWN"}
        if case.status in _FINISHED_STATUSES:
            return {"status": case.status, "message": "Workflow already completed"}

        link = get_payment_link(self.session, case.id)
        if link and link.status == PaymentLinkStatus.PAID:
            previous = case.status
            case.status = CaseStatus.RECOVERED
            case.recovered_amount = case.amount
            learn_from_outcome(self.session, case, recovered=True, previous_status=previous)
            self._audit(case, "workflow.completed", {"recovered": True, "amount": case.amount})
            self.session.flush()
            return {"status": CaseStatus.RECOVERED, "recovered_amount": case.amount}

        if case.contact_attempts == 1:
            if simulated_fast_forward_days < FOLLOW_UP_AFTER_DAYS:
                next_check_at = utc_now() + timedelta(days=FOLLOW_UP_AFTER_DAYS - simulated_fast_forward_days)
                return {
                    "status": CaseStatus.WAITING_FOR_PAYMENT,
                    "attempts": case.contact_attempts,
                    "next_check_at": next_check_at.isoformat(),
                }
            try:
                action = tool_gateway().execute_recovery_action(
                    self.session, case, ActionType.REMINDER, actor="temporal_workflow"
                )
            except PolicyViolationError as refusal:
                return self._follow_up_refused(case, str(refusal))
            self._audit(case, "workflow.followup_sent", {"action_id": action.id, "attempt": case.contact_attempts})
            return {"status": "FOLLOWUP_DISPATCHED", "attempts": case.contact_attempts}

        if case.contact_attempts >= 2:
            # Give the customer the full waiting period after the latest contact, including one a
            # reviewer just approved; escalating at once would bounce the case straight back.
            if simulated_fast_forward_days < ESCALATE_AFTER_DAYS:
                next_check_at = utc_now() + timedelta(days=ESCALATE_AFTER_DAYS - simulated_fast_forward_days)
                return {
                    "status": CaseStatus.WAITING_FOR_PAYMENT,
                    "attempts": case.contact_attempts,
                    "next_check_at": next_check_at.isoformat(),
                }
            escalation = escalate_case(
                self.session,
                case,
                reason="MAX_ATTEMPTS",
                notes="Automated recovery workflow completed 2 attempts without payment.",
                actor="temporal_workflow",
            )
            self._audit(case, "workflow.escalated", {"escalation_id": escalation.id})
            return {"status": CaseStatus.ESCALATED, "escalation_id": escalation.id}

        return {"status": case.status, "contact_attempts": case.contact_attempts}

    def _follow_up_refused(self, case: RecoveryCase, reason: str) -> dict[str, Any]:
        """Policy refused the automatic reminder: stop if contact is forbidden, otherwise ask a person."""
        policy = get_or_create_policy(self.session, case.organization_id)
        customer = self.session.get(Customer, case.customer_id)
        if PolicyEngine.evaluate(policy, case, customer).decision == PolicyDecision.STOP:
            case.status = CaseStatus.STOPPED
            self._audit(case, "workflow.stopped", {"reason": reason})
            self.session.flush()
            return {"status": CaseStatus.STOPPED, "reason": reason}
        escalation = escalate_case(
            self.session, case, reason="FOLLOW_UP_NEEDS_REVIEW", notes=reason, actor="temporal_workflow"
        )
        self._audit(case, "workflow.escalated", {"escalation_id": escalation.id, "reason": reason})
        return {"status": CaseStatus.ESCALATED, "escalation_id": escalation.id}

    def _audit(self, case: RecoveryCase, event_name: str, payload: dict[str, Any]) -> None:
        record_audit(self.session, case.organization_id, event_name, payload, actor=ACTOR, case_id=case.id)
