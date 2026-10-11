"""Part B3: data-quality detectors for lake bars. Each problem raises exactly one `data_quality_events` row.

- `gap`: expected bar ends missing inside a session (one event per contiguous run of missing bars);
- `spike`: a close more than LAKE_SPIKE_PCT away from the previous close;
- `invalid`: OHLC that cannot be (high below open/close, low above them, non-positive prices);
- `late`: a bar ingested more than LAKE_LATE_SECONDS after it closed;
- `duplicate`: the same bar end twice in one incoming batch;
- `mismatch`: two sources disagree on a close by more than LAKE_MISMATCH_PCT.
Thresholds are configuration, never constants here. Detectors are pure; `scan` reads the lake, runs them and records new
events (an event already recorded for the same kind / key / bar / source is not written twice).
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import DataQualityEventRecord, MdCandleRecord
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN
from app.market_lake.ingest import LakeBar, timeframe_minutes


@dataclass(frozen=True)
class QualityEvent:
    kind: str
    instrument_key: str
    timeframe: Optional[str]
    ts: Optional[datetime]
    source: str
    detail: str


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def expected_ends(day: date, timeframe: str) -> List[datetime]:
    """Bar ends of a full NSE session on `day` (UTC), from MARKET_OPEN to MARKET_CLOSE."""
    period = timedelta(minutes=timeframe_minutes(timeframe))
    start = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    close = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    out, end = [], start + period
    while end <= close:
        out.append(end.astimezone(timezone.utc))
        end += period
    return out


def find_gaps(bars: Sequence[LakeBar], day: date, source: str, *, until: Optional[datetime] = None) -> List[QualityEvent]:
    if not bars:
        return []
    key, tf = bars[0].instrument_key, bars[0].timeframe
    have = {_utc(b.ts) for b in bars}
    expected = [e for e in expected_ends(day, tf) if until is None or e <= _utc(until)]
    events, run = [], []
    for end in expected + [None]:
        if end is not None and end not in have:
            run.append(end)
            continue
        if run:
            events.append(QualityEvent("gap", key, tf, run[0], source,
                                       f"{len(run)} missing bar(s) ending {run[0].isoformat()} .. {run[-1].isoformat()}"))
            run = []
    return events


def find_bar_problems(bars: Sequence[LakeBar], source: str, *, ingested: Optional[Dict[datetime, datetime]] = None) -> List[QualityEvent]:
    """Spikes, invalid OHLC and late arrivals in one instrument's bars (sorted by end)."""
    spike = float(config.LAKE_SPIKE_PCT)
    late = timedelta(seconds=float(config.LAKE_LATE_SECONDS))
    events: List[QualityEvent] = []
    prev: Optional[LakeBar] = None
    for bar in sorted(bars, key=lambda b: _utc(b.ts)):
        ts = _utc(bar.ts)
        if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            events.append(QualityEvent("invalid", bar.instrument_key, bar.timeframe, ts, source,
                                       f"OHLC {bar.open}/{bar.high}/{bar.low}/{bar.close}"))
        elif prev is not None and prev.close > 0 and spike > 0 and abs(bar.close / prev.close - 1) * 100 > spike:
            events.append(QualityEvent("spike", bar.instrument_key, bar.timeframe, ts, source,
                                       f"close {prev.close:g} -> {bar.close:g} ({(bar.close / prev.close - 1) * 100:+.1f}%, limit {spike:g}%)"))
        if ingested and ts in ingested and late.total_seconds() > 0 and _utc(ingested[ts]) - ts > late:
            events.append(QualityEvent("late", bar.instrument_key, bar.timeframe, ts, source,
                                       f"ingested {(_utc(ingested[ts]) - ts).total_seconds():.0f}s after the bar closed"))
        prev = bar
    return events


def find_duplicates(batch: Iterable[LakeBar], source: str) -> List[QualityEvent]:
    counts = Counter((b.instrument_key, b.timeframe, _utc(b.ts)) for b in batch)
    return [QualityEvent("duplicate", k, tf, ts, source, f"{n} copies of the same bar in one batch")
            for (k, tf, ts), n in counts.items() if n > 1]


def find_mismatches(by_source: Dict[str, Sequence[LakeBar]]) -> List[QualityEvent]:
    limit = float(config.LAKE_MISMATCH_PCT)
    closes: Dict[datetime, Dict[str, LakeBar]] = defaultdict(dict)
    for source, bars in by_source.items():
        for b in bars:
            closes[_utc(b.ts)][source] = b
    events = []
    for ts, per in sorted(closes.items()):
        if len(per) < 2:
            continue
        values = sorted(per.items(), key=lambda kv: kv[1].close)
        (lo_src, lo), (hi_src, hi) = values[0], values[-1]
        if lo.close > 0 and limit > 0 and (hi.close / lo.close - 1) * 100 > limit:
            events.append(QualityEvent("mismatch", lo.instrument_key, lo.timeframe, ts, f"{lo_src}|{hi_src}",
                                       f"close {lo.close:g} ({lo_src}) vs {hi.close:g} ({hi_src}), limit {limit:g}%"))
    return events


def _bar(row: MdCandleRecord) -> LakeBar:
    return LakeBar(row.instrument_key, row.timeframe, _utc(row.ts), float(row.open), float(row.high), float(row.low),
                   float(row.close), int(row.volume), row.oi)


async def record(session: AsyncSession, events: Iterable[QualityEvent]) -> int:
    written = 0
    for e in events:
        exists = await session.scalar(select(DataQualityEventRecord.id).where(
            DataQualityEventRecord.kind == e.kind, DataQualityEventRecord.instrument_key == e.instrument_key,
            DataQualityEventRecord.timeframe == e.timeframe, DataQualityEventRecord.ts == e.ts,
            DataQualityEventRecord.source == e.source).limit(1))
        if exists is None:
            session.add(DataQualityEventRecord(kind=e.kind, instrument_key=e.instrument_key, timeframe=e.timeframe, ts=e.ts,
                                               source=e.source, detail=e.detail))
            written += 1
    await session.commit()
    return written


async def scan_day(session: AsyncSession, instrument_key: str, timeframe: str, day: date, *,
                   now: Optional[datetime] = None) -> Dict[str, int]:
    """Runs every detector over one instrument's session on `day` (latest version per source) and records new events."""
    ends = expected_ends(day, timeframe)
    period = timedelta(minutes=timeframe_minutes(timeframe))
    rows = list(await session.scalars(select(MdCandleRecord).where(
        MdCandleRecord.instrument_key == instrument_key, MdCandleRecord.timeframe == timeframe,
        MdCandleRecord.ts > ends[0] - period, MdCandleRecord.ts <= ends[-1])))
    latest: Dict[str, Dict[datetime, MdCandleRecord]] = defaultdict(dict)
    for r in rows:
        ts = _utc(r.ts)
        if ts not in latest[r.source] or r.version > latest[r.source][ts].version:
            latest[r.source][ts] = r
    events: List[QualityEvent] = []
    by_source: Dict[str, List[LakeBar]] = {}
    for source, per_ts in latest.items():
        bars = [_bar(r) for _, r in sorted(per_ts.items())]
        by_source[source] = bars
        events += find_gaps(bars, day, source, until=now)
        events += find_bar_problems(bars, source, ingested={ts: r.ingested_at for ts, r in per_ts.items()})
    events += find_mismatches(by_source)
    await record(session, events)
    return dict(Counter(e.kind for e in events))
