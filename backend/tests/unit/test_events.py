import pytest

from app.domain.events import normalize_event
from app.domain.messages import compose_recovery_message


def _envelope(event: str, entity_name: str, **entity) -> dict:
    return {"entity": "event", "event": event, "payload": {entity_name: {"entity": entity}}}


def test_maps_razorpay_failure_and_converts_paise_to_rupees():
    result = normalize_event(
        _envelope("payment.failed", "payment", id="pay_123", customer_id="cus_123", amount=1800000)
    )
    assert result.event_type == "PAYMENT_FAILURE"
    assert result.source_id == "pay_123"
    assert result.customer_id == "cus_123"
    assert result.amount == 18000


def test_small_amounts_are_not_inflated():
    # Regression: 50,000 paise is ₹500, not ₹50,000.
    result = normalize_event(_envelope("payment.failed", "payment", id="pay_1", customer_id="c", amount=50000))
    assert result.amount == 500


def test_maps_checkout_abandonment():
    result = normalize_event(_envelope("checkout.abandoned", "checkout", id="chk_123", customer_id="c", amount=1800000))
    assert result.event_type == "CHECKOUT_ABANDONED"
    assert result.source_id == "chk_123"


def test_paid_links_report_the_amount_actually_paid():
    result = normalize_event(
        _envelope("payment_link.partially_paid", "payment_link", id="plink_2", amount=500000, amount_paid=200000)
    )
    assert (result.event_type, result.amount) == ("PAYMENT_LINK_PAID", 2000)


def test_authorization_is_not_a_recovery():
    result = normalize_event(_envelope("payment.authorized", "payment", id="pay_3", amount=50000))
    assert result.event_type == "PAYMENT_AUTHORIZED"
    assert result.is_informational and not result.is_payment_success


def test_maps_payment_link_abandonment():
    result = normalize_event(
        _envelope("payment_link.abandoned", "payment_link", id="plink_1", customer_id="c", amount=1800000)
    )
    assert result.event_type == "PAYMENT_LINK_ABANDONED"
    assert result.source_entity == "payment_link"


def test_entity_named_by_event_wins_over_attached_entities():
    body = {
        "event": "payment_link.paid",
        "payload": {
            "payment": {"entity": {"id": "pay_9", "amount": 500000}},
            "payment_link": {"entity": {"id": "plink_9", "amount": 500000, "notes": {"recoverx_case_id": "RC-1"}}},
        },
    }
    result = normalize_event(body)
    assert (result.source_entity, result.source_id) == ("payment_link", "plink_9")
    assert result.case_reference == "RC-1"


def test_rejects_unsupported_event():
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_event({"entity": "event", "event": "totally.unknown.event", "payload": {}})


def test_non_numeric_amounts_are_invalid_input_not_crashes():
    with pytest.raises(ValueError, match="must be a number"):
        normalize_event(_envelope("payment.failed", "payment", id="p1", amount="abc"))


@pytest.mark.parametrize("amount", [0, 99])
def test_rejects_amounts_below_one_rupee(amount):
    with pytest.raises(ValueError, match="at least one rupee"):
        normalize_event(_envelope("payment.failed", "payment", id="p1", customer_id="c1", amount=amount))


def test_canonical_payloads_keep_rupee_amounts():
    result = normalize_event({"event_type": "INVOICE_OVERDUE", "amount": 4200, "customer_id": "c1", "id": "inv_1"})
    assert (result.event_type, result.amount, result.source_id) == ("INVOICE_OVERDUE", 4200, "inv_1")


def test_hinglish_message_includes_the_provider_link():
    message = compose_recovery_message("Priya Nair", 2500, "Hinglish", link_url="https://rzp.io/i/abc")
    assert message.startswith("Hi Priya") and "https://rzp.io/i/abc" in message
