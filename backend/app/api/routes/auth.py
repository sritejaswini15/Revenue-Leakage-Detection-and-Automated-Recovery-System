from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import CurrentUser, DbSession, session_token
from app.api.schemas.auth import LoginRequest, LoginResponse, PasswordChangeIn, PasswordChangeOut, UserOut
from app.application import auth
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    responses={401: {"description": "Invalid credentials"}, 429: {"description": "Too many failed attempts"}},
)
def login(payload: LoginRequest, response: Response, session: DbSession) -> LoginResponse:
    """Sets the HttpOnly session cookie for browsers and returns the same token for API clients."""
    token, user = auth.authenticate(session, payload.email, payload.password)
    response.set_cookie(
        settings.SESSION_COOKIE_NAME,
        token,
        max_age=settings.ACCESS_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return LoginResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, session: DbSession, _user: CurrentUser) -> None:
    token = session_token(request)
    if token:
        auth.revoke_token(session, token)
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    """The signed-in user; the web app calls this to restore a session from its cookie."""
    return UserOut.model_validate(user)


@router.post(
    "/password",
    responses={401: {"description": "Current password is incorrect"}, 403: {"description": "Managed by environment"}},
)
def change_password(
    payload: PasswordChangeIn, request: Request, session: DbSession, user: CurrentUser
) -> PasswordChangeOut:
    """Change your own password. Your other sessions are signed out; this one stays signed in."""
    revoked = auth.change_password(
        session,
        user,
        session_token(request),
        payload.current_password.get_secret_value(),
        payload.new_password.get_secret_value(),
    )
    return PasswordChangeOut(sessions_revoked=revoked)
