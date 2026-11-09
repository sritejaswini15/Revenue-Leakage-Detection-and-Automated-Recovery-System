# Environment variables

The backend reads every setting in one place, `backend/app/core/config.py`. It loads `.env` from `backend/` or the repository root. Blank values count as unset. Surrounding whitespace and one pair of quotes are stripped, because hosting dashboards sometimes store them literally.

Templates: [`.env.example`](../.env.example) (local), [`.env.production.example`](../.env.production.example) (production) and [`frontend/.env.example`](../frontend/.env.example).

## Backend

**Required in production** means the API refuses to start with `ENVIRONMENT=production` unless the variable is set (and is not a development default).

| Variable | Default | Required in production | Purpose |
|---|---|---|---|
| `ENVIRONMENT` | `development` | — | `production` enables startup validation, disables demo logins and skips `create_all` |
| `LOG_LEVEL` | `INFO` | | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` in production, else `text` | | `json` writes one JSON object per line with `request_id` and any structured fields |
| `SENTRY_DSN` | — | | Report unhandled errors to Sentry. No request bodies or personal data are sent |
| `SENTRY_TRACES_SAMPLE_RATE` | `0` | | Share of requests traced (0–1) |
| `DATABASE_URL` | local SQLite | **yes** | PostgreSQL URL; `postgres://` and `postgresql://` are accepted |
| `CORS_ORIGINS` | `http://localhost:3000` | set it | Comma-separated frontend origins |
| `AUTH_SECRET` | development value | **yes** | Signs session tokens; use 32+ random characters |
| `AUTH_REQUIRED` | `true` | | `false` acts as the first admin for unauthenticated requests (local debugging only) |
| `ACCESS_TOKEN_TTL_SECONDS` | `28800` | | Session lifetime (8 hours), for the cookie and bearer tokens alike |
| `SESSION_COOKIE_NAME` | `recoverx_session` | | Name of the HttpOnly session cookie set at login |
| `SESSION_COOKIE_SECURE` | `true` in production | | Send the cookie over HTTPS only. Leave the default unless you serve production over plain HTTP |
| `CREDENTIALS_ENCRYPTION_KEY` | derived from `AUTH_SECRET` outside production | to store merchant keys | Encrypts merchants' Razorpay key and webhook secrets at rest (any long random string). **Never change it after credentials are stored**: they could no longer be decrypted. Without it, production refuses to store credentials (HTTP 503). The hourly job needs the same value |
| `ALLOW_LIVE_PAYMENTS` | `false` | | `true` lets merchants save `rzp_live_` keys. Leave `false`: RecoverX is verified in test mode only |
| `BOOTSTRAP_ADMIN_EMAIL` | — | **yes** | Admin account ensured at every start |
| `BOOTSTRAP_ADMIN_PASSWORD` | — | **yes**, 16+ chars | Its password; changing it rotates the password on next start |
| `BOOTSTRAP_ORGANIZATION_NAME` | `RecoverX Merchant` | | Name of the organization created on first start |
| `BOOTSTRAP_RAZORPAY_ACCOUNT_ID` | — | for webhooks | Razorpay account id of the first (primary) merchant; assigned to its organization if it has none. The `RAZORPAY_*` settings below belong to this merchant only |
| `SEED_DEMO_DATA` | `true` | keep `false` | Seed the demo dataset into an empty database; in production also enables `/demo/reset` |
| `PAYMENT_PROVIDER` | `simulated` | | Legacy single-merchant setup: `razorpay` makes the primary merchant use the keys below. Merchants normally store their own keys under Integrations instead |
| `RAZORPAY_KEY_ID` | — | if `razorpay` | `rzp_test_…`. Used only for the primary merchant, never for others |
| `RAZORPAY_KEY_SECRET` | — | if `razorpay` | |
| `RAZORPAY_WEBHOOK_SECRET` | development value | **yes** | Webhook HMAC secret for the primary merchant (and the simulator). A merchant's own stored webhook secret takes precedence; other merchants without one have their webhooks rejected |
| `PAYMENT_LINK_TTL_HOURS` | `72` | | Payment link and customer-page expiry |
| `TEMPORAL_ENABLED` | `false` | | Dispatch workflows to Temporal instead of running them in-process |
| `TEMPORAL_HOST` | `localhost:7233` | if enabled | |
| `TEMPORAL_NAMESPACE` | `default` | | |
| `TEMPORAL_TASK_QUEUE` | `recoverx-recovery` | | |
| `TEMPORAL_API_KEY` | — | Temporal Cloud | |
| `LLM_PROVIDER` | `rules` | | `rules`: RecoverX's deterministic advisor. `anthropic`: Claude diagnoses and recommends inside the same LangGraph graph (see [AI.md](AI.md)) |
| `ANTHROPIC_API_KEY` | — | if `anthropic` | Claude API key. Without one every case fails safe to human review |
| `LLM_MODEL` | `claude-opus-5-5` | | Claude model id |
| `LLM_EFFORT` | `low` | | `low`…`max`: reasoning effort per decision. Higher is slower and costs more |
| `LLM_TIMEOUT_SECONDS` | `20` | | Per request. The advisor runs while a webhook is processed, so keep it short |
| `LLM_MAX_RETRIES` | `1` | | SDK retries of rate limits, 5xx and connection errors |
| `LLM_REFUSAL_FALLBACK` | `true` | | Ask the API to re-run a safety-declined request on a fallback model (Claude API only) |
| `PORT` | `8000` | | Set by the platform; read by the Docker image |

### Which process needs what

| Process | Needs |
|---|---|
| API (`recoverx-api`) | Everything above |
| Hourly job (`recoverx-cleanup`) | `ENVIRONMENT`, `DATABASE_URL`, `CREDENTIALS_ENCRYPTION_KEY`, `AUTH_SECRET`, `PAYMENT_PROVIDER`, `RAZORPAY_KEY_ID`/`SECRET`, `BOOTSTRAP_RAZORPAY_ACCOUNT_ID`, `TEMPORAL_ENABLED`, `SENTRY_DSN`. `render.yaml` copies them from the API, so the two never disagree |
| Temporal worker (optional) | The same as the hourly job, plus every `TEMPORAL_*` value, and `LLM_*`/`ANTHROPIC_API_KEY` if Claude is on |

Secrets (`AUTH_SECRET`, `CREDENTIALS_ENCRYPTION_KEY`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `BOOTSTRAP_ADMIN_PASSWORD`, `ANTHROPIC_API_KEY`, `TEMPORAL_API_KEY`, `SENTRY_DSN`) belong in the platform's secret store, never in the repository. None of them is ever sent to the browser: the frontend only knows the API's address.

## Frontend

| Variable | Default | Purpose |
|---|---|---|
| `API_URL` or `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Where the web server proxies `/api/*`. The browser only calls its own origin, so the session cookie stays first-party. **Read at build time** (rewrites are compiled in): rebuild after changing it. In Docker Compose: `http://backend:8000` |
| `PORT` | `3000` | Read by the standalone server in the Docker image |

## Tests

| Variable | Used by | Purpose |
|---|---|---|
| `E2E_BASE_URL` | Playwright | Web app under test; if unset, Playwright starts `npm run dev` |
| `E2E_API_URL` | Playwright | API under test (default `http://localhost:8000`) |
| `E2E_EMAIL`, `E2E_PASSWORD` | Playwright | Admin credentials (default: the demo admin) |
| `TEST_DATABASE_URL` | pytest | Optional: run the backend suite on this (empty, disposable) database instead of temporary SQLite |

Backend tests set the rest of their environment in `backend/tests/conftest.py`.
