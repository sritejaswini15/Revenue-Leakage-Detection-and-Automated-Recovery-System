from app.domain.risk import RiskEngine
from tests.helpers import make_customer


def test_expected_recovery_follows_probability():
    result = RiskEngine.evaluate(18000, make_customer())
    assert result.risk_score == 24
    assert result.recovery_probability == 0.84
    assert result.expected_recovery_value == 15120


def test_is_deterministic():
    customer = make_customer()
    assert RiskEngine.evaluate(18000, customer) == RiskEngine.evaluate(18000, customer)


def test_lower_reliability_means_higher_risk():
    unreliable = RiskEngine.evaluate(18000, make_customer(payment_reliability_pct=30.0))
    reliable = RiskEngine.evaluate(18000, make_customer(payment_reliability_pct=95.0))
    assert unreliable.risk_score > reliable.risk_score
    assert unreliable.recovery_probability < reliable.recovery_probability


def test_expected_value_is_amount_times_probability():
    result = RiskEngine.evaluate(10000, make_customer())
    assert result.expected_recovery_value == round(10000 * result.recovery_probability)


def test_probability_is_bounded():
    worst = RiskEngine.evaluate(500000, make_customer(payment_reliability_pct=0.0), overdue_days=90, repeat_failures=9)
    assert worst.recovery_probability == 0.15
    assert 5 <= worst.risk_score <= 99
