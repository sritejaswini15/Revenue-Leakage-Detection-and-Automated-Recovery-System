"""The MCP protocol (JSON-RPC 2.0 over Streamable HTTP) on POST /mcp, as a standard MCP client uses it."""

from itertools import count

import pytest

from tests.helpers import first_contactable_customer, login
from tests.integration.test_razorpay_lifecycle import deliver, failed_payment

_ids = count(1)
WRITE_TOOLS = {
    "retry_payment",
    "create_payment_link",
    "send_recovery_message",
    "create_escalation",
    "record_promise_to_pay",
}


def rpc(client, headers, method: str, params: dict | None = None, **extra_headers: str):
    body = {"jsonrpc": "2.0", "id": next(_ids), "method": method, **({"params": params} if params else {})}
    return client.post(
        "/mcp", json=body, headers={**headers, "Accept": "application/json, text/event-stream", **extra_headers}
    )


def call(client, headers, name: str, arguments: dict | None = None) -> dict:
    response = rpc(client, headers, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def _waiting_case(client, headers, rupees: int) -> str:
    body = failed_payment(first_contactable_customer(client, headers), rupees)
    return deliver(client, body).json()["case_id"]


def _audit_names(client, headers, case_id: str) -> list[str]:
    items = client.get("/audit-events", params={"case_id": case_id, "limit": 200}, headers=headers).json()["items"]
    return [item["event_name"] for item in items]


def test_a_client_can_initialize_and_discover_tools(client, admin_headers):
    init = rpc(client, admin_headers, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert init.status_code == 200
    result = init.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "recoverx" and "Tool Gateway" in result["instructions"]

    initialized = client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=admin_headers
    )
    assert initialized.status_code == 202 and initialized.content == b""

    tools = {tool["name"]: tool for tool in rpc(client, admin_headers, "tools/list").json()["result"]["tools"]}
    assert set(tools) >= WRITE_TOOLS
    case_tool = tools["get_recovery_case"]
    assert case_tool["inputSchema"]["type"] == "object"
    assert case_tool["inputSchema"]["required"] == ["case_id"]
    assert case_tool["inputSchema"]["additionalProperties"] is False
    assert case_tool["annotations"]["readOnlyHint"] is True
    assert tools["create_payment_link"]["annotations"] == {
        "title": "Send payment link",
        "readOnlyHint": False,
        "openWorldHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
    }


def test_an_unknown_protocol_version_gets_the_latest(client, admin_headers):
    result = rpc(client, admin_headers, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert result["protocolVersion"] == "2025-06-18"


def test_read_tools_return_structured_content(client, admin_headers):
    result = call(client, admin_headers, "get_risk_assessment", {"case_id": "RC-RAHUL-18K"})
    assert result["isError"] is False
    assert result["structuredContent"]["case_id"] == "RC-RAHUL-18K"
    assert '"RC-RAHUL-18K"' in result["content"][0]["text"]


def test_viewers_see_and_run_only_read_tools(client, admin_headers):
    case_id = _waiting_case(client, admin_headers, 1250)
    actions_before = len(client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()["actions"])
    viewer = login(client, "viewer@recoverx.local")

    listed = {tool["name"] for tool in rpc(client, viewer, "tools/list").json()["result"]["tools"]}
    assert listed and not listed & WRITE_TOOLS

    refused = call(client, viewer, "send_recovery_message", {"case_id": case_id})
    assert refused["isError"] is True and "operator role" in refused["content"][0]["text"]
    legacy = client.post(
        "/mcp", json={"tool": "send_recovery_message", "arguments": {"case_id": case_id}}, headers=viewer
    )
    assert legacy.status_code == 403

    after = client.get(f"/recovery-cases/{case_id}", headers=admin_headers).json()
    assert len(after["actions"]) == actions_before, "a viewer must not be able to message the customer"
    assert "mcp.tool_called" in _audit_names(client, admin_headers, case_id), "refused attempts are audited"


def test_write_tools_go_through_the_gateway_and_are_audited(client, admin_headers):
    case_id = _waiting_case(client, admin_headers, 1300)
    result = call(client, admin_headers, "send_recovery_message", {"case_id": case_id})
    assert result["isError"] is False and result["structuredContent"]["status"] == "EXECUTED"
    names = _audit_names(client, admin_headers, case_id)
    assert "mcp.tool_called" in names and "action.executed" in names and "message.sent" in names

    refused = call(client, admin_headers, "create_payment_link", {"case_id": "RC-RAHUL-18K"})
    if refused["isError"]:  # the demo case may already be closed or need review: policy, not MCP, decides
        assert refused["content"][0]["text"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "case_id"),
        ({"case_id": "RC-RAHUL-18K", "cse_id": "typo"}, "Extra inputs are not permitted"),
        ({"case_id": "RC-DOES-NOT-EXIST"}, "not found"),
    ],
)
def test_bad_arguments_are_tool_errors(client, admin_headers, arguments, message):
    result = call(client, admin_headers, "get_recovery_case", arguments)
    assert result["isError"] is True and message.lower() in result["content"][0]["text"].lower()


def test_promises_need_a_future_date_and_default_to_a_week(client, admin_headers):
    case_id = _waiting_case(client, admin_headers, 1400)
    past = call(
        client,
        admin_headers,
        "record_promise_to_pay",
        {"case_id": case_id, "amount": 500, "promised_date": "2020-01-01"},
    )
    assert past["isError"] is True

    recorded = call(client, admin_headers, "record_promise_to_pay", {"case_id": case_id, "amount": 500})
    assert recorded["isError"] is False and recorded["structuredContent"]["status"] == "PENDING"
    assert recorded["structuredContent"]["promised_date"] > "2026"


def test_protocol_errors(client, admin_headers):
    unknown_tool = rpc(client, admin_headers, "tools/call", {"name": "drop_tables"}).json()
    assert unknown_tool["error"]["code"] == -32602
    unknown_method = rpc(client, admin_headers, "resources/list").json()
    assert unknown_method["error"]["code"] == -32601
    assert rpc(client, admin_headers, "ping").json()["result"] == {}
    not_jsonrpc = client.post("/mcp", json={"jsonrpc": "1.0", "id": 1, "method": "ping"}, headers=admin_headers)
    assert not_jsonrpc.json()["error"]["code"] == -32600


def test_a_batch_gets_one_reply_per_request(client, admin_headers):
    response = client.post(
        "/mcp",
        json=[
            {"jsonrpc": "2.0", "id": "a", "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ],
        headers=admin_headers,
    )
    assert response.status_code == 200 and [reply["id"] for reply in response.json()] == ["a"]


def test_foreign_browser_origins_and_anonymous_callers_are_refused(client, admin_headers):
    assert rpc(client, admin_headers, "ping", Origin="https://evil.example").status_code == 403
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401


def test_a_malformed_legacy_body_is_a_validation_error(client, admin_headers):
    assert client.post("/mcp", json={"arguments": {}}, headers=admin_headers).status_code == 422
