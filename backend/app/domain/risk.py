"""Deterministic revenue risk scoring.

Produces a risk score (5-99), a recovery probability (0.15-0.96) and the
expected recovery value. Financial figures are always computed here, never by
the recovery advisor.
"""

from dataclasses import dataclass
from typing import Any

from app.domain.entities import CustomerProfile

# Assumed profile when the customer is unknown.
_UNKNOWN_RELIABILITY_PCT = 70.0
_UNKNOWN_HISTORICAL_RECOVERY = 0.5
_UNKNOWN_SUCCESSFUL_PAYMENTS = 5
_UNKNOWN_FAILED_PAYMENTS = 1

_EVENT_PROBABILITY_ADJUSTMENT = {
    "CHECKOUT_ABANDONED": -0.05,  # abandonment is harder to recover than a technical failure
    "SUBSCRIPTION_HALTED": 0.04,  # subscribers usually update their card quickly
}


@dataclass(frozen=True)
class RiskReason:
    """One plain-language reason behind a score; ``effect`` is how it moves the chance of recovery."""

    text: str
    effect: str  # "positive", "negative" or "neutral"


def explain_risk(factors: dict[str, Any]) -> list[RiskReason]:
    """Why a case is prioritised, from the factors the engine recorded. Missing factors are skipped."""
    reasons: list[RiskReason] = []
    reliability = factors.get("reliability_pct")
    if reliability is not None:
        if reliability >= 80:
            reasons.append(RiskReason(f"Strong payment history: {reliability:.0f}% of payments succeed", "positive"))
        elif reliability < 60:
            reasons.append(RiskReason(f"Unreliable payer: only {reliability:.0f}% of payments succeed", "negative"))
        else:
            reasons.append(RiskReason(f"Mixed payment history: {reliability:.0f}% of payments succeed", "neutral"))
    recovered = factors.get("historical_recovery_rate")
    if recovered is not None:
        if recovered >= 0.6:
            reasons.append(RiskReason(f"Earlier recoveries usually worked ({recovered:.0%})", "positive"))
        elif recovered < 0.3:
            reasons.append(RiskReason(f"Earlier recoveries rarely worked ({recovered:.0%})", "negative"))
    repeats = factors.get("repeat_failures") or 0
    if repeats:
        reasons.append(RiskReason(f"{repeats} repeated payment failure{'s' if repeats != 1 else ''}", "negative"))
    overdue = factors.get("overdue_days") or 0
    if overdue:
        reasons.append(RiskReason(f"Overdue by {overdue} day{'s' if overdue != 1 else ''}", "negative"))
    amount = factors.get("amount")
    if amount is not None and amount >= 25000:
        reasons.append(RiskReason(f"Large amount at stake (₹{amount:,})", "neutral"))
    event_type = factors.get("event_type")
    if event_type == "CHECKOUT_ABANDONED":
        reasons.append(RiskReason("Abandoned checkouts are harder to recover than failed payments", "negative"))
    elif event_type == "SUBSCRIPTION_HALTED":
        reasons.append(RiskReason("Subscribers usually update their payment method quickly", "positive"))
    return reasons


@dataclass(frozen=True)
class RiskResult:
    risk_score: int
    recovery_probability: float
    expected_recovery_value: int
    model_version: str
    factors: dict[str, Any]


class RiskEngine:
    MODEL_VERSION = "deterministic-v2.0"

    @classmethod
    def evaluate(
        cls,
        amount: int,
        customer: CustomerProfile | None,
        overdue_days: int = 0,
        repeat_failures: int = 0,
        event_type: str = "PAYMENT_FAILURE",
    ) -> RiskResult:
        reliability = customer.payment_reliability_pct if customer else _UNKNOWN_RELIABILITY_PCT
        historical_recovery = customer.historical_recovery_rate if customer else _UNKNOWN_HISTORICAL_RECOVERY
        successful = customer.successful_payments if customer else _UNKNOWN_SUCCESSFUL_PAYMENTS
        failed = customer.failed_payments if customer else _UNKNOWN_FAILED_PAYMENTS
        success_ratio = round(successful / max(1, successful + failed), 2)

        # Risk score: higher means more revenue at stake or less reliable payer.
        amount_factor = min(35.0, amount / 3000.0)
        unreliability_factor = (100.0 - reliability) * 0.35
        overdue_factor = min(25.0, overdue_days * 1.5)
        repeat_failure_factor = min(20.0, repeat_failures * 6.0)
        raw_risk = amount_factor + unreliability_factor + overdue_factor + repeat_failure_factor + 15.0
        risk_score = max(5, min(99, round(raw_risk)))

        # Recovery probability: reliability baseline adjusted by history and aging.
        probability = (
            0.40
            + reliability / 200.0
            + (historical_recovery - 0.5) * 0.15
            - overdue_days * 0.015
            - repeat_failures * 0.08
            - min(0.12, amount / 250000.0)
            + _EVENT_PROBABILITY_ADJUSTMENT.get(event_type, 0.0)
        )
        recovery_probability = max(0.15, min(0.96, round(probability, 2)))

        return RiskResult(
            risk_score=risk_score,
            recovery_probability=recovery_probability,
            expected_recovery_value=round(amount * recovery_probability),
            model_version=cls.MODEL_VERSION,
            factors={
                "amount": amount,
                "reliability_pct": reliability,
                "success_ratio": success_ratio,
                "historical_recovery_rate": historical_recovery,
                "overdue_days": overdue_days,
                "repeat_failures": repeat_failures,
                "event_type": event_type,
                "amount_factor": round(amount_factor, 1),
                "unreliability_factor": round(unreliability_factor, 1),
                "overdue_factor": round(overdue_factor, 1),
                "repeat_failure_factor": round(repeat_failure_factor, 1),
            },
        )
