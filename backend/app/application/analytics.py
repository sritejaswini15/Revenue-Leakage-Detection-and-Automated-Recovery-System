"""Recovery analytics computed live from the database. Nothing here is hardcoded or cached.

Every figure can be limited to a window of ``days``: the cases opened, or payments recorded,
from 00:00 UTC ``days - 1`` days ago until now. The dashboard cards and the trend chart use the
same window, so their totals agree. Definitions are in docs/API.md (Analytics).
"""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy import case as when
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import as_utc, hours_between, utc_now
from app.domain.constants import ActionStatus, CaseStatus, PolicyDecision
from app.infrastructure.database.models import (
    Experiment,
    ExperimentResult,
    Payment,
    RecoveryAction,
    RecoveryCase,
    RiskAssessment,
)


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def window_start(days: int | None) -> datetime | None:
    """Midnight UTC at the start of a ``days``-day window ending today; None means all time."""
    if not days:
        return None
    first_day = (utc_now() - timedelta(days=days - 1)).date()
    return datetime.combine(first_day, time.min, tzinfo=UTC)


def recovery_overview(session: Session, organization_id: str, days: int | None = None) -> dict[str, Any]:
    start = window_start(days)
    is_org = RecoveryCase.organization_id == organization_id
    if start is not None:
        is_org = and_(is_org, RecoveryCase.created_at >= start)
    not_stopped = RecoveryCase.policy_decision != PolicyDecision.STOP

    def count_where(condition: ColumnElement[bool]) -> ColumnElement[int]:
        return func.count(when((condition, 1)))

    row = session.execute(
        select(
            func.count(RecoveryCase.id),
            func.coalesce(func.sum(RecoveryCase.amount), 0),
            func.coalesce(func.sum(RecoveryCase.recovered_amount), 0),
            func.coalesce(func.sum(when((not_stopped, RiskAssessment.expected_recovery_value), else_=0)), 0),
            func.coalesce(func.sum(RiskAssessment.expected_recovery_value), 0),
            count_where(not_stopped),
            count_where(RecoveryCase.action_executed.is_(True)),
            count_where(RecoveryCase.status == CaseStatus.RECOVERED),
            count_where(RecoveryCase.status == CaseStatus.ESCALATED),
            count_where(RecoveryCase.status == CaseStatus.STOPPED),
            count_where(RecoveryCase.policy_decision == PolicyDecision.AUTO_APPROVE),
            func.coalesce(func.avg(RecoveryCase.ai_confidence), 0.0),
        )
        .select_from(RecoveryCase)
        .outerjoin(RiskAssessment, RiskAssessment.id == RecoveryCase.risk_assessment_id)
        .where(is_org)
    ).one()
    (
        cases,
        at_risk,
        recovered,
        targeted,
        expected,
        eligible,
        actions,
        successful,
        escalated,
        stopped,
        auto,
        confidence,
    ) = row

    recovered_spans = session.execute(
        select(RecoveryCase.created_at, RecoveryCase.updated_at).where(
            is_org, RecoveryCase.status == CaseStatus.RECOVERED
        )
    ).all()
    action_failures = session.scalar(
        select(func.count(RecoveryAction.id))
        .join(RecoveryCase, RecoveryCase.id == RecoveryAction.recovery_case_id)
        .where(is_org, RecoveryAction.status == ActionStatus.FAILED)
    )

    return {
        "revenue_at_risk": at_risk,
        "revenue_targeted": targeted,
        "revenue_recovered": recovered,
        "recovery_rate": _ratio(recovered, at_risk),
        "expected_recovery_value": expected,
        "cases": cases,
        "successful_recoveries": successful,
        "escalations": escalated,
        "stopped_cases": stopped,
        "auto_approved": auto,
        "action_failures": action_failures or 0,
        "average_ai_confidence": round(float(confidence), 4),
        "average_recovery_time_hours": _average([hours_between(start, end) for start, end in recovered_spans]),
        "funnel": {"risk_cases": cases, "eligible": eligible, "actions": actions, "successful": successful},
        "payments": _payment_metrics(session, organization_id, start),
        "window_days": days,
        "window_start": start,
        "mode": "live" if settings.uses_live_razorpay else "simulation",
    }


def _payment_metrics(session: Session, organization_id: str, start: datetime | None) -> dict[str, Any]:
    """Payments recorded in the window, by latest status (one row per provider payment)."""
    conditions = [Payment.organization_id == organization_id]
    if start is not None:
        conditions.append(Payment.created_at >= start)
    counts = {
        status: (count, amount or 0)
        for status, count, amount in session.execute(
            select(Payment.status, func.count(Payment.id), func.sum(Payment.amount))
            .where(*conditions)
            .group_by(Payment.status)
        ).all()
    }
    captured, captured_amount = counts.get("CAPTURED", (0, 0))
    failed, failed_amount = counts.get("FAILED", (0, 0))
    return {
        "captured": captured,
        "captured_amount": captured_amount,
        "failed": failed,
        "failed_amount": failed_amount,
        "authorized": counts.get("AUTHORIZED", (0, 0))[0],
        "success_rate": _ratio(captured, captured + failed),
    }


def strategy_performance(session: Session, organization_id: str, days: int | None = None) -> list[dict[str, Any]]:
    """Outcomes per recovery strategy.

    A case's strategy is the action most recently executed on it, or its
    recommended action when no action record exists (e.g. imported history).
    A case counts as an attempt once worked: an action executed or money recovered.
    """
    start = window_start(days)
    in_window = [RecoveryCase.organization_id == organization_id]
    if start is not None:
        in_window.append(RecoveryCase.created_at >= start)
    cases = session.execute(
        select(
            RecoveryCase.id,
            RecoveryCase.recommended_action,
            RecoveryCase.status,
            RecoveryCase.action_executed,
            RecoveryCase.recovered_amount,
            RecoveryCase.created_at,
            RecoveryCase.updated_at,
        ).where(*in_window)
    ).all()
    executed = session.execute(
        select(RecoveryAction.recovery_case_id, RecoveryAction.action_type)
        .join(RecoveryCase, RecoveryCase.id == RecoveryAction.recovery_case_id)
        .where(RecoveryCase.organization_id == organization_id, RecoveryAction.status == ActionStatus.EXECUTED)
        .order_by(RecoveryAction.created_at)
    ).all()
    latest_action = {case_id: action_type for case_id, action_type in executed}

    groups: dict[str, list[Any]] = defaultdict(list)
    for row in cases:
        groups[latest_action.get(row.id, row.recommended_action)].append(row)

    results = []
    for strategy in sorted(groups):
        rows = groups[strategy]
        recovered = [row for row in rows if row.status == CaseStatus.RECOVERED]
        attempts = sum(1 for row in rows if row.action_executed or row.status == CaseStatus.RECOVERED)
        results.append(
            {
                "strategy": strategy,
                "cases": len(rows),
                "attempts": attempts,
                "success": len(recovered),
                "recovery_rate": _ratio(len(recovered), attempts),
                "recovered": sum(row.recovered_amount for row in rows),
                "average_recovery_time": _average([hours_between(row.created_at, row.updated_at) for row in recovered]),
            }
        )
    return results


def recovery_trend(session: Session, organization_id: str, days: int) -> list[dict[str, Any]]:
    """Daily revenue detected and recovered, bucketed by the day each case was opened (UTC)."""
    since = window_start(days)
    start = since.date()
    rows = session.execute(
        select(RecoveryCase.created_at, RecoveryCase.amount, RecoveryCase.recovered_amount).where(
            RecoveryCase.organization_id == organization_id, RecoveryCase.created_at >= since
        )
    ).all()
    buckets: dict[date, dict[str, int]] = {
        start + timedelta(days=offset): {"detected": 0, "recovered": 0, "cases": 0} for offset in range(days)
    }
    for created_at, amount, recovered in rows:
        bucket = buckets.get(as_utc(created_at).date())
        if bucket is not None:
            bucket["detected"] += amount
            bucket["recovered"] += recovered
            bucket["cases"] += 1
    return [{"date": day.isoformat(), **values} for day, values in sorted(buckets.items())]


def experiments(session: Session, organization_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Experiment, ExperimentResult)
        .outerjoin(ExperimentResult, ExperimentResult.experiment_id == Experiment.id)
        .where(Experiment.organization_id == organization_id)
    ).all()

    by_experiment: dict[str, tuple[Experiment, dict[str, dict[str, int]]]] = {}
    for experiment, result in rows:
        _, variants = by_experiment.setdefault(experiment.id, (experiment, {}))
        if result is None:
            continue
        stats = variants.setdefault(result.variant, {"attempts": 0, "recoveries": 0, "revenue": 0})
        stats["attempts"] += 1
        if result.outcome == CaseStatus.RECOVERED:
            stats["recoveries"] += 1
            stats["revenue"] += result.revenue

    return [
        {
            "id": experiment.id,
            "name": experiment.name,
            "segment": experiment.segment,
            "status": experiment.status,
            "variants": [
                {
                    "variant": name,
                    "attempts": stats["attempts"],
                    "recoveries": stats["recoveries"],
                    "recovery_rate": _ratio(stats["recoveries"], stats["attempts"]),
                    "revenue_recovered": stats["revenue"],
                }
                for name, stats in variants.items()
            ],
        }
        for experiment, variants in by_experiment.values()
    ]
