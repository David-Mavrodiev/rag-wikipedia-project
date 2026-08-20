from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_BYTES, MIN_PASSWORD_LENGTH
from app.db.models import Session, User

USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]{3,32}$"


def _reject_overlong_password(value: str) -> str:
    # min/max_length count characters; bcrypt's limit is 72 BYTES, so a short
    # string of multi-byte characters can still overflow it.
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    return value


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., pattern=USERNAME_PATTERN)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES)

    _check_password = field_validator("password")(_reject_overlong_password)


class LoginRequest(BaseModel):
    """Sign-in accepts either the email address or the username."""

    identifier: str = Field(..., min_length=1, max_length=320)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_BYTES)


class SSOStartRequest(BaseModel):
    """Home-realm discovery: the domain decides which IdP to send them to."""

    email: EmailStr


class SSOStartResponse(BaseModel):
    authorize_url: str


class SessionResponse(BaseModel):
    id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    # So the UI can avoid offering "sign out" for the browser in use.
    current: bool

    @classmethod
    def from_session(cls, record: Session, current_id: str | None) -> SessionResponse:
        return cls(
            id=record.id,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            expires_at=record.expires_at,
            ip=record.ip,
            user_agent=record.user_agent,
            current=record.id == current_id,
        )


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    username: str
    created_at: datetime
    # Lets the UI tell a password account from a social-only one, which has no
    # password to sign in with.
    has_password: bool
    providers: list[str]
    role: str
    is_active: bool
    organization: str | None

    @classmethod
    def from_user(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            email=user.email,
            username=user.username,
            created_at=user.created_at,
            has_password=user.password_hash is not None,
            providers=sorted(account.provider for account in user.oauth_accounts),
            role=user.role,
            is_active=user.is_active,
            organization=user.organization.name if user.organization else None,
        )
