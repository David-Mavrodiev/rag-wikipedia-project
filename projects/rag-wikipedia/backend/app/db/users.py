from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ROLE_MEMBER, OAuthAccount, User

_UNSAFE_USERNAME_CHARS = re.compile(r"[^a-z0-9_.-]+")
_USERNAME_FALLBACK = "user"
# After this many "name2", "name3"… attempts, stop scanning and use a random
# suffix instead of walking a long tail of taken names.
_MAX_SUFFIX_ATTEMPTS = 20


def normalize_email(email: str) -> str:
    """Case-fold the address so one mailbox cannot become two accounts."""
    return email.strip().lower()


async def get_by_id(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_by_identifier(session: AsyncSession, identifier: str) -> User | None:
    """Look a user up by either email or username — login accepts both."""
    if "@" in identifier:
        return await get_by_email(session, identifier)
    return await get_by_username(session, identifier)


async def get_by_oauth_account(
    session: AsyncSession, provider: str, account_id: str
) -> User | None:
    result = await session.execute(
        select(User)
        .join(OAuthAccount)
        .where(OAuthAccount.provider == provider, OAuthAccount.account_id == account_id)
    )
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    username: str,
    password_hash: str | None = None,
    organization_id: str | None = None,
    role: str = ROLE_MEMBER,
) -> User:
    user = User(
        email=normalize_email(email),
        username=username,
        password_hash=password_hash,
        organization_id=organization_id,
        role=role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def set_role(session: AsyncSession, user: User, role: str) -> None:
    if user.role != role:
        user.role = role
        await session.commit()


async def deactivate(session: AsyncSession, user: User) -> None:
    if user.is_active:
        user.is_active = False
        await session.commit()


async def link_oauth_account(
    session: AsyncSession, user: User, provider: str, account_id: str
) -> None:
    session.add(OAuthAccount(provider=provider, account_id=account_id, user_id=user.id))
    await session.commit()


async def suggest_username(session: AsyncSession, preferred: str) -> str:
    """Derive a free username from what the provider gave us.

    Social sign-up has no username field, so the provider's login (or the email
    local part) is used and de-duplicated.
    """
    base = _UNSAFE_USERNAME_CHARS.sub("", preferred.strip().lower())[:24] or _USERNAME_FALLBACK
    if len(base) < 3:
        base = f"{base}{_USERNAME_FALLBACK}"[:24]

    if await get_by_username(session, base) is None:
        return base
    for suffix in range(2, _MAX_SUFFIX_ATTEMPTS + 2):
        candidate = f"{base}{suffix}"
        if await get_by_username(session, candidate) is None:
            return candidate
    return f"{base}{secrets.token_hex(4)}"
