"""Authentication: login, server-side sessions, token validation and revocation."""

import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.application.audit import record_audit
from app.core.config import settings
from app.core.security import (
    TokenClaims,
    constant_time_equals,
    decode_token,
    encode_token,
    hash_password,
    sha256_hex,
    verify_password,
)
from app.core.time import as_utc, utc_now
from app.domain.constants import Role
from app.domain.exceptions import AuthenticationError, InvalidRequestError, PermissionDeniedError, RateLimitedError
from app.infrastructure.database.models import AuthSession, LoginFailure, User

logger = logging.getLogger(__name__)

_QUOTES = "\"'"
MAX_LOGIN_FAILURES = 5
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
MIN_PASSWORD_LENGTH = 12


def normalize_email(email: str) -> str:
    return email.strip().lower().strip(_QUOTES)


def _check_login_rate(session: Session, email: str) -> None:
    """Failed logins are counted in the database, so the limit holds across every API instance."""
    cutoff = utc_now() - LOGIN_FAILURE_WINDOW
    session.execute(delete(LoginFailure).where(LoginFailure.email == email, LoginFailure.created_at <= cutoff))
    failures = session.scalar(select(func.count(LoginFailure.id)).where(LoginFailure.email == email)) or 0
    if failures >= MAX_LOGIN_FAILURES:
        session.commit()
        raise RateLimitedError("Too many login attempts; try again later")


def _record_login_failure(session: Session, email: str) -> None:
    session.add(LoginFailure(email=email[:320]))
    session.commit()


def authenticate(session: Session, email: str, password: str) -> tuple[str, User]:
    """Return a new session token for valid credentials.

    Surrounding whitespace and quotes are tolerated because credentials are often
    pasted from hosting dashboards.
    """
    clean_email = normalize_email(email)
    _check_login_rate(session, clean_email)

    user = session.scalar(select(User).where(func.lower(User.email) == clean_email))
    candidates = {password.strip(), password.strip().strip(_QUOTES)} - {""}
    if not user or not any(verify_password(candidate, user.password_hash) for candidate in candidates):
        _record_login_failure(session, clean_email)
        raise AuthenticationError("Invalid credentials")
    if not user.is_active:
        raise AuthenticationError("This account is deactivated; ask an administrator to reactivate it")

    session.execute(delete(LoginFailure).where(LoginFailure.email == clean_email))
    token = issue_token(session, user)
    session.commit()
    return token, user


def issue_token(session: Session, user: User) -> str:
    session_id = str(uuid4())
    expires_at = utc_now() + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS)
    claims = TokenClaims(user.id, user.organization_id, user.role, session_id, int(expires_at.timestamp()))
    token = encode_token(claims, settings.AUTH_SECRET)
    session.add(AuthSession(id=session_id, user_id=user.id, token_hash=sha256_hex(token), expires_at=expires_at))
    session.flush()
    return token


def _session_for_token(session: Session, token: str) -> tuple[AuthSession, TokenClaims] | None:
    claims = decode_token(token, settings.AUTH_SECRET)
    if not claims or claims.expires_at < int(utc_now().timestamp()):
        return None
    auth_session = session.get(AuthSession, claims.session_id)
    if (
        not auth_session
        or auth_session.revoked_at
        or as_utc(auth_session.expires_at) <= utc_now()
        or not constant_time_equals(auth_session.token_hash, sha256_hex(token))
    ):
        return None
    return auth_session, claims


def user_for_token(session: Session, token: str) -> User | None:
    found = _session_for_token(session, token)
    if not found:
        return None
    _, claims = found
    user = session.scalar(select(User).where(User.id == claims.user_id, User.organization_id == claims.organization_id))
    return user if user and user.is_active and user.role == claims.role else None


def resolve_user(session: Session, token: str | None) -> User:
    if token:
        user = user_for_token(session, token)
        if user:
            return user
    if not settings.AUTH_REQUIRED:
        # Development convenience only: act as the first administrator.
        user = session.scalar(select(User).where(User.role == Role.ADMIN, User.is_active.is_(True)).limit(1))
        if user:
            return user
    raise AuthenticationError("Authentication required")


def revoke_token(session: Session, token: str) -> None:
    claims = decode_token(token, settings.AUTH_SECRET)
    auth_session = session.get(AuthSession, claims.session_id) if claims else None
    if auth_session:
        auth_session.revoked_at = utc_now()
        session.commit()


def revoke_sessions(session: Session, user_id: str, keep_session_id: str | None = None) -> int:
    """Sign a user out everywhere (optionally except the current session). Caller commits."""
    query = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if keep_session_id:
        query = query.where(AuthSession.id != keep_session_id)
    return session.execute(query.values(revoked_at=utc_now())).rowcount or 0


def is_environment_managed(user: User) -> bool:
    """The bootstrap administrator's password and role are set from the environment on every start."""
    return bool(settings.BOOTSTRAP_ADMIN_EMAIL) and user.email.lower() == settings.BOOTSTRAP_ADMIN_EMAIL.lower()


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidRequestError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters")
    if password.strip() != password:
        raise InvalidRequestError("Passwords cannot start or end with spaces")


def change_password(session: Session, user: User, token: str | None, current: str, new: str) -> int:
    """Change your own password; every other session of yours is signed out. Returns how many."""
    if is_environment_managed(user):
        raise PermissionDeniedError("This account's password is managed by BOOTSTRAP_ADMIN_PASSWORD")
    if not verify_password(current, user.password_hash):
        raise AuthenticationError("Current password is incorrect")
    validate_new_password(new)
    user.password_hash = hash_password(new)
    found = _session_for_token(session, token) if token else None
    revoked = revoke_sessions(session, user.id, keep_session_id=found[0].id if found else None)
    record_audit(
        session, user.organization_id, "user.password_changed", {"sessions_revoked": revoked}, actor=user.email
    )
    session.commit()
    return revoked
