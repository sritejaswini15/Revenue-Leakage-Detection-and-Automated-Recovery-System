"""User management for merchant administrators."""

from fastapi import APIRouter, status

from app.api.dependencies import AdminUser, DbSession
from app.api.schemas.auth import ManagedUserOut, PasswordResetIn, UserCreateIn, UserUpdateIn
from app.application import users

router = APIRouter(prefix="/users", tags=["users"])
NOT_FOUND = {404: {"description": "User not found"}}


@router.get("")
def list_users(session: DbSession, admin: AdminUser) -> list[ManagedUserOut]:
    return [ManagedUserOut.of(view) for view in users.list_users(session, admin.organization_id)]


@router.post("", status_code=status.HTTP_201_CREATED, responses={409: {"description": "Email already in use"}})
def create_user(payload: UserCreateIn, session: DbSession, admin: AdminUser) -> ManagedUserOut:
    """Create an account with a temporary password. No email is sent; share the password directly."""
    view = users.create_user(
        session, admin, payload.name, payload.email, payload.role, payload.password.get_secret_value()
    )
    return ManagedUserOut.of(view)


@router.patch("/{user_id}", responses={**NOT_FOUND, 409: {"description": "Would leave no active administrator"}})
def update_user(user_id: str, payload: UserUpdateIn, session: DbSession, admin: AdminUser) -> ManagedUserOut:
    """Rename, change role, or (de)activate. Role changes and deactivation sign the user out everywhere."""
    return ManagedUserOut.of(
        users.update_user(session, admin, user_id, name=payload.name, role=payload.role, is_active=payload.is_active)
    )


@router.post("/{user_id}/password", responses=NOT_FOUND)
def reset_password(user_id: str, payload: PasswordResetIn, session: DbSession, admin: AdminUser) -> ManagedUserOut:
    """Set a temporary password and sign the user out everywhere."""
    return ManagedUserOut.of(users.reset_password(session, admin, user_id, payload.password.get_secret_value()))


@router.post("/{user_id}/revoke-sessions", responses=NOT_FOUND)
def revoke_sessions(user_id: str, session: DbSession, admin: AdminUser) -> ManagedUserOut:
    return ManagedUserOut.of(users.revoke_user_sessions(session, admin, user_id))
