"""Whatever the advisor answers, the pipeline stays safe: invalid output and waits never execute anything."""

import pytest

from app import container
from app.infrastructure.ai.recovery_graph import LangGraphRecoveryAdvisor, MalformedOutputReasoner
from tests.helpers import find_escalation, first_contactable_customer
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment, link_paid


class Answers:
    name = "scripted-model"

    def __init__(self, answer: dict) -> None:
        self.answer = answer

    def reason(self, state, feedback):  # noqa: ARG002
        return self.answer


def _use_advisor(monkeypatch, advisor) -> None:
    monkeypatch.setattr(container, "recovery_advisor", lambda: advisor)


def _audit_names(client, headers, case_id: str) -> list[str]:
    items = client.get("/audit-events", params={"case_id": case_id, "limit": 200}, headers=headers).json()["items"]
    return [item["event_name"] for item in items]


def test_an_advisor_that_returns_garbage_sends_the_case_to_a_person(client, admin_headers, monkeypatch):
    _use_advisor(monkeypatch, LangGraphRecoveryAdvisor(MalformedOutputReasoner()))
    customer = first_contactable_customer(client, admin_headers)

    case_id = deliver(client, failed_payment(customer, 1600)).json()["case_id"]

    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert case["status"] == "ESCALATED" and case["recommended_action"] == "ESCALATE_TO_HUMAN"
    assert case["actions"] == [] and case["communications"] == [], "nothing may run on invalid advisor output"
    assert case["escalation"]["reason"].startswith("INVALID_AI_OUTPUT")
    assert case["root_cause"] == "ADVISOR_OUTPUT_INVALID" and "Safe failure" in case["ai_reasoning"]
    assert "ai.invalid_output_safe_failure" in _audit_names(client, admin_headers, case_id)
    assert find_escalation(client, admin_headers, case_id, "LOW_CONFIDENCE")["queue"] == "LOW_CONFIDENCE"


@pytest.mark.parametrize("action", ["MONITOR", "NO_ACTION"])
def test_a_wait_recommendation_contacts_nobody_and_a_payment_still_recovers(client, admin_headers, monkeypatch, action):
    answer = {
        "diagnosis": "Bank downtime; the customer's own retry is likely.",
        "root_cause": "BANK_DOWNTIME",
        "recovery_probability": 0.9,
        "recommended_action": action,
        "reasoning": "Wait a day before contacting.",
        "confidence": 0.9,
    }
    _use_advisor(monkeypatch, LangGraphRecoveryAdvisor(Answers(answer)))
    customer = first_contactable_customer(client, admin_headers)
    case_id = deliver(client, failed_payment(customer, 1650)).json()["case_id"]

    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert case["status"] == "ACTIONABLE" and case["communications"] == [] and case["actions"] == []
    assert case["advisor_model"] == "scripted-model"
    assert "workflow.monitoring" in _audit_names(client, admin_headers, case_id)

    # An operator can still act, and the case is a normal open case.
    sent = client.post(
        f"/recovery-cases/{case_id}/execute", json={"action_type": "PAYMENT_LINK"}, headers=admin_headers
    )
    assert sent.status_code == 200, sent.text
    after = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert deliver(client, link_paid(after, 1650 * 100)).status_code == 200
    assert client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()["status"] == "RECOVERED"


def test_a_stop_recommendation_is_confirmed_by_a_person(client, admin_headers, monkeypatch):
    answer = {
        "diagnosis": "Customer disputes the charge.",
        "root_cause": "DISPUTE",
        "recovery_probability": 0.1,
        "recommended_action": "STOP",
        "reasoning": "Do not chase a disputed charge.",
        "confidence": 0.95,
    }
    _use_advisor(monkeypatch, LangGraphRecoveryAdvisor(Answers(answer)))
    case_id = deliver(client, failed_payment(first_contactable_customer(client, admin_headers), 1700)).json()["case_id"]
    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert case["status"] == "ESCALATED" and case["escalation"]["reason"] == "ADVISOR_RECOMMENDS_REVIEW"


def test_failure_lab_invalid_ai_runs_the_real_advisor_graph(client, admin_headers):
    case_id = deliver(client, failed_payment(first_contactable_customer(client, admin_headers), 1750)).json()["case_id"]
    response = client.post("/simulation/invalid-ai", json={"case_id": case_id}, headers=admin_headers)
    assert response.status_code == 200 and response.json()["status"] == "ESCALATED"

    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert case["advisor_model"] == "failure-lab-malformed-output"
    assert case["recommended_action"] == "ESCALATE_TO_HUMAN" and case["ai_confidence"] == 0.0
    assert "after 2 attempts" in case["ai_reasoning"]
    assert case["escalation"]["reason"].startswith("INVALID_AI_OUTPUT")


def test_failure_lab_scenarios_never_rewrite_a_closed_case(client, admin_headers):
    case_id = deliver(client, failed_payment(first_contactable_customer(client, admin_headers), 1800)).json()["case_id"]
    case = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert deliver(client, link_paid(case, 1800 * 100)).status_code == 200

    for scenario in ("low-confidence", "invalid-ai", "max-attempts"):
        response = client.post(f"/simulation/{scenario}", json={"case_id": case_id}, headers=admin_headers)
        assert response.status_code == 409, (scenario, response.text)
    assert client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()["status"] == "RECOVERED"
