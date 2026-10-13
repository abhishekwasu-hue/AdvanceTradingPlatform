"""One advisory step on a session other steps share, in a SAVEPOINT (ADR-0004).

On Postgres a failed statement aborts the whole transaction (InFailedSqlTransaction): if a step's `except Exception`
only logs, every later statement on the same session fails too - in the trading worker that is the next tenants'
exits and the heartbeat. `guarded(session)` puts the step in a savepoint:

* the step fails before it commits: the savepoint is rolled back - only the step's own work is undone, the session's
  other pending work stays and the session is usable again;
* the step committed (the savepoint is gone with that commit) and failed later: if that broke the transaction, it is
  rolled back - it holds only the step's own work since that commit; otherwise it is left as the step left it;
* the step succeeds: the savepoint is released (nothing is committed here).

The exception always propagates - the caller's `except` decides what to log.

Review LOW-3 (noted, not changed): a worker cycle now opens a savepoint per step, per tenant, per deployment and per
position - tens to a few hundred in one transaction on a busy cycle. Each is a SAVEPOINT / RELEASE pair; Postgres keeps
that cheap (subtransactions past 64 per transaction spill to pg_subtrans, which matters only with long-running readers).
Most steps commit soon after they start, which ends the transaction and its savepoints with it.
"""
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@asynccontextmanager
async def guarded(session: AsyncSession, *, keep: Iterable[Any] = (), discard_after_commit: bool = False) -> AsyncIterator[AsyncSession]:
    """`keep`: objects the caller reads after a failure. A rolled-back savepoint expires what was changed inside it, and an
    async session cannot lazy-load an expired attribute - so these are reloaded (best effort) before the error goes on.
    `discard_after_commit`: when the step committed and then failed, roll back its work since that commit even if the
    transaction still works (a step whose half-done work must never be committed by the caller's next commit)."""
    savepoint = await session.begin_nested()
    try:
        yield session
    except BaseException:
        if _open(session, savepoint):
            await savepoint.rollback()           # (also after a failed flush, which deactivates it without closing it)
        elif session.in_transaction() and (discard_after_commit or not await _usable(session)):
            await session.rollback()         # committed, then broke the transaction: only its own work since that commit
        for obj in keep:
            if obj is not None:
                try:
                    await session.refresh(obj)
                except Exception:  # noqa: BLE001 - best effort; the original error is the one that matters
                    logger.warning("Could not reload %s after a failed step", type(obj).__name__)
        raise
    else:
        if _open(session, savepoint):
            await savepoint.commit()


def _open(session: AsyncSession, savepoint: Any) -> bool:
    """True while `savepoint` is still part of the session's transaction (not ended by a commit or a rollback)."""
    target = savepoint.sync_transaction
    current = session.sync_session.get_nested_transaction()
    while current is not None:
        if current is target:
            return True
        current = current.parent
    return False


async def _usable(session: AsyncSession) -> bool:
    """False when the transaction is aborted (Postgres refuses every statement until a rollback)."""
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
