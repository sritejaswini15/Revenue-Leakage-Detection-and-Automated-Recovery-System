"""Merchant user management: administrators add people, set roles, deactivate and reset access.

RecoverX does not send email. An administrator creates the account with a temporary
password and shares it with the person through their own channel; the person then changes
it under Account. Every change is audited, and any change to a person's access signs them
out everywhere.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.application.auth import (
    is_environment_managed,
    normalize_email,
    revoke_sessions,
    validate_new_password,
)
from app.core.security import hash_password
from app.core.time import utc_now
from app.domain.constants import Role
from app.domain.exceptions import ConflictError, InvalidRequestError, NotFoundError, PermissionDeniedError
from app.infrastructure.database.models import AuthSession, User


@dataclass(frozen=True)
class UserView:
    user: User
    active_sessions: int
    last_sign_in_at: datetime | None
    environment_managed: bool


def _views(session: Session, users: list[User]) -> list[UserView]:
    ids = [user.id for user in users]
    now = utc_now()
    active = dict(
        session.execute(
            select(AuthSession.user_id, func.count(AuthSession.id))
            .where(AuthSession.user_id.in_(ids), AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
            .group_by(AuthSession.user_id)
        ).all()
    )
    last = dict(
        session.execute(
            select(AuthSession.user_id, func.max(AuthSession.created_at))
            .where(AuthSession.user_id.in_(ids))
            .group_by(AuthSession.user_id)
        ).all()
    )
    return [UserView(u, active.get(u.id, 0), last.get(u.id), is_environment_managed(u)) for u in users]


def list_users(session: Session, organization_id: str) -> list[UserView]:
    users = session.scalars(
        select(User).where(User.organization_id == organization_id).order_by(User.is_active.desc(), User.name)
    ).all()
    return _views(session, list(users))


def _get(session: Session, admin: User, user_id: str) -> User:
    user = session.scalar(select(User).where(User.id == user_id, User.organization_id == admin.organization_id))
    if not user:
        raise NotFoundError("User not found")
    return user


def _view(session: Session, user: User) -> UserView:
    return _views(session, [user])[0]


def _active_admins(session: Session, organization_id: str) -> int:
    return (
        session.scalar(
            select(func.count(User.id)).where(
                User.organization_id == organization_id, User.role == Role.ADMIN, User.is_active.is_(True)
            )
        )
        or 0
    )


def create_user(session: Session, admin: User, name: str, email: str, role: Role, password: str) -> UserView:
    email = normalize_email(email)
    if "@" not in email:
        raise InvalidRequestError("Enter a valid email address")
    if session.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise ConflictError("A user with this email already exists")
    validate_new_password(password)
    user = User(
        organization_id=admin.organization_id,
        email=email,
        name=name.strip(),
        role=role,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    session.flush()
    record_audit(session, admin.organization_id, "user.created", {"user": email, "role": role}, actor=admin.email)
    session.commit()
    return _view(session, user)


def update_user(
    session: Session,
    admin: User,
    user_id: str,
    name: str | None = None,
    role: Role | None = None,
    is_active: bool | None = None,
) -> UserView:
    user = _get(session, admin, user_id)
    changes: dict[str, object] = {}
    if name is not None and name.strip() and name.strip() != user.name:
        changes["name"] = name.strip()
    if role is not None and role != user.role:
        changes["role"] = role
    if is_active is not None and is_active != user.is_active:
        changes["is_active"] = is_active
    if not changes:
        return _view(session, user)

    access_change = "role" in changes or changes.get("is_active") is False
    if access_change and user.id == admin.id:
        raise PermissionDeniedError("You cannot change your own role or deactivate yourself")
    if access_change and is_environment_managed(user):
        raise PermissionDeniedError("The bootstrap administrator is managed by the deployment environment")
    loses_admin = user.role == Role.ADMIN and user.is_active and access_change
    if loses_admin and _active_admins(session, admin.organization_id) <= 1:
        raise ConflictError("Every merchant needs at least one active administrator")

    for key, value in changes.items():
        setattr(user, key, value)
    revoked = revoke_sessions(session, user.id) if access_change else 0
    if "is_active" in changes:
        event = "user.activated" if changes["is_active"] else "user.deactivated"
    else:
        event = "user.updated"
    record_audit(
        session,
        admin.organization_id,
        event,
        {"user": user.email, "changes": {k: str(v) for k, v in changes.items()}, "sessions_revoked": revoked},
        actor=admin.email,
    )
    session.commit()
    return _view(session, user)


def reset_password(session: Session, admin: User, user_id: str, password: str) -> UserView:
    """Set a temporary password for someone else and sign them out everywhere."""
    user = _get(session, admin, user_id)
    if user.id == admin.id:
        raise InvalidRequestError("Change your own password under Account, which asks for the current one")
    if is_environment_managed(user):
        raise PermissionDeniedError("This account's password is managed by BOOTSTRAP_ADMIN_PASSWORD")
    validate_new_password(password)
    user.password_hash = hash_password(password)
    revoked = revoke_sessions(session, user.id)
    record_audit(
        session,
        admin.organization_id,
        "user.password_reset",
        {"user": user.email, "sessions_revoked": revoked},
        actor=admin.email,
    )
    session.commit()
    return _view(session, user)


def revoke_user_sessions(session: Session, admin: User, user_id: str) -> UserView:
    user = _get(session, admin, user_id)
    revoked = revoke_sessions(session, user.id)
    record_audit(
        session,
        admin.organization_id,
        "user.sessions_revoked",
        {"user": user.email, "sessions_revoked": revoked},
        actor=admin.email,
    )
    session.commit()
    return _view(session, user)
