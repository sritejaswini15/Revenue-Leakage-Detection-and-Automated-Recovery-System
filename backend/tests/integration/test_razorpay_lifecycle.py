"""Payment lifecycle with webhooks shaped like Razorpay's (no body id, event id in a header).

Amounts on the wire are paise; RecoverX stores and returns whole rupees.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import container
from app.core.time import utc_now
from app.domain.exceptions import ProviderError
from app.infrastructure.database.models import PaymentLink, RevenueEvent
from app.infrastructure.database.session import SessionLocal
from tests.conftest import ACCOUNT_ID
from tests.helpers import first_contactable_customer, signed_webhook


def razorpay_event(event: str, entity_name: str, entity: dict, extra_payload: dict | None = None) -> dict:
    payload = {entity_name: {"entity": entity}, **(extra_payload or {})}
    return {
        "entity": "event",
        "account_id": ACCOUNT_ID,
        "event": event,
        "contains": list(payload),
        "payload": payload,
        "created_at": 1767225600,
    }


def deliver(client, body: dict, event_id: str | None = None):
    raw, headers = signed_webhook(body)
    headers["x-razorpay-event-id"] = event_id or f"evt_{uuid4().hex[:14]}"
    return client.post("/webhooks/razorpay", content=raw, headers=headers)


def failed_payment(customer: dict, rupees: int) -> dict:
    return razorpay_event(
        "payment.failed",
        "payment",
        {
            "id": f"pay_{uuid4().hex[:14]}",
            "entity": "payment",
            "amount": rupees * 100,
            "currency": "INR",
            "status": "failed",
            "method": "upi",
            "customer_id": customer["razorpay_customer_id"],
            "notes": {},
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment failed due to insufficient balance",
        },
    )


def link_paid(case: dict, amount_paise: int) -> dict:
    """What Razorpay sends when a RecoverX-created Payment Link is paid: no customer_id anywhere."""
    link = case["payment_link"]
    notes = {"recoverx_case_id": case["id"]}
    return razorpay_event(
        "payment_link.paid",
        "payment_link",
        {
            "id": link["provider_link_id"],
            "entity": "payment_link",
            "amount": amount_paise,
            "amount_paid": amount_paise,
            "currency": "INR",
            "status": "paid",
            "notes": notes,
            "customer": {"name": "Customer", "contact": "+919900000000"},
        },
        {
            "payment": {
                "entity": {
                    "id": f"pay_{uuid4().hex[:14]}",
                    "amount": amount_paise,
                    "status": "captured",
                    "notes": notes,
                    "customer_id": None,
                }
            },
            "order": {"entity": {"id": f"order_{uuid4().hex[:14]}", "amount": amount_paise}},
        },
    )


def overview(client, headers) -> dict:
    return client.get("/analytics/recovery", headers=headers).json()


@pytest.mark.parametrize("rupees", [500, 1200, 120000])
def test_amounts_are_consistent_from_failure_to_recovery(client, admin_headers, rupees):
    customer = first_contactable_customer(client, admin_headers)
    before = overview(client, admin_headers)

    opened = deliver(client, failed_payment(customer, rupees))
    assert opened.status_code == 200, opened.text
    case_id = opened.json()["case_id"]
    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert case["amount"] == rupees
    assert 0 < case["expected_recovery_value"] <= rupees

    history = client.post(
        "/mcp",
        json={"tool": "get_payment_history", "arguments": {"customer_id": customer["id"]}},
        headers=admin_headers,
    ).json()["result"]["payments"]
    assert any(p["amount"] == rupees and p["status"] == "FAILED" for p in history)

    if case["status"] == "ESCALATED":  # amounts above the auto-action limit need a person
        # The reviewer approves the recommendation if it can run; if the advisor itself asked for a
        # person (unreliable payer, large amount), they choose a payment link instead.
        decision = (
            {"resolution": "APPROVE"}
            if case["recommended_action"] in case["executable_actions"]
            else {"resolution": "EDIT", "action_type": "PAYMENT_LINK"}
        )
        approved = client.post(f"/escalations/{case_id}/resolve", json=decision, headers=admin_headers)
        assert approved.status_code == 200, approved.text
        case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert case["payment_link"]["amount"] == rupees

    paid = deliver(client, link_paid(case, rupees * 100))
    assert paid.status_code == 200, paid.text
    assert paid.json()["case_id"] == case_id
    assert paid.json()["case_status"] == "RECOVERED"
    assert paid.json()["recovered_amount"] == rupees

    after = overview(client, admin_headers)
    assert after["revenue_at_risk"] - before["revenue_at_risk"] == rupees
    assert after["revenue_recovered"] - before["revenue_recovered"] == rupees


def test_event_id_header_is_the_idempotency_key(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    body = failed_payment(customer, 900)
    first = deliver(client, body, event_id="evt_header_dedupe_1")
    replay = deliver(client, body, event_id="evt_header_dedupe_1")
    assert first.json()["duplicate"] is False
    assert replay.json()["duplicate"] is True
    assert replay.json()["dedupe_key"] == "razorpay:event:evt_header_dedupe_1"


def test_repeated_leak_events_for_one_subscription_open_one_case(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    subscription = {
        "id": f"sub_{uuid4().hex[:14]}",
        "amount": 249900,
        "customer_id": customer["razorpay_customer_id"],
        "status": "pending",
    }
    pending = deliver(client, razorpay_event("subscription.pending", "subscription", subscription))
    halted = deliver(
        client, razorpay_event("subscription.halted", "subscription", {**subscription, "status": "halted"})
    )
    assert pending.status_code == halted.status_code == 200
    assert pending.json()["case_id"] is not None
    assert halted.json()["case_id"] == pending.json()["case_id"]


def test_expiry_of_a_recoverx_link_does_not_open_a_second_case(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    case_id = deliver(client, failed_payment(customer, 2500)).json()["case_id"]
    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    total_before = client.get("/recovery-cases?limit=1", headers=admin_headers).json()["total"]

    expired = deliver(
        client,
        razorpay_event(
            "payment_link.expired",
            "payment_link",
            {
                "id": case["payment_link"]["provider_link_id"],
                "amount": 250000,
                "status": "expired",
                "notes": {"recoverx_case_id": case_id},
            },
        ),
    )

    assert expired.status_code == 200, expired.text
    assert client.get("/recovery-cases?limit=1", headers=admin_headers).json()["total"] == total_before
    refreshed = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert refreshed["payment_link"]["status"] == "EXPIRED"
    assert len(refreshed["communications"]) == len(case["communications"])  # no extra message


def test_a_new_message_never_carries_an_expired_link(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    case_id = deliver(client, failed_payment(customer, 3100)).json()["case_id"]
    with SessionLocal() as session:
        link = session.scalar(select(PaymentLink).where(PaymentLink.case_id == case_id))
        old_link_id = link.provider_link_id
        link.expires_at = utc_now() - timedelta(hours=1)
        session.commit()

    sent = client.post(
        "/mcp", json={"tool": "send_recovery_message", "arguments": {"case_id": case_id}}, headers=admin_headers
    )
    assert sent.status_code == 200, sent.text
    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert case["payment_link"]["provider_link_id"] != old_link_id
    assert case["payment_link"]["status"] == "OPEN"
    assert case["payment_link"]["short_url"] in case["communications"][-1]["content"]


def test_successful_retry_recovers_the_case_and_cannot_charge_twice(client, admin_headers):
    executed = client.post("/recovery-cases/RC-ACME-12500/execute", headers=admin_headers)
    assert executed.status_code == 200, executed.text
    case = client.get("/recovery-cases/RC-ACME-12500", headers=admin_headers).json()
    assert case["status"] == "RECOVERED"
    assert case["recovered_amount"] == 12500

    again = client.post("/recovery-cases/RC-ACME-12500/execute", headers=admin_headers)
    assert again.status_code == 409
    history = client.post(
        "/mcp",
        json={"tool": "get_payment_history", "arguments": {"customer_id": "cus_acme_pvt_ltd"}},
        headers=admin_headers,
    ).json()["result"]["payments"]
    assert [p["status"] for p in history if p["id"] == "pay_acme_12500"] == ["CAPTURED"]


def test_authorization_alone_does_not_count_as_recovery(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    case_id = deliver(client, failed_payment(customer, 1800)).json()["case_id"]
    authorized = razorpay_event(
        "payment.authorized",
        "payment",
        {
            "id": f"pay_{uuid4().hex[:14]}",
            "amount": 180000,
            "status": "authorized",
            "customer_id": customer["razorpay_customer_id"],
            "notes": {"recoverx_case_id": case_id},
        },
    )
    response = deliver(client, authorized)
    assert response.status_code == 200, response.text
    assert client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()["status"] != "RECOVERED"


def test_valid_events_that_cannot_be_used_are_acknowledged(client):
    """Razorpay retries non-2xx responses and eventually disables the webhook."""
    unsupported = deliver(client, razorpay_event("refund.processed", "refund", {"id": "rfnd_1", "amount": 1000}))
    unknown_customer = deliver(
        client,
        razorpay_event("payment.failed", "payment", {"id": "pay_nobody_1", "amount": 50000}),
    )
    for response in (unsupported, unknown_customer):
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] is False
        assert response.json()["case_id"] is None


def test_provider_failure_rolls_back_and_the_retried_webhook_succeeds(client, admin_headers, monkeypatch):
    customer = first_contactable_customer(client, admin_headers)
    body = failed_payment(customer, 1500)
    gateway_provider = container.payment_provider()

    def fail(*_args, **_kwargs):
        raise ProviderError("Razorpay unavailable")

    monkeypatch.setattr(gateway_provider, "create_payment_link", fail)
    failed = deliver(client, body, event_id="evt_rollback_1")
    assert failed.status_code == 502
    with SessionLocal() as session:
        assert (
            session.scalar(select(RevenueEvent).where(RevenueEvent.dedupe_key == "razorpay:event:evt_rollback_1"))
            is None
        )

    monkeypatch.undo()
    retried = deliver(client, body, event_id="evt_rollback_1")
    assert retried.status_code == 200, retried.text
    assert retried.json()["duplicate"] is False and retried.json()["case_id"]


def test_first_event_for_a_new_customer_creates_their_profile(client, admin_headers):
    """Production starts with no customers; Razorpay's own data must be enough to start recovering."""
    entity = {
        "id": f"pay_{uuid4().hex[:14]}",
        "amount": 75000,
        "email": "new.buyer@example.test",
        "contact": "+919812345678",
        "notes": {},
    }
    first = deliver(client, razorpay_event("payment.failed", "payment", entity))
    assert first.status_code == 200, first.text
    assert first.json()["accepted"] is True and first.json()["case_id"]
    case = client.get(f"/recovery-cases/{first.json()['case_id']}", headers=admin_headers).json()
    assert (case["customer_email"], case["customer_phone"], case["amount"]) == (
        "new.buyer@example.test",
        "+919812345678",
        750,
    )

    again = deliver(client, razorpay_event("payment.failed", "payment", {**entity, "id": f"pay_{uuid4().hex[:14]}"}))
    second = client.get(f"/recovery-cases/{again.json()['case_id']}", headers=admin_headers).json()
    assert second["customer_id"] == case["customer_id"]  # same person, same profile


def test_a_razorpay_customer_id_is_reused_not_duplicated(client, admin_headers):
    body = razorpay_event(
        "payment.failed", "payment", {"id": f"pay_{uuid4().hex[:14]}", "amount": 99900, "customer_id": "cus_brandnew01"}
    )
    first = deliver(client, body).json()
    body["payload"]["payment"]["entity"]["id"] = f"pay_{uuid4().hex[:14]}"
    second = deliver(client, body).json()
    first_case = client.get(f"/recovery-cases/{first['case_id']}", headers=admin_headers).json()
    second_case = client.get(f"/recovery-cases/{second['case_id']}", headers=admin_headers).json()
    assert first_case["customer_id"] == second_case["customer_id"]
