import pytest

from app.domain.constants import ActionType
from app.domain.decisions import RecoveryContext
from app.infrastructure.ai.recovery_graph import LangGraphRecoveryAdvisor


def _context(**overrides) -> RecoveryContext:
    values = {
        "event_type": "PAYMENT_FAILURE",
        "amount": 18000,
        "currency": "INR",
        "customer_name": "Rahul Sharma",
        "customer_reliability": 91.0,
        "successful_payments": 18,
        "failed_payments": 1,
        "preferred_channel": "whatsapp",
        "preferred_language": "English",
    }
    return RecoveryContext(**{**values, **overrides})


def test_reliable_customer_gets_a_payment_link():
    decision = LangGraphRecoveryAdvisor().advise(_context())
    assert decision.recommended_action == ActionType.PAYMENT_LINK
    assert decision.root_cause == "ISOLATED_PAYMENT_METHOD_ISSUE"
    assert decision.confidence == 0.91


@pytest.mark.parametrize(
    ("event_type", "action", "root_cause"),
    [
        ("INVOICE_OVERDUE", ActionType.REMINDER, "B2B_PAYMENT_DELAY"),
        ("SUBSCRIPTION_HALTED", ActionType.PAYMENT_RETRY, "MANDATE_DECLINE"),
        ("CHECKOUT_ABANDONED", ActionType.PAYMENT_LINK, "CHECKOUT_DROPOFF"),
    ],
)
def test_event_type_drives_strategy(event_type, action, root_cause):
    decision = LangGraphRecoveryAdvisor().advise(_context(event_type=event_type))
    assert (decision.recommended_action, decision.root_cause) == (action, root_cause)


def test_high_value_unreliable_customer_is_escalated():
    decision = LangGraphRecoveryAdvisor().advise(_context(customer_reliability=50.0, amount=80000))
    assert decision.recommended_action == ActionType.ESCALATE_TO_HUMAN
    assert decision.confidence < 0.70
