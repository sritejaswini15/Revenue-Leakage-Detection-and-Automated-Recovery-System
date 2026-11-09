# Architecture

RecoverX is a modular monolith: one FastAPI service, one PostgreSQL database and one Next.js web app. The backend follows clean-architecture layering, applied pragmatically for a SQLAlchemy codebase.

## Backend layers

```
backend/app/
├── api/              Presentation: HTTP routes, request/response schemas, MCP tools, error mapping
├── application/      Use cases: one module per workflow; owns transactions
├── domain/           Business rules: pure Python, no framework or database imports
├── infrastructure/   Adapters: database, payment providers, messaging, recovery advisor, Temporal
├── core/             Cross-cutting: settings, logging and error reporting, security primitives, encryption, time helpers
├── container.py      Composition root: picks the implementation behind each port
└── main.py           App factory: middleware, error handlers, routers, startup
```

**Dependency rule.** Dependencies point inward: `api → application → domain`, and `infrastructure` implements the interfaces in `application/ports.py`. `domain` imports nothing from the rest of the app. `core` imports nothing from the other layers. These rules are checked during review (see [DEVELOPMENT.md](DEVELOPMENT.md#layering-checks)).

**Deliberate trade-off.** Application services use SQLAlchemy sessions and models directly instead of going through repository interfaces for every query. Shared tenant-scoped lookups and aggregate queries (customer lookup across namespaced ids, observed payment totals, policy) live in `infrastructure/database/repositories.py`, and providers are reached only through the ports in `application/ports.py`. API routes and MCP tools call application services and never query the ORM themselves. Business rules that matter (risk, policy, event normalization, message content) are pure functions in `domain/`, so they are unit-tested without a database. The domain describes the fields it needs through protocols in `domain/entities.py`, which the ORM models satisfy.

### Module map

| Module | Responsibility |
|---|---|
| `domain/risk.py` | Risk score, recovery probability, expected recovery value |
| `domain/reliability.py` | Customer reliability: the merchant profile blended with observed payments |
| `domain/policy.py` | Merchant guardrails → `AUTO_APPROVE`, `HUMAN_REVIEW` or `STOP`; channel and language choice; review queues |
| `domain/events.py` | Razorpay envelope → canonical revenue event (paise → rupees) |
| `domain/messages.py` | English and Hinglish outreach templates |
| `domain/decisions.py` | Advisor input (`RecoveryContext`), validated output (`RecoveryDecision`) and the safe-failure decision |
| `domain/constants.py` | Statuses, action vocabulary, roles, policy defaults |
| `domain/exceptions.py` | Business errors; `api/errors.py` maps each to an HTTP status |
| `application/webhooks.py` | Merchant routing, signature check, receipt and replay protection |
| `application/case_ingestion.py` | The core pipeline from event to case |
| `application/recovery_actions.py` | **Tool Gateway**: policy re-check, idempotency, provider calls, audit |
| `application/cases.py` | Case queries and operator commands (execute, stop, escalate, promise, resolve: approve/edit/contact/reject/close) |
| `application/learning.py` | Closed cases update the customer's recovery profile |
| `application/advice.py` | The advisor's view of an existing case, from persisted records |
| `application/simulation.py` | Simulation-center scenarios, run through the real pipeline |
| `application/payment_portal.py` | Public customer payment page (token-addressed) |
| `application/auth.py` | Login, database-backed rate limiting, server-side sessions, password change, revocation |
| `application/users.py` | User administration: create, roles, (de)activation, password reset, sign-out everywhere |
| `application/payment_integrations.py` | Per-merchant Razorpay credentials (encrypted), validation, and which provider and webhook secret act for each merchant |
| `application/customers.py`, `transactions.py` | Customer directory, statistics, CSV import and Razorpay sync; payment history |
| `application/case_expiry.py` | Closes unpaid cases after the merchant's `case_expiry_days` |
| `application/bootstrap.py` | Startup: primary organization, bootstrap admin, demo data |
| `application/analytics.py`, `evaluation.py` | SQL-aggregated analytics; batch replay evaluation |
| `application/workflows/` | Follow-up and promise-to-pay steps; Temporal dispatch, or the in-process runner plus the hourly scheduler |
| `infrastructure/ai/recovery_graph.py` | LangGraph advisor: `load_context → risk_analysis → diagnosis_and_strategy → validate`, retry once, safe failure; the deterministic rules reasoner |
| `infrastructure/ai/claude_reasoner.py` | Claude as the reasoner (`LLM_PROVIDER=anthropic`), structured output; see [AI.md](AI.md) |
| `infrastructure/payments/` | `RazorpayProvider` (REST) and `SimulatedRazorpayProvider` |
| `infrastructure/messaging/simulated.py` | Simulated WhatsApp and email channels |
| `infrastructure/temporal/` | Durable workflows, activities, worker entry point |
| `infrastructure/database/` | ORM models, engine/session, shared queries, deterministic demo seed |
| `api/mcp.py` | MCP tool registry: argument schemas, read/write roles, audit; every tool calls an application service |
| `api/routes/mcp.py` | The MCP protocol (JSON-RPC 2.0, Streamable HTTP) and the original REST shape; see [MCP.md](MCP.md) |
| `api/middleware.py` | Request ids and access logs, security headers, request body limits |

## The recovery pipeline

1. **Receive.** `POST /webhooks/razorpay` resolves the merchant from the account id *before* checking the signature (with that merchant's own secret), so merchant A's secret can never authorize merchant B's events. Each event id (Razorpay's `X-Razorpay-Event-Id`) is recorded once in `webhook_receipts`, and replays return the original outcome. Authentic events RecoverX can't use are acknowledged with 200 and stored as REJECTED, so Razorpay doesn't retry them.
2. **Identify the customer.** The customer comes from the event's customer id, or from the case named in the notes, or is created from the payment's email or contact on first sight.
3. **Normalize.** The Razorpay envelope becomes a `CanonicalRevenueEvent`. Razorpay amounts are in **paise**; RecoverX stores **whole rupees** everywhere else. Events fall into four kinds (`domain/events.py`): leak, success, informational, and the lifecycle of a link RecoverX issued.
4. **Mirror payment state.** Payment events update the merchant's own `payments` record, so retries act on persisted state and customer statistics can be counted. A link, invoice or subscription success records the payment it contains. Each payment is linked to the case it opened or settled. Another merchant's event never touches it.
5. **Resolve, update or open.**
   - A *success* event finds the open case through its payment link, its source payment, or the case id in the notes, and marks it `RECOVERED` with the amount actually paid.
   - An expiry, cancellation or abandonment of a *RecoverX-issued link* updates that link. It never opens a second case.
   - A *leak* event for a payment, invoice or subscription that already has an open case joins that case.
   - Any other *leak* event opens a case. The domain `RiskEngine` scores it, the `RecoveryAdvisor` recommends an action, and the `PolicyEngine` decides. All three see the customer's combined profile: merchant history plus every other payment RecoverX observed. The advisor's answer is validated; an invalid one is retried once and then fails safe to human review ([AI.md](AI.md)).
6. **Act.** `AUTO_APPROVE` goes through the Tool Gateway, which uses the merchant's own Razorpay keys when it has them, the environment keys only for the primary merchant, and otherwise the simulator. It which re-evaluates policy and enforces idempotency (`action:{case}:{type}:{attempt}`). then sends a payment link, issuing a fresh one if the old link expired or was cancelled, and audits each step. A `PAYMENT_RETRY` that the provider captures recovers the case immediately. A retry that is impossible or declined falls back to a payment link, and a captured payment is never captured again. `HUMAN_REVIEW` opens an escalation; `STOP` closes the case. A recommendation the gateway won't run automatically never fails the webhook: `MONITOR`/`NO_ACTION` leave the case open, while `ESCALATE_TO_HUMAN`/`STOP`/`PROMISE_TO_PAY` and actions the merchant switched off go to a person.
7. **Expire.** The hourly job first closes cases left unpaid for the merchant's `case_expiry_days` (default 7) as `EXPIRED`, cancelling their open link at the provider. NEW, ACTIONABLE, WAITING_FOR_PAYMENT and PROMISE_TO_PAY cases can expire; escalated cases wait for a person and never expire. Expiry is deferred while a promise is due in the future, a link is already paid, or a payment is authorized and awaiting capture. A payment that arrives after expiry still recovers the case. Expiry runs before follow-ups, so a case is never reminded and expired in the same run.
8. **Follow up.** After two days without payment the case gets a reminder; three days after that it is escalated. If policy refuses the reminder, the case is escalated, or stopped when contact is forbidden. A promise to pay is checked on its date. With Temporal, durable workflows sleep and resume themselves. Without it (the default, and on Render) the hourly `scripts.run_due_workflows` job advances whatever is due (`application/workflows/scheduler.py`).

### Invariants

- The advisor recommends; it never authorizes, executes, or computes money. It is deterministic rules, not a trained model.
- Every external action goes through the Tool Gateway, including MCP write tools and human approvals.
- There is at most one open case per source payment, invoice or subscription, and a payment is never captured twice.
- Every query is scoped to the caller's organization.
- Consequential state changes write an `audit_events` row, including policy refusals (`policy.rejected`) and MCP write calls (`mcp.tool_called`).
- A recovered, stopped or expired case is final for people and agents: stop, escalate, promise and resolve refuse it (409), so no command can rewrite a recorded recovery. Only money moves it: a payment that arrives for a stopped or expired case recovers it.
- Recovered amounts come from the provider's event and are replaced, never added (Payment Link events carry a running total), so replayed or reordered events can't double-count revenue.
- The case page, MCP's `evaluate_policy` and the Tool Gateway use one policy evaluation (`recovery_actions.evaluate_case_policy`), so what an operator reads is what the gateway decides.
- The merchant's channel, language and allowed-action settings are enforced at execution, not only displayed.
- Use cases own the transaction: lower-level helpers only `flush`, and the use case commits once.

## Authentication and roles

Session tokens are HMAC-signed and backed by a server-side `auth_sessions` row, so logout, password changes, role changes and deactivation revoke them immediately. Browsers carry the token in an HttpOnly, `SameSite=Lax` cookie through the web app's same-origin `/api` proxy, and must add `X-Requested-With` to state-changing requests (CSRF). API clients and MCP use `Authorization: Bearer`. Failed logins are counted in the `login_failures` table, so the limit holds across instances. Roles:

| Role | Can |
|---|---|
| `VIEWER` | Read everything |
| `OPERATOR` | + execute and stop cases, run simulations |
| `FINANCE_MANAGER` | + resolve escalations, edit policy, run evaluations |
| `ADMIN` | + manage users, Razorpay credentials and demo data |

**Merchant credentials.** Each merchant's Razorpay key secret and webhook secret are encrypted with Fernet (key derived from `CREDENTIALS_ENCRYPTION_KEY`) in `payment_integrations` and never returned by the API. The environment-wide `RAZORPAY_*` settings are a legacy single-merchant configuration that applies only to the primary merchant (`BOOTSTRAP_RAZORPAY_ACCOUNT_ID`, or the only organization). Live keys are refused unless `ALLOW_LIVE_PAYMENTS=true`.

## Frontend

```
frontend/src/
├── app/            Routes only: each page.tsx sets metadata and renders one feature
├── features/       Screens by domain: dashboard, cases, escalations, transactions, customers,
│                   analytics, policies, integrations, users, audit, simulator, payments, auth
├── components/     App shell and shared UI primitives (cards, panels, badges, states)
├── services/api/   Typed API client (errors carry the server's `detail` message)
├── hooks/          useApi (load/reload) and useSession (hydration-safe session state)
├── lib/            Formatting, configuration, session storage
└── types/          API response types, mirroring backend/app/api/schemas
```

Pages are client-rendered against the API through the same-origin `/api` rewrite in `next.config.ts`, which also sets the Content-Security-Policy. No token is readable by scripts: only the signed-in user's public profile is cached in `localStorage` (so every tab renders at once) and re-checked with `GET /auth/me` on load. `useSession` reads it through `useSyncExternalStore`, so server and client render the same markup during hydration.

## Data model

| Area | Tables |
|---|---|
| Tenancy and access | `organizations`, `users`, `auth_sessions`, `login_failures`, `policies`, `payment_integrations` |
| Merchant data | `customers`, `payments`, `payment_attempts`, `invoices`, `subscriptions`, `checkout_attempts` |
| Ingestion | `webhook_receipts`, `revenue_events` |
| Recovery | `recovery_cases`, `risk_assessments`, `agent_decisions`, `recovery_actions`, `payment_links`, `communications`, `escalations`, `promises_to_pay` |
| Measurement | `audit_events`, `experiments`, `experiment_results` |

Migrations live in `backend/alembic/versions`. Revision `0001` builds the initial schema from the models; every later change needs its own revision. `0004_merchant_integrations` adds merchant credentials, login throttling, user activation, the case expiry policy, and each payment's origin and case; it is idempotent and was verified upgrading populated 0003 databases on PostgreSQL and SQLite.

`payments.origin` is `webhook` for payments RecoverX observed and `seed` for demo history; only observed payments count toward customer statistics. Rows that existed before `0004` become `webhook`. On a demo database created by an older version, run a demo reset to mark the demo history correctly.

## Extension points

- **Another LLM.** Implement a `Reasoner` (`infrastructure/ai/recovery_graph.py`) like `ClaudeReasoner` and select it in `container.recovery_advisor()`. Validation, retry, safe failure, policy and execution stay unchanged.
- **Real WhatsApp or email delivery.** Implement `MessagingProvider` and register it in `container.messaging_providers()`.
- **Another payment provider.** Implement `PaymentProvider` and choose it per merchant in `application/payment_integrations.payment_provider_for()`.
