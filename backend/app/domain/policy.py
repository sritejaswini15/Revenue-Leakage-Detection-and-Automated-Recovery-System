"""Deterministic merchant policy guardrails.

Every recovery action is authorized here before it reaches a provider.
Rules are evaluated in order; the first STOP rule wins, otherwise any
human-review rule escalates, otherwise the action is auto-approved.
"""

from dataclasses import dataclass

from app.domain.constants import TERMINAL_CASE_STATUSES, PolicyDecision
from app.domain.entities import CaseState, CustomerProfile, PolicyRules

CHANNELS = ("whatsapp", "email")
LANGUAGES = ("English", "Hinglish")


def parse_list(value: str | None) -> list[str]:
    """A merchant setting stored as a comma-separated list, in the merchant's order of preference."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def contact_channel(customer_channel: str | None, merchant_channels: str | None) -> str | None:
    """The customer's preferred channel if the merchant permits it, else the merchant's first choice.

    None means the merchant permits no channel RecoverX can send on, so nobody may be contacted.
    """
    permitted = [channel.lower() for channel in parse_list(merchant_channels) if channel.lower() in CHANNELS]
    preferred = (customer_channel or "").strip().lower()
    if preferred in permitted:
        return preferred
    return permitted[0] if permitted else None


def message_language(customer_language: str | None, merchant_languages: str | None) -> str:
    """The customer's language if the merchant supports it, else the merchant's first supported language."""
    supported = {item.lower(): item for item in LANGUAGES}
    permitted = [supported[item.lower()] for item in parse_list(merchant_languages) if item.lower() in supported]
    preferred = (customer_language or "").strip().lower()
    if preferred in supported and supported[preferred] in permitted:
        return supported[preferred]
    return permitted[0] if permitted else LANGUAGES[0]


# Human-review queues, in priority order, and the escalation reason codes that land in each.
ESCALATION_QUEUES: dict[str, frozenset[str]] = {
    "HIGH_VALUE": frozenset({"AMOUNT_EXCEEDS_AUTO_LIMIT"}),
    "LOW_CONFIDENCE": frozenset({"LOW_AI_CONFIDENCE", "LOW_CONFIDENCE", "INVALID_AI_OUTPUT"}),
    "PROMISE_MISSED": frozenset({"MISSED_PROMISE_TO_PAY"}),
    "MAX_ATTEMPTS": frozenset({"MAX_ATTEMPTS", "MAX_ATTEMPTS_EXCEEDED", "FOLLOW_UP_NEEDS_REVIEW"}),
    "PROVIDER_FAILURE": frozenset({"PROVIDER_FAILURE"}),
    "CUSTOMER_REQUEST": frozenset({"CUSTOMER_REQUEST", "OPERATOR_REQUEST", "MCP_OPERATOR_REQUEST"}),
}
SPECIAL_CASE_QUEUE = "SPECIAL_CASE"


def escalation_queue(reason: str | None) -> str:
    """The review queue for a (possibly comma-separated) escalation reason; unknown reasons are special cases."""
    codes = {code.strip().upper() for code in (reason or "").split(",") if code.strip()}
    return next((queue for queue, members in ESCALATION_QUEUES.items() if codes & members), SPECIAL_CASE_QUEUE)


@dataclass(frozen=True)
class PolicyEvaluationResult:
    decision: PolicyDecision
    reasons: list[str]
    rule_violations: list[str]
    max_amount_limit: int
    min_confidence_limit: float
    current_attempts: int
    max_attempts_limit: int


class PolicyEngine:
    @classmethod
    def evaluate(
        cls,
        policy: PolicyRules,
        case: CaseState,
        customer: CustomerProfile | None,
        ai_confidence: float | None = None,
        overdue_days: int = 0,
    ) -> PolicyEvaluationResult:
        confidence = ai_confidence if ai_confidence is not None else case.ai_confidence

        def result(
            decision: PolicyDecision, reasons: list[str], violations: list[str], amount_limit: int
        ) -> PolicyEvaluationResult:
            return PolicyEvaluationResult(
                decision=decision,
                reasons=reasons,
                rule_violations=violations,
                max_amount_limit=amount_limit,
                min_confidence_limit=policy.min_ai_confidence,
                current_attempts=case.contact_attempts,
                max_attempts_limit=policy.max_contact_attempts,
            )

        stop = cls._stop_rule(policy, case, customer)
        if stop:
            reason, violation = stop
            return result(PolicyDecision.STOP, [reason], [violation], policy.max_auto_action_amount)

        reasons: list[str] = []
        violations: list[str] = []
        amount_limit = min(policy.max_auto_action_amount, policy.human_approval_above_amount)

        if case.amount > amount_limit:
            reasons.append(f"Amount ₹{case.amount:,} exceeds auto-action limit of ₹{amount_limit:,}")
            violations.append("AMOUNT_EXCEEDS_AUTO_LIMIT")
        else:
            reasons.append(f"Amount ₹{case.amount:,} is within auto-action limit of ₹{amount_limit:,}")

        confidence_pct, threshold_pct = int(confidence * 100), int(policy.min_ai_confidence * 100)
        if confidence < policy.min_ai_confidence:
            reasons.append(f"Advisor confidence ({confidence_pct}%) is below required threshold ({threshold_pct}%)")
            violations.append("LOW_AI_CONFIDENCE")
        else:
            reasons.append(f"Advisor confidence ({confidence_pct}%) meets threshold ({threshold_pct}%)")

        if overdue_days > policy.escalate_after_days:
            reasons.append(
                f"Case overdue duration ({overdue_days} days) exceeds escalation threshold "
                f"({policy.escalate_after_days} days)"
            )
            violations.append("OVERDUE_DAYS_EXCEEDED")

        if violations:
            return result(PolicyDecision.HUMAN_REVIEW, reasons, violations, amount_limit)

        reasons.append("All policy guardrails satisfied. Action approved for automatic execution.")
        return result(PolicyDecision.AUTO_APPROVE, reasons, [], amount_limit)

    @staticmethod
    def _stop_rule(policy: PolicyRules, case: CaseState, customer: CustomerProfile | None) -> tuple[str, str] | None:
        if customer and customer.opted_out:
            return "Customer has explicitly opted out of communications", "CUSTOMER_OPTED_OUT"
        if case.status in TERMINAL_CASE_STATUSES:
            return f"Case is already in terminal state: {case.status}", "CASE_ALREADY_TERMINATED"
        if case.contact_attempts >= policy.max_contact_attempts:
            return (
                f"Maximum contact attempts reached ({case.contact_attempts}/{policy.max_contact_attempts})",
                "MAX_ATTEMPTS_EXCEEDED",
            )
        return None
