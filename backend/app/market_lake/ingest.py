"""Part B2: getting data into the lake.

- `CandleBuilder` turns ticks into bars labelled by their END and hands a bar out only once it has closed (the first
  tick of a later bar, or `flush(now)` at or after the end) - the same rule as realism C1, so nothing downstream can see
  a bar that was still forming.
- `write_candles` is idempotent: the same bar again is skipped; a bar whose values changed is written as the next
  `version` (a correction), so `as_of` reads before it still see the original (B1).
- `backfill_from_broker` reads a broker's history (bars labelled by START, as every adapter returns them) into the lake.
- The vendor seam (ADR-0016): `HistoryVendor` is what a paid feed implements; `MockVendor` serves canned bars so the
  pipeline is testable and usable before the owner picks a vendor (OPEN_QUESTIONS B-2 - no paid vendor until then).
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCandleRecord, MdTenantCandleRecord
from app.market_data.calendar import IST

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# The END every DAILY lake bar carries: the NSE close, 15:30 IST = 10:00 UTC on the trade date (HB0 / HB1).
DAILY_END_IST = (15, 30)


def trade_date(ts: datetime) -> date:
    """The trading day a bar belongs to - ONE rule for every reader (import checks, gap detection, adjustment): the IST
    date of (END - 1 microsecond). A bar ending exactly at IST midnight belongs to the day before; NSE bars (09:15 to
    15:30 IST, daily END 15:30 IST) fall on the same date in IST and UTC."""
    ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    return (ts - timedelta(microseconds=1)).astimezone(IST).date()


def timeframe_minutes(timeframe: str) -> int:
    """'1m' / '5min' / '15m' -> minutes. Daily bars are not built from ticks here (B2 handles intraday)."""
    m = re.fullmatch(r"(\d+)\s*(m|min)", (timeframe or "").strip().lower())
    if not m or int(m.group(1)) <= 0:
        raise ValueError(f"Unsupported intraday timeframe {timeframe!r}")
    return int(m.group(1))


@dataclass
class LakeBar:
    instrument_key: str
    timeframe: str
    ts: datetime                 # bar END, UTC
    open: float
    high: float
    low: float
    close: float
    volume: int | None = 0    # None = the source had no volume (M71 PL-7: missing stays missing, never 0)
    oi: Optional[int] = None

    def values(self) -> Tuple:
        return (round(self.open, 4), round(self.high, 4), round(self.low, 4), round(self.close, 4),
                None if self.volume is None else int(self.volume), self.oi)


@dataclass
class _Forming:
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass
class CandleBuilder:
    """One timeframe, many instruments. Ticks must arrive in time order per instrument (late ticks are dropped and
    counted - B3 reports them)."""
    timeframe: str
    _forming: Dict[str, _Forming] = field(default_factory=dict)
    late_ticks: int = 0

    def __post_init__(self) -> None:
        self._period = timedelta(minutes=timeframe_minutes(self.timeframe))

    def _end_of(self, ts: datetime) -> datetime:
        ts = ts.astimezone(timezone.utc)
        periods = (ts - EPOCH) // self._period          # bar [start, end): a tick exactly at `end` opens the next bar
        return EPOCH + (periods + 1) * self._period

    def feed(self, instrument_key: str, ts: datetime, price: float, volume: int = 0) -> List[LakeBar]:
        end = self._end_of(ts)
        bar = self._forming.get(instrument_key)
        closed: List[LakeBar] = []
        if bar is not None and end < bar.end:
            self.late_ticks += 1
            return closed
        if bar is not None and end > bar.end:
            closed.append(self._close(instrument_key, bar))
            bar = None
        if bar is None:
            self._forming[instrument_key] = _Forming(end, price, price, price, price, int(volume or 0))
        else:
            bar.high, bar.low, bar.close = max(bar.high, price), min(bar.low, price), price
            bar.volume += int(volume or 0)
        return closed

    def flush(self, now: datetime) -> List[LakeBar]:
        """Bars whose end has passed (no tick needed) - call on a timer so a quiet instrument's last bar is published."""
        now = now.astimezone(timezone.utc)
        done = [k for k, b in self._forming.items() if b.end <= now]
        return [self._close(k, self._forming.pop(k)) for k in done]

    def _close(self, key: str, bar: _Forming) -> LakeBar:
        self._forming.pop(key, None)
        return LakeBar(key, self.timeframe, bar.end, bar.open, bar.high, bar.low, bar.close, bar.volume)


@dataclass
class WriteResult:
    inserted: int = 0
    corrected: int = 0
    skipped: int = 0


async def write_candles(session: AsyncSession, bars: Iterable[LakeBar], source: str, *,
                        ingested_at: Optional[datetime] = None) -> WriteResult:
    """Inserts new bars, skips identical ones, writes a changed bar as the next version. Commits once."""
    result = WriteResult()
    now = ingested_at or datetime.now(timezone.utc)
    for bar in bars:
        latest = await session.scalar(select(MdCandleRecord).where(
            MdCandleRecord.instrument_key == bar.instrument_key, MdCandleRecord.timeframe == bar.timeframe,
            MdCandleRecord.ts == bar.ts, MdCandleRecord.source == source).order_by(MdCandleRecord.version.desc()).limit(1))
        if latest is not None:
            old = LakeBar(bar.instrument_key, bar.timeframe, bar.ts, float(latest.open), float(latest.high), float(latest.low),
                          float(latest.close), int(latest.volume), latest.oi)
            if old.values() == bar.values():
                result.skipped += 1
                continue
        session.add(MdCandleRecord(instrument_key=bar.instrument_key, timeframe=bar.timeframe, ts=bar.ts, source=source,
                                   version=(latest.version + 1) if latest is not None else 1, open=bar.open, high=bar.high,
                                   low=bar.low, close=bar.close, volume=int(bar.volume), oi=bar.oi, ingested_at=now))
        if latest is None:
            result.inserted += 1
        else:
            result.corrected += 1
    await session.commit()
    return result


def _as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


async def write_candles_bulk(session: AsyncSession, bars: Sequence[LakeBar], source: str, *, tenant_id: Optional[int] = None,
                             ingested_at: Optional[datetime] = None) -> WriteResult:
    """`write_candles` for large batches (HB0 import, HB1 bhavcopy days): the same skip / new-version rules, one read
    per chunk of instruments (one instrument over a year, or two thousand instruments on one day) and one multi-row
    insert. Does NOT commit - the caller commits it together with its own bookkeeping.
    `tenant_id` set writes that tenant's private store (md_tenant_candles) instead of the shared lake. Two bars with the
    same END in one batch are refused (ValueError): the caller must decide which one is true, never this function."""
    model = MdTenantCandleRecord if tenant_id is not None else MdCandleRecord
    now = ingested_at or datetime.now(timezone.utc)
    result = WriteResult()
    groups: Dict[Tuple[str, str], List[LakeBar]] = defaultdict(list)
    for bar in bars:
        groups[(bar.instrument_key, bar.timeframe)].append(bar)
    ends_of: Dict[Tuple[str, str], List[datetime]] = {}
    for (key, tf), group in groups.items():
        ends = [_as_utc(b.ts) for b in group]
        if len(set(ends)) != len(ends):
            raise ValueError(f"{key} {tf}: {len(ends) - len(set(ends))} duplicate bar end(s) in one batch")
        ends_of[(key, tf)] = ends
    latest: Dict[Tuple[str, str, datetime], Tuple] = {}
    group_keys = list(groups)
    for i in range(0, len(group_keys), 500):
        chunk = group_keys[i:i + 500]
        all_ends = [ts for g in chunk for ts in ends_of[g]]
        query = select(model.instrument_key, model.timeframe, model.ts, model.version, model.open, model.high, model.low,
                       model.close, model.volume, model.oi).where(
            model.instrument_key.in_(sorted({k for k, _ in chunk})), model.timeframe.in_(sorted({tf for _, tf in chunk})),
            model.source == source, model.ts >= min(all_ends), model.ts <= max(all_ends))
        if tenant_id is not None:
            query = query.where(MdTenantCandleRecord.tenant_id == tenant_id)
        for r in await session.execute(query):
            ts = _as_utc(r.ts)
            at = (r.instrument_key, r.timeframe, ts)
            if at not in latest or r.version > latest[at][0]:
                latest[at] = (r.version, LakeBar(r.instrument_key, r.timeframe, ts, float(r.open), float(r.high), float(r.low),
                                                 float(r.close), None if r.volume is None else int(r.volume), r.oi))
    rows: List[dict] = []
    for (key, tf), group in groups.items():
        for bar, ts in zip(group, ends_of[(key, tf)]):
            old = latest.get((key, tf, ts))
            if old is not None and old[1].values() == bar.values():
                result.skipped += 1
                continue
            row = dict(instrument_key=key, timeframe=tf, ts=ts, source=source, version=(old[0] + 1) if old else 1,
                       open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                       volume=None if bar.volume is None else int(bar.volume), oi=bar.oi, ingested_at=now)
            if tenant_id is not None:
                row["tenant_id"] = tenant_id
            rows.append(row)
            if old is None:
                result.inserted += 1
            else:
                result.corrected += 1
    for i in range(0, len(rows), 5000):
        await session.execute(insert(model), rows[i:i + 5000])
    return result


async def backfill_from_broker(session: AsyncSession, adapter, *, instrument_key: str, symbol: str, exchange: str,
                               timeframe: str, start: datetime, end: datetime) -> WriteResult:
    """Broker history -> lake. Adapters label bars by START; the lake labels by END (start + timeframe)."""
    period = timedelta(minutes=timeframe_minutes(timeframe))
    raw = await adapter.get_historical_data(symbol, exchange, timeframe, start, end)
    bars = [LakeBar(instrument_key, timeframe, (b.timestamp if b.timestamp.tzinfo else b.timestamp.replace(tzinfo=timezone.utc))
                    .astimezone(timezone.utc) + period, b.open, b.high, b.low, b.close, int(b.volume or 0)) for b in raw]
    return await write_candles(session, bars, f"broker:{adapter.name}")


class HistoryVendor(Protocol):
    """ADR-0016 seam for a market-data vendor: bars labelled by END, UTC."""
    name: str

    async def fetch_candles(self, instrument_key: str, timeframe: str, start: datetime, end: datetime) -> List[LakeBar]: ...


class MockVendor:
    """Canned bars (tests, development, and the pipeline's dry run until a real vendor is chosen)."""
    name = "mock"

    def __init__(self, bars: Sequence[LakeBar] = ()) -> None:
        self._bars = list(bars)

    async def fetch_candles(self, instrument_key: str, timeframe: str, start: datetime, end: datetime) -> List[LakeBar]:
        return [b for b in self._bars if b.instrument_key == instrument_key and b.timeframe == timeframe and start < b.ts <= end]


VENDORS = {"mock": MockVendor}


def vendor(name: str, **kwargs) -> HistoryVendor:
    try:
        return VENDORS[name](**kwargs)
    except KeyError:
        raise ValueError(f"Unknown market-data vendor {name!r} (configured: {', '.join(sorted(VENDORS))})") from None


async def ingest_from_vendor(session: AsyncSession, source: HistoryVendor, instrument_key: str, timeframe: str,
                             start: datetime, end: datetime) -> WriteResult:
    return await write_candles(session, await source.fetch_candles(instrument_key, timeframe, start, end), f"vendor:{source.name}")


async def candle_count(session: AsyncSession, instrument_key: str, timeframe: str) -> int:
    return int(await session.scalar(select(func.count()).select_from(MdCandleRecord).where(
        MdCandleRecord.instrument_key == instrument_key, MdCandleRecord.timeframe == timeframe)) or 0)
