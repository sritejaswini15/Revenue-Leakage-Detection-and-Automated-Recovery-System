# API reference

The FastAPI service publishes a full OpenAPI schema. With the API running, browse **`/docs`** (Swagger UI) or **`/redoc`** for every request and response model. This page summarizes conventions, access rules and the integration contracts.

## Conventions

- **Authentication.** `POST /auth/login` returns a token and also sets it as an HttpOnly, `SameSite=Lax` cookie (`SESSION_COOKIE_NAME`, Secure in production). API clients, scripts and MCP send `Authorization: Bearer <token>`. The web app relies on the cookie through its same-origin `/api` proxy and never sees the token. Sessions expire after `ACCESS_TOKEN_TTL_SECONDS`; logout, a password change, a role change or deactivation revokes them on the server.
- **CSRF.** A request authenticated by the cookie that changes state (anything but GET/HEAD/OPTIONS) must carry an `X-Requested-With` header, or it is refused with 403. Other sites cannot add that header without passing CORS. Bearer requests are unaffected.
- **Errors.** Errors return `{"detail": "<message>"}`: 400 invalid input, 401 not authenticated, 403 role not allowed or missing CSRF header, 404 not found (including other tenants' data), 409 blocked by state or policy, 410 expired, 413 body too large, 422 request schema validation (a list of `{loc, msg, type}`; submitted values are never echoed back), 429 rate limited, 502 provider failure, 503 server misconfiguration (e.g. no `CREDENTIALS_ENCRYPTION_KEY`).
- **Limits.** Request bodies: 256 KiB for webhooks, 4 MiB for customer imports, 1 MiB elsewhere. Failed logins: five per email per 15 minutes, counted in the database so the limit holds across instances.
- **Headers.** Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` and a deny-all `Content-Security-Policy` (not on `/docs` and `/redoc`); HSTS in production.
- **Money.** Whole rupees (INR) as integers. Razorpay webhook amounts are paise and are converted on receipt.
- **Time.** ISO 8601 in UTC, with a `Z` suffix.
- **Tenancy.** Every result is scoped to the caller's organization.

Minimum roles: **V** viewer (any signed-in user), **O** operator, **M** finance manager, **A** admin.

- **Concurrent requests.** Writes are idempotent; when two requests race past an idempotency check, the database keeps one and the other gets 409 (reload and retry), never a duplicate action.
- **Anonymous access.** Only `/health`, `/auth/login`, `/auth/logout`, `/webhooks/razorpay` (HMAC-signed) and `/pay/{token}` (the token is the credential) work without a session; everything else is 401. `tests/integration/test_endpoint_auth.py` checks every route in the schema.
- **Request ids.** Every response carries `X-Request-ID`: the caller's value when it is 8–64 safe characters, otherwise a new one. It appears on every log line for that request.

## Endpoints

### Auth and system

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/auth/login` | public | Email and password → session token and cookie. Deactivated accounts are refused. Five failures per 15 minutes per email returns 429 |
| POST | `/auth/logout` | V | Revoke the current session and clear the cookie (204) |
| GET | `/auth/me` | V | The signed-in user |
| POST | `/auth/password` | V | `{current_password, new_password}` (12+ characters). Signs out your other sessions; returns how many. 403 for the bootstrap administrator, whose password comes from the environment |
| GET | `/health` | public | Database connectivity; 503 when unavailable |
| GET | `/integrations/status` | V | This merchant's integrations: connected, simulated or not configured |

### Recovery cases

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/recovery-cases` | V | Ordered by expected recovery value. Filters: `status` (a case status, or `OPEN` for every status still being worked), `risk` (`HIGH`/`MEDIUM`/`LOW`), `revenue_type`, `query` (customer name or case id), `limit` (≤ 500) |
| GET | `/recovery-cases/{id}` | V | Case with diagnosis, payment link, communications, escalation, promises, `actions` (every recovery action with its outcome and, for a retry that fell back to a link, why), `payment_attempts` (attempts on the case's payments) and `executable_actions` (what policy allows on this open case). `policy_evaluation` is the merchant's *current* policy applied now (decision, reasons, triggered rules): exactly what the Tool Gateway will decide. `risk_reasons` explains the priority in plain language. `advisor_model` says who wrote the diagnosis: `langgraph-v1` (rules), a Claude model id, `failure-lab-malformed-output`, or `synthetic-seed` (demo data) |
| POST | `/recovery-cases/{id}/execute` | O | Optional body `{"action_type": "PAYMENT_LINK"}`: one of the case's `executable_actions`; defaults to the recommended action. A choice that differs is audited as `action.override`. 409 unless policy auto-approves and allows the action (refusals are audited as `policy.rejected`), and 409 for an escalated case, which only a reviewer can act on. A payment retry that succeeds recovers the case at once; one that is impossible or declined falls back to sending a payment link |
| POST | `/recovery-cases/{id}/stop` | O | Stop an open case and cancel its unpaid payment link. 409 if the case is already recovered, stopped or expired |
| POST | `/recovery-cases/{id}/escalate` | O | Optional `{"notes"}`. Send an open case to the review queue (`OPERATOR_REQUEST`) |
| POST | `/recovery-cases/{id}/promise-to-pay` | O | `{"amount", "promised_date", "notes"?}`: a promise the customer made, for at most what is owed, dated today or later. Case becomes `PROMISE_TO_PAY`; the promise is checked on its date (Temporal, or the hourly job). A new promise replaces any earlier one still pending (`SUPERSEDED`), so a revised date never escalates the case early |
| GET | `/recovery-cases/{id}/payment-link` | O | Open link status; 409 closed, 410 expired |
| GET | `/escalations?queue=&limit=&offset=` | V | Cases awaiting human review, most expected recovery first, a page at a time (`limit` ≤ 200, default 50). Each has `escalation_reason` and `queue` (`HIGH_VALUE`, `LOW_CONFIDENCE`, `PROMISE_MISSED`, `MAX_ATTEMPTS`, `PROVIDER_FAILURE`, `CUSTOMER_REQUEST`, `SPECIAL_CASE`). `queue` filters to one of them; `total` counts the filtered cases and `queues` always counts every queue |
| POST | `/escalations/{id}/resolve` | M | `{"resolution", "action_type"?, "notes"?}`. `APPROVE` runs the recommended action, `EDIT` runs `action_type`, `CONTACT` sends personalised outreach: all through the Tool Gateway, which still refuses what policy stops (opt-out, contact limit). `REJECT` and `CLOSE` stop the case and cancel its link. Unknown values are 400; a closed case is 409. Alias: `/recovery-cases/{id}/approve` |

### Customers and transactions

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/customers` | V | `search` (name, email, phone or Razorpay id; matched literally), `contact` (`all`/`contactable`/`opted_out`), `sort` (`value`/`name`/`recent`), `limit` (≤ 500), `offset`. Returns `total` |
| GET | `/customers/{id}` | V | By RecoverX or Razorpay id: profile, statistics, 25 most recent payments and cases |
| POST | `/customers/import` | M | `{"csv": "<text>", "commit": false}` previews; `commit: true` writes. See below |
| POST | `/customers/sync` | M | `{"commit": false}`: preview or apply the merchant's customer list fetched from Razorpay with its own keys (up to 5,000). 400 when the merchant has no Razorpay keys |
| GET | `/transactions` | V | Payments, newest first. `search` (payment id, customer name or email, case id), `status` (`CREATED`/`AUTHORIZED`/`CAPTURED`/`FAILED`), `date_from`, `date_to` (UTC days, inclusive), `case_id`, `limit` (≤ 200), `offset` |
| GET | `/transactions/{id}` | V | By RecoverX or Razorpay payment id, with every attempt and the linked case |

**Customer statistics.** `payment_reliability_pct`, `successful_payments` and `failed_payments` combine two sources, shown separately in `stats`. The *profile* is what the merchant imported (or the demo data): it is never changed by webhooks. *Observed* payments are those RecoverX saw through Razorpay events or captured itself, counted from the payments table by each payment's latest status, so a payment that failed and was later captured counts once, as a success, and replays change nothing. Reliability is a weighted blend: the profile counts as `max(profile payments, 4)` payments at its reliability, plus one data point per observed payment. The same combined figures feed the risk engine, the advisor and the policy when a case is opened (excluding the payment being decided on). Nothing is cached, so the figures are always recomputed from the data.

**CSV import.** UTF-8, first line a header. Recognised columns (case and spacing ignored): `name` (required), `email`, `phone` (or `contact`, `mobile`), `razorpay_customer_id` (or `customer_id`), `preferred_channel` (`whatsapp`/`email`), `preferred_language`, `lifetime_value`, `successful_payments`, `failed_payments`, `opted_out` (yes/no). Each row needs an email, phone or Razorpay id. Emails are lower-cased; 10-digit Indian mobile numbers become `+91…`, other numbers need their country code. Rows are matched to the merchant's existing customers by Razorpay id, then email, then phone (`update`), otherwise `create`; a row repeating an earlier one in the file is a `duplicate`; rows with errors are `invalid`. Invalid and duplicate rows are never written. An import can opt a customer out but never back in. At most 5,000 rows per file; the response lists invalid rows first, then up to 200 others.

### Analytics, audit and policy

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/analytics/recovery?days=` | V | Totals, funnel, rates, payment metrics and operational counts for cases opened in the window (all time without `days`, 1–365) |
| GET | `/analytics/strategy-performance?days=` | V | Per strategy, same window: cases, worked cases, recoveries, amount, time to recover |
| GET | `/analytics/trend?days=30` | V | Daily revenue at risk and recovered, by case opening date (1–180 days) |
| GET | `/analytics/experiments` | V | A/B experiment variants and outcomes |
| GET | `/audit-events?limit=&offset=&case_id=&event_prefix=` | V | Most recent first (≤ 200), with the real `total`. `event_prefix` filters a family, e.g. `user.` |
| GET / PUT | `/policies` | V / M | Merchant guardrails, including `case_expiry_days` (1–365), and three lists in order of preference: `preferred_channels` (`whatsapp`, `email`), `supported_languages` (`English`, `Hinglish`) and `allowed_actions` (what the Tool Gateway may execute). A customer is messaged on their own channel and language when the merchant allows them, otherwise on the merchant's first. Unknown or empty lists are 422. Updates are audited and apply to the next decision |

**Analytics definitions.** A window of `days` starts at 00:00 UTC `days − 1` days ago and runs to now. Case figures cover cases *opened* in the window; payment figures cover payments *recorded* in it. The dashboard and Analytics page send the same `days` to every call, so cards, trend and strategy totals agree.

| Figure | Definition |
|---|---|
| Revenue at risk | Sum of case amounts |
| Revenue recovered | Sum of amounts actually paid on those cases |
| Recovery rate | Revenue recovered ÷ revenue at risk |
| Revenue targeted | Expected recovery value of cases not stopped by policy |
| Payments captured / failed | Payments whose latest status is captured / failed, with their amounts |
| Payment success rate | Captured ÷ (captured + failed) |
| Awaiting capture | Payments authorized but not yet captured |

A case's *strategy* is the action most recently executed on it, or its recommended action if none was executed. A case counts as *worked* once an action ran or money was recovered.

### Merchant administration (A)

| Method | Path | Description |
|---|---|---|
| GET | `/integrations/razorpay` | The merchant's Razorpay configuration: `source` (`merchant`, `legacy-environment` or `simulated`), mode, masked key id, whether a webhook secret is set, last verification. Never a secret |
| PUT | `/integrations/razorpay` | `{key_id, key_secret, account_id, webhook_secret?, verify}`: store or rotate keys. `rzp_live_` keys are refused unless `ALLOW_LIVE_PAYMENTS=true`. A Razorpay account id can belong to one merchant only (409). Omitting `webhook_secret` keeps the current one |
| POST | `/integrations/razorpay/validate` | Call Razorpay with the stored keys and record the result |
| PATCH | `/integrations/razorpay` | `{enabled}`. Disabled: actions use the simulator, and Razorpay events are verified and acknowledged but not acted on |
| GET | `/users` | The merchant's users, with active sessions and last sign-in |
| POST | `/users` | `{name, email, role, password}`: create an account with a temporary password (12+ characters). No email is sent |
| PATCH | `/users/{id}` | `{name?, role?, is_active?}`. Role changes and deactivation sign the user out everywhere. You cannot change your own role or deactivate yourself; the bootstrap administrator is managed by the environment |
| POST | `/users/{id}/password` | `{password}`: set a temporary password and sign the user out |
| POST | `/users/{id}/revoke-sessions` | Sign the user out everywhere |

Every change here is written to the audit trail (`integration.*`, `user.*`) without secret values.

### Simulation center (O)

| Path | Body | Effect |
|---|---|---|
| `POST /simulation/event` | `event_type, customer_id, amount, id?` | Signed simulated webhook through the full pipeline. Repeating an `id` is detected as a replay (`is_duplicate: true`) |
| `POST /simulation/payment` | `case_id` | Customer pays → case `RECOVERED` |
| `POST /simulation/timeout` (alias `/failure`) | `case_id` | Razorpay times out while a payment link is issued: the Tool Gateway records the action as FAILED and nothing reaches the customer, then a retry with the same idempotency key completes that action (one link, one message). Returns `failed_attempts`, `messages_sent`, `action_id`. 409 for a case policy doesn't let RecoverX act on |
| `POST /simulation/opt-out` | `customer_id` or `case_id` | Customer opts out; contactable cases stop |
| `POST /simulation/low-confidence` | `case_id` | The advisor's confidence drops below the merchant's floor; the policy engine re-evaluates and escalates |
| `POST /simulation/max-attempts` | `case_id` | The contact count reaches the limit; the next reminder goes to the Tool Gateway, policy refuses it, and the case stops |
| `POST /simulation/invalid-ai` | `case_id` | The advisor graph runs with a reasoner that returns malformed output: validation rejects it twice and the case fails safe to human review (`INVALID_AI_OUTPUT`) |
| `POST /simulation/promise-to-pay` | `case_id, amount, promised_date` | Record a promise |
| `POST /simulation/promise-to-pay/{id}/fulfill` | — | Evaluate now: recovered if paid, escalated if overdue |

### Demo data

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/demo/reset` (alias `/demo/seed`) | A | Replace business data with the deterministic dataset (`customers`, `cases`, `seed`). User accounts are kept. Disabled in production unless `SEED_DEMO_DATA=true`, and refused (409) when more than one merchant exists |
| POST | `/demo/run-due-workflows` | A | Run the hourly job now (case expiry, follow-ups, due promises); same restrictions as reset |
| POST | `/demo/run-batch` | M | Replay all cases through advisor and policy; returns quality metrics, including `advisor_model`. With `LLM_PROVIDER=anthropic` every case is a Claude request |
| GET | `/demo/evaluation` | V | The latest batch evaluation (`summary`, `run_by`, `run_at`), or `null` |

### Customer payment page (public)

| Method | Path | Description |
|---|---|---|
| GET | `/pay/{token}` | Payment request for the token in a recovery message. No contact details are exposed |
| POST | `/pay/{token}` | `{"action": "pay"}` or `{"action": "promise", "promise_date": "YYYY-MM-DD"}` |

Tokens are random, stored only as SHA-256 hashes, and expire after `PAYMENT_LINK_TTL_HOURS`. The authenticated case detail exposes the page URL as `payment_link.payment_url`. It is available only for links created by the simulated provider; real Razorpay links use `short_url`.

## Razorpay webhooks

`POST /webhooks/razorpay`

| Header | |
|---|---|
| `X-Razorpay-Signature` | Hex HMAC-SHA256 of the raw body, keyed by the merchant's own stored webhook secret; otherwise its organization secret; otherwise, for the primary merchant only, `RAZORPAY_WEBHOOK_SECRET`. Other merchants without a secret have their webhooks rejected (401) |
| `X-Razorpay-Event-Id` | Razorpay's event id; the idempotency key. Without it, a body `id` or an id derived from the entity is used |
| `X-Razorpay-Account-Id` | Optional; otherwise the body's `account_id` is used. Must match an organization's `razorpay_account_id` |

**Routing.** The merchant is resolved from the account id *before* the signature is checked, so a valid signature for one merchant can never be applied to another.

**Responses.** A missing or invalid signature returns 401, and an unknown account returns 404. While a merchant's Razorpay integration is disabled, authentic events are acknowledged with `accepted: false` and not acted on. Every authentic event is answered with 200, because Razorpay retries non-2xx deliveries and eventually disables the webhook. When RecoverX can't use an event (an unsupported type, or one that identifies no customer), it replies `accepted: false` with a `reason` and keeps the receipt as REJECTED. Replays of the same event id return `duplicate: true` with the original outcome.

**How each event is handled.**

| Events | Effect |
|---|---|
| `payment.failed`, `payment_link.abandoned`, `payment_link.expired`, `invoice.overdue\|expired\|partially_paid`, `subscription.halted\|pending`, `checkout.abandoned` | Open a recovery case. A further event for the same payment, invoice or subscription joins its open case instead of opening another |
| `payment.captured`, `payment_link.paid\|partially_paid`, `invoice.paid`, `subscription.charged` | Resolve the matching case (by payment link, source payment, or the case id in `notes`) and record the amount actually paid. This includes a case that already **expired or was stopped**: money that arrives late still counts. Payment Link events report the running total paid, so a later `payment_link.paid` after a `partially_paid` raises the recorded amount; amounts are replaced, never added, so replays and reordered events can't count money twice. The payment inside a link, invoice or subscription event is recorded as a transaction linked to the case |
| `payment.authorized`, `payment_link.cancelled`, `subscription.cancelled` | Recorded only. An authorization isn't captured money, and a cancellation is a deliberate choice |
| `payment_link.expired\|cancelled\|abandoned` for a link RecoverX issued | Update that link's status; never open a second case |

**Customers.** The customer is found by `customer_id` on the entity or in its `notes`. Failing that, it comes from the RecoverX case named in the notes (Payment Link callbacks carry no customer id). The first event for someone new creates a customer profile from the payment's `customer_id`, `email` or `contact`, with neutral history. Customer ids already used by another merchant are namespaced, so merchants never share a profile.

Response:

```json
{"accepted": true, "reason": null, "duplicate": false, "dedupe_key": "razorpay:event:evt_123", "normalized": true,
 "event_id": "…", "case_id": "RC-1A2B3C4D", "case_status": "WAITING_FOR_PAYMENT", "recovered_amount": 0}
```

## MCP tools

`POST /mcp` is a Model Context Protocol server (JSON-RPC 2.0, Streamable HTTP), so standard MCP clients can connect with a bearer token; see [MCP.md](MCP.md). Read tools are open to every role, write tools need an operator role, and every write call is audited as `mcp.tool_called`. The original REST shape still works: `GET /mcp/tools` lists the tools, and `POST /mcp` with `{"tool": "...", "arguments": {...}}` calls one (400 with a reason on failure, 403 for a write tool without the role).

| Read | Write (via the Tool Gateway and policy) |
|---|---|
| `get_recovery_case`, `list_recovery_cases`, `get_customer_context`, `get_payment_history`, `get_risk_assessment`, `get_recovery_recommendation`, `evaluate_policy`, `get_recovery_status`, `get_analytics`, `get_audit_history` | `retry_payment`, `create_payment_link`, `send_recovery_message`, `create_escalation`, `record_promise_to_pay` |
