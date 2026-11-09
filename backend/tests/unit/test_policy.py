import pytest

from app.domain.policy import PolicyEngine
from tests.helpers import make_case, make_customer, make_policy


def test_stops_opted_out_customer():
    result = PolicyEngine.evaluate(make_policy(), make_case(), make_customer(opted_out=True))
    assert result.decision == "STOP"
    assert result.rule_violations == ["CUSTOMER_OPTED_OUT"]


@pytest.mark.parametrize("status", ["RECOVERED", "STOPPED", "EXPIRED"])
def test_stops_terminal_cases(status):
    result = PolicyEngine.evaluate(make_policy(), make_case(status=status), make_customer())
    assert result.decision == "STOP"
    assert "CASE_ALREADY_TERMINATED" in result.rule_violations


def test_stops_at_max_contact_attempts():
    result = PolicyEngine.evaluate(make_policy(max_contact_attempts=3), make_case(contact_attempts=3), make_customer())
    assert result.decision == "STOP"
    assert "MAX_ATTEMPTS_EXCEEDED" in result.rule_violations


def test_requires_review_above_amount_limit():
    result = PolicyEngine.evaluate(make_policy(), make_case(amount=30000), make_customer())
    assert result.decision == "HUMAN_REVIEW"
    assert "AMOUNT_EXCEEDS_AUTO_LIMIT" in result.rule_violations


def test_requires_review_on_low_confidence():
    result = PolicyEngine.evaluate(
        make_policy(min_ai_confidence=0.80), make_case(), make_customer(), ai_confidence=0.60
    )
    assert result.decision == "HUMAN_REVIEW"
    assert "LOW_AI_CONFIDENCE" in result.rule_violations


def test_requires_review_when_overdue_too_long():
    result = PolicyEngine.evaluate(
        make_policy(escalate_after_days=7), make_case(amount=5000), make_customer(), overdue_days=8
    )
    assert result.decision == "HUMAN_REVIEW"
    assert result.rule_violations == ["OVERDUE_DAYS_EXCEEDED"]


def test_collects_every_review_reason():
    result = PolicyEngine.evaluate(
        make_policy(), make_case(amount=30000), make_customer(), ai_confidence=0.5, overdue_days=30
    )
    assert result.rule_violations == ["AMOUNT_EXCEEDS_AUTO_LIMIT", "LOW_AI_CONFIDENCE", "OVERDUE_DAYS_EXCEEDED"]


def test_auto_approves_within_limits():
    result = PolicyEngine.evaluate(make_policy(), make_case(amount=5000), make_customer())
    assert result.decision == "AUTO_APPROVE"
    assert result.rule_violations == []
