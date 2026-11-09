"""Idempotent startup tasks: primary organization, administrator account, demo data."""

import logging

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.application.workflows.scheduler import run_due_workflows
from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.core.time import utc_now
from app.domain.constants import Role
from app.domain.exceptions import ConflictError, PermissionDeniedError
from app.infrastructure.database.models import AuthSession, Customer, Organization, User
from app.infrastructure.database.seed import generate_synthetic_dataset

logger = logging.getLogger(__name__)

DEMO_DATASET = {"customer_count": 1000, "case_count": 500, "seed": 20260902}


def run_startup_tasks(session: Session) -> None:
    ensure_primary_organization(session)
    ensure_bootstrap_admin(session)
    if settings.SEED_DEMO_DATA and not session.scalar(select(func.count(Customer.id))):
        logger.info("Seeding demo dataset %s", DEMO_DATASET)
        generate_synthetic_dataset(session, **DEMO_DATASET)


def ensure_primary_organization(session: Session) -> Organization:
    """Create the merchant organization on first start and attach the configured Razorpay account."""
    organization = session.scalar(select(Organization).order_by(Organization.created_at).limit(1))
    account_id = settings.BOOTSTRAP_RAZORPAY_ACCOUNT_ID
    if organization is None:
        organization = Organization(name=settings.BOOTSTRAP_ORGANIZATION_NAME, razorpay_account_id=account_id)
        session.add(organization)
        logger.info("Created organization %r", organization.name)
    elif account_id and not organization.razorpay_account_id:
        organization.razorpay_account_id = account_id
    session.commit()
    return organization


def ensure_bootstrap_admin(session: Session) -> None:
    """Make sure BOOTSTRAP_ADMIN_EMAIL can sign in with BOOTSTRAP_ADMIN_PASSWORD as an ADMIN.

    Re-running with a changed password rotates it; no other account is touched.
    """
    email, password = settings.BOOTSTRAP_ADMIN_EMAIL, settings.BOOTSTRAP_ADMIN_PASSWORD
    if not email or not password:
        logger.info("BOOTSTRAP_ADMIN_EMAIL/PASSWORD not set; skipping administrator bootstrap")
        return

    email = email.lower()
    admin = session.scalar(select(User).where(func.lower(User.email) == email))
    if admin is None:
        organization = session.scalar(select(Organization).order_by(Organization.created_at).limit(1))
        admin = User(organization_id=organization.id, email=email, name="Bootstrap Administrator", role=Role.ADMIN)
        session.add(admin)
        logger.info("Created bootstrap administrator %s", email)
    admin.role = Role.ADMIN
    admin.is_active = True  # the environment's administrator is always a way back in
    if not verify_password(password, admin.password_hash):
        rotated = admin.password_hash is not None
        admin.password_hash = hash_password(password)
        if rotated:  # sessions issued under the old password must not survive a rotation
            session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == admin.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=utc_now())
            )
        logger.info("Synchronized bootstrap administrator password for %s", email)
    session.commit()


def _require_demo_deployment(session: Session) -> None:
    if not settings.demo_reset_enabled:
        raise PermissionDeniedError("Demo operations are disabled in production (set SEED_DEMO_DATA=true to allow)")
    if (session.scalar(select(func.count(Organization.id))) or 0) > 1:
        raise ConflictError(
            "Demo operations would affect other merchants' data; they need a single-merchant deployment"
        )


def run_due_workflows_now(session: Session) -> dict[str, int]:
    """Run the hourly job immediately (expiry, follow-ups, due promises). Demo deployments only."""
    _require_demo_deployment(session)
    return run_due_workflows(session)


def reset_demo_dataset(session: Session, customer_count: int, case_count: int, seed: int) -> dict[str, int]:
    """Replace business data with the demo dataset. Only for single-merchant demo deployments."""
    _require_demo_deployment(session)
    return generate_synthetic_dataset(session, customer_count=customer_count, case_count=case_count, seed=seed)
