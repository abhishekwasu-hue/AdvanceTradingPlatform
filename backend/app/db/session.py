import time
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.config import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE, DB_POOL_TIMEOUT_SECONDS
from app.db.base import Base

# LD-7: the pool is sized per process (DB_POOL_SIZE / DB_MAX_OVERFLOW / DB_POOL_TIMEOUT_SECONDS, app/core/config.py);
# a pool-less URL (SQLite in a tool) keeps its own pool.


class TimedQueuePool(AsyncAdaptedQueuePool):
    """LD-7: the async queue pool, recording how long each checkout waited (`atp_db_pool_wait_seconds`): a full pool
    shows as waits here, not as CPU. Nothing else changes."""

    def _do_get(self) -> Any:
        started = time.perf_counter()
        try:
            return super()._do_get()
        finally:
            from app.observability.metrics import DB_POOL_WAIT
            DB_POOL_WAIT.observe(time.perf_counter() - started)


_pool_args = ({"pool_size": DB_POOL_SIZE, "max_overflow": DB_MAX_OVERFLOW, "pool_timeout": DB_POOL_TIMEOUT_SECONDS,
               "poolclass": TimedQueuePool}
              if DATABASE_URL.startswith("postgresql") else {})
_engine: AsyncEngine = create_async_engine(
    DATABASE_URL, echo=False,
    # Recycles/validates connections before use so a DB restart, load-balancer idle timeout, or
    # cloud provider connection kill doesn't surface as a mysterious mid-request error - the one
    # extra round-trip is worth it for how often that class of failure hits a long-lived pool.
    pool_pre_ping=True, pool_recycle=1800, **_pool_args,
)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    return _engine


async def init_models() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def release_connection(session: AsyncSession) -> None:
    """LD-7: give the session's pooled connection back before a slow step that needs no database - a bcrypt check, a
    broker call. A session holds its connection from its first query to the end of its transaction, so a request that
    reads, then waits a quarter of a second on bcrypt (or seconds on a broker), keeps a pool connection "idle in
    transaction" all that time; a login wave then empties the pool and every other request waits for a connection
    (the CI load test: 1,411 pool timeouts in a 500-user run). The next query opens a new transaction.

    Only for a transaction that has READ so far: it ends with a commit (a rollback would expire every loaded object -
    the caller still uses them), so unflushed changes are refused rather than committed half-way. The callers release
    before their first write."""
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("release_connection: the session has unsaved changes - commit them deliberately first")
    if session.in_transaction():
        await session.commit()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as session:
        yield session
