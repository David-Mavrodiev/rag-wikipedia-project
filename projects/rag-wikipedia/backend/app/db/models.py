from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"
ROLES = (ROLE_MEMBER, ROLE_ADMIN)


class Base(DeclarativeBase):
    pass


def _new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Make a value read back from the database comparable to `utcnow()`.

    SQLite has no timezone type and hands back naive datetimes even for
    `DateTime(timezone=True)` columns; comparing one of those to an aware
    datetime raises TypeError.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class Organization(Base):
    """An enterprise tenant and its OIDC connection."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)

    # OIDC connection. The issuer is the discovery root; endpoints and signing
    # keys are read from its well-known document rather than stored here, so a
    # provider can rotate them without a config change.
    issuer: Mapped[str] = mapped_column(String(255))
    client_id: Mapped[str] = mapped_column(String(255))
    # Fernet ciphertext — never the raw secret (see app/core/crypto.py).
    client_secret_encrypted: Mapped[str] = mapped_column(String(512))

    # Which claim carries group membership (Entra ID: "groups", Okta: often
    # "groups" with names), and how those values map to a local role.
    groups_claim: Mapped[str] = mapped_column(String(64), default="groups")
    role_mappings: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)

    # Create accounts on first successful sign-in.
    allow_jit: Mapped[bool] = mapped_column(Boolean, default=True)
    # Refuse password login for this organization's domains.
    enforce_sso: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    domains: Mapped[list[OrganizationDomain]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )


class OrganizationDomain(Base):
    """An email domain owned by an organization — how sign-in is routed."""

    __tablename__ = "organization_domains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    # Globally unique: one domain cannot be claimed by two tenants.
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    organization: Mapped[Organization] = relationship(back_populates="domains")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    # Stored lowercased so "A@x.com" and "a@x.com" cannot become two accounts.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # Null for accounts created through an external provider: they have no local
    # password, and a null here is what stops password login for them.
    password_hash: Mapped[str | None] = mapped_column(String(60), default=None)
    # Null for consumer accounts; set for members of an enterprise tenant.
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), default=None, index=True
    )
    role: Mapped[str] = mapped_column(String(16), default=ROLE_MEMBER)
    # Deactivation is the kill switch: it fails every request, including ones
    # holding an otherwise valid session.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        # eager-loaded: the async session cannot lazy-load after the request
        # has committed and detached the instance
        lazy="selectin",
    )
    organization: Mapped[Organization | None] = relationship(lazy="selectin")


class OAuthAccount(Base):
    """A social or enterprise identity linked to a local user."""

    __tablename__ = "oauth_accounts"
    # One provider account maps to exactly one local user.
    __table_args__ = (UniqueConstraint("provider", "account_id", name="uq_provider_account"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    # "google", "github", or "oidc:<organization id>" for an enterprise tenant.
    provider: Mapped[str] = mapped_column(String(64))
    # The provider's immutable user id — never the email, which can change hands.
    account_id: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="oauth_accounts")


class Session(Base):
    """A server-side login session; the cookie only carries this row's id.

    Keeping sessions in the database is what makes revocation possible: a
    stateless JWT stays valid until it expires no matter what happens to the
    account behind it.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Moved forward on use; the gap since then is the idle timeout.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Hard ceiling, never extended.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    ip: Mapped[str | None] = mapped_column(String(45), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)

    user: Mapped[User] = relationship(lazy="selectin")


class AuthEvent(Base):
    """Append-only audit trail. Nothing in the app updates or deletes a row."""

    __tablename__ = "auth_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    event: Mapped[str] = mapped_column(String(48), index=True)
    # Plain columns, not foreign keys: the trail has to outlive the account it
    # describes, and a cascade would erase exactly the history an auditor wants.
    user_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    organization_id: Mapped[str | None] = mapped_column(String(36), default=None)
    # The address that was tried, so failures on non-existent accounts are still
    # attributable. Never a password or a token.
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    ip: Mapped[str | None] = mapped_column(String(45), default=None)
    detail: Mapped[str | None] = mapped_column(String(255), default=None)
