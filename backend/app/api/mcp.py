"""Model Context Protocol tool surface for AI agents.

Read tools are tenant-scoped queries any signed-in user may run. Write tools need an
operator role (the same as the UI) and go through the Tool Gateway, so they are subject to
the same policy, idempotency and audit rules as every other caller. Each call is audited.

The registry is served twice by ``api/routes/mcp.py``: as the MCP protocol (JSON-RPC 2.0,
Streamable HTTP) for standard MCP clients, and as RecoverX's original REST shape.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.application import analytics, audit, customers
from app.application import cases as case_queries
from app.application.audit import record_audit
from app.application.case_state import escalate_case, record_promise
from app.application.cases import CaseFilters
from app.application.customers import CustomerDetail
from app.application.recovery_actions import tool_gateway
from app.application.workflows import runner
from app.core.time import utc_now
from app.domain.constants import OPERATOR_ROLES, ActionType, CaseStatus, Role
from app.domain.exceptions import InvalidRequestError, NotFoundError, PermissionDeniedError
from app.infrastructure.database.models import RecoveryCase, User
from app.infrastructure.database.repositories import find_case, get_payment_link

DEFAULT_PROMISE_DAYS = 7


@dataclass(frozen=True)
class ToolContext:
    session: Session
    user: User

    @property
    def organization_id(self) -> str:
        return self.user.organization_id


Handler = Callable[[ToolContext, dict[str, Any]], dict[str, Any]]


class _Arguments(BaseModel):
    """Tool arguments: unknown keys are refused, so a misspelt argument is never silently ignored."""

    model_config = ConfigDict(extra="forbid")


class NoArguments(_Arguments):
    pass


class CaseArguments(_Arguments):
    case_id: str = Field(min_length=1, max_length=64, description="Recovery case id, e.g. RC-1A2B3C4D")


class CustomerArguments(_Arguments):
    customer_id: str = Field(min_length=1, max_length=80, description="Customer id or Razorpay customer id")


class ListCasesArguments(_Arguments):
    status: CaseStatus | None = Field(None, description="Only cases in this status")
    limit: int = Field(20, ge=1, le=100, description="How many cases, highest expected recovery first")


class AuditArguments(_Arguments):
    case_id: str | None = Field(None, max_length=64, description="Only this case's events; omit for all")


class EscalationArguments(CaseArguments):
    reason: str = Field("MCP_OPERATOR_REQUEST", min_length=2, max_length=64, description="Reason code")
    notes: str | None = Field(None, max_length=2000, description="What the reviewer should know")


class PromiseArguments(CaseArguments):
    amount: int = Field(gt=0, description="Whole rupees, at most the amount owed")
    promised_date: date | None = Field(
        None,
        description=f"When the customer will pay (YYYY-MM-DD, today or later); default {DEFAULT_PROMISE_DAYS} days",
    )
    notes: str | None = Field(None, max_length=2000)


@dataclass(frozen=True)
class Tool:
    name: str
    kind: Literal["read", "write"]
    description: str
    args: type[_Arguments]
    handler: Handler
    title: str = ""

    @property
    def arguments(self) -> tuple[str, ...]:
        return tuple(self.args.model_fields)

    @property
    def roles(self) -> tuple[Role, ...]:
        return tuple(Role) if self.kind == "read" else OPERATOR_ROLES

    def input_schema(self) -> dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        schema.setdefault("properties", {})
        return schema

    def allowed_for(self, user: User) -> bool:
        return user.role in self.roles


def _case(ctx: ToolContext, args: dict[str, Any]) -> RecoveryCase:
    case_id = args.get("case_id")
    if not case_id:
        raise InvalidRequestError("case_id is required")
    case = find_case(ctx.session, ctx.organization_id, case_id)
    if not case:
        raise NotFoundError("Recovery case not found")
    return case


def _customer(ctx: ToolContext, args: dict[str, Any]) -> CustomerDetail:
    if not args.get("customer_id"):
        raise InvalidRequestError("customer_id is required")
    return customers.get_customer_detail(ctx.session, ctx.organization_id, str(args["customer_id"]))


# --- read tools ---------------------------------------------------------------


def get_recovery_case(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case = _case(ctx, args)
    risk = case.risk_assessment
    return {
        "id": case.id,
        "customer_id": case.customer_id,
        "customer_name": case.customer.name,
        "amount": case.amount,
        "currency": case.currency,
        "status": case.status,
        "risk_score": risk.risk_score,
        "recovery_probability": risk.recovery_probability,
        "expected_recovery_value": risk.expected_recovery_value,
        "recommended_action": case.recommended_action,
        "policy_decision": case.policy_decision,
        "ai_confidence": case.ai_confidence,
        "contact_attempts": case.contact_attempts,
        "recovered_amount": case.recovered_amount,
        "action_executed": case.action_executed,
        "created_at": case.created_at.isoformat(),
    }


def list_recovery_cases(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    filters = CaseFilters(status=args.get("status"), limit=min(100, int(args.get("limit", 20))))
    cases, _total = case_queries.list_cases(ctx.session, ctx.organization_id, filters)
    return {
        "count": len(cases),
        "items": [
            {
                "id": case.id,
                "customer": case.customer.name,
                "amount": case.amount,
                "status": case.status,
                "risk_score": case.risk_assessment.risk_score,
                "expected_recovery": case.risk_assessment.expected_recovery_value,
            }
            for case in cases
        ],
    }


def get_customer_context(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    view = _customer(ctx, args).view
    customer, stats = view.customer, view.stats
    return {
        "id": customer.id,
        "name": customer.name,
        "email": customer.email,
        "phone": customer.phone,
        "lifetime_value": customer.lifetime_value,
        "payment_reliability_pct": stats.reliability_pct,
        "successful_payments": stats.successful_payments,
        "failed_payments": stats.failed_payments,
        "observed_payments": {"successful": stats.observed_successful, "failed": stats.observed_failed},
        "preferred_channel": customer.preferred_channel,
        "preferred_language": customer.preferred_language,
        "historical_recovery_rate": customer.historical_recovery_rate,
        "opted_out": customer.opted_out,
    }


def get_payment_history(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    detail = _customer(ctx, args)
    return {
        "customer_id": detail.view.customer.id,
        "payments": [
            {
                "id": payment.provider_payment_id,
                "amount": payment.amount,
                "status": payment.status,
                "failure_reason": payment.failure_reason,
                "origin": payment.origin,
                "recovery_case_id": payment.recovery_case_id,
                "created_at": payment.created_at.isoformat(),
            }
            for payment in detail.payments[:10]
        ],
    }


def get_risk_assessment(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case = _case(ctx, args)
    risk = case.risk_assessment
    return {
        "case_id": case.id,
        "risk_score": risk.risk_score,
        "recovery_probability": risk.recovery_probability,
        "expected_recovery_value": risk.expected_recovery_value,
        "factors": risk.factors,
        "model_version": risk.model_version,
    }


def get_recovery_recommendation(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case = _case(ctx, args)
    return {
        "case_id": case.id,
        "recommended_action": case.recommended_action,
        "ai_confidence": case.ai_confidence,
        "policy_decision": case.policy_decision,
        "status": case.status,
    }


def evaluate_policy(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Exactly what the Tool Gateway would decide for this case now."""
    result = case_queries.current_policy(ctx.session, _case(ctx, args))
    return {"decision": result.decision, "reasons": result.reasons, "violations": result.rule_violations}


def get_recovery_status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case = _case(ctx, args)
    link = get_payment_link(ctx.session, case.id)
    return {
        "case_id": case.id,
        "case_status": case.status,
        "recovered_amount": case.recovered_amount,
        "payment_link_status": link.status if link else None,
        "contact_attempts": case.contact_attempts,
    }


def get_analytics(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    overview = analytics.recovery_overview(ctx.session, ctx.organization_id)
    return {
        "revenue_at_risk": overview["revenue_at_risk"],
        "revenue_recovered": overview["revenue_recovered"],
        "recovery_rate": overview["recovery_rate"],
        "cases_processed": overview["cases"],
        "successful_recoveries": overview["successful_recoveries"],
    }


def get_audit_history(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    events, _total = audit.list_audit_events(ctx.session, ctx.organization_id, case_id=args.get("case_id"), limit=25)
    return {
        "events": [
            {
                "event_name": event.event_name,
                "case_id": event.case_id,
                "actor": event.actor,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]
    }


# --- write tools ----------------------------------------------------------------


def _execute(ctx: ToolContext, args: dict[str, Any], action_type: ActionType) -> tuple[RecoveryCase, str, str]:
    case = _case(ctx, args)
    action = tool_gateway().execute_recovery_action(
        ctx.session, case, action_type, actor=ctx.user.email, organization_id=ctx.organization_id
    )
    return case, action.id, action.status


def retry_payment(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case, action_id, status = _execute(ctx, args, ActionType.PAYMENT_RETRY)
    return {"action_id": action_id, "status": status, "case_id": case.id}


def create_payment_link(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case, action_id, status = _execute(ctx, args, ActionType.PAYMENT_LINK)
    link = get_payment_link(ctx.session, case.id)
    return {
        "action_id": action_id,
        "status": status,
        "case_id": case.id,
        "link_id": link.provider_link_id if link else None,
        "short_url": link.short_url if link else None,
    }


def send_recovery_message(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case, action_id, status = _execute(ctx, args, ActionType.PERSONALIZED_OUTREACH)
    return {"action_id": action_id, "status": status, "case_id": case.id}


def create_escalation(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case = _case(ctx, args)
    escalation = escalate_case(
        ctx.session,
        case,
        reason=args.get("reason", "MCP_OPERATOR_REQUEST"),
        notes=args.get("notes"),
        actor=ctx.user.email,
    )
    return {"escalation_id": escalation.id, "case_id": case.id, "status": case.status}


def record_promise_to_pay(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    case = _case(ctx, args)
    day = args.get("promised_date") or (utc_now() + timedelta(days=DEFAULT_PROMISE_DAYS)).date()
    if day < utc_now().date():
        raise InvalidRequestError("promised_date must be today or later")
    promised_at = datetime.combine(day, time.min, tzinfo=UTC)
    promise = record_promise(
        ctx.session, case, args["amount"], promised_at, actor=ctx.user.email, notes=args.get("notes")
    )
    ctx.session.commit()  # the promise workflow reads it
    runner.schedule_promise_check(promise.id)
    return {"promise_id": promise.id, "case_id": case.id, "status": promise.status, "promised_date": day.isoformat()}


_WRITE_NOTE = " Runs through the Tool Gateway: merchant policy, idempotency and audit apply; refusals are errors."

TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "get_recovery_case",
            "read",
            "Case summary with risk, policy and outcome.",
            CaseArguments,
            get_recovery_case,
            "Get recovery case",
        ),
        Tool(
            "list_recovery_cases",
            "read",
            "Cases ordered by expected recovery value, optionally filtered by status.",
            ListCasesArguments,
            list_recovery_cases,
            "List recovery cases",
        ),
        Tool(
            "get_customer_context",
            "read",
            "Customer profile and payment behaviour: reliability, channel, language, recovery history, opt-out.",
            CustomerArguments,
            get_customer_context,
            "Get customer context",
        ),
        Tool(
            "get_payment_history",
            "read",
            "A customer's ten most recent payments.",
            CustomerArguments,
            get_payment_history,
            "Get payment history",
        ),
        Tool(
            "get_risk_assessment",
            "read",
            "Deterministic risk score, recovery probability, expected recovery value and contributing factors.",
            CaseArguments,
            get_risk_assessment,
            "Get risk assessment",
        ),
        Tool(
            "get_recovery_recommendation",
            "read",
            "The advisor's recommended action and confidence. A recommendation, not an authorization.",
            CaseArguments,
            get_recovery_recommendation,
            "Get recovery recommendation",
        ),
        Tool(
            "evaluate_policy",
            "read",
            "What merchant policy decides for the case right now (AUTO_APPROVE, HUMAN_REVIEW or STOP) and why.",
            CaseArguments,
            evaluate_policy,
            "Evaluate merchant policy",
        ),
        Tool(
            "get_recovery_status",
            "read",
            "Case status, recovered amount, payment link status and contact attempts.",
            CaseArguments,
            get_recovery_status,
            "Get recovery status",
        ),
        Tool(
            "get_analytics",
            "read",
            "Organization-wide recovery totals.",
            NoArguments,
            get_analytics,
            "Get recovery analytics",
        ),
        Tool(
            "get_audit_history",
            "read",
            "The 25 most recent audit events.",
            AuditArguments,
            get_audit_history,
            "Get audit history",
        ),
        Tool(
            "retry_payment",
            "write",
            "Retry the case's failed payment." + _WRITE_NOTE,
            CaseArguments,
            retry_payment,
            "Retry payment",
        ),
        Tool(
            "create_payment_link",
            "write",
            "Create a payment link for the amount owed and message it to the customer." + _WRITE_NOTE,
            CaseArguments,
            create_payment_link,
            "Send payment link",
        ),
        Tool(
            "send_recovery_message",
            "write",
            "Send personalised outreach with a payment link." + _WRITE_NOTE,
            CaseArguments,
            send_recovery_message,
            "Send recovery message",
        ),
        Tool(
            "create_escalation",
            "write",
            "Route an open case to the human review queue.",
            EscalationArguments,
            create_escalation,
            "Escalate to a person",
        ),
        Tool(
            "record_promise_to_pay",
            "write",
            "Record a customer's promise to pay part or all of what is owed by a date; it is checked on that date.",
            PromiseArguments,
            record_promise_to_pay,
            "Record promise to pay",
        ),
    )
}


def tools_for(user: User) -> list[Tool]:
    """The tools this user may call: everyone reads, operators and above also write."""
    return [tool for tool in TOOLS.values() if tool.allowed_for(user)]


def list_tools() -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "type": tool.kind, "description": tool.description, "arguments": list(tool.arguments)}
        for tool in TOOLS.values()
    ]


def get_tool(name: str) -> Tool:
    tool = TOOLS.get(name)
    if not tool:
        raise InvalidRequestError(f"Unknown MCP tool: {name}")
    return tool


def call_tool(session: Session, user: User, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Authorize, validate and run one tool. The caller commits on success and rolls back on failure."""
    tool = get_tool(name)
    if not tool.allowed_for(user):
        raise PermissionDeniedError(f"{name} changes recovery state and needs an operator role")
    try:
        parsed = tool.args.model_validate(arguments or {})
    except ValidationError as exc:
        problems = "; ".join(_describe(error) for error in exc.errors())
        raise InvalidRequestError(f"Invalid arguments for {name}: {problems}") from exc
    values = parsed.model_dump(exclude_none=True)
    result = tool.handler(ToolContext(session, user), values)
    if tool.kind == "write":
        _audit_call(session, user, tool, values, outcome="ok")
    return result


def _describe(error: Any) -> str:  # noqa: ANN401 - pydantic's ErrorDetails
    where = ".".join(str(part) for part in error["loc"]) or "arguments"
    return f"{where}: {error['msg']}"


def record_failed_call(session: Session, user: User, name: str, arguments: dict[str, Any] | None, error: str) -> None:
    """Audit a refused or failed write call after the caller rolled its changes back."""
    tool = TOOLS.get(name)
    if tool and tool.kind == "write":  # refused calls too: an attempt without the role is worth knowing about
        _audit_call(session, user, tool, arguments or {}, outcome="error", error=error)
        session.commit()


def _audit_call(
    session: Session, user: User, tool: Tool, arguments: dict[str, Any], outcome: str, error: str | None = None
) -> None:
    payload: dict[str, Any] = {
        "tool": tool.name,
        "arguments": {key: str(value) for key, value in arguments.items()},
        "outcome": outcome,
    }
    if error:
        payload["error"] = error[:500]
    case_id = arguments.get("case_id")
    case = find_case(session, user.organization_id, str(case_id)) if case_id else None
    record_audit(
        session,
        user.organization_id,
        "mcp.tool_called",
        payload,
        actor=user.email,
        case_id=case.id if case else None,
    )
