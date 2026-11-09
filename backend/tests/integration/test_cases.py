from tests.helpers import first_contactable_customer


def test_cases_require_authentication(client):
    assert client.get("/recovery-cases?limit=10").status_code == 401


def test_cases_are_ordered_by_expected_recovery_value(client, admin_headers):
    data = client.get("/recovery-cases?limit=200", headers=admin_headers).json()
    values = [item["expected_recovery_value"] for item in data["items"]]
    assert data["total"] >= len(values) > 0
    assert values == sorted(values, reverse=True)


def test_case_filters(client, admin_headers):
    escalated = client.get("/recovery-cases?status=escalated&limit=500", headers=admin_headers).json()
    assert escalated["items"] and {item["status"] for item in escalated["items"]} == {"ESCALATED"}

    high_risk = client.get("/recovery-cases?risk=HIGH&limit=500", headers=admin_headers).json()["items"]
    assert all(item["risk_score"] >= 75 for item in high_risk)

    invoices = client.get("/recovery-cases?revenue_type=INVOICE_OVERDUE&limit=500", headers=admin_headers).json()[
        "items"
    ]
    assert invoices and {item["event_type"] for item in invoices} == {"INVOICE_OVERDUE"}

    rahul = client.get("/recovery-cases?query=rahul-18&limit=500", headers=admin_headers).json()["items"]
    assert [item["id"] for item in rahul] == ["RC-RAHUL-18K"]


def test_case_detail_includes_ai_reasoning(client, admin_headers):
    case = client.get("/recovery-cases/RC-RAHUL-18K", headers=admin_headers).json()
    assert case["customer"] == "Rahul Sharma"
    assert case["root_cause"] == "ISOLATED_PAYMENT_METHOD_ISSUE"
    assert case["advisor_model"] == "synthetic-seed"  # seeded text, not an advisor decision
    assert case["created_at"].endswith("Z")  # explicit UTC for browsers
    assert client.get("/recovery-cases/RC-MISSING", headers=admin_headers).status_code == 404


def test_execute_golden_case_creates_link_and_message(client, admin_headers):
    response = client.post("/recovery-cases/RC-RAHUL-18K/execute", headers=admin_headers)
    assert response.status_code == 200, response.text

    case = client.get("/recovery-cases/RC-RAHUL-18K", headers=admin_headers).json()
    assert case["status"] == "WAITING_FOR_PAYMENT"
    assert case["payment_link"]["payment_url"].startswith("/pay/")
    assert any(case["payment_link"]["short_url"] in message["content"] for message in case["communications"])

    link = client.get("/recovery-cases/RC-RAHUL-18K/payment-link", headers=admin_headers).json()
    assert link["status"] == "OPEN" and link["amount"] == 18000


def test_stopped_case_cannot_be_executed(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    created = client.post(
        "/simulation/event",
        json={"event_type": "INVOICE_OVERDUE", "customer_id": customer["id"], "amount": 3000},
        headers=admin_headers,
    ).json()
    case_id = created["case_id"]
    assert client.post(f"/recovery-cases/{case_id}/stop").status_code == 401
    assert client.post(f"/recovery-cases/{case_id}/stop", headers=admin_headers).json()["status"] == "STOPPED"

    response = client.post(f"/recovery-cases/{case_id}/execute", headers=admin_headers)
    assert response.status_code == 409
    assert "STOP" in response.json()["detail"]


def test_escalation_approval_executes_the_action(client, admin_headers):
    escalated = client.get("/escalations", headers=admin_headers).json()["items"]
    target = next(item for item in escalated if item["recommended_action"] in ("PAYMENT_LINK", "REMINDER"))

    response = client.post(
        f"/escalations/{target['id']}/resolve", json={"resolution": "APPROVE"}, headers=admin_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "WAITING_FOR_PAYMENT"
    detail = client.get(f"/recovery-cases/{target['id']}", headers=admin_headers).json()
    assert detail["escalation"]["resolution"] == "APPROVE"
    assert detail["action_executed"] is True


def test_policy_update_is_audited(client, admin_headers):
    policy = client.get("/policies", headers=admin_headers).json()
    updated = client.put("/policies", json={**policy, "max_contact_attempts": 4}, headers=admin_headers)
    assert updated.status_code == 200 and updated.json()["max_contact_attempts"] == 4
    client.put("/policies", json=policy, headers=admin_headers)

    events = client.get("/audit-events?limit=5", headers=admin_headers).json()["items"]
    assert events[0]["event_name"] == "policy.updated"


def test_open_cases_exclude_closed_ones(client, admin_headers):
    """The dashboard's priority list asks for open cases, so closed ones never crowd out the work."""
    open_cases = client.get("/recovery-cases", params={"status": "OPEN", "limit": 500}, headers=admin_headers).json()
    everything = client.get("/recovery-cases", params={"limit": 500}, headers=admin_headers).json()
    assert open_cases["total"] > 0
    assert all(item["status"] not in ("RECOVERED", "STOPPED", "EXPIRED") for item in open_cases["items"])
    closed = sum(
        client.get("/recovery-cases", params={"status": status, "limit": 1}, headers=admin_headers).json()["total"]
        for status in ("RECOVERED", "STOPPED", "EXPIRED")
    )
    assert open_cases["total"] == everything["total"] - closed
