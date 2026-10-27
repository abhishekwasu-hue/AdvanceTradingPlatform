"""P1 (owner bug 27 Oct): the instrument master sync - retried until it succeeds, refreshed on demand, and reported.

Before: the worker synced once per IST day from INSTRUMENT_SYNC_HOUR_IST and, on a failure, marked the day done ("retry
tomorrow"), so one failed download left the whole day without contracts - the chain (live from the broker) showed prices
the ticket could not trade ("not in the instrument list yet ... try again later"), and nobody was told.

Now:
- `due()`: a sync is due whenever today's (IST) successful sync is missing and INSTRUMENT_SYNC_HOUR_IST has passed - also
  right after a worker start - with a back-off of RETRY_MINUTES between attempts (before and during market hours).
- `refresh_on_demand()`: a ticket that finds no row for a contract the chain shows starts one sync in the background,
  rate-limited across processes (the last attempt is stored), and tells the user what is actually happening.
- The state (last success, rows, last attempt, last error) lives in `platform_controls` under STATE_KEY - one row, read
  by the admin console and `atp.sh health`; no migration.
Exits never depend on any of this (ADR-0004): closing a position uses the position's own stored contract.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlatformControlRecord
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

STATE_KEY = "instrument_master_sync"
RETRY_MINUTES = 12               # back-off between failed attempts (the owner asked for every 10-15 minutes)
ON_DEMAND_MINUTES = 5            # at most one on-demand refresh per this many minutes, across every process
_running: Optional[asyncio.Task] = None


def _iso(ts: Optional[datetime]) -> Optional[str]:
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds") if ts else None


def _parse(text: Any) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def read_state(session: AsyncSession) -> Dict[str, Any]:
    """{last_ok_at, last_attempt_at, last_error, rows} - empty values when never synced (a malformed row reads as unset)."""
    row = await session.get(PlatformControlRecord, STATE_KEY)
    try:
        value = json.loads(row.value_json) if row is not None and row.value_json else {}
    except ValueError:
        value = {}
    value = value if isinstance(value, dict) else {}
    return {"last_ok_at": value.get("last_ok_at"), "last_attempt_at": value.get("last_attempt_at"),
            "last_error": value.get("last_error"), "rows": value.get("rows"), "counts": value.get("counts") or {}}


async def _write(session: AsyncSession, value: Dict[str, Any]) -> None:
    row = await session.get(PlatformControlRecord, STATE_KEY)
    if row is None:
        row = PlatformControlRecord(key=STATE_KEY)
        session.add(row)
    row.value_json = json.dumps(value)
    row.updated_by = None


def synced_today(state: Dict[str, Any], now: datetime) -> bool:
    ok = _parse(state.get("last_ok_at"))
    return ok is not None and ok.astimezone(IST).date() == now.astimezone(IST).date()


def due(state: Dict[str, Any], now: datetime, *, hour: int, retry_minutes: int = RETRY_MINUTES) -> bool:
    """A sync is due when today's successful sync is missing, the sync hour has passed and the last attempt is older than
    the back-off. A worker that just started has no memory - the stored state decides, so a restart catches up at once."""
    if now.astimezone(IST).hour < hour or synced_today(state, now):
        return False
    tried = _parse(state.get("last_attempt_at"))
    return tried is None or now - tried >= timedelta(minutes=retry_minutes)


async def run(session: AsyncSession, exchanges: Sequence[str], now: datetime,
              sync: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """One sync attempt, recorded either way. Returns the new state. The sync replaces the rows in one transaction."""
    from app.instruments.master import sync_upstox
    state = await read_state(session)
    state["last_attempt_at"] = _iso(now)
    try:
        counts = await (sync or sync_upstox)(session, list(exchanges))
    except Exception as exc:  # noqa: BLE001 - recorded and shown; the worker carries on
        await session.rollback()
        state["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.exception("Instrument master sync failed")
    else:
        counts = dict(counts or {})
        state.update(last_ok_at=_iso(now), last_error=None, rows=int(sum(counts.values())), counts=counts)
    await _write(session, state)
    await session.commit()
    return state


async def refresh_on_demand(session_factory: Any, exchanges: Sequence[str], now: Optional[datetime] = None) -> str:
    """Starts one background sync when none ran in the last ON_DEMAND_MINUTES (the stored attempt time, so every API
    process shares the limit). Returns the user's plain text of what is happening."""
    global _running
    now = now or datetime.now(timezone.utc)
    async with session_factory() as session:
        state = await read_state(session)
        tried = _parse(state.get("last_attempt_at"))
        busy = _running is not None and not _running.done()
        if busy or (tried is not None and now - tried < timedelta(minutes=ON_DEMAND_MINUTES)):
            return status_text(state, refreshing=busy or not state.get("last_error"))
        state["last_attempt_at"] = _iso(now)           # claim the slot before the download starts
        await _write(session, state)
        await session.commit()

    async def go() -> None:
        async with session_factory() as session:
            await run(session, exchanges, datetime.now(timezone.utc))
    _running = asyncio.get_running_loop().create_task(go())
    return status_text(state, refreshing=True)


def status_text(state: Dict[str, Any], *, refreshing: bool) -> str:
    """The user's text - what actually happened, never an untrue "try again later"."""
    ok = _parse(state.get("last_ok_at"))
    when = ok.astimezone(IST).strftime("%d %b %H:%M IST") if ok else "never"
    if refreshing:
        return f"The contract list is being refreshed now (last update {when}). Try again in about a minute."
    if state.get("last_error"):
        return f"The contract list could not be updated (last update {when}); it is retried every {RETRY_MINUTES} minutes."
    return f"The contract list (last update {when}) does not have this contract."
