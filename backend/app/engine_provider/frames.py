"""M66 EN-2: the engine's input frames built from ATP's own bars, and every gap in them (pure: no I/O).

Generic by design: this file shapes data (bar times, buckets, marks, gaps); it holds no reading rule of any engine.
The I/O that fetches the bars from ATP's stores and the user's broker is in `sources.py`; both feed `build_request`.

Rules every function here follows (CLAUDE.md sections 3-5; contract.py):
- Times: a bar has a START and an END (its close), both in India time (`IST`). ATP's lake labels a bar by its END; a
  broker labels it by its START - both are converted here, nowhere else.
- Closed bars only: a bar whose END is after the read time is never handed in (no look-ahead). A bucket rebuilt from
  1-minute bars counts once its own END has passed (a bucket cut short by the session close ends at that close).
- Missing stays missing: a missing bar, a missing volume or a missing source is never filled, interpolated or
  substituted. It is listed as a `DataGap` (in plain words for the admin, and as a token in the engine result's
  `data_problems` - `merge_problems`). A stored zero volume is a real zero; an index has no volume of its own (None).
- Futures volume beside an index: only the near-month contract of each bar's day (the first expiry on or after that
  day), only from bars the caller fetched live from the user's broker, never two contracts summed. A bar whose span the
  future's bars do not fully cover gets None, and a gap.
- Closing-auction marks (`Bar.closing_auction`): an intraday bar that starts at or after the day's close (the
  exchange's closing-auction stretch) or that ATP flagged `post_close` (DH3). Continuous and auction minutes are never
  put in one bucket. `Bar.extended_session` on a Daily bar: True when ATP flagged its range as set in the closing
  auction (DH3 `auction_high` / `auction_low`) or when that day's complete continuous 1-minute bars show it; None when
  ATP cannot tell. Bars are handed in as stored - the marks say what they are, the engine decides what to do.
- Deterministic: the same inputs give the same frames and the same gaps, in the same order.
Nothing here places, blocks or changes an order.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.engine_provider.contract import Bar, ContractViolation, EarlyClose, EngineRequest, EngineResult, Frame, MarketCalendar
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, ExchangeSession, session_family

DAILY = "1d"
MINUTES = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
INDEX_KEY_PREFIXES = ("NSE_INDEX:", "BSE_INDEX:")
TIMEZONE_NAME = "Asia/Kolkata"
ONE_MINUTE = timedelta(minutes=1)

# Gap kinds (the token is "<kind>:<timeframe>", e.g. "missing_bars:5m").
MISSING_SOURCE = "missing_source"                 # no store and no broker holds bars of this timeframe for the symbol
MISSING_BARS = "missing_bars"                     # trading-session bars absent from the source
INCOMPLETE_BARS = "incomplete_bars"               # a bar rebuilt from 1-minute bars of which some are absent
MISSING_FUTURES_VOLUME = "missing_futures_volume"  # an index bar without its near-month future's volume
INVALID_BARS = "invalid_bars"                     # a stored bar that breaks the bar rules (low above high, ...)
GAP_KINDS = (MISSING_SOURCE, MISSING_BARS, INCOMPLETE_BARS, MISSING_FUTURES_VOLUME, INVALID_BARS)


def _ist(ts: datetime) -> datetime:
    """Any datetime in India time; a naive one is UTC (what ATP's stores hold)."""
    return (ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)).astimezone(IST)


def is_index_key(key: str) -> bool:
    """An index key of ATP's lake (`NSE_INDEX:NIFTY50`, `BSE_INDEX:SENSEX`): an index has no volume of its own."""
    return key.upper().startswith(INDEX_KEY_PREFIXES)


# --- the calendar ----------------------------------------------------------------------------------------------------

def market_calendar(exchange: str, holidays: Iterable[date], first: date, last: date,
                    early_closes: Iterable[EarlyClose] = ()) -> MarketCalendar:
    """The contract's calendar for [first, last]: the exchange family's regular hours (app.market_data.calendar) and
    the holidays / early closes inside the window, sorted. ATP's calendar keeps no early closes today: the caller hands
    them in when it has them (an early close not handed in shows up as missing bars after it - never hidden)."""
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    days = tuple(sorted({d for d in holidays if first <= d <= last}))
    early = tuple(sorted({e for e in early_closes if first <= e.day <= last}, key=lambda e: e.day))
    return MarketCalendar(timezone=TIMEZONE_NAME, regular_open=spec.open, regular_close=spec.close, holidays=days,
                          early_closes=early)


def _closes(cal: MarketCalendar) -> dict[date, time]:
    return {e.day: e.close for e in cal.early_closes}


def is_session_day(cal: MarketCalendar, day: date) -> bool:
    return day.weekday() < 5 and day not in set(cal.holidays)


def session_open(cal: MarketCalendar, day: date) -> datetime:
    return datetime.combine(day, cal.regular_open, tzinfo=IST)


def session_close(cal: MarketCalendar, day: date, closes: Mapping[date, time] | None = None) -> datetime:
    """The day's close: its early close when the calendar lists one, else the regular close."""
    at = (closes if closes is not None else _closes(cal)).get(day, cal.regular_close)
    return datetime.combine(day, at, tzinfo=IST)


def _spec(exchange: str) -> ExchangeSession:
    return EXCHANGE_SESSIONS[session_family(exchange)]


# --- raw bars ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RawBar:
    """One bar as a source holds it, with its START and END already worked out (India time)."""
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    closing_auction: bool | None = None


@dataclass(frozen=True)
class SourceBars:
    """The bars of one timeframe from ONE source (sources are never mixed in one frame).

    source: the source in plain words ("shared index history", "your broker", ...).
    timeframe: the bars' own timeframe key ("1m", "5m", "1d", ...).
    """
    source: str
    timeframe: str
    bars: tuple[RawBar, ...]


def _bar_start(end: datetime, timeframe: str, cal: MarketCalendar) -> datetime:
    if timeframe == DAILY:
        return session_open(cal, _ist(end).date())
    return end - timedelta(minutes=MINUTES[timeframe])


def from_lake_rows(rows: Sequence[Sequence[Any]], timeframe: str, cal: MarketCalendar, *, source: str,
                   own_volume: bool = True, post_close: Iterable[datetime] = ()) -> SourceBars:
    """ATP lake rows (END, version, open, high, low, close, volume, ...), ordered so the winning row of each END comes
    last (the lake readers' order) -> one RawBar per END. `own_volume` False (an index): volume None.
    `post_close`: the ENDs ATP flagged as after continuous trading (DH3) - marked, never dropped."""
    flagged = {_ist(t) for t in post_close}
    best: dict[datetime, Sequence[Any]] = {}
    for row in rows:
        best[_ist(row[0])] = row
    out = []
    for end in sorted(best):
        row = best[end]
        vol = row[6] if own_volume else None
        start = _bar_start(end, timeframe, cal)
        out.append(RawBar(start=start, end=end, open=float(row[2]), high=float(row[3]), low=float(row[4]), close=float(row[5]),
                          volume=None if vol is None else float(vol),
                          closing_auction=_auction(start, timeframe, cal, end in flagged)))
    return SourceBars(source, timeframe, tuple(out))


def from_broker_bars(bars: Sequence[Any], timeframe: str, cal: MarketCalendar, *, source: str,
                     own_volume: bool = True) -> SourceBars:
    """A broker's bars (objects with timestamp = the bar's START, open, high, low, close, volume) -> RawBars; a Daily
    bar spans that day's session (open to its close, an early close included). The same START twice: the later wins."""
    closes = _closes(cal)
    best: dict[datetime, Any] = {}
    for b in bars:
        best[_ist(b.timestamp)] = b
    out = []
    for start in sorted(best):
        b = best[start]
        if timeframe == DAILY:
            day = start.date()
            begin, end = session_open(cal, day), session_close(cal, day, closes)
        else:
            begin, end = start, start + timedelta(minutes=MINUTES[timeframe])
        vol = getattr(b, "volume", None) if own_volume else None
        out.append(RawBar(start=begin, end=end, open=float(b.open), high=float(b.high), low=float(b.low), close=float(b.close),
                          volume=None if vol is None else float(vol), closing_auction=_auction(begin, timeframe, cal, False)))
    return SourceBars(source, timeframe, tuple(out))


def _auction(start: datetime, timeframe: str, cal: MarketCalendar, flagged: bool) -> bool | None:
    """Intraday: True when the bar starts at or after its day's close (the closing-auction stretch) or ATP flagged it;
    else False. Daily: None (a Daily bar carries `extended_session` instead)."""
    if timeframe == DAILY:
        return None
    return flagged or start >= session_close(cal, start.date())


# --- buckets rebuilt from 1-minute bars --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Rebuilt:
    bars: tuple[RawBar, ...]
    incomplete: tuple[tuple[datetime, int, int], ...]     # (bucket START, minutes present, minutes the bucket spans)


def rebuild(minutes: SourceBars, timeframe: str, as_of: datetime, cal: MarketCalendar) -> Rebuilt:
    """1-minute bars -> `timeframe` buckets, counted from the session open of each day; a bucket that the day's close
    cuts short ends at that close. Minutes at or after the close (the closing-auction stretch) form their own buckets
    counted from the close, marked `closing_auction`; continuous and auction minutes are never mixed. Only buckets
    whose END is at or before `as_of` are kept. A bucket's volume is the sum of its minutes' volumes, or None when any
    minute has none. A continuous bucket with fewer minutes than it spans is kept and listed in `incomplete`."""
    if timeframe == "1m":
        return Rebuilt(tuple(b for b in minutes.bars if b.end <= as_of), ())
    step = timedelta(minutes=MINUTES[timeframe])
    closes = _closes(cal)
    buckets: dict[tuple[datetime, bool], list[RawBar]] = {}
    ends: dict[tuple[datetime, bool], datetime] = {}
    for m in minutes.bars:
        day = m.start.date()
        close = session_close(cal, day, closes)
        auction = bool(m.closing_auction) or m.start >= close
        origin = close if auction else session_open(cal, day)
        index = (m.start - origin) // step
        begin = origin + index * step
        end = begin + step if auction else min(begin + step, close)
        buckets.setdefault((begin, auction), []).append(m)
        ends[(begin, auction)] = end
    out: list[RawBar] = []
    incomplete: list[tuple[datetime, int, int]] = []
    for key in sorted(buckets):
        begin, auction = key
        end = ends[key]
        if end > as_of:
            continue
        part = sorted(buckets[key], key=lambda b: b.start)
        volumes = [b.volume for b in part]
        out.append(RawBar(start=begin, end=end, open=part[0].open, high=max(b.high for b in part), low=min(b.low for b in part),
                          close=part[-1].close, volume=None if any(v is None for v in volumes) else float(sum(v for v in volumes if v is not None)),
                          closing_auction=auction))
        span = int((end - begin) / ONE_MINUTE)
        if not auction and len({b.start for b in part}) < span:
            incomplete.append((begin, len({b.start for b in part}), span))
    return Rebuilt(tuple(out), tuple(incomplete))


# --- futures volume beside an index ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class FuturesContract:
    """One futures contract's bars, fetched live from the user's broker (owner decision: the only source).

    symbol / expiry: the contract. valid_from: the first day it is the near month (the day after the previous
    contract's expiry) when the caller knows it; else the first day of its expiry month - for a monthly contract a day
    that is always inside its near-month stretch, so a day before it is reported as a gap, never guessed.
    bars: its bars at the frame's base timeframe or finer (START / END worked out, volume as the broker sent it).
    """
    symbol: str
    expiry: date
    bars: tuple[RawBar, ...]
    valid_from: date | None = None

    def near_month_on(self, day: date) -> bool:
        first = self.valid_from or self.expiry.replace(day=1)
        return first <= day <= self.expiry


def _contract_for(day: date, contracts: Sequence[FuturesContract]) -> FuturesContract | None:
    """The near-month contract of `day` among those handed in: the first expiry on or after the day, and only when that
    day is provably inside its near-month stretch."""
    for c in sorted(contracts, key=lambda c: (c.expiry, c.symbol)):
        if c.expiry >= day:
            return c if c.near_month_on(day) else None
    return None


def futures_volume(bars: Sequence[Bar], contracts: Sequence[FuturesContract]) -> tuple[tuple[float | None, ...], tuple[tuple[datetime, str], ...]]:
    """Per index bar the near-month future's volume over exactly the bar's span: the sum of the future's bars inside
    [start, end) when they cover the whole span and each has a volume, else None. Returns (volumes, misses) where a
    miss is (bar START, reason in plain words). A bar marked closing_auction has no futures counterpart (the futures
    market has no closing-auction stretch): None, and not a miss."""
    by_contract: dict[str, list[RawBar]] = {c.symbol: sorted(c.bars, key=lambda b: b.start) for c in contracts}
    out: list[float | None] = []
    misses: list[tuple[datetime, str]] = []
    for bar in bars:
        if bar.closing_auction:
            out.append(None)
            continue
        contract = _contract_for(_ist(bar.start).date(), contracts)
        if contract is None:
            out.append(None)
            misses.append((bar.start, "no near-month contract of that day was fetched"))
            continue
        inside = [f for f in by_contract[contract.symbol] if bar.start <= f.start and f.end <= bar.end]
        covered = sum(((f.end - f.start) for f in inside), timedelta())
        if not inside or covered < bar.end - bar.start:
            out.append(None)
            misses.append((bar.start, f"the future {contract.symbol} has no bars there" if not inside
                           else f"the future {contract.symbol} has only part of the bar"))
            continue
        if any(f.volume is None for f in inside):
            out.append(None)
            misses.append((bar.start, f"the broker sent no volume for {contract.symbol} there"))
            continue
        out.append(float(sum(f.volume for f in inside if f.volume is not None)))
    return tuple(out), tuple(misses)


# --- gaps --------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DataGap:
    """One gap in the input of one symbol and timeframe, in plain words for the admin (`text`) and as a token for the
    engine result's data problems (`token`). first / last: the first and last bar START it covers (None = the whole
    window); count: how many bars."""
    symbol: str
    timeframe: str
    kind: str
    count: int
    first: datetime | None = None
    last: datetime | None = None
    detail: str = ""

    @property
    def token(self) -> str:
        return f"{self.kind}:{self.timeframe}"

    @property
    def text(self) -> str:
        where = ""
        if self.first is not None:
            a, b = _when(self.first), _when(self.last or self.first)
            where = f", at {a}" if a == b else f", from {a} to {b}"
        what = {MISSING_SOURCE: "no source holds these bars",
                MISSING_BARS: f"{self.count} bar{'s' if self.count != 1 else ''} missing",
                INCOMPLETE_BARS: f"{self.count} bar{'s' if self.count != 1 else ''} built from incomplete 1-minute bars",
                MISSING_FUTURES_VOLUME: f"futures volume missing on {self.count} bar{'s' if self.count != 1 else ''}",
                INVALID_BARS: f"{self.count} stored bar{'s' if self.count != 1 else ''} not usable (prices out of order)"}[self.kind]
        tail = f" - {self.detail}" if self.detail else ""
        return f"{self.symbol} {self.timeframe}: {what}{where} (India time){tail}." if where else f"{self.symbol} {self.timeframe}: {what}{tail}."


def _when(ts: datetime) -> str:
    local = _ist(ts)
    return local.strftime("%d %b %Y %H:%M")


def _runs(starts: Sequence[datetime], expected: Sequence[datetime]) -> list[tuple[datetime, datetime, int]]:
    """Missing STARTs collapsed into runs that are consecutive in the `expected` order: (first, last, count)."""
    position = {s: i for i, s in enumerate(expected)}
    out: list[tuple[datetime, datetime, int]] = []
    for s in sorted(starts, key=lambda s: position[s]):
        if out and position[s] == position[out[-1][1]] + 1:
            out[-1] = (out[-1][0], s, out[-1][2] + 1)
        else:
            out.append((s, s, 1))
    return out


def expected_starts(timeframe: str, start: datetime, as_of: datetime, cal: MarketCalendar) -> tuple[datetime, ...]:
    """The STARTs of every continuous-session bar of `timeframe` that has closed in [start, as_of]: on each session day,
    buckets from the open (the last one cut short by the day's close, an early close included). Daily: the session
    days whose close is at or before `as_of`."""
    closes = _closes(cal)
    out: list[datetime] = []
    day = _ist(start).date()
    last = _ist(as_of).date()
    while day <= last:
        if is_session_day(cal, day):
            begin, close = session_open(cal, day), session_close(cal, day, closes)
            if timeframe == DAILY:
                if begin >= start and close <= as_of:
                    out.append(begin)
            else:
                step = timedelta(minutes=MINUTES[timeframe])
                t = begin
                while t < close:
                    end = min(t + step, close)
                    if t >= start and end <= as_of:
                        out.append(t)
                    t += step
        day += timedelta(days=1)
    return tuple(out)


def missing_bars(symbol: str, timeframe: str, bars: Sequence[Bar], start: datetime, as_of: datetime,
                 cal: MarketCalendar) -> list[DataGap]:
    """The continuous-session bars absent from `bars` in [start, as_of], one gap per run of consecutive ones."""
    want = expected_starts(timeframe, start, as_of, cal)
    if timeframe == DAILY:
        have_days = {_ist(b.start).date() for b in bars}
        missing = [s for s in want if s.date() not in have_days]
    else:
        have = {_ist(b.start) for b in bars if not b.closing_auction}
        missing = [s for s in want if s not in have]
    return [DataGap(symbol, timeframe, MISSING_BARS, n, a, b) for a, b, n in _runs(missing, want)]


def _grouped(symbol: str, timeframe: str, kind: str, starts: Iterable[datetime], detail_of: Mapping[datetime, str],
             order: Sequence[datetime]) -> list[DataGap]:
    """Gaps of one kind, one per run of STARTs that are consecutive in `order` (the frame's bars) with the same detail."""
    position = {s: i for i, s in enumerate(order)}
    out: list[DataGap] = []
    last_pos = -2
    for s in sorted(set(starts), key=lambda s: (position.get(s, len(position)), s)):
        detail = detail_of.get(s, "")
        pos = position.get(s, -10)
        prev = out[-1] if out else None
        if prev is not None and prev.detail == detail and pos >= 0 and pos == last_pos + 1:
            out[-1] = dataclasses.replace(prev, last=s, count=prev.count + 1)
        else:
            out.append(DataGap(symbol, timeframe, kind, 1, s, s, detail))
        last_pos = pos
    return out


# --- frames and the request ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolData:
    """Everything ATP fetched for one symbol, before shaping.

    symbol: ATP's instrument key; exchange: its exchange (the session clock and calendar).
    minutes: 1-minute bars (one source) that intraday timeframes without bars of their own are rebuilt from.
    minutes_by_timeframe: {timeframe: 1-minute bars of another source} for timeframes rebuilt from that source instead
        (e.g. 15m / 1h from the stored index history while 5m comes from the user's broker).
    native: {timeframe: bars of that timeframe} from one source each (they win over a rebuild).
    daily: the Daily bars (one source). extended_days: the days ATP flagged as auction-extended (DH3).
    futures: for an index, the futures contracts fetched live from the user's broker (none = not fetched).
    futures_expected: True for an index with listed futures - its futures timeframes then need the futures volume.
    sources_tried: plain words of every source looked at, for the missing-source text.
    history: {timeframe: HistoryBars} (M79): that timeframe's bars before `HistoryBars.cut`, already rebuilt, from
        the stored history (read once a day); the 1-minute bars then only need to cover the time from the cut.
    """
    symbol: str
    exchange: str
    minutes: SourceBars | None = None
    minutes_by_timeframe: Mapping[str, SourceBars] = field(default_factory=dict)
    native: Mapping[str, SourceBars] = field(default_factory=dict)
    daily: SourceBars | None = None
    extended_days: frozenset[date] = frozenset()
    futures: tuple[FuturesContract, ...] = ()
    futures_expected: bool = False
    sources_tried: tuple[str, ...] = ()
    history: Mapping[str, HistoryBars] = field(default_factory=dict)


@dataclass(frozen=True)
class HistoryBars:
    """M79: one timeframe's bars rebuilt from ONE 1-minute source, every bucket ending by `cut` (a session open), with
    the buckets that had minutes missing. Joined to the same source's newer bars only (sources are never mixed)."""
    source: str
    timeframe: str
    cut: datetime
    bars: tuple[RawBar, ...]
    incomplete: tuple[tuple[datetime, int, int], ...] = ()


def history_of(minutes: SourceBars, timeframe: str, cut: datetime, cal: MarketCalendar) -> HistoryBars:
    """The stored 1-minute history before `cut` -> its `timeframe` buckets (`rebuild`, as a frame would)."""
    built = rebuild(minutes, timeframe, cut, cal)
    return HistoryBars(minutes.source, timeframe, cut, built.bars, built.incomplete)


@dataclass(frozen=True)
class BuiltFrame:
    """`gaps` block the read; `history_gaps` lie wholly before the frame's required window (M79: the stored history
    the engine reads "as far back as it is there") - listed for the admin, never a data problem."""
    frame: Frame
    source: str | None
    gaps: tuple[DataGap, ...]
    history_gaps: tuple[DataGap, ...] = ()


@dataclass(frozen=True)
class Built:
    """`request` is None when the signal timeframe has no closed bar (then nothing can be read); `gaps` lists every gap,
    in a fixed order (symbol, timeframe order asked, kind order, first bar)."""
    request: EngineRequest | None
    gaps: tuple[DataGap, ...]
    sources: Mapping[str, str | None]
    history_gaps: tuple[DataGap, ...] = ()

    @property
    def data_problems(self) -> tuple[str, ...]:
        return problem_tokens(self.gaps)


def _to_bars(raw: Iterable[RawBar], as_of: datetime, extended: Mapping[date, bool | None] | None = None) -> tuple[list[Bar], int, list[datetime]]:
    """RawBars closed by `as_of` -> contract Bars; (bars, how many broke the bar rules, their STARTs)."""
    out: list[Bar] = []
    bad: list[datetime] = []
    for r in sorted(raw, key=lambda r: r.start):
        if r.end > as_of:
            continue
        ext = None if extended is None else extended.get(r.start.date())
        try:
            out.append(Bar(start=r.start, end=r.end, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume,
                           closing_auction=r.closing_auction, extended_session=ext))
        except ContractViolation:
            bad.append(r.start)
    return out, len(bad), bad


def extended_marks(daily: SourceBars, minutes: SourceBars | None, flagged: Iterable[date], cal: MarketCalendar) -> dict[date, bool | None]:
    """Per Daily bar's day: True when ATP flagged it (DH3), else - when that day's continuous 1-minute bars are all
    there - whether the Daily high is above / low below their extremes; else None (ATP cannot tell)."""
    flagged_days = set(flagged)
    closes = _closes(cal)
    by_day: dict[date, list[RawBar]] = {}
    for m in (minutes.bars if minutes is not None else ()):
        if not m.closing_auction and m.start < session_close(cal, m.start.date(), closes):
            by_day.setdefault(m.start.date(), []).append(m)
    out: dict[date, bool | None] = {}
    for d in daily.bars:
        day = d.start.date()
        if day in flagged_days:
            out[day] = True
            continue
        cont = by_day.get(day, [])
        span = int((session_close(cal, day, closes) - session_open(cal, day)) / ONE_MINUTE)
        if len({m.start for m in cont if m.start >= session_open(cal, day)}) < span:
            out[day] = None
            continue
        out[day] = d.high > max(m.high for m in cont) or d.low < min(m.low for m in cont)
    return out


def _minutes(data: SymbolData, timeframe: str) -> SourceBars | None:
    for base in (data.minutes_by_timeframe.get(timeframe), data.minutes):
        if base is not None and base.bars:
            return base
    return None


def _longest_minutes(data: SymbolData) -> SourceBars | None:
    """The 1-minute source holding the most bars (the Daily marks' check); a tie keeps `minutes`."""
    found = [b for b in (data.minutes, *(data.minutes_by_timeframe[k] for k in sorted(data.minutes_by_timeframe))) if b is not None]
    return max(found, key=lambda b: len(b.bars)) if found else None


def _split(starts: Iterable[datetime], required: datetime) -> tuple[list[datetime], list[datetime]]:
    """(STARTs at or after `required`, STARTs before it)."""
    starts = list(starts)
    return [s for s in starts if s >= required], [s for s in starts if s < required]


def build_frame(data: SymbolData, timeframe: str, start: datetime, as_of: datetime, cal: MarketCalendar,
                futures_timeframes: Sequence[str] = ("5m",), required: datetime | None = None) -> BuiltFrame:
    """One timeframe's Frame for `data` with closed bars in (start, as_of], and its gaps. `required` (M79, runner
    setting `history_days`): the first bar START of the window whose gaps block the read; the bars from `start` up to
    it are the stored history, handed in as they are, and their gaps are `history_gaps` (never a data problem). None
    (or not after `start`) = the whole frame is required, as before."""
    sym = data.symbol
    check = start if required is None or required <= start else required
    gaps: list[DataGap] = []
    history: list[DataGap] = []
    source: str | None = None
    raw: tuple[RawBar, ...] = ()
    extended: dict[date, bool | None] | None = None
    if timeframe in data.native and data.native[timeframe].bars:
        source, raw = data.native[timeframe].source, data.native[timeframe].bars
    elif timeframe == DAILY and data.daily is not None and data.daily.bars:
        source, raw = data.daily.source, data.daily.bars
        extended = extended_marks(data.daily, _longest_minutes(data), data.extended_days, cal)
    elif timeframe in MINUTES and _minutes(data, timeframe) is not None:
        base = _minutes(data, timeframe)
        assert base is not None
        built = rebuild(base, timeframe, as_of, cal)
        old = data.history.get(timeframe)
        if old is not None and old.source == base.source:          # the stored history + the same source's newer bars
            built = Rebuilt(tuple(b for b in old.bars if b.end <= old.cut) + tuple(b for b in built.bars if b.start >= old.cut),
                            tuple(i for i in old.incomplete if i[0] < old.cut) + tuple(i for i in built.incomplete if i[0] >= old.cut))
        source, raw = f"{base.source} (rebuilt from 1-minute bars)" if timeframe != "1m" else base.source, built.bars
        order = [b.start for b in built.bars]
        detail = {s: f"{have} of {span} minutes there" for s, have, span in built.incomplete}
        now_part, old_part = _split((s for s, _, _ in built.incomplete if s >= start), check)
        gaps += _grouped(sym, timeframe, INCOMPLETE_BARS, now_part, detail, order)
        history += _grouped(sym, timeframe, INCOMPLETE_BARS, old_part, detail, order)
    raw = tuple(r for r in raw if r.start >= start)
    bars, _, bad = _to_bars(raw, as_of, extended)
    if source is None:
        tried = ", ".join(data.sources_tried) if data.sources_tried else "none"
        gaps.append(DataGap(sym, timeframe, MISSING_SOURCE, 0, detail=f"looked at: {tried}"))
    else:
        if bad:
            now_part, old_part = _split(bad, check)
            gaps += _grouped(sym, timeframe, INVALID_BARS, now_part, {}, [r.start for r in raw])
            history += _grouped(sym, timeframe, INVALID_BARS, old_part, {}, [r.start for r in raw])
        gaps += missing_bars(sym, timeframe, bars, check, as_of, cal)
        if check > start:
            history += missing_bars(sym, timeframe, bars, start, check, cal)
    fut: tuple[float | None, ...] | None = None
    if data.futures_expected and timeframe in futures_timeframes:
        fut, misses = futures_volume(bars, data.futures)
        if not data.futures and bars:
            misses = tuple((b.start, "no futures bars were fetched from your broker") for b in bars if not b.closing_auction)
        now_part, old_part = _split((s for s, _ in misses), check)
        gaps += _grouped(sym, timeframe, MISSING_FUTURES_VOLUME, now_part, dict(misses), [b.start for b in bars])
        history += _grouped(sym, timeframe, MISSING_FUTURES_VOLUME, old_part, dict(misses), [b.start for b in bars])
    frame = Frame(timeframe=timeframe, bars=tuple(bars), futures_volume=fut)

    def order(found: list[DataGap]) -> tuple[DataGap, ...]:
        return tuple(sorted(found, key=lambda g: (GAP_KINDS.index(g.kind), g.first or start)))
    return BuiltFrame(frame, source, order(gaps), order(history))


def build_request(data: SymbolData, timeframes: Sequence[str], signal_timeframe: str, now: datetime,
                  starts: Mapping[str, datetime], cal: MarketCalendar, settings: Mapping[str, Any] | None = None,
                  futures_timeframes: Sequence[str] = ("5m",), required: Mapping[str, datetime] | None = None) -> Built:
    """The EngineRequest for one symbol at `now`: `as_of` is the END of the signal timeframe's last bar closed by
    `now`, and every frame holds only bars closed by that `as_of` (none after it). starts: {timeframe: the frame's
    first bar START}. required (M79): {timeframe: the first bar START whose gaps block} for a frame that reaches back
    into the stored history; a timeframe not in it is required from its start. Every blocking gap of every frame is
    listed (`Built.gaps`, `Built.data_problems`), the history's gaps apart (`Built.history_gaps`); the signal frame's
    missing bars are counted up to `now` (a source that stopped delivering is a gap, not a quiet old `as_of`)."""
    order = list(dict.fromkeys([signal_timeframe, *timeframes]))
    now = _ist(now)
    need = {tf: _ist(v) for tf, v in (required or {}).items()}
    signal = build_frame(data, signal_timeframe, _ist(starts[signal_timeframe]), now, cal, futures_timeframes,
                         need.get(signal_timeframe))
    as_of = signal.frame.bars[-1].end if signal.frame.bars else None
    built: dict[str, BuiltFrame] = {}
    for tf in order:
        built[tf] = build_frame(data, tf, _ist(starts[tf]), as_of or now, cal, futures_timeframes, need.get(tf))
    # The signal frame's gaps are counted up to `now`: bars that should have closed after its last one are missing.
    tail = [g for g in signal.gaps if g.kind == MISSING_BARS and g.first is not None and as_of is not None and g.first >= as_of]
    if tail:
        built[signal_timeframe] = dataclasses.replace(built[signal_timeframe], gaps=built[signal_timeframe].gaps + tuple(tail))
    asked = order
    history = tuple(g for tf in asked for g in built[tf].history_gaps)
    if as_of is None:                                  # nothing to read; every frame's gaps are still listed
        return Built(None, tuple(g for tf in asked for g in built[tf].gaps), {tf: built[tf].source for tf in asked}, history)
    request = EngineRequest(symbol=data.symbol, as_of=as_of, frames={tf: built[tf].frame for tf in asked}, calendar=cal,
                            settings=dict(settings or {}))
    gaps = tuple(g for tf in asked for g in built[tf].gaps)
    return Built(request, gaps, {tf: built[tf].source for tf in asked}, history)


# --- data problems and the probe ---------------------------------------------------------------------------------------

def problem_tokens(gaps: Iterable[DataGap]) -> tuple[str, ...]:
    """The gaps as the contract's data-problem tokens: one per kind and timeframe, sorted."""
    return tuple(sorted({g.token for g in gaps}))


def merge_problems(result: EngineResult, gaps: Iterable[DataGap]) -> EngineResult:
    """The engine's result with the adapter's gaps added to its `data_problems` (any one = no signal is acted on)."""
    return dataclasses.replace(result, data_problems=tuple(sorted(set(result.data_problems) | set(problem_tokens(gaps)))))


def gap_report(gaps: Iterable[DataGap]) -> dict[str, dict[str, list[str]]]:
    """{symbol: {timeframe: [plain-words line, ...]}} - the admin's gap list, in a fixed order; a timeframe without a
    gap is absent."""
    out: dict[str, dict[str, list[str]]] = {}
    for g in sorted(gaps, key=lambda g: (g.symbol, g.timeframe, GAP_KINDS.index(g.kind), _ist(g.first) if g.first else datetime.min.replace(tzinfo=IST))):
        out.setdefault(g.symbol, {}).setdefault(g.timeframe, []).append(g.text)
    return out


__all__ = ["DAILY", "MINUTES", "GAP_KINDS", "MISSING_SOURCE", "MISSING_BARS", "INCOMPLETE_BARS", "MISSING_FUTURES_VOLUME",
           "INVALID_BARS", "RawBar", "SourceBars", "HistoryBars", "history_of", "FuturesContract", "DataGap", "SymbolData", "BuiltFrame", "Built", "Rebuilt",
           "is_index_key", "market_calendar", "session_open", "session_close", "is_session_day", "from_lake_rows",
           "from_broker_bars", "rebuild", "futures_volume", "expected_starts", "missing_bars", "extended_marks", "build_frame",
           "build_request", "problem_tokens", "merge_problems", "gap_report"]
