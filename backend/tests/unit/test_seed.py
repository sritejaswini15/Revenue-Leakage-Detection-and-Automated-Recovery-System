"""The seeder must work with enforced foreign keys (as on PostgreSQL), including re-seeding."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.application.ports import SignedWebhook
from app.application.webhooks import ingest_simulated_webhook
from app.core.security import hash_password
from app.core.time import utc_now
from app.infrastructure.database.models import (
    AuthSession,
    Base,
    Customer,
    ExperimentResult,
    Organization,
    RecoveryCase,
    User,
    WebhookReceipt,
)
from app.infrastructure.database.seed import generate_synthetic_dataset
from app.infrastructure.payments.simulated_razorpay import SimulatedRazorpayProvider


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


def _simulate_live_activity(session: Session) -> None:
    """Process a webhook so that receipts, audit events, links and actions reference seeded rows."""
    organization = session.scalar(select(Organization))
    customer = session.get(Customer, "cus_rahul_golden_demo")
    simulator = SimulatedRazorpayProvider()
    webhook: SignedWebhook = simulator.sign_webhook(
        "payment.failed", {"id": "pay_live_1", "customer_id": customer.razorpay_customer_id, "amount": 500000}
    )
    outcome = ingest_simulated_webhook(session, organization, customer, webhook, actor="test")
    assert outcome.case is not None and outcome.case.action_executed
    session.commit()


def test_seed_with_enforced_foreign_keys(session):
    result = generate_synthetic_dataset(session, customer_count=50, case_count=20, seed=20260902)

    assert result == {"customers": 50, "cases": 20, "seed": 20260902}
    assert session.scalar(select(func.count(RecoveryCase.id))) == 20
    experiment_results = session.scalars(select(ExperimentResult)).all()
    assert experiment_results
    assert all(session.get(RecoveryCase, item.case_id) for item in experiment_results)


def test_reseed_after_activity_keeps_users_and_sessions(session):
    generate_synthetic_dataset(session, customer_count=50, case_count=20)
    organization = session.scalar(select(Organization))
    owner = User(organization_id=organization.id, email="owner@merchant.test", name="Owner", role="ADMIN",
                 password_hash=hash_password("owner-password"))  # fmt: skip
    session.add(owner)
    session.flush()
    session.add(AuthSession(id="session-1", user_id=owner.id, token_hash="x" * 64, expires_at=utc_now()))
    session.commit()
    _simulate_live_activity(session)
    assert session.scalar(select(func.count(WebhookReceipt.id))) == 1

    generate_synthetic_dataset(session, customer_count=50, case_count=20)

    assert session.scalar(select(User).where(User.email == "owner@merchant.test")) is not None
    assert session.get(AuthSession, "session-1") is not None
    assert session.scalar(select(func.count(WebhookReceipt.id))) == 0
    assert session.scalar(select(func.count(RecoveryCase.id))) == 20


def test_seed_is_deterministic(session):
    generate_synthetic_dataset(session, customer_count=60, case_count=40, seed=7)
    first = session.execute(
        select(RecoveryCase.id, RecoveryCase.amount, RecoveryCase.status).order_by(RecoveryCase.id)
    ).all()
    generate_synthetic_dataset(session, customer_count=60, case_count=40, seed=7)
    second = session.execute(
        select(RecoveryCase.id, RecoveryCase.amount, RecoveryCase.status).order_by(RecoveryCase.id)
    ).all()
    assert first == second
