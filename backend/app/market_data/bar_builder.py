"""M71 PL-1b: ticks -> closed 1-minute bars -> 5m / 15m / 60m bars, built in the worker from the universe feed.

Pure (no database, no network, no clock of its own): the caller passes every time. Bars are `LakeBar`s labelled by
their END in UTC like every lake bar; minute boundaries are the same in IST and UTC (IST = UTC + 05:30).

1-minute rules (`MinuteBarBuilder`):
- A tick belongs to the minute [start, start + 1m) that holds its exchange time (`Tick.best_ts`); open = first price,
  high / low = extremes, close = last price.
- A bar is handed out only once it is CLOSED: when the time the caller passes (`now`, the tick's arrival or the flush
  time) is at or after its END plus the grace window. Until then a late tick of that minute still updates it (only that
  open bar - never a bar already handed out); a tick for a minute already handed out is dropped and counted (`late`).
  So nothing downstream ever sees a bar that was still forming (no look-ahead).
- Session (calendar.EXCHANGE_SESSIONS by the instrument's exchange family): a tick of a minute that starts before the
  continuous session opens is not built into a bar (counted `outside_session`) - the broker's own 1-minute history,
  which the backfill stores beside these bars, starts at the open too. A minute that starts at or after the close is
  built and handed out like any other; the store flags it `post_close` with the DH3 rule
  (`index_history.auction_flags`) and the readers drop it.
- Volume: the feed's cumulative day volume (`Tick.volume_total`, Upstox `full` mode). A bar's volume is its last
  cumulative figure minus the last figure of the bar handed out before it; for the first bar seen (no figure before
  it) the difference inside the bar; a cumulative figure below the previous one (a new day) starts from zero. Without
  any figure (indices, `ltpc` mode) the bar is marked `volume_known=False` (its stored 0 is not a volume): the writer
  stores such a bar only for an index (whose broker history carries no traded volume either), never for a stock.
- `suppress(keys, start, end)`: after a reconnect the minutes of the outage are not built from the partial ticks around
  it (the forming bars are discarded); the gap is asked from the broker's history instead (universe_feed).

Resampling (`Resampler`): closed 1-minute bars -> `minutes` bars with exactly the store's read-time rule
(`tenant_store.resample`: aligned to the session open, last bucket capped at the close). A bucket is handed out only when
it is complete - a later bucket's minute arrived, or `now` is at or after its END plus the grace window. DH3: a
`post_close` minute never enters a resampled bar (as the readers drop it before they resample). A bucket that holds a
suppressed (gap) minute is not handed out from the partial data (`gap_range` gives the bucket-aligned span to ask).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.market_data.calendar import EXCHANGE_SESSIONS, IST, session_family
from app.market_lake.ingest import LakeBar
from app.market_lake.tenant_store import resample

MINUTE = timedelta(minutes=1)
RESAMPLE_MINUTES = (5, 15, 60)
BASE_TIMEFRAME = "1m"


def utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def minute_start(ts: datetime) -> datetime:
    ts = utc(ts)
    return ts.replace(second=0, microsecond=0)


def exchange_of(key: str) -> str:
    """'NSE:RELIANCE' -> 'NSE'; 'NSE_INDEX:NIFTY50' -> 'NSE'; 'BSE_INDEX:SENSEX' -> 'BSE' (the session clock)."""
    head = key.split(":", 1)[0].upper() if ":" in key else "NSE"
    return head.split("_", 1)[0] if head.endswith("_INDEX") else head


def session_bounds(key: str, day_ts: datetime) -> tuple[datetime, datetime]:
    """(open, close) in UTC of the session of the IST day holding `day_ts` for `key`'s exchange family."""
    spec = EXCHANGE_SESSIONS[session_family(exchange_of(key))]
    day = utc(day_ts).astimezone(IST).date()
    return (datetime.combine(day, spec.open, tzinfo=IST).astimezone(UTC),
            datetime.combine(day, spec.close, tzinfo=IST).astimezone(UTC))


def is_post_close(key: str, bar: LakeBar) -> bool:
    """DH3: the 1-minute bar STARTS at or after the session close (index_history.auction_flags' post_close rule)."""
    start = utc(bar.ts) - MINUTE
    return start >= session_bounds(key, start)[1]


@dataclass
class FeedBar(LakeBar):
    """A lake bar built from the feed; `volume_known` False = the feed carried no volume figure for it."""
    volume_known: bool = True


@dataclass
class _Open:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    vtt_first: int | None = None
    vtt_last: int | None = None


@dataclass
class BuilderStats:
    ticks: int = 0
    late: int = 0
    outside_session: int = 0
    suppressed: int = 0
    bars: int = 0


@dataclass
class MinuteBarBuilder:
    grace_seconds: float = 3.0
    stats: BuilderStats = field(default_factory=BuilderStats)
    _open: dict[str, dict[datetime, _Open]] = field(default_factory=dict)
    _emitted_until: dict[str, datetime] = field(default_factory=dict)      # END of the last bar handed out
    _emitted_vtt: dict[str, int] = field(default_factory=dict)
    _suppress: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)

    @property
    def grace(self) -> timedelta:
        return timedelta(seconds=self.grace_seconds)

    def _suppressed(self, key: str, start: datetime) -> bool:
        return any(lo <= start < hi for lo, hi in self._suppress.get(key, ()))

    def feed(self, key: str, ts: datetime, price: float, now: datetime, volume_total: int | None = None) -> list[LakeBar]:
        """One tick. Returns the bars of `key` that are closed at `now` (in time order)."""
        self.stats.ticks += 1
        start = minute_start(ts)
        closed = self.close_due(now, keys=(key,))
        if price is None or not price > 0:
            return closed
        if start + MINUTE <= self._emitted_until.get(key, datetime.min.replace(tzinfo=UTC)):
            self.stats.late += 1
            return closed
        if start + MINUTE + self.grace <= utc(now):
            self.stats.late += 1                     # its minute ended beyond the grace window: never built late
            return closed
        if start < session_bounds(key, start)[0]:
            self.stats.outside_session += 1
            return closed
        if self._suppressed(key, start):
            self.stats.suppressed += 1
            return closed
        bars = self._open.setdefault(key, {})
        bar = bars.get(start)
        if bar is None:
            bars[start] = _Open(start, price, price, price, price, volume_total, volume_total)
        else:
            bar.high, bar.low, bar.close = max(bar.high, price), min(bar.low, price), price
            if volume_total is not None:
                bar.vtt_first = volume_total if bar.vtt_first is None else min(bar.vtt_first, volume_total)
                bar.vtt_last = volume_total if bar.vtt_last is None else max(bar.vtt_last, volume_total)
        return closed

    def _volume(self, key: str, bar: _Open) -> int | None:
        if bar.vtt_last is None:
            return None
        base = self._emitted_vtt.get(key)
        if base is None:
            base = bar.vtt_first if bar.vtt_first is not None else bar.vtt_last
        elif base > bar.vtt_last:
            base = 0                                  # the cumulative figure went back: a new day
        self._emitted_vtt[key] = bar.vtt_last
        return max(0, bar.vtt_last - base)

    def close_due(self, now: datetime, keys: Iterable[str] | None = None) -> list[LakeBar]:
        """Every open bar whose END plus the grace window is at or before `now`, oldest first per key."""
        now = utc(now)
        out: list[LakeBar] = []
        for key in list(self._open) if keys is None else list(keys):
            bars = self._open.get(key)
            if not bars:
                continue
            for start in sorted(bars):
                if start + MINUTE + self.grace > now:
                    break
                bar = bars.pop(start)
                end = start + MINUTE
                volume = self._volume(key, bar)
                out.append(FeedBar(key, BASE_TIMEFRAME, end, bar.open, bar.high, bar.low, bar.close, volume or 0,
                                   volume_known=volume is not None))
                self._emitted_until[key] = max(end, self._emitted_until.get(key, end))
                self.stats.bars += 1
            if not bars:
                self._open.pop(key, None)
        return out

    def suppress(self, keys: Iterable[str], start: datetime, end: datetime) -> int:
        """Minutes starting in [start, end) are not built for `keys`; open bars there are discarded. Returns how many."""
        start, end = minute_start(start), utc(end)
        dropped = 0
        for key in keys:
            # the same start again replaces the window (an outage first suppressed open-ended, then closed on reconnect)
            windows = [w for w in self._suppress.get(key, []) if w[0] != start and w[1] > start - timedelta(hours=2)]
            windows.append((start, end))
            self._suppress[key] = windows
            bars = self._open.get(key, {})
            for s in [s for s in bars if start <= s < end]:
                bars.pop(s)
                dropped += 1
        return dropped

    def suppressed_minute(self, key: str, start: datetime) -> bool:
        return self._suppressed(key, minute_start(start))

    def forget(self, keys: Iterable[str]) -> None:
        """A symbol left the universe: its open bars are discarded (never written half-built)."""
        for key in keys:
            self._open.pop(key, None)

    def open_bars(self) -> int:
        return sum(len(v) for v in self._open.values())


def bucket_end(bar: LakeBar, minutes: int) -> datetime:
    """The END of the `minutes` bucket holding the 1-minute `bar` - the store's own resample rule, reused as is."""
    return utc(resample([bar], minutes, exchange=exchange_of(bar.instrument_key))[0].ts)


@dataclass
class _Bucket:
    end: datetime
    bars: list[LakeBar] = field(default_factory=list)
    tainted: bool = False


def gap_range(key: str, start: datetime, end: datetime, minutes: int) -> tuple[datetime, datetime]:
    """The span of whole `minutes` buckets that covers the gap minutes [start, end) of `key` (UTC, END-labelled
    buckets: (first bucket's END - minutes, last bucket's END])."""
    if minutes == 1:
        return minute_start(start), minute_start(end - timedelta(microseconds=1)) + MINUTE
    first = LakeBar(key, BASE_TIMEFRAME, minute_start(start) + MINUTE, 1, 1, 1, 1)
    last = LakeBar(key, BASE_TIMEFRAME, minute_start(end - timedelta(microseconds=1)) + MINUTE, 1, 1, 1, 1)
    return bucket_end(first, minutes) - timedelta(minutes=minutes), bucket_end(last, minutes)


@dataclass
class Resampler:
    minutes: int
    grace_seconds: float = 3.0
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _gaps: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)

    @property
    def timeframe(self) -> str:
        return f"{self.minutes}m"

    def _overlaps_gap(self, key: str, end: datetime) -> bool:
        lo = end - timedelta(minutes=self.minutes)
        return any(s < end and lo < e for s, e in self._gaps.get(key, ()))

    def _emit(self, key: str, bucket: _Bucket) -> list[LakeBar]:
        if bucket.tainted or not bucket.bars:
            return []
        known = all(getattr(b, "volume_known", True) for b in bucket.bars)
        return [FeedBar(b.instrument_key, b.timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume, b.oi, volume_known=known)
                for b in resample(bucket.bars, self.minutes, exchange=exchange_of(key)) if utc(b.ts) == bucket.end]

    def add(self, bar: LakeBar) -> list[LakeBar]:
        """One CLOSED 1-minute bar (in time order per key). Returns the buckets this completes."""
        key = bar.instrument_key
        if is_post_close(key, bar):
            return []                                  # DH3: dropped before resampling, as the readers do
        end = bucket_end(bar, self.minutes)
        out: list[LakeBar] = []
        current = self._buckets.get(key)
        if current is not None and current.end < end:
            out += self._emit(key, self._buckets.pop(key))
            current = None
        if current is not None and current.end > end:
            return out                                 # an older minute than the open bucket: never reopens a bucket
        if current is None:
            current = self._buckets[key] = _Bucket(end, tainted=self._overlaps_gap(key, end))
        current.bars.append(bar)
        return out

    def taint(self, key: str, start: datetime, end: datetime) -> None:
        """Minutes [start, end) of `key` are a gap: no bucket holding one of them is handed out from partial data."""
        start, end = minute_start(start), utc(end)
        self._gaps[key] = [g for g in self._gaps.get(key, []) if g[0] != start and g[1] > start - timedelta(hours=2)] + [(start, end)]
        current = self._buckets.get(key)
        if current is not None and self._overlaps_gap(key, current.end):
            current.tainted = True

    def close_due(self, now: datetime) -> list[LakeBar]:
        now = utc(now)
        grace = timedelta(seconds=self.grace_seconds)
        out: list[LakeBar] = []
        for key in [k for k, b in self._buckets.items() if b.end + grace <= now]:
            out += self._emit(key, self._buckets.pop(key))
        return out

    def forget(self, keys: Iterable[str]) -> None:
        for key in keys:
            self._buckets.pop(key, None)


@dataclass
class BarPipeline:
    """1-minute builder + one resampler per interval: what one feed holds."""
    grace_seconds: float = 3.0
    minutes: tuple[int, ...] = RESAMPLE_MINUTES
    builder: MinuteBarBuilder = field(init=False)
    resamplers: list[Resampler] = field(init=False)

    def __post_init__(self) -> None:
        self.builder = MinuteBarBuilder(self.grace_seconds)
        self.resamplers = [Resampler(m, self.grace_seconds) for m in self.minutes]

    def _through(self, minute_bars: list[LakeBar]) -> list[LakeBar]:
        out = list(minute_bars)
        for bar in minute_bars:
            for r in self.resamplers:
                out += r.add(bar)
        return out

    def feed(self, key: str, ts: datetime, price: float, now: datetime, volume_total: int | None = None) -> list[LakeBar]:
        return self._through(self.builder.feed(key, ts, price, now, volume_total))

    def flush(self, now: datetime) -> list[LakeBar]:
        out = self._through(self.builder.close_due(now))
        for r in self.resamplers:
            out += r.close_due(now)
        return out

    def gap(self, keys: Iterable[str], start: datetime, end: datetime) -> None:
        keys = list(keys)
        self.builder.suppress(keys, start, end)
        for r in self.resamplers:
            for key in keys:
                r.taint(key, start, end)

    def forget(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        self.builder.forget(keys)
        for r in self.resamplers:
            r.forget(keys)
