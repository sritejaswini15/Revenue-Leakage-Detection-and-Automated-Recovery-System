"""Simulation center: exercise real state transitions through the simulated provider."""

from typing import Any

from fastapi import APIRouter

from app.api.dependencies import DbSession, OperatorUser
from app.api.schemas.simulation import (
    EventRequest,
    EventResult,
    InvalidAiResult,
    LowConfidenceResult,
    MaxAttemptsResult,
    OptOutResult,
    PromiseRequest,
    PromiseResult,
    RetryResult,
    SimulatedPaymentResult,
    SimulationRequest,
)
from app.application import cases, simulation
from app.domain.constants import TERMINAL_CASE_STATUSES
from app.domain.messages import format_inr

router = APIRouter(prefix="/simulation", tags=["simulation"])

CASE_ERRORS = {400: {"description": "case_id missing"}, 404: {"description": "Case not found"}}


@router.post("/payment", responses=CASE_ERRORS)
def simulate_payment(request: SimulationRequest, session: DbSession, user: OperatorUser) -> SimulatedPaymentResult:
    """The customer pays: a signed success webhook runs through the pipeline and resolves the case."""
    case = simulation.simulate_payment(session, user, request.case_id)
    return SimulatedPaymentResult(
        case_id=case.id,
        status=case.status,
        recovered_amount=case.recovered_amount,
        message=f"Captured {format_inr(case.recovered_amount)}. Case status: {case.status}.",
    )


@router.post("/failure", responses=CASE_ERRORS)
@router.post("/timeout", responses=CASE_ERRORS)
def simulate_provider_timeout(request: SimulationRequest, session: DbSession, user: OperatorUser) -> RetryResult:
    """Razorpay times out while a payment link is issued; the failure is recorded and the action retried once."""
    outcome = simulation.simulate_provider_retry(session, user, request.case_id)
    return RetryResult(
        case_id=outcome.case.id,
        status=outcome.action.status,
        workflow_preserved=outcome.case.status not in TERMINAL_CASE_STATUSES,
        action_id=outcome.action.id,
        action_type=outcome.action.action_type,
        failed_attempts=outcome.failed_attempts,
        messages_sent=outcome.messages_sent,
    )


@router.post("/opt-out", responses={404: {"description": "Customer not found"}})
def simulate_opt_out(request: SimulationRequest, session: DbSession, user: OperatorUser) -> OptOutResult:
    """Opt a customer (by customer_id, or via one of their cases) out of all automated contact."""
    result = simulation.opt_out_customer(session, user, request.customer_id, request.case_id)
    return OptOutResult(customer_id=result.customer.id, opted_out=True, cases_stopped=result.cases_stopped)


@router.post("/low-confidence", responses=CASE_ERRORS)
def simulate_low_confidence(request: SimulationRequest, session: DbSession, user: OperatorUser) -> LowConfidenceResult:
    case = simulation.simulate_low_confidence(session, user, request.case_id)
    return LowConfidenceResult(case_id=case.id, status=case.status, ai_confidence=case.ai_confidence)


@router.post("/max-attempts", responses=CASE_ERRORS)
def simulate_max_attempts(request: SimulationRequest, session: DbSession, user: OperatorUser) -> MaxAttemptsResult:
    case = simulation.simulate_max_attempts(session, user, request.case_id)
    return MaxAttemptsResult(case_id=case.id, status=case.status, contact_attempts=case.contact_attempts)


@router.post("/invalid-ai", responses={**CASE_ERRORS, 409: {"description": "Case already closed"}})
def simulate_invalid_ai(request: SimulationRequest, session: DbSession, user: OperatorUser) -> InvalidAiResult:
    case = simulation.simulate_invalid_ai(session, user, request.case_id)
    return InvalidAiResult(case_id=case.id, status=case.status, fallback="HUMAN_REVIEW")


@router.post("/promise-to-pay", responses={404: {"description": "Case not found"}})
def create_promise(payload: PromiseRequest, session: DbSession, user: OperatorUser) -> PromiseResult:
    promise = cases.create_promise(session, user, payload.case_id, payload.amount, payload.promised_date, payload.notes)
    return PromiseResult.model_validate(promise)


@router.post("/promise-to-pay/{promise_id}/fulfill", responses={404: {"description": "Promise not found"}})
def evaluate_promise(promise_id: str, session: DbSession, user: OperatorUser) -> dict[str, Any]:
    """Check the promise now: the case closes if paid and escalates if the date has passed."""
    return cases.evaluate_promise(session, user.organization_id, promise_id)


@router.post("/event", responses={400: {"description": "Invalid event"}, 404: {"description": "Customer not found"}})
def simulate_event(payload: EventRequest, session: DbSession, user: OperatorUser) -> EventResult:
    """A revenue event arrives from the simulated provider and runs the full recovery pipeline."""
    outcome = simulation.simulate_event(
        session, user, payload.event_type, payload.customer_id, payload.amount, payload.currency, payload.id
    )
    return EventResult(
        event_id=outcome.event.id if outcome.event else None,
        case_id=outcome.case.id if outcome.case else None,
        event_type=outcome.event.event_type if outcome.event else None,
        status=outcome.case.status if outcome.case else None,
        is_duplicate=outcome.duplicate,
    )
