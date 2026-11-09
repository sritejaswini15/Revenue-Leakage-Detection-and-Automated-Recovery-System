# Development

## Prerequisites

- Python 3.12+ (3.13 is used in Docker and CI)
- Node.js 22+
- Docker (optional, for the PostgreSQL stack)

## Setup

```bash
# Backend: from the repository root
python -m venv .venv
.venv/Scripts/activate              # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env                # optional; every setting has a local default

# Frontend
cd frontend
npm ci
cp .env.example .env.local          # optional; defaults to http://localhost:8000
```

## Running

```bash
cd backend && uvicorn app.main:app --reload    # http://localhost:8000/docs
cd frontend && npm run dev                     # http://localhost:3000
```

Without `DATABASE_URL`, the API uses `backend/recoverx.db` (SQLite). It creates tables and seeds the demo dataset on first start. Delete the file to start fresh.

Demo logins (password `recoverx-demo`): `admin@`, `finance@`, `operator@` and `viewer@recoverx.local`, one per role.

For the full stack on PostgreSQL:

```bash
docker compose up --build
TEMPORAL_ENABLED=true docker compose --profile workflows up --build   # with durable workflows
```

### Useful scripts (run from `backend/`)

| Command | Purpose |
|---|---|
| `python -m scripts.seed_demo_data --customers 1000 --cases 500` | Re-seed demo data (keeps users) |
| `python -m scripts.run_batch_evaluation` | Replay all cases through advisor and policy; exits non-zero on failure |
| `python -m scripts.expire_payment_links` | Expire overdue payment links (runs hourly in production) |
| `python -m scripts.run_due_workflows` | Expire unpaid cases, then send due reminders, escalate unanswered cases and check promises (runs hourly; with Temporal enabled only expiry runs) |
| `python -m scripts.razorpay_test_roundtrip [--keep-link]` | Verify Razorpay test keys from your shell: create, read and cancel a ₹1 Payment Link. Refuses live keys |
| `python -m app.infrastructure.temporal.worker` | Temporal worker (requires `TEMPORAL_ENABLED=true`) |

## Tests and quality checks

| | Command (from the package directory) |
|---|---|
| Backend tests | `pytest` (unit tests in `tests/unit`, HTTP integration tests in `tests/integration`) |
| Migration drift | `alembic upgrade head && alembic check` against an empty database (CI uses SQLite) |
| Backend lint and format | `ruff check .` and `ruff format .` |
| Frontend lint, types, format | `npm run lint`, `npm run typecheck`, `npm run format` |
| Frontend build | `npm run build` |
| End-to-end | start the API and web app, then `E2E_BASE_URL=http://localhost:3000 npm run e2e` |

Backend tests run against a temporary SQLite database. Environment variables are pinned in `tests/conftest.py`, so a local `.env` can't leak in. To run the same suite on PostgreSQL, point `TEST_DATABASE_URL` at an empty, disposable database (CI does both). The E2E suite resets the demo dataset first and runs the hourly job at the end (expiring demo cases), so point it only at a disposable, non-production backend. Give that backend a `BOOTSTRAP_RAZORPAY_ACCOUNT_ID` so the forged-webhook check exercises signature verification. Without Docker or a PostgreSQL install, a throwaway container is enough: `docker run -d --rm --name recoverx-test-pg -e POSTGRES_USER=rxtest -e POSTGRES_PASSWORD=rxtest -e POSTGRES_DB=rxtest -p 127.0.0.1:55432:5432 postgres:16-alpine`, then `TEST_DATABASE_URL=postgresql://rxtest:rxtest@127.0.0.1:55432/rxtest pytest`, then `docker stop recoverx-test-pg`. If `E2E_BASE_URL` is unset, Playwright starts `npm run dev` itself; run `npx playwright install chromium` once.

CI (`.github/workflows/ci.yml`) runs all of the above on every push and pull request.

## Database migrations

Production schema changes go through Alembic. The container runs `alembic upgrade head` on start.

```bash
cd backend
# 1. change app/infrastructure/database/models.py
alembic revision --autogenerate -m "describe the change"
# 2. review the generated file in alembic/versions, then
alembic upgrade head
```

Locally the API also calls `create_all` on start, so new tables appear without a migration. Write the migration anyway, because production does not do this.

## Conventions

- **Where code goes.** Business rules go in `domain/` and stay pure and unit-tested. Workflows that touch the database or providers go in `application/`, with one module per use case. HTTP shape goes in `api/`. Anything that talks to the outside world goes in `infrastructure/`, behind a port in `application/ports.py`. See [ARCHITECTURE.md](ARCHITECTURE.md).
- **Errors.** Raise a `domain.exceptions` class; `api/errors.py` maps it to a status code. Don't raise `HTTPException` from application code.
- **Transactions.** The top-level use case commits; helpers only `flush`.
- **External actions.** Always go through `tool_gateway().execute_recovery_action(...)`, never a provider directly.
- **Frontend.** Routes in `src/app` stay thin. Screens live in `src/features/<area>`. Call the API only through `services/api/recoverx.ts`, and keep `types/api.ts` in sync with `backend/app/api/schemas`.

### Layering checks

These should print nothing (run from `backend/app`):

```bash
grep -rhoE "^from app\.[a-z_]+" domain | grep -v app.domain      # domain stays pure
grep -rn "from app.api" application infrastructure domain core   # nothing depends on the API layer
```

## Troubleshooting

- **Login fails after changing `BOOTSTRAP_ADMIN_PASSWORD`.** The password re-syncs on the next API start.
- **Webhook returns 404 "Unknown Razorpay merchant account".** The account id in the webhook must match the organization's `razorpay_account_id`. On startup, `BOOTSTRAP_RAZORPAY_ACCOUNT_ID` is assigned to the organization if it has none yet.
- **Frontend calls the wrong API.** The `/api` proxy target (`API_URL`, or `NEXT_PUBLIC_API_URL`) is read at build time; restart `npm run dev` or rebuild after changing it.
- **403 "Cookie-authenticated requests must send the X-Requested-With header".** A script is reusing the browser cookie; send `Authorization: Bearer <token>` instead, or add the header.
- **Signed out after changing a role.** Role changes, deactivation and password resets revoke the user's sessions by design.
