"""Merchant policy settings change what RecoverX actually does, immediately."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.infrastructure.database.models import Communication, Customer
from app.infrastructure.database.session import SessionLocal
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment, razorpay_event


@pytest.fixture
def policy(client, admin_headers):
    """The current policy; restored after the test so other tests see the defaults."""
    original = client.get("/policies", headers=admin_headers).json()
    yield original
    assert client.put("/policies", json=original, headers=admin_headers).status_code == 200


def _put(client, headers, policy, **changes):
    response = client.put("/policies", json={**policy, **changes}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _customer_preferring(client, headers, channel: str, language: str = "Hinglish") -> dict:
    customers = client.get("/customers?limit=200", headers=headers).json()["items"]
    customer = next(c for c in customers if not c["opted_out"] and c["preferred_channel"] == channel)
    with SessionLocal() as session:  # pin the language so the test does not depend on the seed
        session.get(Customer, customer["id"]).preferred_language = language
        session.commit()
    return customer


def _latest_message(case_id: str) -> Communication:
    with SessionLocal() as session:
        messages = session.scalars(select(Communication).where(Communication.case_id == case_id)).all()
        session.expunge_all()
        return messages[-1]


def test_switching_whatsapp_off_routes_messages_to_email(client, admin_headers, policy):
    _put(client, admin_headers, policy, preferred_channels="email")
    customer = _customer_preferring(client, admin_headers, "whatsapp")

    case_id = deliver(client, failed_payment(customer, 1900)).json()["case_id"]

    assert _latest_message(case_id).channel.lower() == "email"
    audit = client.get("/audit-events", params={"case_id": case_id, "limit": 50}, headers=admin_headers).json()
    sent = next(item for item in audit["items"] if item["event_name"] == "message.sent")
    assert sent["payload"]["channel"] == "email" and sent["payload"]["recipient"] == customer["email"]


def test_unsupported_languages_fall_back_to_the_merchants_language(client, admin_headers, policy):
    _put(client, admin_headers, policy, supported_languages="English")
    customer = _customer_preferring(client, admin_headers, "whatsapp", language="Hinglish")

    case_id = deliver(client, failed_payment(customer, 1950)).json()["case_id"]

    assert "Your payment of ₹1,950 could not be completed" in _latest_message(case_id).content


def test_a_lower_limit_shows_up_in_the_case_evaluation_at_once(client, admin_headers, policy):
    customer = _customer_preferring(client, admin_headers, "whatsapp")
    case_id = deliver(client, failed_payment(customer, 2000)).json()["case_id"]
    before = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()["policy_evaluation"]
    assert before["decision"] == "AUTO_APPROVE"

    _put(client, admin_headers, policy, max_auto_action_amount=1000, human_approval_above_amount=1000)

    after = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert after["policy_evaluation"]["decision"] == "HUMAN_REVIEW"
    assert "AMOUNT_EXCEEDS_AUTO_LIMIT" in after["policy_evaluation"]["violations"]
    assert after["risk_reasons"], "the case should explain why it is prioritised"
    refused = client.post(f"/recovery-cases/{case_id}/execute", headers=admin_headers)
    assert refused.status_code == 409


def test_allowed_actions_are_editable_and_enforced(client, admin_headers, policy):
    saved = _put(client, admin_headers, policy, allowed_actions="payment_link, REMINDER, PAYMENT_LINK")
    assert saved["allowed_actions"] == "PAYMENT_LINK,REMINDER"
    customer = _customer_preferring(client, admin_headers, "whatsapp")
    case_id = deliver(client, failed_payment(customer, 2050)).json()["case_id"]

    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert "PAYMENT_RETRY" not in case["executable_actions"]
    retry = client.post(
        f"/recovery-cases/{case_id}/execute", json={"action_type": "PAYMENT_RETRY"}, headers=admin_headers
    )
    assert retry.status_code == 409


@pytest.mark.parametrize(
    "changes",
    [
        {"preferred_channels": "sms"},
        {"preferred_channels": ""},
        {"supported_languages": "Klingon"},
        {"allowed_actions": "ESCALATE_TO_HUMAN"},
    ],
)
def test_unusable_settings_are_refused(client, admin_headers, policy, changes):
    response = client.put("/policies", json={**policy, **changes}, headers=admin_headers)
    assert response.status_code == 422


def test_a_recommended_action_the_merchant_switched_off_goes_to_a_person(client, admin_headers, policy):
    """Subscription failures are recommended a retry; with retries off the case must still open."""
    _put(client, admin_headers, policy, allowed_actions="PAYMENT_LINK,REMINDER")
    customer = _customer_preferring(client, admin_headers, "whatsapp")
    subscription = {
        "id": f"sub_{uuid4().hex[:14]}",
        "amount": 99900,
        "customer_id": customer["razorpay_customer_id"],
        "status": "halted",
    }
    body = razorpay_event("subscription.halted", "subscription", subscription)

    response = deliver(client, body)

    assert response.status_code == 200, response.text
    assert response.json().get("case_id"), response.text
    case = client.get(f"/recovery-cases/{response.json()['case_id']}", headers=admin_headers).json()
    assert case["recommended_action"] == "PAYMENT_RETRY", case
    assert case["status"] == "ESCALATED" and case["escalation"]["reason"] == "ACTION_NOT_ALLOWED"
