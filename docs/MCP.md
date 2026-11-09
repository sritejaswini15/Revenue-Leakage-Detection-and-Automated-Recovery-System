# MCP server

RecoverX is a [Model Context Protocol](https://modelcontextprotocol.io) server. AI agents get the same tenant-scoped, policy-bound tools as the web app: they can read cases, customers, risk, policy, analytics and the audit trail, and an operator's agent can act on cases. An agent never reaches Razorpay or a messaging provider directly. Every write goes through the Tool Gateway, which re-checks the merchant's policy, enforces idempotency and writes the audit trail.

```
MCP client ─▶ POST /mcp ─▶ authenticate (bearer) ─▶ role check ─▶ argument validation
                                                                        │
       audit (mcp.tool_called) ◀── provider ◀── Tool Gateway + policy ◀─┘  (write tools)
```

## Connecting

The endpoint is `POST /mcp` on the API, using the Streamable HTTP transport (one JSON response per request; no server-initiated stream, so `GET /mcp` is 405). Authenticate with a session token from `POST /auth/login`:

```bash
TOKEN=$(curl -s https://<api>/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"operator@example.com","password":"…"}' | jq -r .access_token)

# Claude Code
claude mcp add --transport http recoverx https://<api>/mcp --header "Authorization: Bearer $TOKEN"
```

Any MCP client that supports HTTP servers with custom headers works the same way. Tokens expire after `ACCESS_TOKEN_TTL_SECONDS` (8 hours by default) and are revoked by logout, password changes, role changes and deactivation, so give an agent a dedicated user with the least role it needs.

Supported protocol versions: `2025-06-18`, `2025-03-26` and `2024-11-05`. The server answers `initialize` with the client's version when it supports it, and the newest otherwise. Browser requests from an origin outside `CORS_ORIGINS` are refused (DNS-rebinding protection).

Verified with the official MCP Python SDK (2.3.0): initialize, `tools/list` and `tools/call` against a running API, as an admin (15 tools) and as a viewer (10 read tools; write calls refused).

## Tools

`tools/list` returns only the tools the caller's role may run, each with a JSON Schema for its arguments (unknown arguments are rejected) and annotations (`readOnlyHint`, `openWorldHint` for tools that reach a provider).

| Tool | Kind | Arguments | Returns |
|---|---|---|---|
| `get_recovery_case` | read | `case_id` | Case summary with risk, policy decision and outcome |
| `list_recovery_cases` | read | `status`?, `limit`? (1–100) | Cases by expected recovery value |
| `get_customer_context` | read | `customer_id` | Profile: reliability, channel, language, recovery history, opt-out |
| `get_payment_history` | read | `customer_id` | Ten most recent payments |
| `get_risk_assessment` | read | `case_id` | Risk score, recovery probability, expected value and factors |
| `get_recovery_recommendation` | read | `case_id` | The advisor's recommended action and confidence |
| `evaluate_policy` | read | `case_id` | What policy decides now and why: what the Tool Gateway will decide |
| `get_recovery_status` | read | `case_id` | Status, recovered amount, link status, contact attempts |
| `get_analytics` | read | — | Organization totals |
| `get_audit_history` | read | `case_id`? | 25 most recent audit events |
| `retry_payment` | write | `case_id` | Retry through the gateway; falls back to a link if the payment can't be retried |
| `create_payment_link` | write | `case_id` | Link for the amount owed, messaged on the customer's channel |
| `send_recovery_message` | write | `case_id` | Personalised outreach with a link |
| `create_escalation` | write | `case_id`, `reason`?, `notes`? | Send an open case to the review queue |
| `record_promise_to_pay` | write | `case_id`, `amount`, `promised_date`? (default +7 days), `notes`? | A promise for at most what is owed, dated today or later |

## Roles and safety

- **Read tools** are open to every role. **Write tools** need `OPERATOR`, `FINANCE_MANAGER` or `ADMIN`, the same as the web app. A viewer's write call is a tool error and is audited.
- **Policy.** Write tools run through the Tool Gateway exactly as an operator's click would: opt-outs, contact limits, amount limits, advisor confidence and the merchant's allowed actions apply. A refusal comes back as a tool error (`isError: true`) whose text says why. Agents should treat it as final, not retry with different wording.
- **Tenancy.** Every lookup is scoped to the caller's organization; another merchant's case is "not found".
- **Closed cases** (recovered, stopped, expired) can't be escalated, promised or acted on.
- **Audit.** Each write call records `mcp.tool_called` with the tool, its arguments and outcome (including refusals), alongside the gateway's own `action.*`, `payment_link.*`, `message.sent` and `policy.rejected` events.

## Errors

| Situation | Response |
|---|---|
| Unknown method | JSON-RPC error `-32601` |
| Unknown tool, or `arguments` not an object | JSON-RPC error `-32602` |
| Not a JSON-RPC 2.0 message | JSON-RPC error `-32600` |
| Invalid arguments, missing case, policy refusal, provider failure, missing role | Tool result with `isError: true` and the reason |
| No or expired token | HTTP 401 |
| Foreign browser origin | HTTP 403 |

## Original REST shape

Earlier clients can keep using `GET /mcp/tools` and `POST /mcp` with `{"tool": "...", "arguments": {...}}`. It runs the same registry with the same checks: 400 with a reason on failure, 403 for a write tool without the role.
