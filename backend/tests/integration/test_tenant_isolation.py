"""A second merchant must never see or change the first merchant's data.

Runs after test_system.py (alphabetical order) because it leaves a second organization behind,
which correctly blocks the single-merchant demo reset.
"""

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.infrastructure.database.models import Customer, Organization, Payment, User
from app.infrastructure.database.session import SessionLocal
from tests.helpers import login, signed_webhook

OTHER_ACCOUNT, OTHER_SECRET = "acct_other_0002", "other-merchant-secret"


@pytest.fixture(scope="module")
def other_merchant(client):
    with SessionLocal() as session:
        organization = Organization(
            name="Other Merchant", razorpay_account_id=OTHER_ACCOUNT, webhook_secret=OTHER_SECRET
        )
        session.add(organization)
        session.flush()
        session.add_all(
            [
                User(
                    organization_id=organization.id,
                    email="owner@other.test",
                    name="Other Owner",
                    role="ADMIN",
                    password_hash=hash_password("other-password-123"),
                ),
                Customer(
                    organization_id=organization.id,
                    razorpay_customer_id="cus_other_1",
                    name="Other Customer",
                    email="customer@other.test",
                    phone="+910000000001",
                ),
            ]
        )
        session.commit()
    return login(client, "owner@other.test", "other-password-123")


def test_reads_are_scoped_to_the_callers_merchant(client, other_merchant):
    headers = other_merchant
    assert client.get("/recovery-cases?limit=500", headers=headers).json()["total"] == 0
    assert client.get("/analytics/recovery", headers=headers).json()["cases"] == 0
    assert [c["razorpay_customer_id"] for c in client.get("/customers", headers=headers).json()["items"]] == [
        "cus_other_1"
    ]
    assert client.get("/recovery-cases/RC-RAHUL-18K", headers=headers).status_code == 404
    assert client.get("/customers/cus_rahul_golden_demo", headers=headers).status_code == 404
    assert all(event["case_id"] is None for event in client.get("/audit-events", headers=headers).json()["items"])
    mcp = client.post(
        "/mcp", json={"tool": "get_recovery_case", "arguments": {"case_id": "RC-RAHUL-18K"}}, headers=headers
    )
    assert mcp.status_code == 400


def test_writes_cannot_target_another_merchants_cases(client, other_merchant):
    headers = other_merchant
    for path, body in [
        ("/recovery-cases/RC-RAHUL-18K/execute", None),
        ("/recovery-cases/RC-RAHUL-18K/stop", None),
        ("/escalations/RC-RAHUL-18K/resolve", {"resolution": "REJECT"}),
        ("/simulation/payment", {"case_id": "RC-RAHUL-18K"}),
        ("/simulation/event", {"event_type": "PAYMENT_FAILURE", "customer_id": "cus_rahul_golden_demo", "amount": 100}),
        ("/recovery-cases/RC-RAHUL-18K/escalate", None),
        ("/recovery-cases/RC-RAHUL-18K/approve", {"resolution": "APPROVE"}),
        ("/recovery-cases/RC-RAHUL-18K/promise-to-pay", {"amount": 100, "promised_date": "2099-01-01T00:00:00Z"}),
        ("/simulation/low-confidence", {"case_id": "RC-RAHUL-18K"}),
        ("/simulation/invalid-ai", {"case_id": "RC-RAHUL-18K"}),
        ("/simulation/max-attempts", {"case_id": "RC-RAHUL-18K"}),
    ]:
        assert client.post(path, json=body, headers=headers).status_code == 404, path


def test_mcp_protocol_calls_cannot_reach_another_merchants_cases(client, other_merchant):
    for name in ("get_recovery_case", "evaluate_policy", "send_recovery_message", "record_promise_to_pay"):
        arguments = {"case_id": "RC-RAHUL-18K", **({"amount": 100} if name == "record_promise_to_pay" else {})}
        reply = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
            headers=other_merchant,
        ).json()
        assert reply["result"]["isError"] is True and "not found" in reply["result"]["content"][0]["text"].lower(), name


def test_evaluations_are_per_merchant(client, admin_headers, other_merchant):
    assert client.post("/demo/run-batch", headers=admin_headers).status_code == 200
    assert client.get("/demo/evaluation", headers=other_merchant).json() is None


def test_a_merchants_webhook_cannot_change_another_merchants_payment(client, admin_headers, other_merchant):
    captured_for_rahul = {
        "account_id": OTHER_ACCOUNT,
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_rahul_18000", "amount": 1800000, "customer_id": "cus_other_1"}}},
    }
    raw, headers = signed_webhook(captured_for_rahul, secret=OTHER_SECRET)
    headers["x-razorpay-event-id"] = "evt_cross_tenant_1"
    assert client.post("/webhooks/razorpay", content=raw, headers=headers).status_code == 200

    with SessionLocal() as session:
        payment = session.scalar(select(Payment).where(Payment.provider_payment_id == "pay_rahul_18000"))
        assert payment.status == "FAILED"
    assert client.get("/recovery-cases/RC-RAHUL-18K", headers=admin_headers).json()["status"] != "RECOVERED"

    # The other merchant's secret is not valid for this merchant's account.
    forged = {**captured_for_rahul, "account_id": "acct_test_0001"}
    raw, headers = signed_webhook(forged, secret=OTHER_SECRET)
    assert client.post("/webhooks/razorpay", content=raw, headers=headers).status_code == 401


def test_demo_reset_refuses_to_delete_other_merchants_data(client, admin_headers, other_merchant):
    response = client.post("/demo/reset", headers=admin_headers)
    assert response.status_code == 409
    assert "other merchants" in response.json()["detail"]
