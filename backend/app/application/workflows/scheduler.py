"""Expire stale cases and advance in-process workflows whose waiting period has elapsed.

With TEMPORAL_ENABLED=false nothing else wakes a waiting case up, so this runs on a
schedule (see scripts/run_due_workflows.py and the Render cron job). Temporal
deployments do not need it: the durable workflows sleep and resume themselves.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.case_expiry import expire_due_cases
from app.application.workflows.promise import PromiseToPayWorkflow
from app.application.workflows.recovery import ESCALATE_AFTER_DAYS, FOLLOW_UP_AFTER_DAYS, RecoveryWorkflow
from app.core.time import as_utc, utc_now
from app.domain.constants import ActionStatus, CaseStatus, PromiseStatus
from app.domain.exceptions import RecoverXError
from app.infrastructure.database.models import PromiseToPay, RecoveryAction, RecoveryCase

logger = logging.getLogger(__name__)


def run_due_workflows(session: Session, now: datetime | None = None) -> dict[str, int]:
    """Expire stale cases, send due reminders, escalate unanswered cases and check promises past their date.

    Each case commits on its own, so one failure never blocks the rest.
    """
    now = now or utc_now()
    # Expiry runs first so a case is never sent a reminder and expired in the same run.
    counts = {"expired": expire_due_cases(session, now), "advanced": 0, "promises_checked": 0, "failed": 0}

    last_action = (
        select(RecoveryAction.recovery_case_id, func.max(RecoveryAction.created_at).label("at"))
        .where(RecoveryAction.status == ActionStatus.EXECUTED)
        .group_by(RecoveryAction.recovery_case_id)
        .subquery()
    )
    waiting = session.execute(
        select(RecoveryCase.id, RecoveryCase.contact_attempts, RecoveryCase.created_at, last_action.c.at)
        .outerjoin(last_action, last_action.c.recovery_case_id == RecoveryCase.id)
        .where(RecoveryCase.status == CaseStatus.WAITING_FOR_PAYMENT)
    ).all()
    for case_id, attempts, created_at, last_contact in waiting:
        days = (now - as_utc(last_contact or created_at)).days
        due = (attempts == 1 and days >= FOLLOW_UP_AFTER_DAYS) or (attempts >= 2 and days >= ESCALATE_AFTER_DAYS)
        if due and _run(session, case_id, counts, RecoveryWorkflow(case_id, session).execute_step, days):
            counts["advanced"] += 1

    promise_ids = session.scalars(
        select(PromiseToPay.id).where(PromiseToPay.status == PromiseStatus.PENDING, PromiseToPay.promised_date <= now)
    ).all()
    for promise_id in promise_ids:
        if _run(session, promise_id, counts, PromiseToPayWorkflow(promise_id, session).evaluate_commitment):
            counts["promises_checked"] += 1
    return counts


def _run(session: Session, subject: str, counts: dict[str, int], step: Callable[..., Any], *args: Any) -> bool:  # noqa: ANN401
    try:
        step(*args)
        session.commit()
        return True
    except RecoverXError as exc:
        session.rollback()
        counts["failed"] += 1
        logger.warning("Scheduled workflow step for %s failed: %s", subject, exc)
        return False
