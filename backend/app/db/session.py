from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import DATABASE_URL
from app.db.base import Base

_engine: AsyncEngine = create_async_engine(
    DATABASE_URL, echo=False,
    # Recycles/validates connections before use so a DB restart, load-balancer idle timeout, or
    # cloud provider connection kill doesn't surface as a mysterious mid-request error - the one
    # extra round-trip is worth it for how often that class of failure hits a long-lived pool.
    pool_pre_ping=True, pool_recycle=1800,
)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    return _engine


async def init_models() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as session:
        yield session
