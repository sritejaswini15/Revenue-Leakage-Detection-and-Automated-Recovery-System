"""Case expiry: open cases with no payment close after the merchant's expiry window."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.application.case_expiry import expire_due_cases
from app.application.workflows.scheduler import run_due_workflows
from app.core.time import utc_now
from app.infrastructure.database.models import PaymentLink, RecoveryAction, RecoveryCase
from app.infrastructure.database.session import SessionLocal
from tests.helpers import first_contactable_customer
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment, link_paid, razorpay_event


def _open_case(client, headers, rupees: int = 2100) -> tuple[dict, dict]:
    """A payment.failed webhook that opens an auto-approved case with a payment link."""
    body = failed_payment(first_contactable_customer(client, headers), rupees)
    case_id = deliver(client, body).json()["case_id"]
    case = client.get(f"/recovery-cases/{case_id}", headers=headers).json()
    assert case["status"] == "WAITING_FOR_PAYMENT" and case["payment_link"]
    return case, body


def _set_opened_at(case_id: str, opened_at) -> None:
    with SessionLocal() as session:
        session.execute(update(RecoveryCase).where(RecoveryCase.id == case_id).values(created_at=opened_at))
        session.commit()


def _age(case_id: str, days: float) -> None:
    _set_opened_at(case_id, utc_now() - timedelta(days=days))


def _expire(now=None) -> int:
    with SessionLocal() as session:
        return expire_due_cases(session, now)


def _detail(client, headers, case_id: str) -> dict:
    return client.get(f"/recovery-cases/{case_id}", headers=headers).json()


def _expiry_audits(client, headers, case_id: str) -> list[dict]:
    events = client.get(f"/audit-events?case_id={case_id}&limit=200", headers=headers).json()["items"]
    return [event for event in events if event["event_name"] == "case.expired"]


def test_case_expires_exactly_at_the_end_of_the_window(client, admin_headers):
    case, _ = _open_case(client, admin_headers)
    reference = utc_now()
    _set_opened_at(case["id"], reference - timedelta(days=7))

    _expire(now=reference - timedelta(seconds=1))
    assert _detail(client, admin_headers, case["id"])["status"] == "WAITING_FOR_PAYMENT"

    _expire(now=reference)
    detail = _detail(client, admin_headers, case["id"])
    assert detail["status"] == "EXPIRED"
    assert detail["payment_link"]["status"] == "CANCELLED"
    (audit,) = _expiry_audits(client, admin_headers, case["id"])
    assert audit["payload"]["previous_status"] == "WAITING_FOR_PAYMENT"
    assert audit["payload"]["expiry_days"] == 7
    assert audit["payload"]["payment_link_cancelled"] == case["payment_link"]["provider_link_id"]


def test_repeated_runs_expire_a_case_once(client, admin_headers):
    case, _ = _open_case(client, admin_headers)
    _age(case["id"], 8)

    _expire()
    _expire()
    _expire()

    assert _detail(client, admin_headers, case["id"])["status"] == "EXPIRED"
    assert len(_expiry_audits(client, admin_headers, case["id"])) == 1


def test_a_young_case_is_left_open(client, admin_headers):
    case, _ = _open_case(client, admin_headers)
    _age(case["id"], 6.9)
    _expire()
    detail = _detail(client, admin_headers, case["id"])
    assert detail["status"] == "WAITING_FOR_PAYMENT"
    assert detail["payment_link"]["status"] == "OPEN"


def test_the_merchant_policy_sets_the_window(client, admin_headers):
    original = client.get("/policies", headers=admin_headers).json()
    assert client.put("/policies", json={**original, "case_expiry_days": 30}, headers=admin_headers).status_code == 200
    try:
        case, _ = _open_case(client, admin_headers)
        _age(case["id"], 10)
        _expire()
        assert _detail(client, admin_headers, case["id"])["status"] == "WAITING_FOR_PAYMENT"
        _age(case["id"], 31)
        _expire()
        assert _detail(client, admin_headers, case["id"])["status"] == "EXPIRED"
    finally:
        client.put("/policies", json=original, headers=admin_headers)


def test_expiry_window_must_be_at_least_a_day(client, admin_headers):
    original = client.get("/policies", headers=admin_headers).json()
    response = client.put("/policies", json={**original, "case_expiry_days": 0}, headers=admin_headers)
    assert response.status_code == 422


def test_a_promise_due_in_the_future_defers_expiry(client, admin_headers):
    case, _ = _open_case(client, admin_headers)
    promised = client.post(
        "/simulation/promise-to-pay",
        json={
            "case_id": case["id"],
            "amount": case["amount"],
            "promised_date": (utc_now() + timedelta(days=2)).isoformat(),
        },
        headers=admin_headers,
    )
    assert promised.status_code == 200, promised.text
    _age(case["id"], 20)

    _expire()

    assert _detail(client, admin_headers, case["id"])["status"] == "PROMISE_TO_PAY"


def test_an_authorized_payment_defers_expiry(client, admin_headers):
    """Razorpay can authorize a payment after reporting it failed; that money is on its way."""
    case, body = _open_case(client, admin_headers)
    payment = body["payload"]["payment"]["entity"]
    authorized = razorpay_event("payment.authorized", "payment", {**payment, "status": "authorized"})
    assert deliver(client, authorized).status_code == 200
    _age(case["id"], 20)

    _expire()

    assert _detail(client, admin_headers, case["id"])["status"] == "WAITING_FOR_PAYMENT"


def test_a_paid_link_defers_expiry(client, admin_headers):
    case, _ = _open_case(client, admin_headers)
    with SessionLocal() as session:
        session.execute(update(PaymentLink).where(PaymentLink.case_id == case["id"]).values(status="PAID"))
        session.commit()
    _age(case["id"], 20)

    _expire()

    assert _detail(client, admin_headers, case["id"])["status"] == "WAITING_FOR_PAYMENT"


def test_escalated_and_recovered_cases_are_never_expired(client, admin_headers):
    body = failed_payment(first_contactable_customer(client, admin_headers), 120000)
    escalated = deliver(client, body).json()["case_id"]
    assert _detail(client, admin_headers, escalated)["status"] == "ESCALATED"

    recovered, _ = _open_case(client, admin_headers)
    assert deliver(client, link_paid(recovered, recovered["amount"] * 100)).status_code == 200
    assert _detail(client, admin_headers, recovered["id"])["status"] == "RECOVERED"

    for case_id in (escalated, recovered["id"]):
        _age(case_id, 60)
    _expire()

    assert _detail(client, admin_headers, escalated)["status"] == "ESCALATED"
    assert _detail(client, admin_headers, recovered["id"])["status"] == "RECOVERED"


@pytest.mark.parametrize("route", ["link", "source-payment"])
def test_a_payment_after_expiry_is_still_recorded_as_recovered(client, admin_headers, route):
    case, body = _open_case(client, admin_headers, rupees=2300)
    _age(case["id"], 8)
    _expire()
    assert _detail(client, admin_headers, case["id"])["status"] == "EXPIRED"

    if route == "link":
        late = link_paid(case, case["amount"] * 100)
    else:
        payment = body["payload"]["payment"]["entity"]
        late = razorpay_event("payment.captured", "payment", {**payment, "status": "captured"})
    assert deliver(client, late, event_id=f"evt_late_{uuid4().hex[:10]}").status_code == 200

    detail = _detail(client, admin_headers, case["id"])
    assert detail["status"] == "RECOVERED"
    assert detail["recovered_amount"] == 2300


def test_hourly_job_expires_before_sending_follow_ups(client, admin_headers):
    """A case that is both past its window and due a reminder is expired, not messaged."""
    case, _ = _open_case(client, admin_headers)
    _age(case["id"], 8)
    with SessionLocal() as session:
        session.execute(
            update(RecoveryAction)
            .where(RecoveryAction.recovery_case_id == case["id"])
            .values(created_at=utc_now() - timedelta(days=3))
        )
        session.commit()

    with SessionLocal() as session:
        counts = run_due_workflows(session)

    assert counts["expired"] >= 1
    detail = _detail(client, admin_headers, case["id"])
    assert detail["status"] == "EXPIRED"
    with SessionLocal() as session:
        actions = session.scalars(select(RecoveryAction).where(RecoveryAction.recovery_case_id == case["id"])).all()
    assert len(actions) == 1  # the original contact only; no reminder after expiry
