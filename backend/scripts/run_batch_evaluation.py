"""Replay every case of the primary organization through the advisor and policy engine.

Usage (from backend/): python -m scripts.run_batch_evaluation
Exits non-zero when the evaluation fails (policy violations or invalid AI decisions).
"""

import sys

from sqlalchemy import select

from app.application.evaluation import run_evaluation
from app.infrastructure.database.models import Organization
from app.infrastructure.database.session import SessionLocal

_ROWS = (
    ("Cases processed", "cases_processed", "{:,}"),
    ("Revenue at risk", "revenue_at_risk", "INR {:,}"),
    ("Revenue targeted", "revenue_targeted", "INR {:,}"),
    ("Revenue recovered", "revenue_recovered", "INR {:,}"),
    ("Recovery rate", "recovery_rate", "{:.1%}"),
    ("Average recovery probability", "average_recovery_probability", "{:.1%}"),
    ("Average AI confidence", "average_ai_confidence", "{:.1%}"),
    ("Successful recoveries", "successful_recoveries", "{:,}"),
    ("Escalations", "escalations", "{:,}"),
    ("False escalations", "false_escalations", "{}"),
    ("Policy violations", "policy_violations", "{}"),
    ("Invalid AI decisions", "invalid_ai_decisions", "{}"),
    ("Action failures", "action_failures", "{}"),
    ("Average recovery time (hours)", "average_recovery_time_hours", "{}"),
)


def main() -> int:
    with SessionLocal() as session:
        organization = session.scalar(select(Organization).order_by(Organization.created_at).limit(1))
        if not organization:
            print("No organization found. Start the API or run scripts.seed_demo_data first.")
            return 1
        results = run_evaluation(session, organization.id, actor="cli")

    if "error" in results:
        print(results["error"])
        return 1
    width = max(len(label) for label, _, _ in _ROWS)
    print(f"RecoverX batch evaluation - {organization.name}")
    for label, key, template in _ROWS:
        print(f"  {label:<{width}}  {template.format(results[key])}")
    print(f"  {'Status':<{width}}  {results['evaluation_status']}")
    return 0 if results["evaluation_status"] == "PASSED" else 2


if __name__ == "__main__":
    sys.exit(main())
