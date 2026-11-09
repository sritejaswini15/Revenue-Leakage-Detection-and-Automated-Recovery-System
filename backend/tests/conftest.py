"""Test configuration.

Environment variables are pinned *before* the application is imported so a
developer's local .env can never point the suite at a real database. To run the
suite against PostgreSQL, set TEST_DATABASE_URL to an empty, disposable database.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TEST_DIR = Path(tempfile.mkdtemp(prefix="recoverx-tests-"))
WEBHOOK_SECRET = "test-webhook-secret"
ACCOUNT_ID = "acct_test_0001"

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": os.getenv("TEST_DATABASE_URL") or f"sqlite:///{(_TEST_DIR / 'recoverx.db').as_posix()}",
        "AUTH_REQUIRED": "true",
        "AUTH_SECRET": "test-auth-secret",
        "SEED_DEMO_DATA": "true",
        "PAYMENT_PROVIDER": "simulated",
        "RAZORPAY_KEY_ID": "",
        "RAZORPAY_KEY_SECRET": "",
        "RAZORPAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "BOOTSTRAP_RAZORPAY_ACCOUNT_ID": ACCOUNT_ID,
        "BOOTSTRAP_ADMIN_EMAIL": "",
        "BOOTSTRAP_ADMIN_PASSWORD": "",
        "TEMPORAL_ENABLED": "false",
        "LOG_LEVEL": "WARNING",
    }
)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_database() -> Iterator[None]:
    yield
    from app.infrastructure.database.session import engine

    engine.dispose()
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """The full application, including startup (schema, organization, demo data)."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    from tests.helpers import login

    return login(client)
