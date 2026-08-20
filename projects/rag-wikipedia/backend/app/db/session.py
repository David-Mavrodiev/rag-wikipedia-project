from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.models import Base


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Build the engine lazily so DATABASE_URL can be swapped before first use."""
    url = settings.database_url
    if _is_in_memory(url):
        # StaticPool keeps every session on one connection: with the default
        # pool each new connection would open its own empty in-memory database.
        return create_async_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    if url.startswith("sqlite"):
        _ensure_parent_dir(url)
    return create_async_engine(url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session scoped to one request."""
    async with get_sessionmaker()() as session:
        yield session


async def init_db() -> None:
    """Create missing tables at startup.

    Enough for a single-service schema that only grows by addition. A real
    migration tool (Alembic) is what you want once columns start changing under
    live data.
    """
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _is_in_memory(url: str) -> bool:
    return ":memory:" in url or url.endswith("://")


def _ensure_parent_dir(url: str) -> None:
    _, _, path = url.partition("///")
    if path and path != ":memory:":
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
