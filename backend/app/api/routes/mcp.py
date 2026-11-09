"""RecoverX tools for AI agents, over the Model Context Protocol.

``POST /mcp`` speaks MCP's Streamable HTTP transport (JSON-RPC 2.0, one JSON response per
request, no server-initiated stream), so standard MCP clients can connect with a bearer token:

    claude mcp add --transport http recoverx https://<api>/mcp --header "Authorization: Bearer <token>"

The same path still accepts RecoverX's original ``{"tool", "arguments"}`` body, and
``GET /mcp/tools`` still lists every tool. Both shapes call the same registry in ``api/mcp.py``,
which enforces roles, validates arguments and routes writes through the Tool Gateway.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api import mcp
from app.api.dependencies import CurrentUser, DbSession
from app.api.schemas.misc import MCPRequest, MCPResponse, MCPTool
from app.core.config import settings
from app.domain.exceptions import PermissionDeniedError, RecoverXError
from app.infrastructure.database.models import User

# Newest first; the server answers with the client's version when it supports it.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "recoverx", "title": "RecoverX", "version": settings.APP_VERSION}
INSTRUCTIONS = (
    "RecoverX recovers failed payments, halted subscriptions, overdue invoices and abandoned checkouts for a "
    "Razorpay merchant. Read tools are safe to call freely. Write tools never move money directly: they go "
    "through RecoverX's Tool Gateway, which re-checks the merchant's policy (amount limits, advisor confidence, "
    "contact limits, opt-outs) and refuses what policy forbids. A refusal is a final answer for that action; do "
    "not retry it with different wording. Amounts are whole rupees and come from RecoverX's records."
)
# Tools whose call reaches a payment or messaging provider.
PROVIDER_TOOLS = frozenset({"retry_payment", "create_payment_link", "send_recovery_message"})

# JSON-RPC error codes
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _check_origin(request: Request) -> None:
    """Streamable HTTP servers must reject foreign browser origins (DNS-rebinding protection)."""
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in {allowed.rstrip("/") for allowed in settings.CORS_ORIGINS}:
        raise PermissionDeniedError("Origin not allowed")


@router.get("/tools")
def list_tools(_user: CurrentUser) -> list[MCPTool]:
    """Every tool, in RecoverX's original listing shape. MCP clients use ``tools/list`` instead."""
    return [MCPTool.model_validate(tool) for tool in mcp.list_tools()]


@router.post(
    "",
    dependencies=[Depends(_check_origin)],
    responses={
        202: {"description": "MCP notification accepted"},
        400: {"description": "Legacy call: unknown tool, bad arguments, or action rejected"},
        403: {"description": "Legacy call: write tool without an operator role, or a foreign Origin"},
    },
)
def call(
    payload: Annotated[
        dict[str, Any] | list[dict[str, Any]],
        Body(description="An MCP JSON-RPC 2.0 message (or batch), or a legacy {tool, arguments} call"),
    ],
    session: DbSession,
    user: CurrentUser,
) -> Any:  # noqa: ANN401 - JSON-RPC result, batch, or legacy MCPResponse
    if isinstance(payload, list):
        replies = [reply for message in payload if (reply := _rpc(message, session, user)) is not None]
        return JSONResponse(replies) if replies else Response(status_code=status.HTTP_202_ACCEPTED)
    if "jsonrpc" in payload:
        reply = _rpc(payload, session, user)
        return JSONResponse(reply) if reply is not None else Response(status_code=status.HTTP_202_ACCEPTED)
    try:
        request = MCPRequest.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    return _legacy_call(request, session, user)


def _legacy_call(request: MCPRequest, session: DbSession, user: User) -> MCPResponse:
    """Every failure is a 400 with a reason the calling agent can act on; a missing role is a 403."""
    try:
        result = mcp.call_tool(session, user, request.tool, request.arguments)
    except PermissionDeniedError as exc:
        session.rollback()
        mcp.record_failed_call(session, user, request.tool, request.arguments, str(exc))
        raise
    except (RecoverXError, ValueError, TypeError, KeyError) as exc:
        session.rollback()
        mcp.record_failed_call(session, user, request.tool, request.arguments, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return MCPResponse(tool=request.tool, result=result)


# --- MCP (JSON-RPC 2.0) ------------------------------------------------------------


def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN401 - JSON-RPC id
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:  # noqa: ANN401
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _rpc(message: dict[str, Any], session: DbSession, user: User) -> dict[str, Any] | None:
    """Answer one JSON-RPC message; None for notifications and client responses, which get no reply."""
    message_id = message.get("id")
    method = message.get("method")
    if "method" not in message and ("result" in message or "error" in message):
        return None  # a client's answer to a server request; this server sends none
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _error(message_id, INVALID_REQUEST, "Expected a JSON-RPC 2.0 request")
    if "id" not in message:
        return None  # notifications/initialized, notifications/cancelled, …: nothing to answer
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(message_id, INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(
            message_id,
            {
                "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(message_id, {})
    if method == "tools/list":
        return _result(message_id, {"tools": [_describe(tool) for tool in mcp.tools_for(user)]})
    if method == "tools/call":
        return _call_tool(message_id, params, session, user)
    return _error(message_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def _describe(tool: mcp.Tool) -> dict[str, Any]:
    read = tool.kind == "read"
    annotations: dict[str, Any] = {
        "title": tool.title,
        "readOnlyHint": read,
        "openWorldHint": tool.name in PROVIDER_TOOLS,
    }
    if not read:
        # Writes add records or message the customer; nothing is deleted. A second call is a second contact.
        annotations |= {"destructiveHint": False, "idempotentHint": False}
    return {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": tool.input_schema(),
        "annotations": annotations,
    }


def _call_tool(message_id: Any, params: dict[str, Any], session: DbSession, user: User) -> dict[str, Any]:  # noqa: ANN401
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str) or name not in mcp.TOOLS:
        return _error(message_id, INVALID_PARAMS, f"Unknown tool: {name}")
    if not isinstance(arguments, dict):
        return _error(message_id, INVALID_PARAMS, "arguments must be an object")
    try:
        result = mcp.call_tool(session, user, name, arguments)
    except (RecoverXError, ValueError, TypeError, KeyError) as exc:
        # A tool error, not a protocol error: the agent sees why, so it can explain or choose differently.
        session.rollback()
        mcp.record_failed_call(session, user, name, arguments, str(exc))
        return _result(message_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
    session.commit()
    return _result(
        message_id,
        {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "structuredContent": result,
            "isError": False,
        },
    )
