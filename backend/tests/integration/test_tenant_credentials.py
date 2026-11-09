"""Each merchant's own Razorpay credentials: encrypted, never returned, and used only for that merchant.

Razorpay's HTTP API is replaced by a fake that records which keys each request was signed with;
nothing here reaches the network. Runs after test_system.py because it adds organizations.
"""

import base64
import io
import json
from urllib.error import HTTPError
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import container
from app.application import payment_integrations
from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.security import hash_password
from app.infrastructure.database.models import AuditEvent, Organization, PaymentIntegration, User
from app.infrastructure.database.session import SessionLocal
from app.infrastructure.payments import razorpay as razorpay_module
from app.infrastructure.payments.razorpay import RazorpayProvider
from app.infrastructure.payments.simulated_razorpay import SimulatedRazorpayProvider
from tests.conftest import ACCOUNT_ID
from tests.helpers import login, signed_webhook

PASSWORD = "merchant-password-123"


class FakeRazorpayApi:
    """Answers the Razorpay endpoints RecoverX uses and records the key id of every call."""

    def __init__(self, customers: list[dict] | None = None) -> None:
        self.key_ids: list[str] = []
        self.customers = customers or []

    def __call__(self, request, timeout):  # noqa: ARG002 - mirrors urllib.request.urlopen
        credentials = base64.b64decode(request.get_header("Authorization").removeprefix("Basic ")).decode()
        self.key_ids.append(credentials.split(":", 1)[0])
        path = request.full_url.removeprefix("https://api.razorpay.com/v1")
        if request.get_method() == "POST" and path == "/payment_links":
            link_id = f"plink_{uuid4().hex[:10]}"
            body = {"id": link_id, "status": "created", "short_url": f"https://rzp.io/i/{link_id}"}
        elif path.startswith("/customers"):
            body = {"entity": "collection", "count": len(self.customers), "items": self.customers}
        elif path.startswith("/payments/"):
            body = {"id": path.rsplit("/", 1)[-1], "status": "failed", "amount": 90_000, "currency": "INR"}
        else:
            body = {"items": [], "count": 0}
        return io.BytesIO(json.dumps(body).encode())


def _merchant(client, account_id: str, role: str = "ADMIN") -> dict[str, str]:
    email = f"{role.lower()}@{account_id}.test"
    with SessionLocal() as session:
        organization = session.scalar(select(Organization).where(Organization.razorpay_account_id == account_id))
        if organization is None:
            organization = Organization(name=f"Merchant {account_id}", razorpay_account_id=account_id)
            session.add(organization)
            session.flush()
        session.add(
            User(
                organization_id=organization.id,
                email=email,
                name=f"{role.title()} {account_id}",
                role=role,
                password_hash=hash_password(PASSWORD),
            )
        )
        session.commit()
    return login(client, email, PASSWORD)


def _credentials(account_id: str, suffix: str, **overrides) -> dict:
    return {
        "key_id": f"rzp_test_{suffix}Key01",
        "key_secret": f"secret-{suffix}-value-0001",
        "account_id": account_id,
        "webhook_secret": f"whsec-{suffix}-value-0001",
        "verify": False,
        **overrides,
    }


def _organization_id(account_id: str) -> str:
    with SessionLocal() as session:
        return session.scalar(select(Organization.id).where(Organization.razorpay_account_id == account_id))


def _failed_payment(account_id: str, rupees: int = 900) -> dict:
    entity = {
        "id": f"pay_{uuid4().hex[:14]}",
        "amount": rupees * 100,
        "currency": "INR",
        "status": "failed",
        "email": f"buyer.{uuid4().hex[:6]}@example.test",
        "contact": "+919800000000",
        "notes": {},
    }
    return {
        "entity": "event",
        "account_id": account_id,
        "event": "payment.failed",
        "payload": {"payment": {"entity": entity}},
    }


def _deliver(client, body: dict, secret: str):
    raw, headers = signed_webhook(body, secret)
    headers["x-razorpay-event-id"] = f"evt_{uuid4().hex[:14]}"
    return client.post("/webhooks/razorpay", content=raw, headers=headers)


@pytest.fixture(scope="module")
def merchants(client):
    a, b = f"acc_cred_a{uuid4().hex[:6]}", f"acc_cred_b{uuid4().hex[:6]}"
    headers = {"a": _merchant(client, a), "b": _merchant(client, b)}
    for name, account in (("a", a), ("b", b)):
        response = client.put("/integrations/razorpay", json=_credentials(account, name.upper()), headers=headers[name])
        assert response.status_code == 200, response.text
    return {"a": (a, headers["a"]), "b": (b, headers["b"])}


def test_secrets_are_encrypted_at_rest_and_never_returned(client, merchants):
    account, headers = merchants["a"]
    credentials = _credentials(account, "A")

    for response in (
        client.get("/integrations/razorpay", headers=headers),
        client.get("/integrations/status", headers=headers),
    ):
        assert response.status_code == 200
        assert credentials["key_secret"] not in response.text
        assert credentials["webhook_secret"] not in response.text
        assert credentials["key_id"] not in response.text  # only a masked key id is shown
    view = client.get("/integrations/razorpay", headers=headers).json()
    assert view["source"] == "merchant" and view["mode"] == "test" and view["enabled"] is True
    assert view["has_webhook_secret"] is True and view["account_id"] == account

    with SessionLocal() as session:
        stored = session.scalar(
            select(PaymentIntegration).where(PaymentIntegration.organization_id == _organization_id(account))
        )
        assert credentials["key_secret"] not in stored.key_secret_encrypted
        assert decrypt_secret(stored.key_secret_encrypted) == credentials["key_secret"]
        assert decrypt_secret(stored.webhook_secret_encrypted) == credentials["webhook_secret"]
        audits = session.scalars(select(AuditEvent).where(AuditEvent.organization_id == stored.organization_id)).all()
    assert audits and all(credentials["key_secret"] not in json.dumps(a.payload) for a in audits)


def test_validation_errors_do_not_echo_the_submitted_secret(client, merchants):
    account, headers = merchants["a"]
    response = client.put(
        "/integrations/razorpay", json=_credentials(account, "A", key_secret="abc12"), headers=headers
    )
    assert response.status_code == 422
    assert "abc12" not in response.text


def test_live_keys_are_refused_unless_explicitly_allowed(client, merchants):
    account, headers = merchants["a"]
    response = client.put(
        "/integrations/razorpay", json=_credentials(account, "A", key_id="rzp_live_AKey01"), headers=headers
    )
    assert response.status_code == 400
    assert "Live keys are disabled" in response.json()["detail"]


def test_only_admins_can_see_or_change_credentials(client, merchants):
    account, _ = merchants["a"]
    manager = _merchant(client, account, role="FINANCE_MANAGER")
    assert client.get("/integrations/razorpay", headers=manager).status_code == 403
    assert client.put("/integrations/razorpay", json=_credentials(account, "A"), headers=manager).status_code == 403
    assert client.patch("/integrations/razorpay", json={"enabled": False}, headers=manager).status_code == 403
    assert client.get("/integrations/razorpay").status_code == 401


def test_a_razorpay_account_belongs_to_one_merchant(client, merchants):
    account_a, _ = merchants["a"]
    _, headers_b = merchants["b"]
    response = client.put("/integrations/razorpay", json=_credentials(account_a, "B"), headers=headers_b)
    assert response.status_code == 409


def test_each_merchant_is_charged_through_its_own_keys(client, merchants, monkeypatch):
    api = FakeRazorpayApi()
    monkeypatch.setattr(razorpay_module, "urlopen", api)
    (account_a, headers_a), (account_b, headers_b) = merchants["a"], merchants["b"]

    opened_a = _deliver(client, _failed_payment(account_a), "whsec-A-value-0001")
    calls_a = list(api.key_ids)
    opened_b = _deliver(client, _failed_payment(account_b), "whsec-B-value-0001")
    calls_b = api.key_ids[len(calls_a) :]

    assert opened_a.status_code == opened_b.status_code == 200, (opened_a.text, opened_b.text)
    assert calls_a and set(calls_a) == {"rzp_test_AKey01"}
    assert calls_b and set(calls_b) == {"rzp_test_BKey01"}
    case_a = client.get(f"/recovery-cases/{opened_a.json()['case_id']}", headers=headers_a).json()
    assert case_a["payment_link"]["short_url"].startswith("https://rzp.io/i/")
    assert client.get(f"/recovery-cases/{opened_a.json()['case_id']}", headers=headers_b).status_code == 404


def test_a_webhook_signed_with_another_merchants_secret_is_rejected(client, merchants):
    account_b, _ = merchants["b"]
    response = _deliver(client, _failed_payment(account_b), "whsec-A-value-0001")
    assert response.status_code == 401


def test_validation_records_the_outcome(client, merchants, monkeypatch):
    _, headers = merchants["b"]
    monkeypatch.setattr(razorpay_module, "urlopen", FakeRazorpayApi())
    ok = client.post("/integrations/razorpay/validate", headers=headers).json()
    assert ok["validated_at"] and ok["last_error"] is None

    def rejected(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(razorpay_module, "urlopen", rejected)
    failed = client.post("/integrations/razorpay/validate", headers=headers).json()
    assert failed["validated_at"] is None and "401" in failed["last_error"]
    status = client.get("/integrations/status", headers=headers).json()
    razorpay = next(item for item in status["integrations"] if item["name"] == "Razorpay")
    assert razorpay["status"] == "NOT_CONFIGURED" and razorpay["label"] == "Check failed"


def test_rotation_replaces_keys_and_keeps_an_omitted_webhook_secret(client, merchants):
    account, headers = merchants["b"]
    rotated = _credentials(account, "B", key_id="rzp_test_BKey02", key_secret="secret-B-rotated-0002")
    del rotated["webhook_secret"]
    assert client.put("/integrations/razorpay", json=rotated, headers=headers).status_code == 200

    with SessionLocal() as session:
        provider = payment_integrations.payment_provider_for(session, _organization_id(account))
        stored = session.scalar(
            select(PaymentIntegration).where(PaymentIntegration.organization_id == _organization_id(account))
        )
        assert isinstance(provider, RazorpayProvider) and provider.key_id == "rzp_test_BKey02"
        assert decrypt_secret(stored.webhook_secret_encrypted) == "whsec-B-value-0001"
    restored = _credentials(account, "B")
    assert client.put("/integrations/razorpay", json=restored, headers=headers).status_code == 200


def test_disabled_credentials_stop_acting_but_still_acknowledge_events(client, merchants):
    account, headers = merchants["b"]
    disabled = client.patch("/integrations/razorpay", json={"enabled": False}, headers=headers)
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    try:
        with SessionLocal() as session:
            provider = payment_integrations.payment_provider_for(session, _organization_id(account))
        assert isinstance(provider, SimulatedRazorpayProvider)
        response = _deliver(client, _failed_payment(account), "whsec-B-value-0001")
        assert response.status_code == 200
        assert response.json()["accepted"] is False
        assert response.json()["reason"] == "Razorpay integration is disabled for this merchant"
    finally:
        client.patch("/integrations/razorpay", json={"enabled": True}, headers=headers)


def test_environment_keys_belong_only_to_the_primary_merchant(client, merchants, monkeypatch):
    """Legacy RAZORPAY_* settings must never act for, or verify webhooks of, any other merchant."""
    container.payment_provider.cache_clear()
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_LegacyKey01")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "legacy-secret-0001")
    other = f"acc_cred_c{uuid4().hex[:6]}"
    headers_c = _merchant(client, other)
    try:
        with SessionLocal() as session:
            primary = session.scalar(select(Organization).where(Organization.razorpay_account_id == ACCOUNT_ID))
            third = session.scalar(select(Organization).where(Organization.razorpay_account_id == other))
            legacy = payment_integrations.payment_provider_for(session, primary.id)
            assert isinstance(legacy, RazorpayProvider) and legacy.key_id == "rzp_test_LegacyKey01"
            assert isinstance(payment_integrations.payment_provider_for(session, third.id), SimulatedRazorpayProvider)
            assert payment_integrations.webhook_secret_for(session, primary) == settings.RAZORPAY_WEBHOOK_SECRET
            assert payment_integrations.webhook_secret_for(session, third) is None
        assert client.get("/integrations/razorpay", headers=headers_c).json()["source"] == "simulated"
        assert _deliver(client, _failed_payment(other), settings.RAZORPAY_WEBHOOK_SECRET).status_code == 401
    finally:
        container.payment_provider.cache_clear()


def test_customer_sync_reads_the_merchants_own_razorpay_account(client, merchants, monkeypatch):
    _, headers = merchants["a"]
    tag = uuid4().hex[:6]
    api = FakeRazorpayApi(
        customers=[
            {"id": f"cust_{tag}One", "name": "Sync One", "email": f"one.{tag}@example.test", "contact": "9000000001"},
            {"id": f"cust_{tag}Two", "name": "", "email": f"two.{tag}@example.test", "contact": ""},
            {"id": f"cust_{tag}Bad", "name": "Bad", "email": "", "contact": "12"},
        ]
    )
    monkeypatch.setattr(razorpay_module, "urlopen", api)

    preview = client.post("/customers/sync", json={"commit": False}, headers=headers).json()
    assert (preview["source"], preview["committed"]) == ("razorpay", False)
    assert (preview["created"], preview["invalid"]) == (2, 1)
    assert set(api.key_ids) == {"rzp_test_AKey01"}
    applied = client.post("/customers/sync", json={"commit": True}, headers=headers).json()
    assert applied["created"] == 2

    synced = client.get("/customers", params={"search": tag}, headers=headers).json()["items"]
    assert {item["razorpay_customer_id"] for item in synced} == {f"cust_{tag}One", f"cust_{tag}Two"}
    assert {item["name"] for item in synced} == {"Sync One", f"two.{tag}@example.test"}
    again = client.post("/customers/sync", json={"commit": True}, headers=headers).json()
    assert (again["created"], again["updated"]) == (0, 2)


def _ids(client, path: str, headers: dict, key: str = "provider_payment_id", **params) -> list[str]:
    return [item[key] for item in client.get(path, params=params, headers=headers).json()["items"]]


def test_merchants_never_see_each_others_transactions_or_customers(client, merchants):
    (_, headers_a), (_, headers_b) = merchants["a"], merchants["b"]
    payment_ids = _ids(client, "/transactions", headers_a)
    assert payment_ids
    assert client.get(f"/transactions/{payment_ids[0]}", headers=headers_b).status_code == 404
    assert not set(_ids(client, "/transactions", headers_b, limit=200)) & set(payment_ids)
    customer_id = _ids(client, "/customers", headers_a, key="id")[0]
    assert client.get(f"/customers/{customer_id}", headers=headers_b).status_code == 404


def test_the_same_email_imported_by_two_merchants_stays_separate(client, merchants):
    (_, headers_a), (_, headers_b) = merchants["a"], merchants["b"]
    email = f"shared.{uuid4().hex[:6]}@example.test"
    for headers, name in ((headers_a, "Seen by A"), (headers_b, "Seen by B")):
        csv_text = f"name,email\n{name},{email}\n"
        result = client.post("/customers/import", json={"csv": csv_text, "commit": True}, headers=headers).json()
        assert result["created"] == 1
    assert _ids(client, "/customers", headers_a, key="name", search=email) == ["Seen by A"]
    assert _ids(client, "/customers", headers_b, key="name", search=email) == ["Seen by B"]
