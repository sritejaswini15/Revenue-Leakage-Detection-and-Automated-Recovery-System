"""Operators choosing a recovery action, and the action and payment-attempt history of a case."""

from tests.helpers import first_contactable_customer, login
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment


def _waiting_case(client, headers, rupees: int = 1700) -> dict:
    body = failed_payment(first_contactable_customer(client, headers), rupees)
    case = client.get(f"/recovery-cases/{deliver(client, body).json()['case_id']}", headers=headers).json()
    assert case["status"] == "WAITING_FOR_PAYMENT"
    return case


def _audit(client, headers, case_id: str) -> list[dict]:
    return client.get("/audit-events", params={"case_id": case_id, "limit": 200}, headers=headers).json()["items"]


def test_detail_lists_actions_attempts_and_what_can_be_run(client, admin_headers):
    case = _waiting_case(client, admin_headers)
    assert case["actions"] and case["actions"][0]["status"] == "EXECUTED"
    assert [attempt["status"] for attempt in case["payment_attempts"]] == ["FAILED"]
    assert "PAYMENT_RETRY" in case["executable_actions"] and "PAYMENT_LINK" in case["executable_actions"]
    assert "ESCALATE_TO_HUMAN" not in case["executable_actions"]


def test_an_operator_can_choose_a_different_action(client, admin_headers):
    case = _waiting_case(client, admin_headers)
    chosen = "REMINDER" if case["recommended_action"] != "REMINDER" else "PAYMENT_LINK"

    response = client.post(f"/recovery-cases/{case['id']}/execute", json={"action_type": chosen}, headers=admin_headers)

    assert response.status_code == 200, response.text
    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["contact_attempts"] == case["contact_attempts"] + 1
    assert after["actions"][0]["action_type"] == chosen
    override = [e for e in _audit(client, admin_headers, case["id"]) if e["event_name"] == "action.override"]
    assert override and override[0]["payload"] == {"recommended": case["recommended_action"], "chosen": chosen}


def test_a_retry_records_its_attempt_and_closes_the_case(client, admin_headers):
    case = _waiting_case(client, admin_headers, 1800)
    response = client.post(
        f"/recovery-cases/{case['id']}/execute", json={"action_type": "PAYMENT_RETRY"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    attempts = [attempt["status"] for attempt in after["payment_attempts"]]
    if after["status"] == "RECOVERED":
        assert attempts[0] == "SUCCESS" and "FAILED" in attempts
        assert after["executable_actions"] == []
    else:  # declined again: the retry falls back to a payment link, and says so
        assert attempts.count("FAILED") == 2
        assert "sent a payment link instead" in after["actions"][0]["detail"]


def test_actions_outside_policy_are_refused(client, admin_headers):
    case = _waiting_case(client, admin_headers)
    for action in ("ESCALATE_TO_HUMAN", "MONITOR", "NOT_AN_ACTION"):
        response = client.post(
            f"/recovery-cases/{case['id']}/execute", json={"action_type": action}, headers=admin_headers
        )
        assert response.status_code in (400, 409), (action, response.status_code)


def test_viewers_cannot_execute(client, admin_headers):
    case = _waiting_case(client, admin_headers)
    viewer = login(client, "viewer@recoverx.local")
    response = client.post(f"/recovery-cases/{case['id']}/execute", json={"action_type": "REMINDER"}, headers=viewer)
    assert response.status_code == 403
