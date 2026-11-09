"""Closed cases update the customer's recovery profile, which the next case then uses."""

from sqlalchemy import update

from app.infrastructure.database.models import Customer
from app.infrastructure.database.session import SessionLocal
from tests.integration.test_case_expiry import _age, _expire
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment, link_paid


def _fresh_customer(client, headers) -> dict:
    """A contactable customer with a known starting profile: 1 of 2 past recoveries succeeded."""
    customers = client.get("/customers?limit=200&contact=contactable", headers=headers).json()["items"]
    customer = customers[-1]
    with SessionLocal() as session:
        session.execute(
            update(Customer)
            .where(Customer.id == customer["id"])
            .values(
                historical_recovery_rate=0.5, previous_recovery_attempts=2, best_historical_recovery_action="REMINDER"
            )
        )
        session.commit()
    return customer


def _profile(client, headers, customer_id: str) -> dict:
    return client.get(f"/customers/{customer_id}", headers=headers).json()


def _open(client, headers, customer: dict, rupees: int) -> dict:
    case_id = deliver(client, failed_payment(customer, rupees)).json()["case_id"]
    return client.get(f"/recovery-cases/{case_id}", headers=headers).json()


def test_a_recovery_raises_the_rate_and_records_what_worked(client, admin_headers):
    customer = _fresh_customer(client, admin_headers)
    case = _open(client, admin_headers, customer, 2900)
    assert case["status"] == "WAITING_FOR_PAYMENT"

    assert deliver(client, link_paid(case, 2900 * 100)).status_code == 200

    profile = _profile(client, admin_headers, customer["id"])
    assert profile["previous_recovery_attempts"] == 3
    assert abs(profile["historical_recovery_rate"] - 2 / 3) < 0.001
    assert profile["best_historical_recovery_action"] == case["actions"][0]["action_type"]
    events = client.get("/audit-events", params={"case_id": case["id"], "limit": 100}, headers=admin_headers).json()
    assert "customer.profile_learned" in [event["event_name"] for event in events["items"]]


def test_an_expiry_lowers_the_rate_and_a_late_payment_turns_it_into_a_recovery(client, admin_headers):
    customer = _fresh_customer(client, admin_headers)
    case = _open(client, admin_headers, customer, 3050)
    _age(case["id"], 8)
    _expire()

    expired = _profile(client, admin_headers, customer["id"])
    assert expired["previous_recovery_attempts"] == 3 and abs(expired["historical_recovery_rate"] - 1 / 3) < 0.001

    assert deliver(client, link_paid(case, 3050 * 100)).status_code == 200  # paid after all
    late = _profile(client, admin_headers, customer["id"])
    assert late["previous_recovery_attempts"] == 3, "the same case is not counted twice"
    assert abs(late["historical_recovery_rate"] - 2 / 3) < 0.001
