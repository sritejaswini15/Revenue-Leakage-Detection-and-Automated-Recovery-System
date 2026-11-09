from app.application.bootstrap import ensure_bootstrap_admin
from app.core.config import settings
from app.infrastructure.database.session import SessionLocal
from tests.helpers import login


def test_bootstrap_admin_is_created_and_password_rotates(client, monkeypatch):
    email = "owner@merchant.test"
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAIL", email)
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_PASSWORD", "first-password-0001")
    with SessionLocal() as session:
        ensure_bootstrap_admin(session)
    old_session = login(client, email, "first-password-0001")

    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_PASSWORD", "second-password-0002")
    with SessionLocal() as session:
        ensure_bootstrap_admin(session)

    stale = client.post("/auth/login", json={"email": email, "password": "first-password-0001"})
    assert stale.status_code == 401
    assert stale.json()["detail"] == "Invalid credentials"
    login(client, email, "second-password-0002")
    # Sessions issued under the old password are revoked by the rotation.
    assert client.get("/recovery-cases?limit=1", headers=old_session).status_code == 401
    # Other administrators are never renamed or reset by the bootstrap.
    login(client, "admin@recoverx.local")


def test_login_tolerates_whitespace_case_and_quotes(client, monkeypatch):
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAIL", "paste@merchant.test")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_PASSWORD", "pasted-password-0003")
    with SessionLocal() as session:
        ensure_bootstrap_admin(session)

    login(client, "  PASTE@MERCHANT.TEST  ", "  pasted-password-0003  ")
    login(client, '"paste@merchant.test"', '"pasted-password-0003"')


def test_token_lifecycle(client):
    headers = login(client)
    assert client.get("/recovery-cases?limit=1", headers=headers).status_code == 200
    assert client.get("/recovery-cases", headers={"Authorization": "Bearer invalid"}).status_code == 401

    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/recovery-cases?limit=1", headers=headers).status_code == 401


def test_oversized_credentials_are_rejected_before_hashing(client):
    response = client.post("/auth/login", json={"email": "a@b.test", "password": "x" * 10_000})
    assert response.status_code == 422


def test_repeated_failures_are_rate_limited(client):
    statuses = [
        client.post("/auth/login", json={"email": "nobody@merchant.test", "password": "wrong"}).status_code
        for _ in range(6)
    ]
    assert statuses == [401] * 5 + [429]


def test_viewer_cannot_act_on_cases(client):
    viewer = login(client, "viewer@recoverx.local")
    case_id = client.get("/recovery-cases?limit=1", headers=viewer).json()["items"][0]["id"]

    assert client.post(f"/recovery-cases/{case_id}/execute", headers=viewer).status_code == 403
    assert client.post(f"/recovery-cases/{case_id}/stop", headers=viewer).status_code == 403
    assert client.put("/policies", json={}, headers=viewer).status_code == 403


def test_operator_cannot_change_policy_or_reset_data(client):
    operator = login(client, "operator@recoverx.local")
    assert client.put("/policies", json={}, headers=operator).status_code == 403
    assert client.post("/demo/reset", headers=operator).status_code == 403
