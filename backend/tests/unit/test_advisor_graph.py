"""The advisor graph's validate, retry-once and safe-failure paths, and the Claude reasoner's wire format."""

import json
from typing import Any

import anthropic
import httpx2
import pytest

from app.domain.constants import ActionType
from app.domain.decisions import ADVISOR_OUTPUT_INVALID, RecoveryContext
from app.infrastructure.ai.claude_reasoner import REFUSAL_FALLBACK_BETA, ClaudeReasoner
from app.infrastructure.ai.recovery_graph import (
    AdvisorUnavailableError,
    InvalidAdvisorOutputError,
    LangGraphRecoveryAdvisor,
    MalformedOutputReasoner,
)

VALID = {
    "diagnosis": "Card declined for insufficient balance.",
    "root_cause": "INSUFFICIENT_FUNDS",
    "recovery_probability": 0.8,
    "recommended_action": "PAYMENT_LINK",
    "reasoning": "Reliable payer; a link on WhatsApp usually works.",
    "confidence": 0.86,
    "communication_tone": "empathetic",
    "suggested_language": "Hinglish",
}


def _context(**overrides: Any) -> RecoveryContext:
    values = {
        "event_type": "PAYMENT_FAILURE",
        "amount": 18000,
        "currency": "INR",
        "customer_name": "Rahul Sharma",
        "customer_reliability": 91.0,
        "successful_payments": 18,
        "failed_payments": 1,
        "preferred_channel": "whatsapp",
        "preferred_language": "Hinglish",
        "risk_score": 24,
        "recovery_probability": 0.84,
        "expected_recovery_value": 15120,
    }
    return RecoveryContext(**{**values, **overrides})


class Scripted:
    """A reasoner that plays back answers (dicts) or raises (exceptions), recording the feedback it got."""

    name = "scripted"

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.feedback: list[str | None] = []

    def reason(self, state, feedback):  # noqa: ARG002 - the Reasoner signature
        self.feedback.append(feedback)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_valid_answer_is_used_as_is():
    reasoner = Scripted(VALID)
    decision = LangGraphRecoveryAdvisor(reasoner).advise(_context())
    assert decision.recommended_action == ActionType.PAYMENT_LINK and decision.confidence == 0.86
    assert reasoner.feedback == [None]


@pytest.mark.parametrize(
    "bad",
    [
        {**VALID, "confidence": 1.7},
        {**VALID, "recovery_probability": -0.1},
        {**VALID, "recommended_action": "WIRE_TRANSFER"},
        {key: value for key, value in VALID.items() if key != "reasoning"},
    ],
)
def test_an_invalid_answer_is_retried_once_with_the_error(bad):
    reasoner = Scripted(bad, VALID)
    decision = LangGraphRecoveryAdvisor(reasoner).advise(_context())
    assert decision.recommended_action == ActionType.PAYMENT_LINK
    assert reasoner.feedback[0] is None and "invalid decision" in reasoner.feedback[1]


def test_two_invalid_answers_fail_safe_to_human_review():
    reasoner = Scripted(InvalidAdvisorOutputError("not JSON"), {**VALID, "confidence": 3})
    decision = LangGraphRecoveryAdvisor(reasoner).advise(_context())
    assert decision.recommended_action == ActionType.ESCALATE_TO_HUMAN
    assert decision.confidence == 0.0 and decision.root_cause == ADVISOR_OUTPUT_INVALID
    assert decision.is_safe_failure and "after 2 attempts" in decision.reasoning
    assert len(reasoner.feedback) == 2


def test_an_unavailable_model_fails_safe_without_retrying():
    reasoner = Scripted(AdvisorUnavailableError("the advisor model timed out"))
    decision = LangGraphRecoveryAdvisor(reasoner).advise(_context())
    assert decision.is_safe_failure and "timed out" in decision.reasoning
    assert len(reasoner.feedback) == 1


def test_the_failure_lab_reasoner_is_always_rejected():
    decision = LangGraphRecoveryAdvisor(MalformedOutputReasoner()).advise(_context())
    assert decision.is_safe_failure and decision.recommended_action == ActionType.ESCALATE_TO_HUMAN


def test_rules_use_what_worked_for_the_customer_before():
    advisor = LangGraphRecoveryAdvisor()
    decision = advisor.advise(_context(best_historical_recovery_action="REMINDER", historical_recovery_rate=0.82))
    assert decision.recommended_action == ActionType.REMINDER and "82%" in decision.reasoning
    weak = advisor.advise(_context(best_historical_recovery_action="REMINDER", historical_recovery_rate=0.3))
    assert weak.recommended_action == ActionType.PAYMENT_LINK
    retry = advisor.advise(_context(best_historical_recovery_action="PAYMENT_RETRY", historical_recovery_rate=0.9))
    assert retry.recommended_action == ActionType.PAYMENT_LINK  # one-off payments are not re-charged


# --- the Claude reasoner, through the real SDK against a recorded-style HTTP exchange ---------------


def _claude(handler) -> LangGraphRecoveryAdvisor:
    client = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return LangGraphRecoveryAdvisor(ClaudeReasoner(model="claude-opus-5-5", effort="low", client=client))


def _message(text: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5", **extra: Any) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": 120},
        **extra,
    }


def test_claude_gets_a_structured_request_without_personal_data():
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_message(json.dumps(VALID)))

    decision = _claude(handler).advise(_context())

    assert decision.suggested_language == "Hinglish" and decision.confidence == 0.86
    request = seen[0]
    assert request.url.path == "/v1/messages"
    assert REFUSAL_FALLBACK_BETA in request.headers["anthropic-beta"]
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5-5" and "thinking" not in body
    assert body["fallbacks"] == "default"
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]["schema"]
    assert body["output_config"]["format"]["type"] == "json_schema" and schema["additionalProperties"] is False
    assert set(schema["properties"]["recommended_action"]["enum"]) == {action.value for action in ActionType}
    prompt = body["messages"][0]["content"]
    assert "15120" in prompt and "Rahul" not in prompt and "Sharma" not in prompt


def test_claude_output_that_is_not_json_is_retried_then_fails_safe():
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(json.loads(request.content)["messages"][0]["content"])
        return httpx2.Response(200, json=_message("I think a payment link is best."))

    decision = _claude(handler).advise(_context())
    assert decision.is_safe_failure and len(calls) == 2
    assert "previous answer was rejected" in calls[1]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}), "529"),
        (
            httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}),
            "rate",
        ),
        (httpx2.Response(200, json=_message("", stop_reason="refusal")), "declined"),
    ],
)
def test_claude_errors_and_refusals_fail_safe(response, expected):
    decision = _claude(lambda _request: response).advise(_context())
    assert decision.is_safe_failure and expected in decision.reasoning


def test_a_fallback_answer_says_which_model_answered():
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_message(json.dumps(VALID), model="claude-opus-4-8"))

    decision = _claude(handler).advise(_context())
    assert "answered by claude-opus-4-8" in decision.reasoning


# --- configuration -------------------------------------------------------------------------------


def test_llm_provider_selects_the_reasoner(monkeypatch):
    from app import container
    from app.core.config import settings
    from app.infrastructure.ai.recovery_graph import RulesReasoner

    build = container.recovery_advisor.__wrapped__  # bypass the process-wide cache
    monkeypatch.setattr(settings, "LLM_PROVIDER", "rules")
    assert isinstance(build().reasoner, RulesReasoner) and build().model_name == "langgraph-v1"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "LLM_MODEL", "claude-opus-5-5")
    advisor = build()
    assert isinstance(advisor.reasoner, ClaudeReasoner) and advisor.model_name == "claude-opus-5-5"


@pytest.mark.parametrize(
    ("provider", "key", "status", "label"),
    [
        ("rules", None, "NOT_CONFIGURED", "Rules only"),
        ("anthropic", None, "NOT_CONFIGURED", "No credentials"),
        ("anthropic", "test-key-not-real", "CONFIGURED", "Claude"),
    ],
)
def test_the_integrations_page_reports_the_advisor_truthfully(monkeypatch, provider, key, status, label):
    from app.application import integrations
    from app.core.config import settings

    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", key)
    entry = integrations._advisor()
    assert (entry["status"], entry["label"]) == (status, label)
    assert key is None or key not in entry["detail"], "the key itself is never shown"
