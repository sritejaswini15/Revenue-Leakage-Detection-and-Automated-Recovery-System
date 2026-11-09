"""Every endpoint outside a short public allowlist refuses anonymous callers.

Walks the OpenAPI schema, so a new route is covered the moment it is added.
"""

import re

import pytest

# Reachable without a session, each for a reason.
PUBLIC = {
    ("GET", "/health"),  # platform health check
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),  # clearing a session needs none
    ("POST", "/webhooks/razorpay"),  # authenticated by the HMAC signature instead
    ("GET", "/pay/{token}"),  # the customer's own payment page: the token is the credential
    ("POST", "/pay/{token}"),
}


def _operations(client) -> list[tuple[str, str]]:
    schema = client.app.openapi()
    return [(method.upper(), path) for path, operations in schema["paths"].items() for method in operations]


def test_the_schema_lists_the_api(client):
    operations = _operations(client)
    assert len(operations) > 40
    assert set(operations) >= PUBLIC, "the allowlist names real routes"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_anonymous_callers_are_refused(client, method):
    client.cookies.clear()
    checked = 0
    for operation_method, path in _operations(client):
        if operation_method != method or (method, path) in PUBLIC:
            continue
        url = re.sub(r"\{[^}]+\}", "RC-RAHUL-18K", path)
        response = client.request(method, url, json={})
        assert response.status_code == 401, f"{method} {path} answered {response.status_code} without a session"
        checked += 1
    if method in ("GET", "POST"):
        assert checked > 5
