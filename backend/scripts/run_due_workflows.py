"""Expire stale cases, then advance waiting recovery cases and due promises.

Usage (from backend/): python -m scripts.run_due_workflows
Scheduled hourly in render.yaml. With Temporal enabled, only case expiry runs here.
"""

from app.application.case_expiry import expire_due_cases
from app.application.workflows.scheduler import run_due_workflows
from app.core.config import settings
from app.core.observability import setup_process
from app.infrastructure.database.session import SessionLocal


def main() -> None:
    setup_process()
    with SessionLocal() as session:
        if settings.TEMPORAL_ENABLED:  # durable workflows schedule follow-ups themselves
            print(f"Expired {expire_due_cases(session)} cases; follow-ups are handled by Temporal.")
            return
        counts = run_due_workflows(session)
    print(
        f"Expired {counts['expired']} cases, advanced {counts['advanced']}, "
        f"checked {counts['promises_checked']} promises, {counts['failed']} failed."
    )


if __name__ == "__main__":
    main()
