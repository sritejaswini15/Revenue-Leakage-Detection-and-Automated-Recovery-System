"""Regressions found in the deployment-readiness audit: attribution, partial payments, log hygiene,
concurrent duplicates and evaluation accuracy."""

import logging
from uuid import uuid4

from sqlalchemy.orm import Session

from app.application import payment_portal
from app.domain.exceptions import ProviderError
from app.infrastructure.database.models import RecoveryAction
from app.infrastructure.database.session import SessionLocal
from tests.helpers import first_contactable_customer
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment, razorpay_event


def _case(client, headers, case_id: str) -> dict:
    return client.get(f"/recovery-cases/{case_id}", headers=headers).json()


def test_money_for_a_stopped_case_is_still_recovered_revenue(client, admin_headers):
    """The original payment can still settle after an operator stopped chasing it."""
    body = failed_payment(first_contactable_customer(client, admin_headers), 2650)
    payment = body["payload"]["payment"]["entity"]
    case_id = deliver(client, body).json()["case_id"]
    assert client.post(f"/recovery-cases/{case_id}/stop", headers=admin_headers).status_code == 200

    captured = razorpay_event("payment.captured", "payment", {**payment, "status": "captured"})
    assert deliver(client, captured).status_code == 200

    after = _case(client, admin_headers, case_id)
    assert after["status"] == "RECOVERED" and after["recovered_amount"] == 2650


def test_a_link_paid_in_parts_records_everything_that_was_paid(client, admin_headers):
    """Merchant-created links can accept partial payments; Razorpay reports the running total."""
    customer = first_contactable_customer(client, admin_headers)
    link = {
        "id": f"plink_{uuid4().hex[:12]}",
        "amount": 500000,
        "amount_paid": 0,
        "status": "expired",
        "customer": {"email": customer["email"]},
        "notes": {},
    }
    case_id = deliver(
        client,
        razorpay_event(
            "payment_link.expired", "payment_link", {**link, "customer_id": customer["razorpay_customer_id"]}
        ),
    ).json()["case_id"]
    assert case_id, "a merchant's expired link opens a case"

    partly = razorpay_event(
        "payment_link.partially_paid", "payment_link", {**link, "status": "partially_paid", "amount_paid": 200000}
    )
    fully = razorpay_event("payment_link.paid", "payment_link", {**link, "status": "paid", "amount_paid": 500000})
    assert deliver(client, partly).status_code == 200
    assert _case(client, admin_headers, case_id)["recovered_amount"] == 2000
    assert deliver(client, fully).status_code == 200

    after = _case(client, admin_headers, case_id)
    assert after["status"] == "RECOVERED" and after["recovered_amount"] == 5000


def test_new_customers_are_not_logged_by_email_or_phone(client, caplog):
    email = f"private-{uuid4().hex[:8]}@example.in"
    body = razorpay_event(
        "payment.failed",
        "payment",
        {"id": f"pay_{uuid4().hex[:14]}", "amount": 150000, "email": email, "contact": "+919812345678"},
    )
    with caplog.at_level(logging.INFO):
        assert deliver(client, body).status_code == 200
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Created customer" in logged
    assert email not in logged and "9812345678" not in logged


def test_error_logs_never_contain_payment_tokens(client, caplog, monkeypatch):
    def provider_down(*_args, **_kwargs):
        raise ProviderError("provider unavailable")

    monkeypatch.setattr(payment_portal, "pay", provider_down)
    with caplog.at_level(logging.WARNING):
        response = client.post("/pay/very-secret-token-abc", json={"action": "pay"})
    assert response.status_code == 502
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "failed upstream" in logged and "very-secret-token-abc" not in logged


def test_a_concurrent_duplicate_action_is_a_conflict_not_a_server_error(client, admin_headers, monkeypatch):
    """Two clicks race past the idempotency lookup; the unique key must stop the second one cleanly."""
    case_id = deliver(client, failed_payment(first_contactable_customer(client, admin_headers), 2750)).json()["case_id"]
    key = f"action:{case_id}:REMINDER:{_case(client, admin_headers, case_id)['contact_attempts']}"
    with SessionLocal() as session:  # the other request won the race and committed first
        session.add(
            RecoveryAction(
                recovery_case_id=case_id,
                action_type="REMINDER",
                status="EXECUTED",
                idempotency_key=key,
                trace_id="race",
            )
        )
        session.commit()
    real_scalar = Session.scalar

    def looked_before_the_commit(self, statement, *args, **kwargs):
        if "recovery_actions.idempotency_key" in str(statement):
            return None
        return real_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", looked_before_the_commit)
    second = client.post(f"/recovery-cases/{case_id}/execute", json={"action_type": "REMINDER"}, headers=admin_headers)

    assert second.status_code == 409, second.text
    monkeypatch.undo()
    assert len([a for a in _case(client, admin_headers, case_id)["actions"] if a["action_type"] == "REMINDER"]) == 1


def test_a_reviewers_approval_is_not_counted_as_a_policy_violation(client, admin_headers):
    """Above the auto-action limit policy wants a person; a person approving is policy working."""
    body = failed_payment(first_contactable_customer(client, admin_headers), 60000)
    case_id = deliver(client, body).json()["case_id"]
    assert _case(client, admin_headers, case_id)["status"] == "ESCALATED"
    approved = client.post(f"/escalations/{case_id}/resolve", json={"resolution": "APPROVE"}, headers=admin_headers)
    assert approved.status_code == 200 and approved.json()["status"] == "WAITING_FOR_PAYMENT", approved.text

    evaluation = client.post("/demo/run-batch", headers=admin_headers).json()
    assert evaluation["policy_violations"] == 0, evaluation


def test_an_older_auto_approved_case_is_not_a_policy_violation(client, admin_headers):
    """Policy decides on the days overdue at intake; a case simply getting older changes nothing."""
    from datetime import timedelta

    from sqlalchemy import update

    from app.core.time import utc_now
    from app.infrastructure.database.models import RecoveryCase, RevenueEvent

    body = failed_payment(first_contactable_customer(client, admin_headers), 2850)
    case_id = deliver(client, body).json()["case_id"]
    assert _case(client, admin_headers, case_id)["status"] == "WAITING_FOR_PAYMENT"
    with SessionLocal() as session:
        event_id = session.get(RecoveryCase, case_id).revenue_event_id
        session.execute(
            update(RevenueEvent).where(RevenueEvent.id == event_id).values(occurred_at=utc_now() - timedelta(days=12))
        )
        session.commit()

    evaluation = client.post("/demo/run-batch", headers=admin_headers).json()
    assert evaluation["policy_violations"] == 0, evaluation


def test_a_revised_promise_replaces_the_earlier_one(client, admin_headers):
    """The customer moved their date: only the new promise is checked, so the case isn't escalated early."""
    from datetime import timedelta

    from sqlalchemy import update

    from app.core.time import utc_now
    from app.infrastructure.database.models import PromiseToPay

    case_id = deliver(client, failed_payment(first_contactable_customer(client, admin_headers), 2950)).json()["case_id"]
    url = f"/recovery-cases/{case_id}/promise-to-pay"
    first = client.post(
        url, json={"amount": 2950, "promised_date": "2099-01-10T00:00:00Z"}, headers=admin_headers
    ).json()
    second = client.post(
        url, json={"amount": 2950, "promised_date": "2099-01-15T00:00:00Z"}, headers=admin_headers
    ).json()
    with SessionLocal() as session:  # the first date has now passed without payment
        session.execute(
            update(PromiseToPay)
            .where(PromiseToPay.id == first["id"])
            .values(promised_date=utc_now() - timedelta(days=1))
        )
        session.commit()

    checked = client.post(f"/simulation/promise-to-pay/{first['id']}/fulfill", headers=admin_headers).json()

    assert checked["status"] == "SUPERSEDED"
    case = _case(client, admin_headers, case_id)
    assert case["status"] == "PROMISE_TO_PAY", "the revised promise is still good"
    statuses = {promise["id"]: promise["status"] for promise in case["promises_to_pay"]}
    assert statuses == {first["id"]: "SUPERSEDED", second["id"]: "PENDING"}
