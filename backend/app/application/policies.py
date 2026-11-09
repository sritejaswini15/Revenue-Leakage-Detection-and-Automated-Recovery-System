"""Merchant recovery policy management."""

from typing import Any

from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.infrastructure.database.models import Policy, User
from app.infrastructure.database.repositories import get_or_create_policy


def get_policy(session: Session, organization_id: str) -> Policy:
    policy = get_or_create_policy(session, organization_id)
    session.commit()
    return policy


def update_policy(session: Session, user: User, changes: dict[str, Any]) -> Policy:
    policy = get_or_create_policy(session, user.organization_id)
    for key, value in changes.items():
        setattr(policy, key, value)
    policy.updated_by = user.email
    record_audit(session, user.organization_id, "policy.updated", changes, actor=user.email)
    session.commit()
    return policy
