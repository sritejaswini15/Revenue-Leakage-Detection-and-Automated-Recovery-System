"""In-process follow-ups: what the hourly job does when Temporal is not running."""

from datetime import timedelta

from sqlalchemy import select, update

from app.application.workflows.scheduler import run_due_workflows
from app.core.time import utc_now
from app.infrastructure.database.models import RecoveryAction, RecoveryCase
from app.infrastructure.database.session import SessionLocal
from tests.helpers import first_contactable_customer


def _new_waiting_case(client, headers, amount: int) -> str:
    customer = first_contactable_customer(client, headers)
    created = client.post(
        "/simulation/event",
        json={"event_type": "INVOICE_OVERDUE", "customer_id": customer["id"], "amount": amount},
        headers=headers,
    ).json()
    assert created["status"] == "WAITING_FOR_PAYMENT"
    return created["case_id"]


def _age_contacts(case_id: str, days: int) -> None:
    with SessionLocal() as session:
        session.execute(
            update(RecoveryAction)
            .where(RecoveryAction.recovery_case_id == case_id)
            .values(created_at=utc_now() - timedelta(days=days))
        )
        session.commit()


def _case(case_id: str) -> RecoveryCase:
    with SessionLocal() as session:
        return session.get(RecoveryCase, case_id)


def test_reminder_after_two_days_then_escalation_after_three_more(client, admin_headers):
    case_id = _new_waiting_case(client, admin_headers, 4400)

    with SessionLocal() as session:
        run_due_workflows(session)
    assert _case(case_id).contact_attempts == 1  # not due yet

    _age_contacts(case_id, 2)
    with SessionLocal() as session:
        run_due_workflows(session)
    case = _case(case_id)
    assert (case.status, case.contact_attempts) == ("WAITING_FOR_PAYMENT", 2)
    with SessionLocal() as session:
        actions = session.scalars(select(RecoveryAction.action_type).where(RecoveryAction.recovery_case_id == case_id))
        assert sorted(actions) == ["REMINDER", "REMINDER"]

    _age_contacts(case_id, 3)
    with SessionLocal() as session:
        run_due_workflows(session)
        run_due_workflows(session)  # idempotent: nothing further happens
    detail = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert detail["status"] == "ESCALATED"
    assert detail["escalation"]["reason"] == "MAX_ATTEMPTS"
    assert len(detail["communications"]) == 2


def test_missed_promise_is_escalated(client, admin_headers):
    case_id = _new_waiting_case(client, admin_headers, 4500)
    promise = client.post(
        "/simulation/promise-to-pay",
        json={"case_id": case_id, "amount": 4500, "promised_date": (utc_now() - timedelta(days=1)).isoformat()},
        headers=admin_headers,
    ).json()

    with SessionLocal() as session:
        counts = run_due_workflows(session)

    assert counts["promises_checked"] >= 1
    detail = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert detail["status"] == "ESCALATED"
    assert [p["status"] for p in detail["promises_to_pay"] if p["id"] == promise["id"]] == ["BROKEN"]
