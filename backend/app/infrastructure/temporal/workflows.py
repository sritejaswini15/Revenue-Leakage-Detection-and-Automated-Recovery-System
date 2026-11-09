"""Durable Temporal workflows for multi-day recovery and promise-to-pay checks."""

from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.infrastructure.temporal.activities import (
        evaluate_promise_commitment,
        execute_recovery_step,
        get_promise_due_at,
    )


_ACTIVITY_TIMEOUT = timedelta(seconds=45)
_RETRY_POLICY = RetryPolicy(maximum_attempts=3)


@workflow.defn(name="RecoverXRecoveryWorkflow")
class RecoverXRecoveryWorkflow:
    @workflow.run
    async def run(self, case_id: str) -> dict[str, Any]:
        result = await workflow.execute_activity(
            execute_recovery_step,
            args=[case_id, 0],
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY_POLICY,
        )
        if result.get("status") != "WAITING_FOR_PAYMENT":
            return result

        await workflow.sleep(timedelta(days=2))
        result = await workflow.execute_activity(
            execute_recovery_step,
            args=[case_id, 2],
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY_POLICY,
        )
        # A case contacted twice already (e.g. a reviewer approved another message) skips the
        # reminder and still waits before escalating.
        if result.get("status") not in ("FOLLOWUP_DISPATCHED", "WAITING_FOR_PAYMENT"):
            return result

        await workflow.sleep(timedelta(days=3))
        return await workflow.execute_activity(
            execute_recovery_step,
            args=[case_id, 3],
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY_POLICY,
        )


@workflow.defn(name="RecoverXPromiseToPayWorkflow")
class RecoverXPromiseToPayWorkflow:
    @workflow.run
    async def run(self, promise_id: str) -> dict[str, Any]:
        due_at = await workflow.execute_activity(
            get_promise_due_at,
            promise_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY_POLICY,
        )
        if due_at:
            due = datetime.fromisoformat(due_at)
            now = workflow.now()
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            if due > now:
                await workflow.sleep(due - now)
        return await workflow.execute_activity(
            evaluate_promise_commitment,
            promise_id,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY_POLICY,
        )
