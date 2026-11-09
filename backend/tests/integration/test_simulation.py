from tests.helpers import first_contactable_customer


def _open_case_ids(client, headers, count):
    cases = client.get("/recovery-cases?status=ESCALATED&limit=500", headers=headers).json()["items"]
    assert len(cases) >= count
    return [case["id"] for case in cases[-count:]]


def test_failure_lab_safe_fallbacks_are_persisted(client, admin_headers):
    first, second = _open_case_ids(client, admin_headers, 2)

    low = client.post("/simulation/low-confidence", json={"case_id": first}, headers=admin_headers)
    assert low.status_code == 200 and low.json()["status"] == "ESCALATED"

    capped = client.post("/simulation/max-attempts", json={"case_id": second}, headers=admin_headers)
    assert capped.status_code == 200 and capped.json()["status"] == "STOPPED"

    invalid = client.post("/simulation/invalid-ai", json={"case_id": first}, headers=admin_headers)
    assert invalid.status_code == 200 and invalid.json()["fallback"] == "HUMAN_REVIEW"

    closed = client.post("/simulation/invalid-ai", json={"case_id": second}, headers=admin_headers)
    assert closed.status_code == 409


def test_simulation_requires_a_case_id(client, admin_headers):
    assert client.post("/simulation/timeout", json={}, headers=admin_headers).status_code == 400


def test_provider_timeout_is_recorded_and_retried_without_repeating_actions(client, admin_headers):
    """Razorpay times out: the failure is recorded, nothing reaches the customer, and one retry
    completes the same action, so the customer gets exactly one new link and one message."""
    customer = first_contactable_customer(client, admin_headers)
    case_id = client.post(
        "/simulation/event",
        json={"event_type": "PAYMENT_FAILURE", "customer_id": customer["id"], "amount": 2200},
        headers=admin_headers,
    ).json()["case_id"]
    before = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()

    response = client.post("/simulation/timeout", json={"case_id": case_id}, headers=admin_headers)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "EXECUTED" and result["workflow_preserved"] is True
    assert result["failed_attempts"] == 1 and result["messages_sent"] == 1
    after = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    retried = [action for action in after["actions"] if action["id"] == result["action_id"]]
    assert len(retried) == 1 and retried[0]["status"] == "EXECUTED", "the retry completes the same action"
    assert len(after["actions"]) == len(before["actions"]) + 1
    assert len(after["communications"]) == len(before["communications"]) + 1
    assert after["payment_link"]["status"] not in ("CANCELLED", "EXPIRED")
    events = client.get("/audit-events", params={"case_id": case_id, "limit": 100}, headers=admin_headers).json()
    names = [event["event_name"] for event in events["items"]]
    assert "action.failed" in names and "failure_lab.provider_timeout" in names


def test_provider_timeout_needs_a_case_policy_lets_recoverx_act_on(client, admin_headers):
    (case_id,) = _open_case_ids(client, admin_headers, 1)  # escalated: waiting for a person
    response = client.post("/simulation/timeout", json={"case_id": case_id}, headers=admin_headers)
    assert response.status_code == 409


def test_invoice_reminder_uses_a_persisted_provider_link(client, admin_headers):
    """Reminders never contain a fabricated URL: the link in the message is the stored one."""
    customer = first_contactable_customer(client, admin_headers)
    response = client.post(
        "/simulation/event",
        json={"event_type": "INVOICE_OVERDUE", "customer_id": customer["id"], "amount": 5000},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    case = client.get(f"/recovery-cases/{response.json()['case_id']}", headers=admin_headers).json()
    short_url = case["payment_link"]["short_url"]
    assert short_url.startswith("https://rzp.io/i/")
    assert any(short_url in message["content"] for message in case["communications"])


def test_simulated_payment_recovers_the_full_amount(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    created = client.post(
        "/simulation/event",
        json={"event_type": "PAYMENT_FAILURE", "customer_id": customer["id"], "amount": 120000},
        headers=admin_headers,
    ).json()

    paid = client.post("/simulation/payment", json={"case_id": created["case_id"]}, headers=admin_headers)

    assert paid.status_code == 200, paid.text
    # Regression: large amounts used to be divided by 100 on the way back in.
    assert paid.json()["status"] == "RECOVERED"
    assert paid.json()["recovered_amount"] == 120000


def test_promise_to_pay_can_be_recorded_and_evaluated(client, admin_headers):
    (case_id,) = _open_case_ids(client, admin_headers, 1)
    promise = client.post(
        "/simulation/promise-to-pay",
        json={"case_id": case_id, "amount": 1000, "promised_date": "2020-01-01T00:00:00Z"},
        headers=admin_headers,
    )
    assert promise.status_code == 200, promise.text

    evaluated = client.post(f"/simulation/promise-to-pay/{promise.json()['id']}/fulfill", headers=admin_headers)
    assert evaluated.status_code == 200
    assert evaluated.json()["status"] == "BROKEN"  # the promised date has passed


def test_opt_out_stops_contact(client, admin_headers):
    customer = first_contactable_customer(client, admin_headers)
    response = client.post("/simulation/opt-out", json={"customer_id": customer["id"]}, headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["customer_id"] == customer["id"]
    assert response.json()["opted_out"] is True

    refreshed = client.get(f"/customers/{customer['id']}", headers=admin_headers).json()
    assert refreshed["opted_out"] is True
