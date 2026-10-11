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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCandleRecord

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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
    volume: int = 0
    oi: Optional[int] = None

    def values(self) -> Tuple:
        return (round(self.open, 4), round(self.high, 4), round(self.low, 4), round(self.close, 4), int(self.volume), self.oi)


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
