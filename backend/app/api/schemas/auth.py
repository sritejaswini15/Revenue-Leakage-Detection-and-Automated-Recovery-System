from pydantic import BaseModel, Field, SecretStr

from app.api.schemas.common import ApiModel, UtcDatetime
from app.application.users import UserView
from app.domain.constants import Role


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class UserOut(ApiModel):
    id: str
    name: str
    email: str
    role: str
    organization_id: str


class LoginResponse(BaseModel):
    """Browsers use the HttpOnly session cookie set alongside; API clients send ``access_token``."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    user: UserOut


class PasswordChangeIn(BaseModel):
    current_password: SecretStr = Field(max_length=256)
    new_password: SecretStr = Field(max_length=128)


class PasswordChangeOut(BaseModel):
    sessions_revoked: int


class ManagedUserOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    is_active: bool
    created_at: UtcDatetime
    active_sessions: int
    last_sign_in_at: UtcDatetime | None
    environment_managed: bool

    @classmethod
    def of(cls, view: UserView) -> "ManagedUserOut":
        user = view.user
        return cls(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
            active_sessions=view.active_sessions,
            last_sign_in_at=view.last_sign_in_at,
            environment_managed=view.environment_managed,
        )


class UserCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=255)
    role: Role
    password: SecretStr = Field(max_length=128, description="Temporary password; share it with the person directly")


class UserUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)
    role: Role | None = None
    is_active: bool | None = None


class PasswordResetIn(BaseModel):
    password: SecretStr = Field(max_length=128)
