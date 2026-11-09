"""Temporal worker entry point: ``python -m app.infrastructure.temporal.worker``.

Requires TEMPORAL_ENABLED=true and a reachable Temporal server.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.core.observability import setup_process
from app.infrastructure.temporal.activities import (
    evaluate_promise_commitment,
    execute_recovery_step,
    get_promise_due_at,
)
from app.infrastructure.temporal.workflows import RecoverXPromiseToPayWorkflow, RecoverXRecoveryWorkflow

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    client = await Client.connect(
        settings.TEMPORAL_HOST, namespace=settings.TEMPORAL_NAMESPACE, api_key=settings.TEMPORAL_API_KEY
    )
    with ThreadPoolExecutor(max_workers=8) as activity_executor:
        worker = Worker(
            client,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            workflows=[RecoverXRecoveryWorkflow, RecoverXPromiseToPayWorkflow],
            activities=[execute_recovery_step, evaluate_promise_commitment, get_promise_due_at],
            activity_executor=activity_executor,
        )
        logger.info("Temporal worker polling %s on %s", settings.TEMPORAL_TASK_QUEUE, settings.TEMPORAL_HOST)
        await worker.run()


def main() -> None:
    setup_process()
    if not settings.TEMPORAL_ENABLED:
        raise SystemExit("TEMPORAL_ENABLED is false; the worker has nothing to do.")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
