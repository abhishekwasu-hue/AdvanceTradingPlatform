"""M66 EN-2: the I/O half of the engine data adapter - reads ATP's own bars and hands them to `frames.py`.

Sources (read only; nothing here writes a row, places an order or reads one):
- ATP's lake: the shared market history (`md_candles`, the shared-licensed sources the chart layer reads) and the
  reader's own stored history (`md_tenant_candles` through `own_bars`; M55 T4: the shared index intraday history of
  the history owner for every reader while the data-tier rules allow it). One source per frame, the one holding more
  bars (`own_bars.prefer_own`). The rows are read AS STORED (`own_bars.fetch_rows(..., dh3=False)`): the DH3 flags
  become the contract's marks, they are not applied.
- The user's own broker session (any object with `get_historical_data`, and `get_intraday_candles` where the broker
  serves the running day from a separate endpoint): 1-minute and Daily bars of the symbol, and - for an index - the
  near-month future's 1-minute bars (owner decision: futures volume comes only live from the broker; the zero-filled
  chart path `app.market_data.index_volume.replace_volume` is not used - only its contract lookup `near_future`).
- ATP's market calendar (`market_holidays` through `app.market_data.calendar.family_holidays`).
A source that fails or holds nothing is listed in plain words; `frames.build_frame` turns that into a gap. No new
table and no migration. EN-3 (the runner) calls `collect` + `frames.build_request`; `probe` is the admin's gap list.

M71 W2-C (flag `market_store_reads`, the runner hands `store=StoreRead(...)`; OFF = no `store`, exactly the path above):
the broker's 1-minute bars of the symbol and of an index's near-month future are read through ATP's own store
(`app.market_store.read_bars(..., dh3=False)`): the reader's own stored 1-minute rows, then only the newest missing
bars through his OWN broker session (M71Q-2, paced by the credential's data budget, kept until the next bar closes, so
a second run makes no history call). The rows stay AS STORED and the DH3 flags become marks, as in `lake_bars`. The
store's START labels go back to the END labels `frames.from_lake_rows` takes (START + 1 minute). A missing minute or
volume stays missing (a gap, never 0). A symbol the store holds nothing of keeps the broker path above.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine_provider import frames as F
from app.engine_provider.contract import EarlyClose, MarketCalendar

log = logging.getLogger(__name__)

LAKE_TIMEFRAMES = ("15m", "1h")            # read from the stored history (M55 T4); the rest from the user's broker
SHARED_LAKE = "shared market history"
OWN_LAKE = "your stored history"
SHARED_INDEX = "shared index history"
BROKER = "your broker"
DH3_POST_CLOSE = "post_close"
DH3_EXTENDED = ("auction_high", "auction_low")
STORE = f"{OWN_LAKE} and {BROKER}"          # M71 W2-C: the own store, topped up with the newest bars from the own session
STORE_TRIED = "your stored history (1-minute, newest bars from your broker)"


@dataclass(frozen=True)
class StoreRead:
    """M71 W2-C: read the 1-minute bars through the own store (flag `market_store_reads` ON). `user`: whose stored
    rows (M71Q-2: only his own); `rest`: his own broker session for the newest bars (`market_store.RestSource`) or None
    (the newest bars are then a gap)."""
    user: Any
    rest: Any = None


def _utc(ts: datetime) -> datetime:
    return (ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)).astimezone(UTC)


async def load_calendar(session: AsyncSession, exchange: str, first: date, last: date,
                        early_closes: Sequence[EarlyClose] = ()) -> MarketCalendar:
    """The contract's calendar of [first, last] from ATP's holiday table (early closes: only as handed in)."""
    from app.market_data.calendar import family_holidays
    days: set[date] = set()
    for year in range(first.year, last.year + 1):
        days |= await family_holidays(session, exchange, year)
    return F.market_calendar(exchange, days, first, last, early_closes)


def _row_limit(timeframe: str, start: datetime, end: datetime) -> int:
    from app.charts import price_action_layer as pal
    minutes = F.MINUTES.get(timeframe)
    if minutes is None:
        return pal.MAX_BARS * pal.ROWS_PER_BAR
    return max(pal.MAX_BARS, int((end - start) / timedelta(minutes=minutes)) + 1) * pal.ROWS_PER_BAR


async def lake_bars(session: AsyncSession, key: str, timeframe: str, start: datetime, end: datetime, cal: MarketCalendar, *,
                    scope: Any = None) -> tuple[F.SourceBars | None, frozenset[date], list[str]]:
    """The lake's own bars of `timeframe` ("1m", "15m", "1h", "1d") with END in (start, end] from ONE source, as stored,
    with the DH3 marks: (bars or None, the Daily days flagged auction-extended, notes in plain words)."""
    from app.charts import price_action_layer as pal
    from app.market_lake import own_bars
    notes: list[str] = []
    own_volume = not F.is_index_key(key)
    whose = SHARED_INDEX if own_bars.is_shared(scope) else OWN_LAKE
    limit = _row_limit(timeframe, start, end)
    for label in pal.LAKE_LABELS.get(timeframe, (timeframe,)):
        try:
            shared = await pal._fetch(session, key, label, _utc(start), _utc(end), limit)
        except pal.WindowTooLarge as exc:
            notes.append(f"{SHARED_LAKE}: {exc}")
            shared = []
        own: list[Any] = []
        if scope is not None:
            try:
                own = await own_bars.fetch_rows(session, scope, key, label, _utc(start), _utc(end), limit=limit, dh3=False)
            except (ValueError, PermissionError) as exc:
                notes.append(f"{whose}: {exc}")
        if scope is not None and own_bars.prefer_own(shared, own):
            marks = await own_bars.flags(session, int(scope[0]), key, label, _utc(start), _utc(end))
            post = [t for t, kinds in marks.items() if DH3_POST_CLOSE in kinds]
            extended = frozenset(t.astimezone(F.IST).date() for t, kinds in marks.items() if any(k in kinds for k in DH3_EXTENDED))
            return F.from_lake_rows(own, timeframe, cal, source=whose, own_volume=own_volume, post_close=post), extended, notes
        if shared:
            return F.from_lake_rows(shared, timeframe, cal, source=SHARED_LAKE, own_volume=own_volume), frozenset(), notes
    return None, frozenset(), notes


async def broker_bars(broker: Any, symbol: str, exchange: str, timeframe: str, start: datetime, end: datetime,
                      cal: MarketCalendar, *, own_volume: bool = True, source: str = BROKER) -> tuple[F.SourceBars | None, list[str]]:
    """The user's broker's 1-minute ("1m") or Daily ("1d") bars of [start, end], plus the running day's intraday bars
    where the broker serves them separately. A failed call is a note, never an exception."""
    interval = "day" if timeframe == F.DAILY else "1min"
    try:
        bars = list(await broker.get_historical_data(symbol, exchange, interval, start, end))
        intraday = getattr(broker, "get_intraday_candles", None)
        if timeframe != F.DAILY and intraday is not None:                 # bars after `end` are dropped by the frames
            bars += list(await intraday(symbol, exchange, interval))
    except Exception as exc:  # noqa: BLE001 - a broker failure is a gap the admin sees, never a crash of the read
        log.info("engine adapter: broker bars of %s %s failed: %s", symbol, timeframe, type(exc).__name__)
        return None, [f"{source}: {symbol} {timeframe} could not be loaded ({type(exc).__name__})"]
    if not bars:
        return None, [f"{source}: no {timeframe} bars of {symbol}"]
    return F.from_broker_bars(bars, timeframe, cal, source=source, own_volume=own_volume), []


async def store_bars(session: AsyncSession, store: StoreRead, symbol: str, exchange: str, start: datetime, end: datetime,
                     cal: MarketCalendar, *, own_volume: bool = True) -> tuple[F.SourceBars | None, list[str]]:
    """M71 W2-C: the 1-minute bars of [start, end] from the own store (rows AS STORED, `dh3=False`) plus only the newest
    missing ones through the reader's own session: (bars or None when the store holds nothing of the window, the
    store's gap lines in plain words). Only bars closed by `end`; START labels back to END labels; DH3 flags as marks."""
    from app.market_lake import own_bars
    from app.market_store import read_bars
    from app.market_store.reader import intraday_key
    try:
        got = await read_bars(session, symbol, "1m", start, end, user=store.user, purpose="engine", exchange=exchange,
                              rest=store.rest, now=end, dh3=False)
    except Exception as exc:  # noqa: BLE001 - a store failure is a gap the admin sees, never a crash of the read
        log.info("engine adapter: stored bars of %s failed: %s", symbol, type(exc).__name__)
        return None, [f"{OWN_LAKE}: {symbol} 1m could not be read ({type(exc).__name__})"]
    lines = [f"{OWN_LAKE}: {symbol} 1m: {line}" for line in got.gap_lines()]
    if not got.served:
        return None, lines
    rows = []
    for ts, bar in got.frame.iterrows():
        vol = float(bar.volume)
        rows.append((ts.to_pydatetime() + F.ONE_MINUTE, 1, float(bar.open), float(bar.high), float(bar.low), float(bar.close),
                     None if math.isnan(vol) else vol))
    key = intraday_key(symbol, exchange)
    tenant_id = int(getattr(store.user, "tenant_id", 0) or 0)
    marks = await own_bars.flags(session, tenant_id, key, "1m", _utc(start), _utc(end)) if key and tenant_id > 0 else {}
    post = [t for t, kinds in marks.items() if DH3_POST_CLOSE in kinds]
    source = STORE if got.topped_up_bars else OWN_LAKE
    return F.from_lake_rows(rows, "1m", cal, source=source, own_volume=own_volume, post_close=post), lines


async def _minute_bars(session: AsyncSession, store: StoreRead | None, broker: Any, symbol: str, exchange: str, start: datetime,
                       end: datetime, cal: MarketCalendar, *, own_volume: bool = True) -> tuple[F.SourceBars | None, list[str]]:
    """The 1-minute bars of `symbol`: through the own store when `store` is set (flag ON), else - or when the store holds
    nothing of the window - from the broker as before."""
    if store is not None:
        got, lines = await store_bars(session, store, symbol, exchange, start, end, cal, own_volume=own_volume)
        if got is not None or broker is None:
            return got, lines
    if broker is None:
        return None, [f"{BROKER}: not connected, so no 1m bars of {symbol}"]
    return await broker_bars(broker, symbol, exchange, "1m", start, end, cal, own_volume=own_volume)


async def futures_contracts(session: AsyncSession, broker: Any, index_symbol: str, exchange: str, start: datetime, end: datetime,
                            cal: MarketCalendar, *, store: StoreRead | None = None) -> tuple[bool, tuple[F.FuturesContract, ...], list[str]]:
    """(the index has listed futures, the near-month contract's live 1-minute bars from the user's broker, notes).
    Only the contract that is the near month at `end` (brokers do not serve expired contracts); its near-month stretch
    is not known before its expiry month, so earlier days are gaps (frames.FuturesContract.valid_from)."""
    from app.market_data import index_volume
    if index_volume.index_underlying(index_symbol, exchange) is None:
        return False, (), []
    near = await index_volume.near_future(session, index_symbol, exchange, _utc(end).astimezone(F.IST).date())
    if near is None:
        return True, (), [f"no near-month future of {index_symbol} in the instrument list"]
    if broker is None and store is None:
        return True, (), [f"{BROKER}: not connected, so no futures volume of {near.symbol}"]
    if store is None:
        got, notes = await broker_bars(broker, near.symbol, near.exchange, "1m", start, end, cal)
    else:
        got, notes = await _minute_bars(session, store, broker, near.symbol, near.exchange, start, end, cal)
    if got is None:
        return True, (), notes
    return True, (F.FuturesContract(symbol=near.symbol, expiry=near.expiry, bars=got.bars),), notes


async def collect(session: AsyncSession, *, key: str, exchange: str, timeframes: Sequence[str], starts: Mapping[str, datetime],
                  now: datetime, cal: MarketCalendar, scope: Any = None, broker: Any = None, broker_symbol: str | None = None,
                  lake_timeframes: Sequence[str] = LAKE_TIMEFRAMES, store: StoreRead | None = None,
                  required: Mapping[str, datetime] | None = None) -> tuple[F.SymbolData, list[str]]:
    """Everything ATP has for one symbol: the user's broker 1-minute bars (1m / 5m / ... timeframes), the stored
    history's 1-minute bars for `lake_timeframes` (15m / 1h; the broker's when the store has none), the Daily bars
    (stored history first, else the broker's), the DH3 marks and, for an index, the near-month future's live bars.
    `store` (M71 W2-C, flag `market_store_reads`): the broker's 1-minute bars and the future's come through the own
    store first (`store_bars`); None = exactly the broker path.
    required (M79, runner setting `history_days`): {timeframe: the start of the window whose gaps block}, for a frame
    whose `starts` reaches back into the stored history. The broker is never asked for that history (only from the
    required start); a stored-history timeframe reads its bars before today's session open once a day
    (`history_bars`, rebuilt to the timeframe) and the 1-minute bars from that open."""
    notes: list[str] = []
    tried: list[str] = []
    own_volume = not F.is_index_key(key)
    intraday = [tf for tf in timeframes if tf in F.MINUTES]
    need = dict(required or {})
    first = min((max(starts[tf], need.get(tf, starts[tf])) for tf in intraday), default=now)
    hist_tfs = [tf for tf in intraday if tf in lake_timeframes and tf in need and need[tf] > starts[tf]]
    cut = last_open(cal, now) if hist_tfs else None
    if cut is None or any(starts[tf] >= cut for tf in hist_tfs):
        hist_tfs, cut = [], None
    lake_first = min(([cut] if cut is not None else []) + [starts[tf] for tf in intraday if tf in lake_timeframes and tf not in hist_tfs],
                     default=None)
    minutes = None
    if store is not None and broker_symbol and intraday:
        tried.append(STORE_TRIED)
        if broker is not None:
            tried.append(BROKER)
        minutes, more = await _minute_bars(session, store, broker, broker_symbol, exchange, first, now, cal, own_volume=own_volume)
        notes += more
    elif broker is not None and broker_symbol and intraday:
        tried.append(BROKER)
        minutes, more = await broker_bars(broker, broker_symbol, exchange, "1m", first, now, cal, own_volume=own_volume)
        notes += more
    by_tf: dict[str, F.SourceBars] = {}
    history: dict[str, F.HistoryBars] = {}
    if lake_first is not None:
        lake_min, _, more = await lake_bars(session, key, "1m", lake_first, now, cal, scope=scope)
        tried.append("stored history (1-minute)")
        notes += more
        if lake_min is not None:
            by_tf = {tf: lake_min for tf in intraday if tf in lake_timeframes}
            if minutes is None:
                minutes = lake_min
    if cut is not None:
        for tf in hist_tfs:
            got, more = await history_bars(session, key, tf, starts[tf], cut, cal, scope=scope)
            notes += more
            if got is not None:
                history[tf] = got
    daily, extended = None, frozenset[date]()
    if F.DAILY in timeframes:
        tried.append("stored history (Daily)")
        daily, extended, more = await lake_bars(session, key, F.DAILY, starts[F.DAILY], now, cal, scope=scope)
        notes += more
        if daily is None and broker is not None and broker_symbol:
            daily, more = await broker_bars(broker, broker_symbol, exchange, F.DAILY, max(starts[F.DAILY], need.get(F.DAILY, starts[F.DAILY])),
                                            now, cal, own_volume=own_volume)
            notes += more
    expected, contracts = False, tuple[F.FuturesContract, ...]()
    if not own_volume and broker_symbol:
        expected, contracts, more = await futures_contracts(session, broker, broker_symbol, exchange, first, now, cal, store=store)
        notes += more
    data = F.SymbolData(symbol=key, exchange=exchange, minutes=minutes, minutes_by_timeframe=by_tf, daily=daily,
                        extended_days=extended, futures=contracts, futures_expected=expected, sources_tried=tuple(tried),
                        history=history)
    return data, notes


# --- the stored history, read once a day (M79) ------------------------------------------------------------------------

def last_open(cal: MarketCalendar, now: datetime) -> datetime | None:
    """The open of the newest session that opened by `now` (the stored history's daily cut); None when the calendar
    has none in the look-back."""
    day = now.astimezone(F.IST).date()
    for _ in range(31):                     # a month back at most (operational look-back)
        if F.is_session_day(cal, day) and F.session_open(cal, day) <= now:
            return F.session_open(cal, day)
        day -= timedelta(days=1)
    return None


HISTORY_CACHE_SIZE = 32                     # (symbol, timeframe, scope, start, cut) entries kept; only today's cut
_HISTORY: dict[tuple[Any, ...], tuple[F.HistoryBars | None, list[str]]] = {}


async def history_bars(session: AsyncSession, key: str, timeframe: str, start: datetime, cut: datetime, cal: MarketCalendar, *,
                       scope: Any = None) -> tuple[F.HistoryBars | None, list[str]]:
    """The stored 1-minute history of `key` in [start, cut) rebuilt to `timeframe` (`frames.history_of`), kept in this
    process until the cut moves: closed days do not change, so the long history is read once a day, not every cycle
    (as Trade's live feed keeps its prior-day history). None when the store holds nothing of it."""
    ident = (key, timeframe, scope, _utc(start), _utc(cut))
    if ident in _HISTORY:
        return _HISTORY[ident]
    for old in [k for k in _HISTORY if k[4] != ident[4]]:
        _HISTORY.pop(old, None)
    raw, _, notes = await lake_bars(session, key, "1m", start, cut, cal, scope=scope)
    got = None if raw is None or not raw.bars else F.history_of(raw, timeframe, cut, cal)
    while len(_HISTORY) >= HISTORY_CACHE_SIZE:
        _HISTORY.pop(next(iter(_HISTORY)))
    _HISTORY[ident] = (got, list(notes))
    return got, list(notes)


@dataclass(frozen=True)
class ProbeItem:
    """One symbol to probe: ATP's lake key, its exchange and the broker's name of it (e.g. "NIFTY 50")."""
    key: str
    exchange: str
    broker_symbol: str | None = None


CALENDAR_NOTE = ("The market calendar holds holidays only; early closes are not recorded, so a day that closed early "
                 "shows its later bars as missing.")


async def probe(session: AsyncSession, items: Sequence[ProbeItem], *, timeframes: Sequence[str], signal_timeframe: str,
                now: datetime, starts: Mapping[str, datetime], broker: Any = None, user: Any = None) -> dict[str, Any]:
    """The admin's gap list: per symbol and timeframe every gap (missing source, missing bars, incomplete bars,
    missing futures volume) in plain words, plus the source notes. Reads only."""
    from app.market_lake import own_bars
    first = min(starts.values()).astimezone(F.IST).date()
    gaps: list[F.DataGap] = []
    notes: dict[str, list[str]] = {}
    for item in items:
        cal = await load_calendar(session, item.exchange, first, now.astimezone(F.IST).date())
        scope = await own_bars.scope_for(session, user, item.key) if user is not None else None
        data, more = await collect(session, key=item.key, exchange=item.exchange, timeframes=timeframes, starts=starts, now=now,
                                   cal=cal, scope=scope, broker=broker, broker_symbol=item.broker_symbol)
        built = F.build_request(data, timeframes, signal_timeframe, now, starts, cal)
        gaps += built.gaps
        if more:
            notes[item.key] = more
    return {"gaps": F.gap_report(gaps), "problems": list(F.problem_tokens(gaps)), "notes": notes, "calendar": CALENDAR_NOTE,
            "checked": [i.key for i in items]}


__all__ = ["LAKE_TIMEFRAMES", "ProbeItem", "CALENDAR_NOTE", "StoreRead", "load_calendar", "lake_bars", "broker_bars", "store_bars",
           "history_bars", "last_open",
           "futures_contracts", "collect", "probe"]
