"""HB0: the tenant-PRIVATE candle store (`md_tenant_candles`) - the owner's licensed 1-minute history.

Licensing rule (docs/design/HB_HISTORY_BACKFILL.md): intraday bars bought under one person's data entitlement are for
that tenant's own research and backtests. They are never served to another tenant and never shown on a shared page.
The store is therefore a separate table that no shared read path touches, and every function here takes the reader's
`tenant_id` and refuses None - there is no "all tenants" read.

Only 1-minute bars are stored; 3/5/15/30/60-minute bars are built on read (`resample`), aligned to the exchange session
open (NSE 09:15 IST, so a 30-minute bar is 09:15-09:45, not 09:00-09:30) and labelled by their END like every lake bar.
The session's last, shorter bucket ends at the session close (a 60-minute 15:15 bar ends at 15:30).
As-of and versioning follow the shared lake exactly (B1): `as_of` hides rows ingested later and later corrections.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import MdTenantCandleRecord
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, session_family
from app.market_lake import adjust as adjust_mod
from app.market_lake.ingest import LakeBar, timeframe_minutes

BASE_TIMEFRAME = "1m"
RESAMPLE_MINUTES = (1, 3, 5, 15, 30, 60)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _require_tenant(tenant_id: Optional[int]) -> int:
    if tenant_id is None or int(tenant_id) <= 0:
        raise PermissionError("The private candle store is read per tenant; a tenant id is required")
    return int(tenant_id)


def resample_minutes(timeframe: str) -> int:
    """'1m' / '5m' / '15min' / '60m' -> minutes, only for the timeframes built from the 1-minute store."""
    minutes = timeframe_minutes(timeframe)
    if minutes not in RESAMPLE_MINUTES:
        raise ValueError(f"Timeframe {timeframe!r} is not built from the 1-minute store (allowed: "
                         f"{', '.join(f'{m}m' for m in RESAMPLE_MINUTES)})")
    return minutes


def _rank(sources: Optional[Sequence[str]], source: str) -> int:
    if not sources:
        return 0
    return list(sources).index(source) if source in sources else len(sources)


HOLDER_PREFIX = "broker:"        # HB2 rows: source broker:<name>:u<holder>, read through ONE member's own broker session


def holder_visible(source_column, reader_user_id: Optional[int]):
    """BT1b inside a tenant: rows from a member's own broker session (`broker:<name>:u<id>`) are visible only to that
    member; every other row of the private store is the tenant's. `reader_user_id=None` sees no such rows."""
    others = source_column.not_like(f"{HOLDER_PREFIX}%")
    if reader_user_id is None:
        return others
    return or_(others, source_column.like(f"{HOLDER_PREFIX}%:u{int(reader_user_id)}"))


async def rows_as_of(session: AsyncSession, tenant_id: int, instrument_key: str, timeframe: str, start: datetime, end: datetime, *,
                     as_of: Optional[datetime] = None, sources: Optional[Sequence[str]] = None,
                     reader_user_id: Optional[int] = None) -> List[MdTenantCandleRecord]:
    """The tenant's own bars with start < ts <= end, one per ts (preferred source, then highest visible version).
    Rows read through a member's own broker session are included only for that member (`reader_user_id`)."""
    tenant = _require_tenant(tenant_id)
    query = select(MdTenantCandleRecord).where(
        MdTenantCandleRecord.tenant_id == tenant, MdTenantCandleRecord.instrument_key == instrument_key,
        MdTenantCandleRecord.timeframe == timeframe, MdTenantCandleRecord.ts > start, MdTenantCandleRecord.ts <= end,
        holder_visible(MdTenantCandleRecord.source, reader_user_id))
    if as_of is not None:
        query = query.where(MdTenantCandleRecord.ingested_at <= as_of)
    if sources:
        query = query.where(MdTenantCandleRecord.source.in_(list(sources)))
    best: Dict[datetime, Tuple[Tuple[int, int], MdTenantCandleRecord]] = {}
    for row in await session.scalars(query):
        key = (_rank(sources, row.source), -row.version)
        ts = _utc(row.ts)
        if ts not in best or key < best[ts][0]:
            best[ts] = (key, row)
    return [best[ts][1] for ts in sorted(best)]


def _volume_sum(group: Sequence[LakeBar]) -> Optional[int]:
    """The bucket's volume: the sum, or None when any of its bars has no volume (M71 PL-7: never a partial sum)."""
    if any(b.volume is None for b in group):
        return None
    return sum(int(b.volume or 0) for b in group)


def resample(bars: Sequence[LakeBar], minutes: int, *, exchange: str = "NSE") -> List[LakeBar]:
    """1-minute bars (END labels) -> `minutes` bars aligned to the session open, END labels, last bucket capped at the
    close. Open = first, high = max, low = min, close = last, volume = sum, oi = last known. Only bars that exist are
    used - a missing minute is not invented, it just leaves its bucket with fewer bars."""
    if minutes == 1:
        return sorted(bars, key=lambda b: _utc(b.ts))
    session = EXCHANGE_SESSIONS[session_family(exchange)]
    period, one = timedelta(minutes=minutes), timedelta(minutes=1)
    buckets: Dict[datetime, List[LakeBar]] = {}
    for bar in sorted(bars, key=lambda b: _utc(b.ts)):
        start = _utc(bar.ts) - one
        day = start.astimezone(IST).date()
        open_ = datetime.combine(day, session.open, tzinfo=IST)
        close = datetime.combine(day, session.close, tzinfo=IST)
        end = open_ + ((start - open_) // period + 1) * period
        if open_ <= start < close:
            end = min(end, close)
        buckets.setdefault(end.astimezone(timezone.utc), []).append(bar)
    out: List[LakeBar] = []
    tf = f"{minutes}m"
    for end, group in sorted(buckets.items()):
        oi = next((b.oi for b in reversed(group) if b.oi is not None), None)
        out.append(LakeBar(group[0].instrument_key, tf, end, group[0].open, max(b.high for b in group), min(b.low for b in group),
                           group[-1].close, _volume_sum(group), oi))
    return out


async def candles(session: AsyncSession, tenant_id: Optional[int], instrument_key: str, timeframe: str, start: datetime,
                  end: datetime, *, symbol: str, exchange: str = "NSE", kind: str = "EQ", adjusted: bool = True,
                  as_of: Optional[datetime] = None, sources: Optional[Sequence[str]] = None,
                  reader_user_id: Optional[int] = None) -> List[LakeBar]:
    """The tenant's own bars for (start, end] at `timeframe` (built from 1-minute), as known at `as_of`, adjusted for
    splits/bonuses known then (equity only) when `adjusted` - the private twin of `adjust.candles`."""
    tenant = _require_tenant(tenant_id)
    minutes = resample_minutes(timeframe)
    raw_start = start - timedelta(minutes=minutes)        # the first bucket ending after `start` may begin before it
    stored = int(await session.scalar(select(func.count()).select_from(MdTenantCandleRecord).where(
        MdTenantCandleRecord.tenant_id == tenant, MdTenantCandleRecord.instrument_key == instrument_key,
        MdTenantCandleRecord.timeframe == BASE_TIMEFRAME, MdTenantCandleRecord.ts > raw_start,
        MdTenantCandleRecord.ts <= end, holder_visible(MdTenantCandleRecord.source, reader_user_id))) or 0)
    if stored > config.LAKE_TENANT_MAX_BASE_ROWS:         # counted BEFORE anything is read into memory
        raise ValueError(f"{stored} one-minute rows in the window; at most {config.LAKE_TENANT_MAX_BASE_ROWS} are read per "
                         "request - narrow the window")
    rows = await rows_as_of(session, tenant, instrument_key, BASE_TIMEFRAME, raw_start, end, as_of=as_of, sources=sources,
                            reader_user_id=reader_user_id)
    base = [LakeBar(r.instrument_key, r.timeframe, _utc(r.ts), float(r.open), float(r.high), float(r.low), float(r.close),
                    None if r.volume is None else int(r.volume), r.oi) for r in rows]
    bars = [b for b in resample(base, minutes, exchange=exchange) if start < _utc(b.ts) <= end]
    if not adjusted or kind.upper() not in adjust_mod.EQUITY_KINDS:
        return bars
    return adjust_mod.adjust(bars, await adjust_mod.actions_as_of(session, symbol, exchange, as_of=as_of))
