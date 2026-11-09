"""Operator commands must never rewrite a closed case's outcome or revive it behind policy's back."""

import pytest
from sqlalchemy import select

from app.infrastructure.database.models import Communication, RecoveryCase
from app.infrastructure.database.session import SessionLocal
from tests.helpers import find_escalation, first_contactable_customer
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment, link_paid


def _waiting_case(client, headers, rupees: int) -> dict:
    body = failed_payment(first_contactable_customer(client, headers), rupees)
    case = client.get(f"/recovery-cases/{deliver(client, body).json()['case_id']}", headers=headers).json()
    assert case["status"] == "WAITING_FOR_PAYMENT", case["status"]
    return case


def _recovered_case(client, headers, rupees: int) -> dict:
    case = _waiting_case(client, headers, rupees)
    assert deliver(client, link_paid(case, rupees * 100)).status_code == 200
    after = client.get(f"/recovery-cases/{case['id']}", headers=headers).json()
    assert after["status"] == "RECOVERED"
    return after


def _messages(case_id: str) -> int:
    with SessionLocal() as session:
        return len(session.scalars(select(Communication).where(Communication.case_id == case_id)).all())


@pytest.mark.parametrize("command", ["stop", "reject"])
def test_a_recovered_case_keeps_its_outcome(client, admin_headers, command):
    case = _recovered_case(client, admin_headers, 2100)
    if command == "stop":
        response = client.post(f"/recovery-cases/{case['id']}/stop", headers=admin_headers)
    else:
        response = client.post(
            f"/escalations/{case['id']}/resolve", json={"resolution": "REJECT"}, headers=admin_headers
        )

    assert response.status_code == 409, response.text
    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["status"] == "RECOVERED"
    assert after["recovered_amount"] == 2100


@pytest.mark.parametrize("closed_status", ["STOPPED", "EXPIRED"])
def test_approval_cannot_revive_a_closed_case(client, admin_headers, closed_status):
    case = _waiting_case(client, admin_headers, 2200)
    with SessionLocal() as session:
        session.get(RecoveryCase, case["id"]).status = closed_status
        session.commit()
    sent_before = _messages(case["id"])

    response = client.post(
        f"/recovery-cases/{case['id']}/approve", json={"resolution": "APPROVE"}, headers=admin_headers
    )

    assert response.status_code == 409, response.text
    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["status"] == closed_status
    assert _messages(case["id"]) == sent_before, "no new message may reach the customer"


def test_stopping_a_case_cancels_its_open_payment_link(client, admin_headers):
    case = _waiting_case(client, admin_headers, 2300)
    assert case["payment_link"]["status"] not in ("CANCELLED", "EXPIRED", "PAID")

    assert client.post(f"/recovery-cases/{case['id']}/stop", headers=admin_headers).status_code == 200

    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["status"] == "STOPPED"
    assert after["payment_link"]["status"] == "CANCELLED"


def _audit_names(client, headers, case_id: str) -> list[str]:
    items = client.get("/audit-events", params={"case_id": case_id, "limit": 200}, headers=headers).json()["items"]
    return [item["event_name"] for item in items]


def _escalated_case(client, headers, rupees: int) -> dict:
    case = _waiting_case(client, headers, rupees)
    escalated = client.post(f"/recovery-cases/{case['id']}/escalate", json={"notes": "check"}, headers=headers)
    assert escalated.status_code == 200, escalated.text
    assert escalated.json()["status"] == "ESCALATED"
    return client.get(f"/recovery-cases/{case['id']}", headers=headers).json()


def test_an_approved_case_waits_for_payment_instead_of_bouncing_back(client, admin_headers):
    case = _escalated_case(client, admin_headers, 2400)  # already contacted once
    response = client.post(f"/escalations/{case['id']}/resolve", json={"resolution": "APPROVE"}, headers=admin_headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "WAITING_FOR_PAYMENT"
    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["contact_attempts"] == case["contact_attempts"] + 1
    assert after["escalation"]["resolution"] == "APPROVE"


def test_edit_runs_the_reviewers_action_and_records_the_override(client, admin_headers):
    case = _escalated_case(client, admin_headers, 2500)
    chosen = "REMINDER" if case["recommended_action"] != "REMINDER" else "PERSONALIZED_OUTREACH"

    missing = client.post(f"/escalations/{case['id']}/resolve", json={"resolution": "EDIT"}, headers=admin_headers)
    assert missing.status_code == 400

    response = client.post(
        f"/escalations/{case['id']}/resolve",
        json={"resolution": "EDIT", "action_type": chosen, "notes": "customer prefers a reminder"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["actions"][0]["action_type"] == chosen
    assert "action.override" in _audit_names(client, admin_headers, case["id"])


def test_contact_sends_personalised_outreach(client, admin_headers):
    case = _escalated_case(client, admin_headers, 2600)
    response = client.post(f"/escalations/{case['id']}/resolve", json={"resolution": "contact"}, headers=admin_headers)
    assert response.status_code == 200, response.text
    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["actions"][0]["action_type"] == "PERSONALIZED_OUTREACH"


def test_an_unknown_resolution_is_refused_rather_than_stopping_the_case(client, admin_headers):
    case = _escalated_case(client, admin_headers, 2700)
    response = client.post(f"/escalations/{case['id']}/resolve", json={"resolution": "APROVE"}, headers=admin_headers)
    assert response.status_code == 400
    assert client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()["status"] == "ESCALATED"


@pytest.mark.parametrize("resolution", ["REJECT", "CLOSE"])
def test_reject_and_close_stop_the_case_and_its_link(client, admin_headers, resolution):
    case = _escalated_case(client, admin_headers, 2800)
    response = client.post(f"/escalations/{case['id']}/resolve", json={"resolution": resolution}, headers=admin_headers)
    assert response.status_code == 200, response.text
    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["status"] == "STOPPED"
    assert after["payment_link"]["status"] == "CANCELLED"


def test_operators_record_promises_only_for_what_is_owed_and_a_future_date(client, admin_headers):
    case = _waiting_case(client, admin_headers, 3000)
    url = f"/recovery-cases/{case['id']}/promise-to-pay"
    too_much = client.post(url, json={"amount": 3001, "promised_date": "2099-01-01T00:00:00Z"}, headers=admin_headers)
    in_the_past = client.post(
        url, json={"amount": 1000, "promised_date": "2020-01-01T00:00:00Z"}, headers=admin_headers
    )
    assert too_much.status_code == 400 and in_the_past.status_code == 400

    created = client.post(url, json={"amount": 3000, "promised_date": "2099-01-01T00:00:00Z"}, headers=admin_headers)
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "PENDING"
    assert client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()["status"] == "PROMISE_TO_PAY"


def test_checking_a_promise_after_payment_keeps_the_amount_actually_paid(client, admin_headers):
    case = _waiting_case(client, admin_headers, 3100)
    promise = client.post(
        f"/recovery-cases/{case['id']}/promise-to-pay",
        json={"amount": 500, "promised_date": "2099-01-01T00:00:00Z"},
        headers=admin_headers,
    ).json()
    assert deliver(client, link_paid(case, 3100 * 100)).status_code == 200

    checked = client.post(f"/simulation/promise-to-pay/{promise['id']}/fulfill", headers=admin_headers)

    assert checked.status_code == 200 and checked.json()["status"] == "FULFILLED"
    after = client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()
    assert after["status"] == "RECOVERED" and after["recovered_amount"] == 3100


def test_closed_cases_cannot_be_escalated_or_promised(client, admin_headers):
    case = _recovered_case(client, admin_headers, 3200)
    escalated = client.post(f"/recovery-cases/{case['id']}/escalate", headers=admin_headers)
    promised = client.post(
        f"/recovery-cases/{case['id']}/promise-to-pay",
        json={"amount": 100, "promised_date": "2099-01-01T00:00:00Z"},
        headers=admin_headers,
    )
    assert escalated.status_code == 409 and promised.status_code == 409
    assert client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()["status"] == "RECOVERED"


def test_a_policy_refusal_is_audited(client, admin_headers):
    case = _waiting_case(client, admin_headers, 3300)
    response = client.post(
        f"/recovery-cases/{case['id']}/execute", json={"action_type": "MONITOR"}, headers=admin_headers
    )
    assert response.status_code == 409
    assert "policy.rejected" in _audit_names(client, admin_headers, case["id"])


def test_max_attempts_is_stopped_by_the_policy_engine(client, admin_headers):
    case = _waiting_case(client, admin_headers, 3400)
    response = client.post("/simulation/max-attempts", json={"case_id": case["id"]}, headers=admin_headers)
    assert response.status_code == 200 and response.json()["status"] == "STOPPED"
    names = _audit_names(client, admin_headers, case["id"])
    assert "policy.rejected" in names and "workflow.stopped" in names

    again = client.post("/simulation/max-attempts", json={"case_id": case["id"]}, headers=admin_headers)
    assert again.status_code == 409


def test_the_review_queue_says_why_each_case_is_there(client, admin_headers):
    case = _escalated_case(client, admin_headers, 3500)
    entry = find_escalation(client, admin_headers, case["id"], "CUSTOMER_REQUEST")
    assert entry["escalation_reason"] == "OPERATOR_REQUEST" and entry["queue"] == "CUSTOMER_REQUEST"
    queue = client.get("/escalations", params={"queue": "CUSTOMER_REQUEST", "limit": 200}, headers=admin_headers).json()
    assert queue["queues"]["CUSTOMER_REQUEST"] == queue["total"] >= 1
    values = [item["expected_recovery_value"] for item in queue["items"]]
    assert values == sorted(values, reverse=True)


def test_escalating_twice_keeps_the_original_reason(client, admin_headers):
    case = _escalated_case(client, admin_headers, 3600)
    again = client.post(f"/recovery-cases/{case['id']}/escalate", headers=admin_headers)
    assert again.status_code == 409
    assert (
        client.get(f"/recovery-cases/{case['id']}", headers=admin_headers).json()["escalation"]["reason"]
        == "OPERATOR_REQUEST"
    )


def test_the_review_queue_comes_a_page_at_a_time(client, admin_headers):
    everything = client.get("/escalations", params={"limit": 200}, headers=admin_headers).json()
    assert everything["total"] == sum(everything["queues"].values())
    first = client.get("/escalations", params={"limit": 2}, headers=admin_headers).json()
    second = client.get("/escalations", params={"limit": 2, "offset": 2}, headers=admin_headers).json()
    assert len(first["items"]) == 2 and first["total"] == everything["total"]
    assert {item["id"] for item in first["items"]}.isdisjoint(item["id"] for item in second["items"])
    assert [item["id"] for item in first["items"] + second["items"]] == [item["id"] for item in everything["items"][:4]]
    assert client.get("/escalations", params={"limit": 500}, headers=admin_headers).status_code == 422
