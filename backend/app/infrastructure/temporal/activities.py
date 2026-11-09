"""Temporal activities. Each opens its own session and commits or rolls back atomically."""

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session
from temporalio import activity

from app.application.workflows.promise import PromiseToPayWorkflow
from app.application.workflows.recovery import RecoveryWorkflow
from app.infrastructure.database.models import PromiseToPay
from app.infrastructure.database.session import SessionLocal


def _in_transaction[T](work: Callable[[Session], T]) -> T:
    session = SessionLocal()
    try:
        result = work(session)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@activity.defn(name="execute_recovery_step")
def execute_recovery_step(case_id: str, fast_forward_days: int = 0) -> dict[str, Any]:
    return _in_transaction(
        lambda session: RecoveryWorkflow(case_id, session).execute_step(simulated_fast_forward_days=fast_forward_days)
    )


@activity.defn(name="evaluate_promise_commitment")
def evaluate_promise_commitment(promise_id: str) -> dict[str, Any]:
    return _in_transaction(lambda session: PromiseToPayWorkflow(promise_id, session).evaluate_commitment())


@activity.defn(name="get_promise_due_at")
def get_promise_due_at(promise_id: str) -> str | None:
    def due_at(session: Session) -> str | None:
        promise = session.get(PromiseToPay, promise_id)
        return promise.promised_date.isoformat() if promise else None

    return _in_transaction(due_at)
