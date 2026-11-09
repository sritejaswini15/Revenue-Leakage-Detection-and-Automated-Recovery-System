"""Plain-language explanations and the merchant's channel and language choices."""

import pytest

from app.domain.policy import contact_channel, escalation_queue, message_language
from app.domain.risk import RiskEngine, explain_risk
from tests.helpers import make_customer


def test_a_reliable_customer_is_explained_as_such():
    reasons = explain_risk(RiskEngine.evaluate(18000, make_customer()).factors)
    assert reasons[0].effect == "positive" and "91%" in reasons[0].text
    assert any("Earlier recoveries usually worked" in reason.text for reason in reasons)


def test_risks_are_called_out():
    factors = RiskEngine.evaluate(
        40000,
        make_customer(payment_reliability_pct=40.0, historical_recovery_rate=0.1),
        overdue_days=9,
        repeat_failures=2,
    ).factors
    texts = {reason.text: reason.effect for reason in explain_risk(factors)}
    assert texts["Unreliable payer: only 40% of payments succeed"] == "negative"
    assert texts["2 repeated payment failures"] == "negative"
    assert texts["Overdue by 9 days"] == "negative"
    assert "Large amount at stake (₹40,000)" in texts


def test_missing_factors_are_skipped():
    assert explain_risk({}) == []


@pytest.mark.parametrize(
    ("customer", "merchant", "expected"),
    [
        ("whatsapp", "whatsapp,email", "whatsapp"),
        ("whatsapp", "email", "email"),  # the merchant has switched WhatsApp off
        ("email", "whatsapp", "whatsapp"),
        ("EMAIL", " Email , WhatsApp ", "email"),
        (None, "email,whatsapp", "email"),
        ("whatsapp", "sms", None),  # nothing RecoverX can send on
        ("whatsapp", "", None),
    ],
)
def test_contact_channel(customer, merchant, expected):
    assert contact_channel(customer, merchant) == expected


@pytest.mark.parametrize(
    ("customer", "merchant", "expected"),
    [
        ("Hinglish", "English,Hinglish", "Hinglish"),
        ("hinglish", "English", "English"),
        ("Tamil", "Hinglish,English", "Hinglish"),
        ("English", "", "English"),
    ],
)
def test_message_language(customer, merchant, expected):
    assert message_language(customer, merchant) == expected


@pytest.mark.parametrize(
    ("reason", "queue"),
    [
        ("AMOUNT_EXCEEDS_AUTO_LIMIT", "HIGH_VALUE"),
        ("LOW_AI_CONFIDENCE,AMOUNT_EXCEEDS_AUTO_LIMIT", "HIGH_VALUE"),  # the higher-priority queue wins
        ("LOW_AI_CONFIDENCE", "LOW_CONFIDENCE"),
        ("MISSED_PROMISE_TO_PAY", "PROMISE_MISSED"),
        ("MAX_ATTEMPTS", "MAX_ATTEMPTS"),
        ("OPERATOR_REQUEST", "CUSTOMER_REQUEST"),
        ("OVERDUE_DAYS_EXCEEDED", "SPECIAL_CASE"),
        (None, "SPECIAL_CASE"),
    ],
)
def test_escalation_queue(reason, queue):
    assert escalation_queue(reason) == queue
