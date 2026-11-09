"""Read-mostly endpoints: analytics, audit trail and policy."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, DbSession, ManagerUser
from app.api.schemas.analytics import ExperimentOut, RecoveryOverview, StrategyPerformance, TrendPoint
from app.api.schemas.audit import AuditEventList, AuditEventOut
from app.api.schemas.policies import PolicySettings, PolicyUpdate
from app.application import analytics, audit, policies

router = APIRouter()


WindowDays = Annotated[int | None, Query(ge=1, le=365, description="Cases opened in the last N UTC days")]


@router.get("/analytics/recovery", tags=["analytics"])
def recovery_overview(session: DbSession, user: CurrentUser, days: WindowDays = None) -> RecoveryOverview:
    """Totals for cases opened in the window (all time without ``days``)."""
    return RecoveryOverview.model_validate(analytics.recovery_overview(session, user.organization_id, days))


@router.get("/analytics/strategy-performance", tags=["analytics"])
def strategy_performance(session: DbSession, user: CurrentUser, days: WindowDays = None) -> list[StrategyPerformance]:
    rows = analytics.strategy_performance(session, user.organization_id, days)
    return [StrategyPerformance.model_validate(row) for row in rows]


@router.get("/analytics/trend", tags=["analytics"])
def recovery_trend(
    session: DbSession, user: CurrentUser, days: Annotated[int, Query(ge=1, le=180)] = 30
) -> list[TrendPoint]:
    """Daily revenue detected and recovered, by the day each case was opened."""
    return [TrendPoint.model_validate(row) for row in analytics.recovery_trend(session, user.organization_id, days)]


@router.get("/analytics/experiments", tags=["analytics"])
def experiments(session: DbSession, user: CurrentUser) -> list[ExperimentOut]:
    return [ExperimentOut.model_validate(row) for row in analytics.experiments(session, user.organization_id)]


@router.get("/audit-events", tags=["audit"])
def audit_events(
    session: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    case_id: str | None = None,
    event_prefix: Annotated[str | None, Query(max_length=64)] = None,
) -> AuditEventList:
    events, total = audit.list_audit_events(
        session, user.organization_id, case_id=case_id, event_prefix=event_prefix, limit=limit, offset=offset
    )
    return AuditEventList(items=[AuditEventOut.model_validate(event) for event in events], total=total)


@router.get("/policies", tags=["policies"])
def get_policy(session: DbSession, user: CurrentUser) -> PolicySettings:
    return PolicySettings.model_validate(policies.get_policy(session, user.organization_id))


@router.put("/policies", tags=["policies"])
def update_policy(payload: PolicyUpdate, session: DbSession, user: ManagerUser) -> PolicySettings:
    return PolicySettings.model_validate(policies.update_policy(session, user, payload.model_dump()))
