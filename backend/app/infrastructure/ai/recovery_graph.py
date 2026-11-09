"""LangGraph recovery advisor.

A stateful decision graph that recommends, and never executes:

    load_context -> risk_analysis -> diagnosis_and_strategy -> validate -> END
                                          ^                       |
                                          +---- retry once -------+--> safe_failure -> END

``diagnosis_and_strategy`` asks a *reasoner*: RecoverX's deterministic rules, or Claude
(``claude_reasoner.py``). Whatever it returns is validated against ``RecoveryDecision``. Invalid
output is retried once with the validation error as feedback; a second failure, or a reasoner
that is unavailable (timeout, refusal, provider error), ends in a safe failure: ESCALATE_TO_HUMAN
at zero confidence, which merchant policy always routes to a person. Authorization is done by the
domain PolicyEngine and execution by the Tool Gateway, so no output of this graph can move money.
"""

from dataclasses import asdict
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.domain.constants import LINK_ACTIONS, ActionType
from app.domain.decisions import RecoveryContext, RecoveryDecision, safe_failure_decision
from app.domain.risk import RiskEngine

RULES_MODEL_NAME = "langgraph-v1"
MAX_ATTEMPTS = 2  # the first answer, and one retry


class InvalidAdvisorOutputError(Exception):
    """The reasoner answered, but not with a usable decision. Worth one retry."""


class AdvisorUnavailableError(Exception):
    """The reasoner could not answer (timeout, refusal, provider error). Not retried."""


class Reasoner(Protocol):
    """Diagnoses the case and picks a strategy. Returns the decision fields, unvalidated."""

    name: str

    def reason(self, state: "RecoveryAgentState", feedback: str | None) -> dict[str, Any]: ...


class RecoveryAgentState(TypedDict, total=False):
    # Context
    event_type: str
    amount: int
    currency: str
    customer_name: str
    customer_reliability: float
    successful_payments: int
    failed_payments: int
    preferred_channel: str
    preferred_language: str
    overdue_days: int
    repeat_failures: int
    historical_recovery_rate: float
    previous_recovery_attempts: int
    best_historical_recovery_action: str | None
    lifetime_value: int
    # The deterministic risk engine's figures, as passed in by the caller (financial truth)
    engine_risk_score: int | None
    engine_recovery_probability: float | None
    engine_expected_recovery_value: int | None
    # The advisor's own severity estimate
    risk_score: int
    recovery_probability: float
    expected_recovery_value: int
    risk_factors: dict[str, Any]
    # Reasoning
    attempts: int
    raw_decision: dict[str, Any] | None
    errors: list[str]
    unavailable: str | None
    decision: dict[str, Any]


def load_context(state: RecoveryAgentState) -> dict[str, Any]:
    return {
        "customer_reliability": state.get("customer_reliability", 85.0),
        "repeat_failures": state.get("repeat_failures", 0),
        "overdue_days": state.get("overdue_days", 0),
        "engine_risk_score": state.get("risk_score"),
        "engine_recovery_probability": state.get("recovery_probability"),
        "engine_expected_recovery_value": state.get("expected_recovery_value"),
        "attempts": 0,
        "errors": [],
        "unavailable": None,
        "raw_decision": None,
    }


def risk_analysis(state: RecoveryAgentState) -> dict[str, Any]:
    """Score severity with the domain RiskEngine and estimate the advisor's recovery likelihood."""
    amount = state.get("amount", 10000)
    reliability = state.get("customer_reliability", 85.0)
    overdue_days = state.get("overdue_days", 0)
    repeat_failures = state.get("repeat_failures", 0)

    risk = RiskEngine.evaluate(
        amount=amount,
        customer=None,
        overdue_days=overdue_days,
        repeat_failures=repeat_failures,
        event_type=state.get("event_type", "PAYMENT_FAILURE"),
    )
    probability = max(
        0.18,
        min(0.95, round(0.45 + (reliability / 200.0) - (repeat_failures * 0.07) - (overdue_days * 0.01), 2)),
    )
    return {
        "risk_score": risk.risk_score,
        "recovery_probability": probability,
        "expected_recovery_value": round(amount * probability),
        "risk_factors": risk.factors,
    }


def _make_reason_node(reasoner: Reasoner):  # noqa: ANN202 - LangGraph node
    def diagnosis_and_strategy(state: RecoveryAgentState) -> dict[str, Any]:
        attempts = state.get("attempts", 0) + 1
        errors = state.get("errors", [])
        try:
            raw = reasoner.reason(state, errors[-1] if errors else None)
        except InvalidAdvisorOutputError as exc:
            return {"attempts": attempts, "raw_decision": None, "errors": [*errors, str(exc)]}
        except AdvisorUnavailableError as exc:
            return {"attempts": attempts, "raw_decision": None, "unavailable": str(exc)}
        return {"attempts": attempts, "raw_decision": raw}

    return diagnosis_and_strategy


def validate(state: RecoveryAgentState) -> dict[str, Any]:
    """Only a decision that passes the RecoveryDecision schema leaves the graph."""
    raw = state.get("raw_decision")
    if raw is None:
        return {}
    try:
        decision = RecoveryDecision.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'decision'}: {e['msg']}" for e in exc.errors())
        return {"errors": [*state.get("errors", []), f"invalid decision: {problems}"]}
    return {"decision": decision.model_dump(mode="json")}


def route_after_validation(state: RecoveryAgentState) -> Literal["done", "retry", "safe_failure"]:
    if state.get("decision"):
        return "done"
    if state.get("unavailable") or state.get("attempts", 0) >= MAX_ATTEMPTS:
        return "safe_failure"
    return "retry"


def safe_failure(state: RecoveryAgentState) -> dict[str, Any]:
    problem = state.get("unavailable") or "; ".join(state.get("errors", [])) or "no decision"
    attempts = state.get("attempts", 0)
    detail = f"{problem} (after {attempts} attempt{'s' if attempts != 1 else ''})"
    decision = safe_failure_decision(detail, language=state.get("preferred_language", "English"))
    return {"decision": decision.model_dump(mode="json")}


def build_graph(reasoner: Reasoner):  # noqa: ANN201 - LangGraph's compiled type is not public
    graph = StateGraph(RecoveryAgentState)
    graph.add_node("load_context", load_context)
    graph.add_node("risk_analysis", risk_analysis)
    graph.add_node("diagnosis_and_strategy", _make_reason_node(reasoner))
    graph.add_node("validate", validate)
    graph.add_node("safe_failure", safe_failure)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "risk_analysis")
    graph.add_edge("risk_analysis", "diagnosis_and_strategy")
    graph.add_edge("diagnosis_and_strategy", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validation,
        {"done": END, "retry": "diagnosis_and_strategy", "safe_failure": "safe_failure"},
    )
    graph.add_edge("safe_failure", END)
    return graph.compile()


# --- deterministic rules --------------------------------------------------------------


def _diagnose(state: RecoveryAgentState) -> tuple[str, str]:
    event_type = state.get("event_type", "PAYMENT_FAILURE")
    reliability = state.get("customer_reliability", 85.0)
    repeat_failures = state.get("repeat_failures", 0)
    overdue_days = state.get("overdue_days", 0)

    if event_type == "CHECKOUT_ABANDONED":
        return (
            "CHECKOUT_DROPOFF",
            "Customer dropped off during checkout before completing transaction. Cart or intent is recoverable.",
        )
    if event_type == "SUBSCRIPTION_HALTED":
        return (
            "MANDATE_DECLINE",
            "Recurring mandate failed. Likely card expiration, daily limit, or insufficient balance at billing cycle.",
        )
    if event_type in ("INVOICE_OVERDUE", "INVOICE_EXPIRED"):
        return (
            "B2B_PAYMENT_DELAY",
            f"Invoice overdue by {overdue_days} days. Standard commercial receivables cycle delay.",
        )
    if repeat_failures > 1:
        return (
            "REPEATED_METHOD_DECLINE",
            f"Multiple consecutive transaction attempts declined ({repeat_failures} failures). "
            "Method may be locked or balance exhausted.",
        )
    if reliability >= 80.0:
        return (
            "ISOLATED_PAYMENT_METHOD_ISSUE",
            f"Customer has strong payment reliability ({int(reliability)}%). "
            "Failure is an isolated incident rather than intentional default.",
        )
    return "INSUFFICIENT_FUNDS_OR_LIMIT", "Transaction failed due to insufficient funds or banking limit exhaustion."


# A customer's own track record overrides the generic strategy when it is this strong.
_HISTORY_TRUSTED_RATE = 0.6
_HISTORY_ROOT_CAUSES = frozenset({"ISOLATED_PAYMENT_METHOD_ISSUE", "INSUFFICIENT_FUNDS_OR_LIMIT"})


def _strategy(state: RecoveryAgentState, root_cause: str) -> tuple[ActionType, str, float]:
    event_type = state.get("event_type", "PAYMENT_FAILURE")
    reliability = state.get("customer_reliability", 85.0)
    channel = state.get("preferred_channel", "whatsapp")
    probability = state.get("recovery_probability", 0.80)

    if event_type == "CHECKOUT_ABANDONED":
        return (
            ActionType.PAYMENT_LINK,
            "Send targeted Payment Link via preferred channel with item reservation notice.",
            min(0.95, probability + 0.05),
        )
    if event_type in ("INVOICE_OVERDUE", "INVOICE_EXPIRED"):
        return (
            ActionType.REMINDER,
            "Send polite professional statement reminder with one-click payment options.",
            min(0.92, probability),
        )
    best = (state.get("best_historical_recovery_action") or "").upper()
    rate = state.get("historical_recovery_rate", 0.0)
    if (
        root_cause in _HISTORY_ROOT_CAUSES
        # A one-off payment cannot be charged again server-side, so history chooses among the
        # link-based actions; retries stay with recurring mandates (MANDATE_DECLINE below).
        and best in LINK_ACTIONS
        and best != ActionType.PAYMENT_LINK
        and rate >= _HISTORY_TRUSTED_RATE
    ):
        action = ActionType(best)
        return (
            action,
            f"{action.replace('_', ' ').title()} recovered this customer's earlier failures "
            f"({rate:.0%} of past recoveries succeeded); using what works for them via {channel.title()}.",
            min(0.9, max(0.75, probability)),
        )
    if root_cause == "ISOLATED_PAYMENT_METHOD_ISSUE":
        return (
            ActionType.PAYMENT_LINK,
            f"Customer has strong historical track record ({int(reliability)}% reliability). "
            f"Recommended Payment Link + {channel.title()} outreach for highest conversion.",
            0.91,
        )
    if root_cause == "MANDATE_DECLINE":
        return (
            ActionType.PAYMENT_RETRY,
            "Schedule automated payment retry at next optimal billing window + card update link.",
            0.88,
        )
    if reliability < 60.0 and state.get("amount", 0) > 50000:
        return (
            ActionType.ESCALATE_TO_HUMAN,
            "High-value case with low customer reliability. Operator intervention required.",
            0.65,
        )
    return (
        ActionType.PAYMENT_LINK,
        "Deliver instant Payment Link to customer's verified channel.",
        max(0.70, probability),
    )


class RulesReasoner:
    """RecoverX's deterministic, explainable rules. Always produces a valid decision."""

    name = RULES_MODEL_NAME

    def reason(self, state: RecoveryAgentState, feedback: str | None) -> dict[str, Any]:  # noqa: ARG002
        root_cause, diagnosis = _diagnose(state)
        action, reasoning, confidence = _strategy(state, root_cause)
        return {
            "diagnosis": diagnosis,
            "root_cause": root_cause,
            "recovery_probability": state.get("recovery_probability", 0.80),
            "recommended_action": action.value,
            "reasoning": reasoning,
            "confidence": confidence,
            "suggested_language": state.get("preferred_language", "English"),
        }


class LangGraphRecoveryAdvisor:
    """The RecoveryAdvisor port: runs the graph with one reasoner and returns a validated decision."""

    def __init__(self, reasoner: Reasoner | None = None) -> None:
        self.reasoner = reasoner or RulesReasoner()
        self.graph = build_graph(self.reasoner)

    @property
    def model_name(self) -> str:
        return self.reasoner.name

    def advise(self, context: RecoveryContext) -> RecoveryDecision:
        state = self.graph.invoke(asdict(context))
        return RecoveryDecision.model_validate(state["decision"])


class MalformedOutputReasoner:
    """Failure-lab double: answers the way a misbehaving model might, so validation and the safe
    failure run for real. Like the simulated providers, it stands in for an external dependency."""

    name = "failure-lab-malformed-output"

    def reason(self, state: RecoveryAgentState, feedback: str | None) -> dict[str, Any]:  # noqa: ARG002
        return {"diagnosis": "", "recommended_action": "TRANSFER_FUNDS_NOW", "confidence": 7, "amount": 999999}
