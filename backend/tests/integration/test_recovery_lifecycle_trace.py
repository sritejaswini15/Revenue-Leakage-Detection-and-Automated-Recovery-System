"""One failed payment, traced through every stage of recovery to measured, learned revenue."""

from uuid import uuid4

from app.core.security import hmac_sha256_hex
from tests.helpers import first_contactable_customer
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment


def test_a_failed_payment_becomes_measured_recovered_revenue(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    profile_before = client.get(f"/customers/{customer['id']}", headers=admin_headers).json()
    overview_before = client.get("/analytics/recovery", headers=admin_headers).json()
    body = failed_payment(customer, 4200)
    event_id = f"evt_trace_{uuid4().hex[:10]}"

    # 1-2. The event arrives and must be authentic: a bad signature changes nothing.
    import json

    raw = json.dumps(body).encode()
    forged = client.post(
        "/webhooks/razorpay",
        content=raw,
        headers={"x-razorpay-signature": hmac_sha256_hex("wrong-secret", raw), "x-razorpay-event-id": event_id},
    )
    assert forged.status_code == 401

    # 3-9. Processed once: merchant and customer identified, scored, advised, policy-checked, case opened, acted on.
    first = deliver(client, body, event_id=event_id)
    replay = deliver(client, body, event_id=event_id)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["duplicate"] is True and replay.json()["case_id"] == first.json()["case_id"]
    case_id = first.json()["case_id"]
    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert case["customer_id"] == customer["id"] and case["amount"] == 4200
    assert 0 < case["risk_score"] <= 100 and 0 < case["recovery_probability"] <= 1
    assert case["expected_recovery_value"] == round(4200 * case["recovery_probability"])
    assert case["root_cause"] and case["advisor_model"] == "langgraph-v1"
    assert case["policy_decision"] == "AUTO_APPROVE" and case["policy_evaluation"]["decision"] == "AUTO_APPROVE"

    # 10-11. A payment link went out on the customer's channel, and only once.
    assert case["status"] == "WAITING_FOR_PAYMENT"
    assert len(case["actions"]) == 1 and case["actions"][0]["status"] == "EXECUTED"
    assert (
        len(case["communications"]) == 1 and case["payment_link"]["short_url"] in case["communications"][0]["content"]
    )

    # 12. The customer pays on the payment page (simulated Razorpay emits a signed payment_link.paid).
    token = case["payment_link"]["payment_url"].removeprefix("/pay/")
    page = client.get(f"/pay/{token}")
    assert page.status_code == 200 and page.json()["amount"] == 4200
    paid = client.post(f"/pay/{token}", json={"action": "pay"})
    assert paid.status_code == 200, paid.text

    # 13-14. The case is recovered with the amount actually paid, and can't be rewritten or paid twice.
    after = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert after["status"] == "RECOVERED" and after["recovered_amount"] == 4200
    assert client.post(f"/pay/{token}", json={"action": "pay"}).status_code in (404, 409)
    assert client.post(f"/recovery-cases/{case_id}/stop", headers=admin_headers).status_code == 409

    # 15. The customer's recovery history learned from it.
    profile_after = client.get(f"/customers/{customer['id']}", headers=admin_headers).json()
    assert profile_after["previous_recovery_attempts"] == profile_before["previous_recovery_attempts"] + 1
    assert profile_after["historical_recovery_rate"] >= profile_before["historical_recovery_rate"]

    # 16. Analytics measure it, exactly once.
    overview_after = client.get("/analytics/recovery", headers=admin_headers).json()
    assert overview_after["revenue_at_risk"] - overview_before["revenue_at_risk"] == 4200
    assert overview_after["revenue_recovered"] - overview_before["revenue_recovered"] == 4200
    events = client.get("/audit-events", params={"case_id": case_id, "limit": 200}, headers=admin_headers).json()
    names = {event["event_name"] for event in events["items"]}
    assert {
        "revenue_event.received",
        "risk.assessed",
        "agent.decision.created",
        "policy.evaluated",
        "payment_link.created",
        "message.sent",
        "action.executed",
        "payment.recovered",
        "workflow.closed",
        "customer.profile_learned",
    } <= names
