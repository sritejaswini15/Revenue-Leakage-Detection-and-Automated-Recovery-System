"""FastAPI dependencies: database session, authenticated user and role checks.

Two ways to authenticate:

* ``Authorization: Bearer <token>`` for API clients, scripts and MCP.
* The HttpOnly session cookie set at login, for the web app. Because browsers attach
  cookies automatically, state-changing requests that rely on the cookie must also carry
  the ``X-Requested-With`` header, which another site cannot add without passing CORS.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.application import auth
from app.core.config import settings
from app.domain.constants import MANAGER_ROLES, OPERATOR_ROLES, Role
from app.domain.exceptions import PermissionDeniedError
from app.infrastructure.database.models import User
from app.infrastructure.database.session import get_session

DbSession = Annotated[Session, Depends(get_session)]

CSRF_HEADER = "X-Requested-With"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    return authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else None


def session_token(request: Request) -> str | None:
    """The caller's token: a bearer header, otherwise the session cookie (CSRF-checked)."""
    token = bearer_token(request)
    if token:
        return token
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie and request.method not in SAFE_METHODS and not request.headers.get(CSRF_HEADER):
        raise PermissionDeniedError(f"Cookie-authenticated requests must send the {CSRF_HEADER} header")
    return cookie


def current_user(request: Request, session: DbSession) -> User:
    return auth.resolve_user(session, session_token(request))


CurrentUser = Annotated[User, Depends(current_user)]


def require_roles(*roles: Role) -> Callable[[User], User]:
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise PermissionDeniedError("Insufficient permissions")
        return user

    return dependency


OperatorUser = Annotated[User, Depends(require_roles(*OPERATOR_ROLES))]
ManagerUser = Annotated[User, Depends(require_roles(*MANAGER_ROLES))]
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
