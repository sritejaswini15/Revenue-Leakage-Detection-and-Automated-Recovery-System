"""Workflow dispatch: Temporal when enabled, otherwise an in-process step."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.application.workflows.promise import PromiseToPayWorkflow
from app.application.workflows.recovery import RecoveryWorkflow
from app.core.config import settings

logger = logging.getLogger(__name__)


def _dispatch_failed(kind: str, exc: Exception) -> dict[str, Any]:
    logger.error("Temporal %s workflow dispatch failed: %s", kind, exc)
    return {"status": "DISPATCH_FAILED", "workflow_id": None, "error": str(exc)}


def trigger_recovery_workflow(session: Session, case_id: str, fast_forward_days: int = 0) -> dict[str, Any]:
    if settings.TEMPORAL_ENABLED:
        from app.infrastructure.temporal.client import start_recovery_workflow

        try:
            return start_recovery_workflow(case_id)
        except Exception as exc:  # noqa: BLE001 - dispatch failure must not fail the business action
            return _dispatch_failed("recovery", exc)
    return RecoveryWorkflow(case_id, session).execute_step(simulated_fast_forward_days=fast_forward_days)


def schedule_promise_check(promise_id: str) -> dict[str, Any] | None:
    """Have Temporal watch a newly recorded promise until its date.

    Call it after the promise is committed, so the workflow can read it. Without Temporal
    it does nothing: the hourly scheduler checks promises once their date has passed.
    """
    if not settings.TEMPORAL_ENABLED:
        return None
    from app.infrastructure.temporal.client import start_promise_workflow

    try:
        return start_promise_workflow(promise_id)
    except Exception as exc:  # noqa: BLE001 - the promise is recorded; the scheduler can still pick it up
        return _dispatch_failed("promise", exc)


def trigger_promise_workflow(session: Session, promise_id: str) -> dict[str, Any]:
    if settings.TEMPORAL_ENABLED:
        from app.infrastructure.temporal.client import start_promise_workflow

        try:
            return start_promise_workflow(promise_id)
        except Exception as exc:  # noqa: BLE001
            return _dispatch_failed("promise", exc)
    return PromiseToPayWorkflow(promise_id, session).evaluate_commitment()
