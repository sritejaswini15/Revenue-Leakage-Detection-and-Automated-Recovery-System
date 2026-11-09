from uuid import uuid4

from tests.conftest import ACCOUNT_ID
from tests.helpers import first_contactable_customer, payment_failed_event, signed_webhook


def _post(client, payload, secret=None):
    body, headers = signed_webhook(payload) if secret is None else signed_webhook(payload, secret)
    return client.post("/webhooks/razorpay", content=body, headers=headers)


def test_signed_failure_webhook_opens_a_case(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    payload = payment_failed_event(f"evt_{uuid4().hex[:12]}", f"pay_{uuid4().hex[:10]}", customer["id"], 500000)

    response = _post(client, payload)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["accepted"] is True and data["duplicate"] is False and data["normalized"] is True
    case = client.get(f"/recovery-cases/{data['case_id']}", headers=admin_headers).json()
    assert case["amount"] == 5000  # 500000 paise
    assert case["advisor_model"] == "langgraph-v1"


def test_replayed_webhook_is_idempotent(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    payload = payment_failed_event(f"evt_{uuid4().hex[:12]}", f"pay_{uuid4().hex[:10]}", customer["id"], 750000)

    first, second = _post(client, payload), _post(client, payload)

    assert first.status_code == second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["case_id"] == first.json()["case_id"]


def test_unsigned_webhook_is_rejected(client):
    response = client.post(
        "/webhooks/razorpay",
        json={"account_id": ACCOUNT_ID, "event": "payment.failed", "payload": {}},
    )
    assert response.status_code == 401


def test_wrongly_signed_webhook_is_rejected(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    payload = payment_failed_event("evt_bad_sig", "pay_bad_sig", customer["id"], 500000)
    assert _post(client, payload, secret="not-the-secret").status_code == 401


def test_unknown_merchant_account_is_rejected(client):
    payload = {**payment_failed_event("evt_x", "pay_x", "cus_x", 500000), "account_id": "acct_unknown"}
    assert _post(client, payload).status_code == 404


def test_event_identifying_nobody_is_acknowledged_but_ignored(client):
    payload = payment_failed_event(f"evt_{uuid4().hex[:12]}", "pay_nobody", None, 500000)
    response = _post(client, payload)
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["reason"] == "No customer available for event"


def test_payment_link_paid_webhook_resolves_the_case(client, admin_headers):
    """A Razorpay payment_link.paid callback must find the case through the persisted link."""
    customer = first_contactable_customer(client, admin_headers)
    opened = _post(
        client, payment_failed_event(f"evt_{uuid4().hex[:12]}", f"pay_{uuid4().hex[:10]}", customer["id"], 600000)
    )
    case = client.get(f"/recovery-cases/{opened.json()['case_id']}", headers=admin_headers).json()
    assert case["payment_link"], "auto-approved case should have a payment link"

    paid = {
        "id": f"evt_{uuid4().hex[:12]}",
        "account_id": ACCOUNT_ID,
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": case["payment_link"]["provider_link_id"],
                    "amount": 600000,
                    "customer_id": customer["id"],
                }
            }
        },
    }
    response = _post(client, paid)

    assert response.status_code == 200, response.text
    assert response.json()["case_id"] == case["id"]
    assert response.json()["case_status"] == "RECOVERED"
    assert response.json()["recovered_amount"] == 6000
