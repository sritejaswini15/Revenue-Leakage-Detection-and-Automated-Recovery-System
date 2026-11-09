"""Claude as the recovery advisor's reasoner (LLM_PROVIDER=anthropic).

Claude sees the canonical case context RecoverX built from its own records: amounts, the
deterministic risk figures and the customer's payment and recovery history. It never sees raw
Razorpay payloads or the customer's name, email or phone number. It answers with a structured
decision (``output_config.format``), which the graph validates; it has no tools and no way to act.

Failures map onto the graph's two outcomes: an answer that is not a usable decision is
``InvalidAdvisorOutputError`` (retried once), anything that prevents an answer (timeout,
rate limit, provider error, a safety refusal) is ``AdvisorUnavailableError`` (safe failure).
"""

import json
import logging
from typing import Any

import anthropic

from app.domain.constants import ActionType
from app.domain.policy import LANGUAGES
from app.infrastructure.ai.recovery_graph import (
    AdvisorUnavailableError,
    InvalidAdvisorOutputError,
    RecoveryAgentState,
)

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000  # thinking counts against this; the decision itself is a few hundred tokens
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are the recovery advisor in RecoverX, a revenue recovery system for merchants \
in India who take payments through Razorpay. For one case of revenue at risk (a failed payment, a halted \
subscription, an overdue invoice or an abandoned checkout or payment link) you diagnose what most likely \
happened and recommend the single next recovery action.

Your recommendation is advice. A deterministic policy engine decides whether it may run, and a separate \
gateway executes it, so recommend what is best for recovering the money while treating the customer fairly; \
do not try to anticipate the merchant's limits. Amounts, risk score and expected recovery value come from \
RecoverX's records and are authoritative: use them, never recompute or restate them differently.

Actions you can recommend:
- PAYMENT_LINK: send a payment link on the customer's channel. The usual choice for a one-off failure.
- PAYMENT_RETRY: retry the failed charge. Suits mandate or card failures on reliable payers.
- REMINDER: a polite reminder with a payment link. Suits overdue invoices and business customers.
- PERSONALIZED_OUTREACH: a message tailored to the customer's history, with a payment link.
- CHECKOUT_RECOVERY: a link that brings an abandoned checkout back.
- FOLLOW_UP: a second contact after an earlier one went unanswered.
- PROMISE_TO_PAY: ask the customer to commit to a payment date.
- MONITOR or NO_ACTION: wait; the money is likely to arrive without contact.
- ESCALATE_TO_HUMAN: a person should decide (unusual, ambiguous, high-stakes, or the history argues against \
automated contact).
- STOP: recovery should not continue.

Weigh the customer's own history: a reliable payer with one failure is usually a payment-method problem; \
repeated failures, long overdue periods and weak past recoveries lower the odds. If one action has worked \
for this customer before, prefer it unless the situation clearly differs.

confidence is how sure you are that the recommended action is the right next step, from 0 to 1; say so \
honestly, because low confidence sends the case to a person. recovery_probability is your own estimate \
that the money is recovered. Keep diagnosis and reasoning to one or two sentences each, written for the \
merchant's operations team. suggested_language is the language to message the customer in."""

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string", "description": "What most likely happened, in one or two sentences"},
        "root_cause": {"type": "string", "description": "Short UPPER_SNAKE_CASE category, e.g. CARD_DECLINED"},
        "recovery_probability": {"type": "number", "description": "0 to 1"},
        "recommended_action": {"type": "string", "enum": [action.value for action in ActionType]},
        "reasoning": {"type": "string", "description": "Why this action, in one or two sentences"},
        "confidence": {"type": "number", "description": "0 to 1"},
        "communication_tone": {"type": "string", "description": "e.g. empathetic, professional, urgent"},
        "suggested_language": {"type": "string", "enum": list(LANGUAGES)},
    },
    "required": [
        "diagnosis",
        "root_cause",
        "recovery_probability",
        "recommended_action",
        "reasoning",
        "confidence",
        "communication_tone",
        "suggested_language",
    ],
    "additionalProperties": False,
}


def case_facts(state: RecoveryAgentState) -> dict[str, Any]:
    """What Claude is told about the case. No personal data: the decision does not need it."""
    successful, failed = state.get("successful_payments", 0), state.get("failed_payments", 0)
    return {
        "revenue_event": state.get("event_type"),
        "amount_at_risk": {"value": state.get("amount"), "currency": state.get("currency", "INR")},
        "risk_engine": {
            "risk_score_0_to_100": state.get("engine_risk_score"),
            "recovery_probability": state.get("engine_recovery_probability"),
            "expected_recovery_value": state.get("engine_expected_recovery_value"),
        },
        "days_overdue": state.get("overdue_days", 0),
        "repeat_failures_for_this_payment": state.get("repeat_failures", 0),
        "customer": {
            "payment_reliability_pct": state.get("customer_reliability"),
            "successful_payments": successful,
            "failed_payments": failed,
            "lifetime_value": state.get("lifetime_value", 0),
            "previous_recovery_attempts": state.get("previous_recovery_attempts", 0),
            "historical_recovery_rate": state.get("historical_recovery_rate", 0.0),
            "best_historical_recovery_action": state.get("best_historical_recovery_action"),
            "preferred_channel": state.get("preferred_channel"),
            "preferred_language": state.get("preferred_language"),
        },
    }


class ClaudeReasoner:
    def __init__(
        self,
        model: str,
        effort: str = "low",
        refusal_fallback: bool = True,
        client: anthropic.Anthropic | None = None,
        timeout: float = 20.0,
        max_retries: int = 1,
    ) -> None:
        self.name = model
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        # Credentials come from the environment (ANTHROPIC_API_KEY, or an `ant auth login` profile).
        self.client = client or anthropic.Anthropic(timeout=timeout, max_retries=max_retries)

    def reason(self, state: RecoveryAgentState, feedback: str | None) -> dict[str, Any]:
        content = "Recovery case:\n" + json.dumps(case_facts(state), indent=2)
        if feedback:
            content += f"\n\nYour previous answer was rejected ({feedback}). Answer again with a corrected decision."
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
        }
        if self.refusal_fallback:
            # On a safety decline the API re-runs the request on a fallback model within the same call.
            request |= {"betas": [REFUSAL_FALLBACK_BETA], "fallbacks": "default"}

        try:
            response = self.client.beta.messages.create(**request)
        except anthropic.APITimeoutError as exc:
            raise AdvisorUnavailableError("the advisor model timed out") from exc
        except anthropic.RateLimitError as exc:
            raise AdvisorUnavailableError("the advisor model is rate limited") from exc
        except anthropic.APIStatusError as exc:
            logger.warning("Advisor request failed (%s, request %s): %s", exc.status_code, exc.request_id, exc.message)
            raise AdvisorUnavailableError(f"the advisor model returned HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise AdvisorUnavailableError("the advisor model could not be reached") from exc

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            raise AdvisorUnavailableError(f"the advisor model declined the request ({category or 'no category'})")
        if response.stop_reason == "max_tokens":
            raise InvalidAdvisorOutputError("the answer was cut off before it was complete")
        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            decision = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidAdvisorOutputError(f"the answer was not valid JSON ({exc.msg})") from exc
        if not isinstance(decision, dict):
            raise InvalidAdvisorOutputError("the answer was not a JSON object")
        if response.model != self.model and isinstance(decision.get("reasoning"), str):
            decision["reasoning"] += f" (answered by {response.model} after a fallback)"
        return decision
