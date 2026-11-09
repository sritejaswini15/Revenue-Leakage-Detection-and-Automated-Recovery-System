"""Synchronous bridge from request handlers to the Temporal service."""

import asyncio
from typing import Any

from app.core.config import settings


def _start(workflow_type: str, workflow_id: str, argument: str) -> dict[str, Any]:
    from temporalio.client import Client

    from app.infrastructure.temporal.workflows import RecoverXPromiseToPayWorkflow, RecoverXRecoveryWorkflow

    workflow_class = RecoverXRecoveryWorkflow if workflow_type == "recovery" else RecoverXPromiseToPayWorkflow

    async def start() -> str:
        client = await Client.connect(
            settings.TEMPORAL_HOST, namespace=settings.TEMPORAL_NAMESPACE, api_key=settings.TEMPORAL_API_KEY
        )
        handle = await client.start_workflow(
            workflow_class.run, argument, id=workflow_id, task_queue=settings.TEMPORAL_TASK_QUEUE
        )
        return handle.id

    try:
        started_id = asyncio.run(start())
    except RuntimeError as exc:
        raise RuntimeError("Temporal dispatch failed; verify TEMPORAL_HOST and worker availability") from exc
    return {"status": "DISPATCHED", "workflow_id": started_id}


def start_recovery_workflow(case_id: str) -> dict[str, Any]:
    return _start("recovery", f"recoverx-recovery-{case_id}", case_id)


def start_promise_workflow(promise_id: str) -> dict[str, Any]:
    return _start("promise", f"recoverx-promise-{promise_id}", promise_id)
