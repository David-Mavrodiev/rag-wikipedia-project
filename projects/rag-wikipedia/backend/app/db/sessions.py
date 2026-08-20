from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Session, User, ensure_utc, utcnow

REASON_REVOKED = "revoked"
REASON_EXPIRED = "expired"
REASON_IDLE = "idle"

# `last_seen_at` only has to be accurate to within the idle window, so it is not
# worth a database write on every single request.
TOUCH_INTERVAL_SECONDS = 60


async def create_session(
    db: AsyncSession, user: User, *, ip: str | None = None, user_agent: str | None = None
) -> Session:
    now = utcnow()
    record = Session(
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_lifetime_hours),
        ip=ip,
        user_agent=user_agent[:255] if user_agent else None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get(db: AsyncSession, session_id: str) -> Session | None:
    return await db.get(Session, session_id)


def expiry_reason(record: Session, now: datetime | None = None) -> str | None:
    """Why this session is no longer usable, or None while it still is."""
    moment = now or utcnow()
    if record.revoked_at is not None:
        return REASON_REVOKED
    if ensure_utc(record.expires_at) <= moment:
        return REASON_EXPIRED
    idle_deadline = ensure_utc(record.last_seen_at) + timedelta(
        minutes=settings.session_idle_timeout_minutes
    )
    if idle_deadline <= moment:
        return REASON_IDLE
    return None


async def touch(db: AsyncSession, record: Session) -> None:
    now = utcnow()
    if (now - ensure_utc(record.last_seen_at)).total_seconds() < TOUCH_INTERVAL_SECONDS:
        return
    record.last_seen_at = now
    await db.commit()


async def revoke(db: AsyncSession, record: Session) -> None:
    if record.revoked_at is None:
        record.revoked_at = utcnow()
        await db.commit()


async def revoke_all_for_user(
    db: AsyncSession, user_id: str, *, except_id: str | None = None
) -> int:
    records = await list_active_for_user(db, user_id)
    revoked = 0
    now = utcnow()
    for record in records:
        if record.id == except_id:
            continue
        record.revoked_at = now
        revoked += 1
    if revoked:
        await db.commit()
    return revoked


async def list_active_for_user(db: AsyncSession, user_id: str) -> list[Session]:
    result = await db.execute(
        select(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .order_by(Session.created_at.desc())
    )
    return [record for record in result.scalars().all() if expiry_reason(record) is None]
