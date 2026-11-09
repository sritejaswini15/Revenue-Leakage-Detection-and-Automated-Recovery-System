"""Promise-to-pay check: on the promised date, close the case if paid, otherwise escalate."""

from typing import Any

from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.application.case_state import escalate_case
from app.application.learning import learn_from_outcome
from app.core.time import as_utc, utc_now
from app.domain.constants import TERMINAL_CASE_STATUSES, CaseStatus, PaymentLinkStatus, PromiseStatus
from app.infrastructure.database.models import PromiseToPay, RecoveryCase
from app.infrastructure.database.repositories import get_payment_link

ACTOR = "promise_workflow"


class PromiseToPayWorkflow:
    def __init__(self, promise_id: str, session: Session) -> None:
        self.promise_id = promise_id
        self.session = session

    def evaluate_commitment(self) -> dict[str, Any]:
        promise = self.session.get(PromiseToPay, self.promise_id)
        if not promise:
            return {"error": "Promise not found"}
        case = self.session.get(RecoveryCase, promise.case_id)
        if not case:
            return {"error": "Case not found"}

        if promise.status != PromiseStatus.PENDING:
            return {"status": promise.status, "case_status": case.status}

        link = get_payment_link(self.session, case.id)
        paid = case.status == CaseStatus.RECOVERED or (link is not None and link.status == PaymentLinkStatus.PAID)
        if paid or promise.fulfilled:
            promise.fulfilled = True
            promise.status = PromiseStatus.FULFILLED
            if case.status != CaseStatus.RECOVERED:
                previous = case.status
                case.status = CaseStatus.RECOVERED
                learn_from_outcome(self.session, case, recovered=True, previous_status=previous)
                # What was actually paid, never the (possibly partial) amount promised.
                case.recovered_amount = link.amount if paid and link else promise.amount
            record_audit(
                self.session,
                case.organization_id,
                "promise.fulfilled",
                {"promise_id": promise.id, "amount": promise.amount, "recovered_amount": case.recovered_amount},
                actor=ACTOR,
                case_id=case.id,
            )
            self.session.flush()
            return {"status": PromiseStatus.FULFILLED, "case_status": CaseStatus.RECOVERED}

        if case.status in TERMINAL_CASE_STATUSES:
            # The case was stopped or expired meanwhile: the promise lapses, nobody needs to review it.
            promise.status = PromiseStatus.BROKEN
            record_audit(
                self.session,
                case.organization_id,
                "promise.lapsed",
                {"promise_id": promise.id, "case_status": case.status},
                actor=ACTOR,
                case_id=case.id,
            )
            self.session.flush()
            return {"status": PromiseStatus.BROKEN, "case_status": case.status}

        if as_utc(promise.promised_date) < utc_now():
            promise.status = PromiseStatus.BROKEN
            escalate_case(
                self.session,
                case,
                reason="MISSED_PROMISE_TO_PAY",
                notes=f"Customer missed promised payment date of {promise.promised_date.date()}",
                actor=ACTOR,
            )
            record_audit(
                self.session,
                case.organization_id,
                "promise.broken",
                {"promise_id": promise.id},
                actor=ACTOR,
                case_id=case.id,
            )
            self.session.flush()
            return {"status": PromiseStatus.BROKEN, "case_status": CaseStatus.ESCALATED}

        return {"status": promise.status, "promised_date": promise.promised_date.isoformat()}
