"""DATA-H: the reader's own history - a user's PRIVATE bars (`md_tenant_candles`) as the chart reader, the market-zones
job and the context panel read them, beside the shared lake.

Data terms (docs/DATA_SOURCES.md; HB2 / TM-7): bars read through one member's own broker session are stored in his
organisation's private store with the holder in the source (`broker:<name>:u<id>`). Only that member reads them
(`tenant_store.holder_visible`); another organisation never does - every read here takes (tenant_id, user_id) and the
query filters on both. Nothing here writes a bar.

DH3: the closing-auction flags (`md_bar_flags`, written by app/market_lake/index_history.py) are applied on every read:
an intraday bar after continuous trading is dropped, and a Daily bar whose extreme was set only in the closing auction
is read with the continuous session's extremes (its open and close held inside them), so no structure or zone is built
from either. The official close stays in the stored bar and is read as such (`official_close`).

M55 T4 (owner order): the history owner's INDEX intraday rows (the keys `index_history` backfills) are read by every
member of every organisation through a `SharedScope` (`shared_scope`) - only while the bring-your-own-broker data tier
(`byob_data_tier`) is off for the reader's organisation and the owner's (licence: docs/COMPLIANCE_IN.md, "Data
licences"; OPEN_QUESTIONS M55Q-1). A shared scope reads 1-minute rows of an index key only (never his Daily rows, never
another symbol) and compares unequal to the owner's own scope (no cache keyed by a scope mixes the two); the reader's
own rows still come first (`scope_for`). Nothing here writes a bar.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdBarFlagRecord, MdTenantCandleRecord
from app.market_lake.tenant_store import holder_visible

Scope = Tuple[int, int]                 # (tenant_id, user_id): whose private bars, read by whom
RowTuple = Tuple[Any, ...]              # (ts, version, open, high, low, close, volume) - the lake readers' row shape
PARTITION = 5000
DROP_KINDS = ("post_close",)
EXTREME_KINDS = ("auction_high", "auction_low")
DAILY_LABELS = ("day", "1d", "1w", "week")
SHARED_TAG = "shared-index-intraday"
SEED_PREFIX = "seed:"          # M71 PL-7: rows seeded from the owner's private data (app.market_lake.seed_import)
SHARED_OFF_LINE = ("Longer intraday index history is not shared while each organisation brings its own broker data, so "
                   "only your broker's own history is shown.")


class SharedScope(tuple):
    """M55 T4: the history owner's (tenant_id, user_id) read by ANOTHER reader - index keys and intraday labels only.
    A 3-tuple (tenant_id, user_id, SHARED_TAG): never equal to the owner's own (tenant_id, user_id)."""

    def __new__(cls, tenant_id: int, user_id: int) -> SharedScope:
        return super().__new__(cls, (int(tenant_id), int(user_id), SHARED_TAG))


def is_shared(scope: Any) -> bool:
    return isinstance(scope, SharedScope)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _scoped(scope: Scope, key: str, labels: Sequence[str]) -> List[Any]:
    tenant_id, user_id = int(scope[0]), int(scope[1])
    if tenant_id <= 0 or user_id <= 0:
        raise PermissionError("private bars are read per tenant and member")
    if is_shared(scope):
        from app.market_lake.index_history import is_target_key
        if not is_target_key(key):
            raise PermissionError("the shared scope reads the backfilled index keys only")
        labels = [label for label in labels if label not in DAILY_LABELS]       # intraday only, never his Daily rows
        # M71 PL-7c / M71Q-2: what was seeded from the owner's private data stays his own - never read by another reader
        return [MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.instrument_key == key,
                MdTenantCandleRecord.timeframe.in_(list(labels)), holder_visible(MdTenantCandleRecord.source, user_id),
                MdTenantCandleRecord.source.not_like(f"{SEED_PREFIX}%")]
    return [MdTenantCandleRecord.tenant_id == tenant_id, MdTenantCandleRecord.instrument_key == key,
            MdTenantCandleRecord.timeframe.in_(list(labels)), holder_visible(MdTenantCandleRecord.source, user_id)]


async def has_rows(session: AsyncSession, scope: Optional[Scope], key: str) -> bool:
    """Whether the member sees any private bar of `key` (one indexed lookup)."""
    if scope is None:
        return False
    return (await session.scalar(select(MdTenantCandleRecord.ts).where(*_scoped(scope, key, ("day", "1d", "1m"))).limit(1))) is not None


async def scope_for(session: AsyncSession, user: Any, key: str) -> Optional[Scope]:
    """The reader's private scope for `key` when he has private bars of it; else (M55 T4) the shared index scope when
    `shared_scope` allows it; else None (the shared lake only)."""
    tenant_id, user_id = getattr(user, "tenant_id", None), getattr(user, "id", None)
    if not tenant_id or not user_id:
        return None
    scope = (int(tenant_id), int(user_id))
    if await has_rows(session, scope, key):
        return scope
    return await shared_scope(session, int(tenant_id), key)


async def shared_off(session: AsyncSession, tenant_id: int | None, holder_tenant: int | None = None) -> bool:
    """True while the bring-your-own-broker data tier is on for the reader's organisation or the owner's: then no
    broker-sourced row of one session is served to another reader (licence; docs/COMPLIANCE_IN.md)."""
    from app.market_data import live_access
    if await live_access.byob_on(session, tenant_id):
        return True
    return holder_tenant is not None and await live_access.byob_on(session, holder_tenant)


async def shared_scope(session: AsyncSession, tenant_id: int | None, key: str) -> SharedScope | None:
    """M55 T4: the history owner's index intraday rows for a reader of `tenant_id` - a `SharedScope` when `key` is a
    backfilled index key, the owner is configured and holds rows of it, and the data tier is off for both
    organisations; else None. Never a non-index key, never Daily rows."""
    if not tenant_id or int(tenant_id) <= 0:
        return None
    from app.market_lake.index_history import is_target_key
    if not is_target_key(key):
        return None
    holder = await history_owner(session)
    if holder is None or await shared_off(session, int(tenant_id), holder[0]):
        return None
    scope = SharedScope(*holder)
    return scope if await has_rows(session, scope, key) else None


async def flags(session: AsyncSession, tenant_id: int, key: str, label: str, start: datetime, end: datetime) -> Dict[datetime, Dict[str, Optional[float]]]:
    """{bar END: {kind: value}} of the private store's DH3 flags in (start, end]."""
    rows = await session.execute(select(MdBarFlagRecord.ts, MdBarFlagRecord.kind, MdBarFlagRecord.value).where(
        MdBarFlagRecord.tenant_id == int(tenant_id), MdBarFlagRecord.instrument_key == key, MdBarFlagRecord.timeframe == label,
        MdBarFlagRecord.ts > start, MdBarFlagRecord.ts <= end))
    out: Dict[datetime, Dict[str, Optional[float]]] = {}
    for ts, kind, value in rows:
        out.setdefault(_utc(ts), {})[str(kind)] = None if value is None else float(value)
    return out


def apply_flags(rows: Sequence[RowTuple], marks: Dict[datetime, Dict[str, Optional[float]]]) -> List[RowTuple]:
    """DH3 on raw rows: drop the bars after continuous trading; read an auction-extended Daily bar with the continuous
    session's extremes (open and close held inside them). Pure; rows without a flag pass unchanged."""
    if not marks:
        return list(rows)
    out: List[RowTuple] = []
    for row in rows:
        mark = marks.get(_utc(row[0]))
        if not mark:
            out.append(row)
            continue
        if any(k in mark for k in DROP_KINDS):
            continue
        if not any(k in mark for k in EXTREME_KINDS):
            out.append(row)
            continue
        ts, version, o, h, low, c, volume = row[:7]
        hi = mark.get("auction_high")
        lo = mark.get("auction_low")
        high = float(hi) if hi is not None else float(h)
        low_ = float(lo) if lo is not None else float(low)
        if low_ > high:                                   # never: a broken flag reads as the stored bar
            out.append(row)
            continue
        out.append((ts, version, min(max(float(o), low_), high), high, low_, min(max(float(c), low_), high), volume, *row[7:]))
    return out


async def fetch_rows(session: AsyncSession, scope: Scope, key: str, label: str, start: datetime, end: datetime, *,
                     limit: Optional[int] = None, point_in_time: str = "first_version_by_end", dh3: bool = True) -> List[RowTuple]:
    """The member's private bars of one lake label with END in (start, end], one per END (its highest visible version),
    as plain tuples, DH3 flags applied. point_in_time: "first_version_by_end" (a bar's first version counts by its END,
    a correction once ingested - the chart layer's rule) or "ingested" (only rows ingested by `end` - the zones job's).
    More than `limit` rows raises ValueError (refused, never cut). dh3=False (M66 EN-2, the engine adapter): the rows as
    stored, without the DH3 flags applied - the adapter hands the flags to the engine as marks instead."""
    cond = _scoped(scope, key, (label,))
    if point_in_time == "ingested":
        cond.append(MdTenantCandleRecord.ingested_at <= end)
    else:
        from sqlalchemy import or_
        cond.append(or_(MdTenantCandleRecord.version == 1, MdTenantCandleRecord.ingested_at <= end))
    query = select(MdTenantCandleRecord.ts, MdTenantCandleRecord.version, cast(MdTenantCandleRecord.open, Float),
                   cast(MdTenantCandleRecord.high, Float), cast(MdTenantCandleRecord.low, Float), cast(MdTenantCandleRecord.close, Float),
                   MdTenantCandleRecord.volume).where(*cond, MdTenantCandleRecord.ts > start, MdTenantCandleRecord.ts <= end).order_by(
        MdTenantCandleRecord.ts, MdTenantCandleRecord.version).execution_options(yield_per=PARTITION)
    best: Dict[datetime, RowTuple] = {}
    result = await session.stream(query)
    seen = 0
    async for part in result.partitions(PARTITION):
        for row in part:
            seen += 1
            best[_utc(row[0])] = tuple(row)             # ordered by version: the highest visible one wins
        if limit is not None and len(best) > limit:
            raise ValueError(f"the window holds more than {limit} private bars on {label}; choose a later start")
        await asyncio.sleep(0)
    rows = [best[t] for t in sorted(best)]
    if not dh3:
        return rows
    return apply_flags(rows, await flags(session, scope[0], key, label, start, end)) if rows else rows


async def probe(session: AsyncSession, scope: Scope, key: str, labels: Sequence[str], start: datetime, end: datetime) -> Tuple[Any, int, int, Any]:
    """(newest END, rows, highest version, newest ingest) of the member's private rows a read of (start, end] sees."""
    row = (await session.execute(select(func.max(MdTenantCandleRecord.ts), func.count(), func.max(MdTenantCandleRecord.version),
                                        func.max(MdTenantCandleRecord.ingested_at)).where(
        *_scoped(scope, key, labels), MdTenantCandleRecord.ts > start, MdTenantCandleRecord.ts <= end))).one()
    return row[0], int(row[1] or 0), int(row[2] or 0), row[3]


def distinct_bars(rows: Sequence[RowTuple]) -> int:
    return len({_utc(r[0]) for r in rows})


def prefer_own(shared: Sequence[RowTuple], own: Sequence[RowTuple]) -> bool:
    """The member's own bars are read instead of the shared lake's when they hold MORE bars of the window (a tie keeps
    the shared lake). One source per read: bars of two sources are never mixed in one frame."""
    return bool(own) and distinct_bars(own) > distinct_bars(shared)


async def history_owner(session: AsyncSession) -> Optional[Scope]:
    """(tenant_id, user_id) of HB2's owner (HISTORY_OWNER_TENANT_ID confirmed by HISTORY_OWNER_TENANT_NAME, the pinned
    HISTORY_OWNER_USER_ID or the lowest-id active OWNER) - None when it is not configured."""
    from app.market_lake import intraday_history
    try:
        tenant_id = await intraday_history.owner_tenant(session)
        user = await intraday_history.owner_user(session, tenant_id)
    except intraday_history.IntradayConfigError:
        return None
    return (tenant_id, int(user.id)) if user is not None else None


async def official_close(session: AsyncSession, key: str, scope: Optional[Scope]) -> Optional[Dict[str, Any]]:
    """DH6: the newest stored Daily close of `key` - the session's official close - with its date and source: the shared
    lake's NSE index close file (`nse_bhav:index`) or the member's own broker Daily history, whichever is newer (a tie
    keeps NSE's file). None when no Daily bar is stored."""
    from sqlalchemy import or_

    from app.charts.price_action_layer import SHARED_SOURCES
    from app.db.models import MdCandleRecord
    from app.market_data.calendar import IST
    shared = (await session.execute(select(MdCandleRecord.ts, MdCandleRecord.close, MdCandleRecord.source).where(
        MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe.in_(("1d", "day")),
        or_(*[MdCandleRecord.source.like(f"{p}%") for p in SHARED_SOURCES])).order_by(
        MdCandleRecord.ts.desc(), MdCandleRecord.version.desc()).limit(1))).first()
    own = None
    if scope is not None:
        own = (await session.execute(select(MdTenantCandleRecord.ts, MdTenantCandleRecord.close, MdTenantCandleRecord.source).where(
            *_scoped(scope, key, ("1d", "day"))).order_by(MdTenantCandleRecord.ts.desc(), MdTenantCandleRecord.version.desc()).limit(1))).first()
    pick, whose = shared, "NSE index close file"
    if own is not None and (shared is None or _utc(own[0]) > _utc(shared[0])):
        pick, whose = own, "your broker's Daily history"
    if pick is None:
        return None
    return {"close": round(float(pick[1]), 2), "date": _utc(pick[0]).astimezone(IST).date().isoformat(), "source": whose}


__all__ = ["Scope", "SharedScope", "SHARED_OFF_LINE", "is_shared", "shared_scope", "shared_off", "has_rows", "scope_for", "flags", "apply_flags", "fetch_rows", "probe", "distinct_bars", "prefer_own",
           "history_owner", "official_close"]
