"""Shared test helpers and in-memory domain object factories."""

import json
from typing import Any

from fastapi.testclient import TestClient

from app.core.security import hmac_sha256_hex
from app.infrastructure.database.models import Customer, Policy, RecoveryCase
from tests.conftest import ACCOUNT_ID, WEBHOOK_SECRET

DEMO_PASSWORD = "recoverx-demo"


def login(client: TestClient, email: str = "admin@recoverx.local", password: str = DEMO_PASSWORD) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    client.cookies.clear()  # tests authenticate explicitly with the bearer token
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def signed_webhook(payload: dict[str, Any], secret: str = WEBHOOK_SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    return body, {"x-razorpay-signature": hmac_sha256_hex(secret, body), "content-type": "application/json"}


def payment_failed_event(event_id: str, payment_id: str, customer_id: str | None, amount_paise: int) -> dict[str, Any]:
    entity = {"id": payment_id, "customer_id": customer_id, "amount": amount_paise}
    return {
        "id": event_id,
        "account_id": ACCOUNT_ID,
        "entity": "event",
        "event": "payment.failed",
        "payload": {"payment": {"entity": {k: v for k, v in entity.items() if v is not None}}},
    }


def first_contactable_customer(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    customers = client.get("/customers?limit=50", headers=headers).json()["items"]
    return next(item for item in customers if not item["opted_out"])


def make_customer(**overrides: Any) -> Customer:
    values = {
        "payment_reliability_pct": 91.0,
        "historical_recovery_rate": 0.88,
        "successful_payments": 18,
        "failed_payments": 1,
        "opted_out": False,
    }
    return Customer(**{**values, **overrides})


def make_policy(**overrides: Any) -> Policy:
    values = {
        "organization_id": "org-1",
        "min_ai_confidence": 0.70,
        "max_auto_action_amount": 25000,
        "human_approval_above_amount": 25000,
        "max_contact_attempts": 3,
        "escalate_after_days": 7,
    }
    return Policy(**{**values, **overrides})


def make_case(**overrides: Any) -> RecoveryCase:
    values = {
        "organization_id": "org-1",
        "amount": 18000,
        "status": "ACTIONABLE",
        "contact_attempts": 0,
        "ai_confidence": 0.91,
    }
    return RecoveryCase(**{**values, **overrides})


def find_escalation(client: TestClient, headers: dict[str, str], case_id: str, queue: str) -> dict[str, Any]:
    """Walk the review queue a page at a time until the case turns up."""
    offset = 0
    while True:
        page = client.get(
            "/escalations", params={"queue": queue, "limit": 200, "offset": offset}, headers=headers
        ).json()
        match = next((item for item in page["items"] if item["id"] == case_id), None)
        if match or offset + 200 >= page["total"]:
            assert match, f"{case_id} not in the {queue} queue"
            return match
        offset += 200
