"""Append-only audit trail for authentication events.

Everything security-relevant that a reviewer would ask about — who signed in,
what failed, who was provisioned, whose access was cut off — lands here. Nothing
in the application ever updates or deletes a row.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthEvent, User

EVENT_REGISTERED = "user.registered"
EVENT_LOGIN_SUCCEEDED = "login.password.succeeded"
EVENT_LOGIN_FAILED = "login.password.failed"
EVENT_LOGIN_BLOCKED_SSO = "login.password.blocked_sso_required"
EVENT_LOGOUT = "logout"
EVENT_OAUTH_SUCCEEDED = "login.oauth.succeeded"
EVENT_OAUTH_FAILED = "login.oauth.failed"
EVENT_SSO_SUCCEEDED = "login.sso.succeeded"
EVENT_SSO_FAILED = "login.sso.failed"
EVENT_SSO_PROVISIONED = "user.provisioned.sso"
EVENT_ROLE_CHANGED = "user.role.changed"
EVENT_SESSION_ENDED = "session.ended"
EVENT_SESSIONS_REVOKED = "session.revoked"
EVENT_USER_DEACTIVATED = "user.deactivated"
EVENT_ORGANIZATION_CREATED = "organization.created"


async def record(
    db: AsyncSession,
    event: str,
    *,
    user: User | None = None,
    email: str | None = None,
    organization_id: str | None = None,
    ip: str | None = None,
    detail: str | None = None,
) -> None:
    """Write one event. Commits, so call it after the change it describes."""
    db.add(
        AuthEvent(
            event=event,
            user_id=user.id if user else None,
            email=(email or (user.email if user else None)),
            organization_id=organization_id or (user.organization_id if user else None),
            ip=ip,
            detail=detail[:255] if detail else None,
        )
    )
    await db.commit()


async def list_events(db: AsyncSession, *, limit: int = 50, offset: int = 0) -> list[AuthEvent]:
    result = await db.execute(
        select(AuthEvent)
        .order_by(AuthEvent.created_at.desc(), AuthEvent.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
