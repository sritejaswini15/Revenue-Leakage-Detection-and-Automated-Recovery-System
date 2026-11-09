"""Customer payment reliability: the merchant's profile combined with payments RecoverX observed.

The profile (``payment_reliability_pct``, ``successful_payments``, ``failed_payments``) comes
from the merchant's import or the demo data and is never changed by webhooks. Payments that
RecoverX sees are counted separately from the payments table, by each payment's final status,
so a payment is never counted twice and the combined figures can be recomputed at any time.
"""

from dataclasses import dataclass

from app.domain.entities import CustomerProfile

# A thin or empty profile still carries the weight of this many payments, so one observed
# payment cannot swing a new customer's reliability from 50% to 100%.
PRIOR_MIN_WEIGHT = 4


@dataclass(frozen=True)
class ObservedPayments:
    successful: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.successful + self.failed


@dataclass(frozen=True)
class CombinedProfile:
    """Satisfies ``CustomerProfile``; what the risk engine, advisor and policy see."""

    payment_reliability_pct: float
    historical_recovery_rate: float
    successful_payments: int
    failed_payments: int
    opted_out: bool


def combine(profile: CustomerProfile, observed: ObservedPayments) -> CombinedProfile:
    """Bayesian blend: the profile is the prior, each observed payment is one more data point."""
    prior_weight = max(profile.successful_payments + profile.failed_payments, PRIOR_MIN_WEIGHT)
    prior_successes = profile.payment_reliability_pct / 100 * prior_weight
    reliability = (prior_successes + observed.successful) / (prior_weight + observed.total) * 100
    return CombinedProfile(
        payment_reliability_pct=round(reliability, 1),
        historical_recovery_rate=profile.historical_recovery_rate,
        successful_payments=profile.successful_payments + observed.successful,
        failed_payments=profile.failed_payments + observed.failed,
        opted_out=profile.opted_out,
    )
