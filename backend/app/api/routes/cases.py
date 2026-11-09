from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, DbSession, ManagerUser, OperatorUser
from app.api.schemas.cases import (
    CaseActionResult,
    CaseDetailOut,
    CaseList,
    CasePromiseRequest,
    CaseSummary,
    EscalateRequest,
    EscalationItem,
    EscalationList,
    ExecuteRequest,
    PaymentLinkStatusOut,
    PromiseOut,
    ResolutionRequest,
    ResolutionResult,
)
from app.application import cases
from app.application.cases import CaseFilters

router = APIRouter()
CASES = ["cases"]

NOT_FOUND = {404: {"description": "Case not found"}}


@router.get("/recovery-cases", tags=CASES)
def list_cases(
    session: DbSession,
    user: CurrentUser,
    status: Annotated[str | None, Query(description="A case status, OPEN for every open status, or ALL")] = None,
    risk: Annotated[str | None, Query(description="HIGH, MEDIUM or LOW")] = None,
    revenue_type: Annotated[str | None, Query(description="Canonical event type, e.g. PAYMENT_FAILURE")] = None,
    query: Annotated[str | None, Query(description="Matches customer name or case id")] = None,
    limit: Annotated[int, Query(ge=1, le=cases.MAX_PAGE_SIZE)] = 100,
) -> CaseList:
    """Cases ordered by expected recovery value, highest first."""
    filters = CaseFilters(status=status, risk=risk, revenue_type=revenue_type, query=query, limit=limit)
    items, total = cases.list_cases(session, user.organization_id, filters)
    return CaseList(items=[CaseSummary.from_case(case) for case in items], total=total)


@router.get("/recovery-cases/{case_id}", tags=CASES, responses=NOT_FOUND)
def get_case(case_id: str, session: DbSession, user: CurrentUser) -> CaseDetailOut:
    return CaseDetailOut.from_detail(cases.get_case_detail(session, user.organization_id, case_id))


@router.post(
    "/recovery-cases/{case_id}/execute",
    tags=CASES,
    responses={
        **NOT_FOUND,
        409: {"description": "Blocked by merchant policy"},
        502: {"description": "Provider failed"},
    },
)
def execute_case(
    case_id: str, session: DbSession, user: OperatorUser, payload: ExecuteRequest | None = None
) -> CaseActionResult:
    """Run the recommended action, or the operator's choice. Only policy auto-approved cases run here."""
    action = cases.execute_case(session, user, case_id, payload.action_type if payload else None)
    return CaseActionResult(case_id=case_id, status="EXECUTED", action_id=action.id)


CLOSED = {409: {"description": "Case already recovered, stopped or expired"}}


@router.post("/recovery-cases/{case_id}/stop", tags=CASES, responses={**NOT_FOUND, **CLOSED})
def stop_case(case_id: str, session: DbSession, user: OperatorUser) -> CaseActionResult:
    """Stop an open case and cancel its unpaid payment link."""
    case = cases.stop_case(session, user, case_id)
    return CaseActionResult(case_id=case.id, status=case.status)


@router.post("/recovery-cases/{case_id}/escalate", tags=CASES, responses={**NOT_FOUND, **CLOSED})
def escalate_case(
    case_id: str, session: DbSession, user: OperatorUser, payload: EscalateRequest | None = None
) -> CaseActionResult:
    """Hand an open case to the human review queue."""
    case = cases.escalate(session, user, case_id, payload.notes if payload else None)
    return CaseActionResult(case_id=case.id, status=case.status)


@router.post(
    "/recovery-cases/{case_id}/promise-to-pay",
    tags=CASES,
    responses={**NOT_FOUND, **CLOSED, 400: {"description": "Amount above what is owed, or a past date"}},
)
def record_promise(case_id: str, payload: CasePromiseRequest, session: DbSession, user: OperatorUser) -> PromiseOut:
    """Record a promise the customer made; the promise workflow checks it on the promised date."""
    promise = cases.promise_on_behalf_of_customer(
        session, user, case_id, payload.amount, payload.promised_date, payload.notes
    )
    return PromiseOut.model_validate(promise)


@router.get(
    "/recovery-cases/{case_id}/payment-link",
    tags=CASES,
    responses={**NOT_FOUND, 409: {"description": "Link already closed"}, 410: {"description": "Link expired"}},
)
def get_payment_link(case_id: str, session: DbSession, user: OperatorUser) -> PaymentLinkStatusOut:
    """Status of the case's open payment link. The customer token is never returned after creation."""
    case, link = cases.get_open_payment_link(session, user.organization_id, case_id)
    return PaymentLinkStatusOut(
        case_id=case.id,
        payment_link_id=link.id,
        amount=link.amount,
        currency=case.currency,
        status=link.status,
        short_url=link.short_url,
        expires_at=link.expires_at,
    )


@router.get("/escalations", tags=["escalations"])
def list_escalations(
    session: DbSession,
    user: CurrentUser,
    queue: Annotated[str | None, Query(description="Only this review queue, e.g. HIGH_VALUE")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EscalationList:
    """The human review queue, most recoverable value first, a page at a time.

    `total` counts the cases matching `queue`; `queues` always counts every queue.
    """
    escalated = cases.list_escalated_cases(session, user.organization_id)
    wanted = queue.strip().upper() if queue and queue.strip().upper() != "ALL" else None
    selected = [entry for entry in escalated if wanted is None or entry.queue == wanted]
    return EscalationList(
        items=[
            EscalationItem(
                **CaseSummary.from_case(entry.case).model_dump(),
                escalation_reason=entry.reason,
                queue=entry.queue,
                escalated_at=entry.escalated_at,
            )
            for entry in selected[offset : offset + limit]
        ],
        total=len(selected),
        limit=limit,
        offset=offset,
        queues=dict(Counter(entry.queue for entry in escalated)),
    )


RESOLUTION_ERRORS = {
    **NOT_FOUND,
    400: {"description": "Unknown resolution, or EDIT without an action"},
    409: {"description": "Case already closed, action not executable, or blocked by merchant policy"},
    502: {"description": "Provider failed; the escalation stays open"},
}


@router.post("/recovery-cases/{case_id}/approve", tags=["escalations"], responses=RESOLUTION_ERRORS)
@router.post("/escalations/{case_id}/resolve", tags=["escalations"], responses=RESOLUTION_ERRORS)
def resolve_escalation(
    case_id: str, payload: ResolutionRequest, session: DbSession, user: ManagerUser
) -> ResolutionResult:
    """Human decision on a case awaiting review: APPROVE, EDIT, CONTACT, REJECT or CLOSE."""
    case = cases.resolve_escalation(
        session, user, case_id, payload.resolution, payload.notes, action_type=payload.action_type
    )
    return ResolutionResult(case_id=case.id, resolution=payload.resolution.strip().upper(), status=case.status)
