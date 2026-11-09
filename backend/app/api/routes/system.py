"""Health and demo-data endpoints."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import AdminUser, CurrentUser, DbSession, ManagerUser
from app.api.schemas.analytics import EvaluationSummary, LatestEvaluation
from app.api.schemas.misc import DueWorkflowRun, Health, SeedRequest, SeedResult
from app.application import evaluation
from app.application.bootstrap import reset_demo_dataset, run_due_workflows_now
from app.core.config import settings
from app.core.time import utc_now
from app.domain.exceptions import InvalidRequestError

router = APIRouter()


@router.get("/health", tags=["system"], responses={503: {"description": "Database unavailable"}})
def health(session: DbSession) -> Health:
    try:
        session.execute(select(1))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    return Health(
        status="ok",
        service="recoverx-control-plane",
        mode="live" if settings.uses_live_razorpay else "simulation",
        timestamp=utc_now(),
    )


DEMO_RESET_ERRORS = {403: {"description": "Disabled in production"}, 409: {"description": "More than one merchant"}}


@router.post("/demo/seed", tags=["demo"], responses=DEMO_RESET_ERRORS)
@router.post("/demo/reset", tags=["demo"], responses=DEMO_RESET_ERRORS)
def reset_demo_data(session: DbSession, _user: AdminUser, request: SeedRequest | None = None) -> SeedResult:
    """Replace all business data with the deterministic synthetic dataset. User accounts are kept."""
    request = request or SeedRequest()
    return SeedResult.model_validate(
        reset_demo_dataset(session, customer_count=request.customers, case_count=request.cases, seed=request.seed)
    )


@router.post("/demo/run-due-workflows", tags=["demo"], responses=DEMO_RESET_ERRORS)
def run_due_workflows(session: DbSession, _user: AdminUser) -> DueWorkflowRun:
    """Run the hourly scheduled job now: expire stale cases, send due follow-ups, check promises."""
    return DueWorkflowRun.model_validate(run_due_workflows_now(session))


@router.post("/demo/run-batch", tags=["demo"])
def run_batch_evaluation(session: DbSession, user: ManagerUser) -> EvaluationSummary:
    """Replay every case through the advisor and policy engine and report quality metrics."""
    summary = evaluation.run_evaluation(session, user.organization_id, actor=user.email)
    if "error" in summary:
        raise InvalidRequestError(summary["error"])
    return EvaluationSummary.model_validate(summary)


@router.get("/demo/evaluation", tags=["demo"])
def latest_batch_evaluation(session: DbSession, user: CurrentUser) -> LatestEvaluation | None:
    """The last batch evaluation's metrics, or null if none has been run."""
    latest = evaluation.latest_evaluation(session, user.organization_id)
    if latest is None:
        return None
    summary, run_by, run_at = latest
    return LatestEvaluation(summary=EvaluationSummary.model_validate(summary), run_by=run_by, run_at=run_at)
