"""User management, cookie sessions with CSRF protection, and account lifecycle."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.infrastructure.database.models import AuditEvent, LoginFailure
from app.infrastructure.database.session import SessionLocal
from tests.helpers import login
from tests.integration.test_tenant_credentials import _merchant

TEMPORARY = "temporary-pass-0001"


def _create(client, headers, role: str = "OPERATOR", password: str = TEMPORARY) -> dict:
    email = f"person.{uuid4().hex[:8]}@merchant.test"
    response = client.post(
        "/users", json={"name": "New Person", "email": email, "role": role, "password": password}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _audit_names(client, headers) -> list[str]:
    events = client.get("/audit-events", params={"event_prefix": "user.", "limit": 200}, headers=headers).json()
    return [event["event_name"] for event in events["items"]]


# --- administration ------------------------------------------------------------------------


def test_admin_creates_a_user_who_can_sign_in(client, admin_headers):
    created = _create(client, admin_headers, role="VIEWER")
    assert (created["role"], created["is_active"], created["active_sessions"]) == ("VIEWER", True, 0)
    assert "password" not in created and "password_hash" not in created

    viewer = login(client, created["email"], TEMPORARY)
    assert client.get("/auth/me", headers=viewer).json()["email"] == created["email"]
    listed = {user["email"]: user for user in client.get("/users", headers=admin_headers).json()}
    assert listed[created["email"]]["active_sessions"] == 1
    assert "user.created" in _audit_names(client, admin_headers)


def test_only_admins_manage_users(client):
    manager = login(client, "finance@recoverx.local")
    assert client.get("/users", headers=manager).status_code == 403
    assert client.post("/users", json={}, headers=manager).status_code in (403, 422)


def test_duplicate_emails_and_weak_passwords_are_refused(client, admin_headers):
    existing = _create(client, admin_headers)
    again = client.post(
        "/users",
        json={"name": "Dup", "email": existing["email"].upper(), "role": "OPERATOR", "password": TEMPORARY},
        headers=admin_headers,
    )
    assert again.status_code == 409
    weak = client.post(
        "/users",
        json={
            "name": "Weak",
            "email": f"weak.{uuid4().hex[:6]}@merchant.test",
            "role": "OPERATOR",
            "password": "short",
        },
        headers=admin_headers,
    )
    assert weak.status_code == 400 and "12 characters" in weak.json()["detail"]
    assert "short" not in weak.text


def test_deactivation_signs_the_user_out_and_blocks_sign_in(client, admin_headers):
    person = _create(client, admin_headers)
    headers = login(client, person["email"], TEMPORARY)

    updated = client.patch(f"/users/{person['id']}", json={"is_active": False}, headers=admin_headers).json()

    assert updated["is_active"] is False and updated["active_sessions"] == 0
    assert client.get("/auth/me", headers=headers).status_code == 401
    refused = client.post("/auth/login", json={"email": person["email"], "password": TEMPORARY})
    assert refused.status_code == 401 and "deactivated" in refused.json()["detail"]

    client.patch(f"/users/{person['id']}", json={"is_active": True}, headers=admin_headers)
    login(client, person["email"], TEMPORARY)
    assert {"user.deactivated", "user.activated"} <= set(_audit_names(client, admin_headers))


def test_a_role_change_takes_effect_immediately(client, admin_headers):
    person = _create(client, admin_headers, role="OPERATOR")
    headers = login(client, person["email"], TEMPORARY)
    assert client.patch(f"/users/{person['id']}", json={"role": "VIEWER"}, headers=admin_headers).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401  # old session carried the old role
    assert client.get("/auth/me", headers=login(client, person["email"], TEMPORARY)).json()["role"] == "VIEWER"


def test_admins_cannot_demote_or_deactivate_themselves(client, admin_headers):
    me = client.get("/auth/me", headers=admin_headers).json()
    assert client.patch(f"/users/{me['id']}", json={"is_active": False}, headers=admin_headers).status_code == 403
    assert client.patch(f"/users/{me['id']}", json={"role": "VIEWER"}, headers=admin_headers).status_code == 403
    renamed = client.patch(f"/users/{me['id']}", json={"name": f"{me['name']} (owner)"}, headers=admin_headers)
    assert renamed.status_code == 200 and renamed.json()["name"] == f"{me['name']} (owner)"
    client.patch(f"/users/{me['id']}", json={"name": me["name"]}, headers=admin_headers)


def test_admin_password_reset_signs_the_user_out(client, admin_headers):
    person = _create(client, admin_headers)
    headers = login(client, person["email"], TEMPORARY)
    reset = client.post(f"/users/{person['id']}/password", json={"password": "replacement-0002"}, headers=admin_headers)
    assert reset.status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post("/auth/login", json={"email": person["email"], "password": TEMPORARY}).status_code == 401
    login(client, person["email"], "replacement-0002")
    assert "user.password_reset" in _audit_names(client, admin_headers)


def test_revoking_sessions(client, admin_headers):
    person = _create(client, admin_headers)
    first, second = login(client, person["email"], TEMPORARY), login(client, person["email"], TEMPORARY)
    revoked = client.post(f"/users/{person['id']}/revoke-sessions", headers=admin_headers).json()
    assert revoked["active_sessions"] == 0
    assert (
        client.get("/auth/me", headers=first).status_code == client.get("/auth/me", headers=second).status_code == 401
    )


def test_users_of_another_merchant_are_invisible(client, admin_headers):
    other = _merchant(client, f"acc_users_{uuid4().hex[:6]}")
    person = _create(client, admin_headers)
    assert client.patch(f"/users/{person['id']}", json={"is_active": False}, headers=other).status_code == 404
    assert person["email"] not in [user["email"] for user in client.get("/users", headers=other).json()]


# --- own account ---------------------------------------------------------------------------


def test_changing_your_password_keeps_this_session_and_ends_the_others(client, admin_headers):
    person = _create(client, admin_headers)
    current, other = login(client, person["email"], TEMPORARY), login(client, person["email"], TEMPORARY)

    wrong = client.post(
        "/auth/password", json={"current_password": "nope", "new_password": "brand-new-pass-0003"}, headers=current
    )
    assert wrong.status_code == 401
    changed = client.post(
        "/auth/password",
        json={"current_password": TEMPORARY, "new_password": "brand-new-pass-0003"},
        headers=current,
    )
    assert changed.status_code == 200 and changed.json()["sessions_revoked"] == 1
    assert client.get("/auth/me", headers=current).status_code == 200
    assert client.get("/auth/me", headers=other).status_code == 401
    login(client, person["email"], "brand-new-pass-0003")


def test_the_bootstrap_admin_password_is_managed_by_the_environment(client, admin_headers, monkeypatch):
    me = client.get("/auth/me", headers=admin_headers).json()
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_EMAIL", me["email"])
    response = client.post(
        "/auth/password",
        json={"current_password": "recoverx-demo", "new_password": "another-pass-0004"},
        headers=admin_headers,
    )
    assert response.status_code == 403


# --- cookie sessions -----------------------------------------------------------------------


@pytest.fixture
def cookie_client(client):
    """The test client with only the session cookie: what a browser has."""
    response = client.post("/auth/login", json={"email": "admin@recoverx.local", "password": "recoverx-demo"})
    cookie = response.headers["set-cookie"]
    assert settings.SESSION_COOKIE_NAME in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    yield client
    client.cookies.clear()


def test_the_session_cookie_authenticates_reads(cookie_client):
    assert cookie_client.get("/auth/me").status_code == 200


def test_cookie_writes_need_the_csrf_header(cookie_client):
    original = cookie_client.get("/policies").json()
    forged = cookie_client.put("/policies", json=original)
    assert forged.status_code == 403 and "X-Requested-With" in forged.json()["detail"]
    allowed = cookie_client.put("/policies", json=original, headers={"X-Requested-With": "recoverx-web"})
    assert allowed.status_code == 200


def test_logout_revokes_the_cookie_session(cookie_client):
    token = cookie_client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert cookie_client.post("/auth/logout", headers={"X-Requested-With": "recoverx-web"}).status_code == 204
    assert cookie_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# --- login throttling ----------------------------------------------------------------------


def test_failed_logins_are_counted_in_the_database_and_cleared_on_success(client, admin_headers):
    person = _create(client, admin_headers)
    for _ in range(3):
        assert client.post("/auth/login", json={"email": person["email"], "password": "wrong"}).status_code == 401
    with SessionLocal() as session:
        stored = session.scalar(select(func.count(LoginFailure.id)).where(LoginFailure.email == person["email"]))
    assert stored == 3  # shared by every API instance, unlike an in-memory counter
    login(client, person["email"], TEMPORARY)
    with SessionLocal() as session:
        assert not session.scalar(select(func.count(LoginFailure.id)).where(LoginFailure.email == person["email"]))
        assert session.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_name == "user.created"))
