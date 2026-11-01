"""Owner order 23 Oct: backtests without a daily broker login - one place that decides which bars a backtest runs on.

Order of sources (`resolve`):
1. The organisation's own broker session when it is usable (the Upstox Analytics Token counts): exactly the bars every
   backtest path read before (`evidence.server_frame`), so a run with a broker session is unchanged.
2. Otherwise, with the flag `backtest_stored_history` on, the SHARED market lake: Daily bars only (and Weekly, built
   from them here), from the licensed end-of-day sources `BACKTEST_STORED_SOURCES` allows - by default the NSE daily
   files (`nse_bhav:*`, HB1) only (RDY-6); the R2 daily import (`r2:*`, HB0) once its licence position allows it
   (OPEN_QUESTIONS BSH-2). Nothing else is read: never the owner's private 1-minute store (`md_tenant_candles`), never
   intraday bars in the shared lake (recorded from other organisations' broker feeds), never daily bars another
   organisation's broker wrote.
3. Otherwise a plain refusal (409) that says what is missing. Never sample data.

M55 T4 (owner order): 15M / 30M / 1H of a backfilled index (`index_history`'s keys) can also run on ATP's stored index
history - the history owner's 1-minute rows, built into the timeframe on read (`own_bars.scope_for`: the reader's own
rows, else the shared index scope, ONLY while the bring-your-own-broker data tier is off). The broker stays first for
every window it serves; the backtest bars route's window beyond the broker's 30 days (`stored_beyond_broker`), or no
usable broker session with the flag above on, reads the stored history for the whole window (one source per run, never
mixed). With the data tier on nothing is shared: the broker's 30
days, and the answer says so in one line.

Parity: lake bars are turned into the very frame a broker's Daily candles give - the same `OHLCVBar` ->
`bars_to_dataframe` path, each bar stamped like the broker stamps a Daily candle (the trade date at 00:00 India time) -
so equal bars give an identical frame and an identical backtest result (tests/test_backtest_bar_source.py).
The window mirrors the broker's Daily window (`MarketDataService._fetch`): from `lookback` calendar days ago (at least
`DAILY_MIN_LOOKBACK_DAYS`) to yesterday; today's bar is never read. Prices are adjusted for splits and bonuses on record
(equity only, `market_lake.adjust`), the way a broker's chart history is.

No look-ahead: a Daily bar enters the frame only once its file was stored (after that day's close), and the engine
reads each bar as closed, exactly as with the broker's Daily candles.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.models import OHLCVBar, bars_to_dataframe
from app.market_data.calendar import IST

FLAG = "backtest_stored_history"
LAKE_SOURCE = "lake:daily"                     # starts with "lake": server evidence (evidence.is_server)
LAKE_TIMEFRAME = "day"
LICENSED_PREFIXES = ("nse_bhav:", "r2:")      # every licensed Daily source, in preference order (the exchange's files first)
DAILY = frozenset({"day", "1d", "1day", "d", "daily"})
WEEKLY = frozenset({"week", "1w", "weekly"})
DAILY_MIN_LOOKBACK_DAYS = 400                  # the broker path's Daily minimum (market_data.service)
STORED_WORDS = "ATP's stored end-of-day history (NSE's daily files)"      # read after "Prices from"
INTRADAY_NEEDS_BROKER = ("Intraday backtests need a connected broker. Connect it on the Broker page, or test the "
                         "strategy on the Daily chart.")
STORED_ONLY_NSE = "Stored history covers NSE stocks and indices only; connect your broker for {exchange}."


def not_stored(symbol: str) -> str:
    return f"Daily history for {symbol.strip().upper()} is not stored yet."


def broker_words(source: str) -> str:
    name = source.split(":", 1)[1] if ":" in source else source
    name = name.split(":", 1)[0]
    return f"your broker ({name.replace('_', ' ').title()})" if name else "your broker"


def day_words(ts: Any) -> Optional[str]:
    """A bar's day in India time, as results print it ("2 Jan 2026")."""
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    d = t.tz_convert("Asia/Kolkata")
    return f"{d.day} {d.strftime('%b %Y')}"


@dataclass
class BacktestBars:
    frame: pd.DataFrame
    source: str          # broker:<name> | lake:daily - the label recorded on the run
    kind: str            # broker | stored
    words: str           # where the prices come from, in plain words read after "Prices from" (no dates)
    note: str | None = None    # M55 T4: one plain line, e.g. why longer stored history is not used

    def info(self) -> Dict[str, Any]:
        first = day_words(self.frame.index[0]) if len(self.frame) else None
        last = day_words(self.frame.index[-1]) if len(self.frame) else None
        line = f"Prices from {self.words}, {first} to {last}." if first and last else f"Prices from {self.words}."
        return {"source": self.source, "kind": self.kind, "words": self.words, "from_day": first, "to_day": last,
                "bars": int(len(self.frame)), "line": line, **({"note": self.note} if self.note else {})}


STORED_INTRADAY_SOURCE = "lake:index-intraday"         # starts with "lake": server evidence (evidence.is_server)
STORED_INTRADAY_WORDS = "ATP's stored index history"
BROKER_INTRADAY_DAYS = 30                               # the candles route's intraday cap (INTRADAY_MAX_LOOKBACK_DAYS)
_STORED_TF = {15: "15m", 30: "30m", 60: "1h"}


def stored_intraday_tf(timeframe: str) -> str | None:
    """The chart layer's timeframe ('15m' / '30m' / '1h') when stored index history can serve `timeframe`, else None."""
    tf = (timeframe or "").strip().lower()
    if tf in _STORED_TF.values():
        return tf
    from app.market_data.intervals import minutes_of
    minutes = minutes_of(tf) if tf.endswith("min") else (int(tf[:-1]) if tf.endswith("m") and tf[:-1].isdigit() else None)
    return _STORED_TF.get(minutes) if minutes else None


def stored_intraday_key(symbol: str, exchange: str) -> str | None:
    """The lake key of a backfilled index (NIFTY -> NSE_INDEX:NIFTY50), else None."""
    if (exchange or "NSE").strip().upper() not in ("NSE", "BSE"):
        return None
    from app.market_lake.index_history import is_target_key, lake_key_for
    try:
        key = lake_key_for(symbol)
    except Exception:  # noqa: BLE001 - not an index
        return None
    return key if is_target_key(key) else None


async def stored_intraday_frame(session: AsyncSession, tenant_id: int, user: Any, symbol: str, exchange: str, timeframe: str,
                                lookback_days: int, *, now: datetime | None = None) -> pd.DataFrame | None:
    """M55 T4: the stored index history as a broker-like frame (bar START stamps, closed bars only, by `now`), or None
    when it may not be read (not an index, the data tier on, nothing stored). Reads only through `own_bars`."""
    from app.charts import price_action_layer as pal
    from app.market_lake import own_bars
    tf, key = stored_intraday_tf(timeframe), stored_intraday_key(symbol, exchange)
    if tf is None or key is None:
        return None
    if user is not None and getattr(user, "tenant_id", None) == tenant_id:
        scope = await own_bars.scope_for(session, user, key)
    else:
        scope = await own_bars.shared_scope(session, tenant_id, key)
    if scope is None:
        return None
    end = now or datetime.now(UTC)
    days = max(1, min(int(lookback_days), config.LAKE_BACKFILL_INTRADAY_DAYS))
    rows = await own_bars.fetch_rows(session, scope, key, "1m", end - timedelta(days=days), end, limit=400 * (days + 1))
    if not rows:
        return None
    frame = pal._to_frame(rows, tf, key, end, True)
    bars = [OHLCVBar(timestamp=pd.Timestamp(r.timestamp).to_pydatetime(), open=float(r.open), high=float(r.high), low=float(r.low),
                     close=float(r.close), volume=0.0 if pd.isna(r.volume) else float(r.volume)) for r in frame.itertuples(index=False)]
    if not bars:
        return None
    df = bars_to_dataframe(bars)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def timeframe_kind(timeframe: str) -> Optional[str]:
    """'day' / 'week' when stored history can serve the timeframe, else None (intraday)."""
    tf = (timeframe or "").strip().lower()
    if tf in DAILY:
        return "day"
    if tf in WEEKLY:
        return "week"
    return None


def window(lookback_days: int, now: Optional[datetime] = None) -> tuple[date, date]:
    """The broker path's Daily window in India dates: [today - lookback, yesterday]."""
    now_ist = (now or datetime.now(timezone.utc)).astimezone(IST)
    lookback = max(int(lookback_days or 0), DAILY_MIN_LOOKBACK_DAYS)
    return (now_ist - timedelta(days=lookback)).date(), (now_ist - timedelta(days=1)).date()


def _bar_end(day: date) -> datetime:
    from app.market_lake.bhavcopy_backfill import bar_end
    return bar_end(day)


def to_frame(rows: List[Any]) -> pd.DataFrame:
    """Lake Daily bars (END-labelled, UTC) -> the frame a broker's Daily candles give: each bar stamped with its trade
    date at 00:00 India time, in the same ISO form the broker sends, then the shared `bars_to_dataframe`."""
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], tz="UTC", name="timestamp"))
    bars = []
    for r in rows:
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
        day = ts.astimezone(IST).date()
        bars.append(OHLCVBar.model_validate({"timestamp": f"{day.isoformat()}T00:00:00+05:30", "open": float(r.open), "high": float(r.high),
                                             "low": float(r.low), "close": float(r.close), "volume": float(r.volume or 0)}))
    df = bars_to_dataframe(bars)
    if len(df) and df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Daily -> Weekly by the India calendar week (Monday to Friday): open = the week's first, high = max, low = min,
    close = the last, volume = sum; stamped with the week's first trading day (like a broker's Weekly candle). Only
    days that exist are used - nothing is filled."""
    if df.empty:
        return df
    local = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index
    iso = local.isocalendar()
    keys = list(zip(iso.year.to_numpy(), iso.week.to_numpy()))
    groups: Dict[Any, List[int]] = {}
    for i, k in enumerate(keys):
        groups.setdefault(k, []).append(i)
    rows = []
    for k in sorted(groups, key=lambda g: groups[g][0]):
        part = df.iloc[groups[k]]
        rows.append((part.index[0], float(part["open"].iloc[0]), float(part["high"].max()), float(part["low"].min()),
                     float(part["close"].iloc[-1]), float(part["volume"].sum())))
    out = pd.DataFrame([r[1:] for r in rows], columns=["open", "high", "low", "close", "volume"],
                       index=pd.DatetimeIndex([r[0] for r in rows], name=df.index.name))
    return out


async def lake_sources(session: AsyncSession, instrument_key: str) -> List[str]:
    """The licensed Daily sources the shared lake holds for this instrument, exchange files first."""
    from app.db.models import MdCandleRecord
    found = await session.scalars(select(MdCandleRecord.source).distinct().where(
        MdCandleRecord.instrument_key == instrument_key, MdCandleRecord.timeframe == LAKE_TIMEFRAME))
    allowed = tuple(p for p in config.BACKTEST_STORED_SOURCES if p.startswith(LICENSED_PREFIXES))   # never beyond the licensed set
    keep = [s for s in found if s and allowed and s.startswith(allowed)]
    return sorted(keep, key=lambda s: (next(i for i, p in enumerate(LICENSED_PREFIXES) if s.startswith(p)), s))


async def lake_frame(session: AsyncSession, symbol: str, timeframe: str, lookback_days: int, *,
                     now: Optional[datetime] = None) -> pd.DataFrame:
    """The shared lake's Daily (or Weekly, built from Daily) frame for `symbol` over the broker path's window; empty
    when nothing is stored. Reads `md_candles` only - never a tenant's private store."""
    from app.ai.tools.copilot import lake_key
    from app.market_lake import adjust
    kind = timeframe_kind(timeframe)
    if kind is None:
        raise ValueError(f"stored history serves Daily and Weekly bars, not {timeframe!r}")
    key = lake_key(symbol)
    sources = await lake_sources(session, key)
    if not sources:
        return to_frame([])
    first, last = window(lookback_days, now)
    sym = key.split(":", 1)[-1]
    rows = await adjust.candles(session, key, LAKE_TIMEFRAME, _bar_end(first - timedelta(days=1)), _bar_end(last), symbol=sym,
                                exchange="NSE", kind="INDEX" if key.startswith("NSE_INDEX:") else "EQ", adjusted=True,
                                sources=sources)
    df = to_frame(rows)
    return weekly(df) if kind == "week" else df


STORE_TOPUP_WORDS = " and your broker for the newest bars"


async def from_store(session: AsyncSession, tenant_id: int, symbol: str, exchange: str, timeframe: str, lookback_days: int, *,
                     user: Any = None, broker: Optional[str] = None, now: Optional[datetime] = None) -> BacktestBars | None:
    """M71 PL-3 (flag `market_store_reads`): the backtest's bars from the own store (app/market_store.read_bars), or None
    when the store holds none of the window (the caller then keeps its order of sources).
    - Daily / Weekly on NSE: the NSE daily files over the broker path's window (`window`), zero broker calls.
    - Intraday: the reader's own stored one-minute bars built into the timeframe, plus only the newest bars through
      his own session (M71Q-2; never another member's bars or session)."""
    from app.market_store import read_bars
    from app.market_store.models import UnknownInterval, canonical
    from app.market_store.sources import own_rest
    end = now or datetime.now(UTC)
    kind = timeframe_kind(timeframe)
    if kind is not None:
        if (exchange or "NSE").strip().upper() != "NSE":
            return None
        first, _last = window(lookback_days, now)
        start = datetime.combine(first, datetime.min.time(), IST).astimezone(UTC)
        got = await read_bars(session, symbol, "1d" if kind == "day" else "1w", start, end, purpose="backtest", exchange="NSE", now=now)
        return BacktestBars(got.frame, LAKE_SOURCE, "stored", STORED_WORDS) if got.served else None
    try:
        name = canonical(timeframe)
    except UnknownInterval:
        return None
    if user is None or getattr(user, "tenant_id", None) != tenant_id:
        return None
    rest = await own_rest(session, user, symbol.strip().upper(), (exchange or "NSE").strip().upper(), broker=broker)
    got = await read_bars(session, symbol, name, end - timedelta(days=max(1, int(lookback_days))), end, user=user, purpose="backtest",
                          exchange=(exchange or "NSE").strip().upper(), rest=rest, now=now)
    if not got.served:
        return None
    topped = "+broker:" in got.source
    return BacktestBars(got.frame, got.source, "stored", STORED_INTRADAY_WORDS + (STORE_TOPUP_WORDS if topped else ""),
                        " ".join(got.gap_lines()) or None)


async def resolve(session: AsyncSession, tenant_id: int, symbol: str, exchange: str, timeframe: str, *, lookback_days: int = 30,
                  user: Any = None, broker: Optional[str] = None, now: Optional[datetime] = None,
                  broker_fetch: Callable[[], Awaitable[tuple[pd.DataFrame, str]]] | None = None,
                  stored_beyond_broker: bool = False) -> BacktestBars:
    """The bars a backtest runs on: the broker session when usable, else stored Daily history (flag on), else a plain
    409 saying what is missing. `broker_fetch`: the caller's own broker read (the Copilot tools' `_frame`), so a path with a
    broker session reads exactly what it read before; default `evidence.server_frame`. `stored_beyond_broker` (M55 T4,
    the backtest bars route, whose intraday window was capped at the broker's 30 days): a longer 15M / 30M / 1H window of
    a backfilled index reads the stored index history (else the broker's 30 days, with the reason when the data tier
    is on); every other caller keeps its broker read exactly as before."""
    from app.ai import evidence
    from app.market_store.models import FLAG as STORE_FLAG
    from app.platform.controls import flag_enabled
    if session is not None and await flag_enabled(session, STORE_FLAG, tenant_id):
        stored_first = await from_store(session, tenant_id, symbol, exchange, timeframe, lookback_days, user=user, broker=broker, now=now)
        if stored_first is not None:
            return stored_first                    # M71 PL-3: the own store first; nothing stored -> the order below
    note: str | None = None
    if stored_beyond_broker and stored_intraday_tf(timeframe) and lookback_days > BROKER_INTRADAY_DAYS and stored_intraday_key(symbol, exchange):
        # M55 T4: beyond the broker's 30 days - the stored index history for the whole window, when it may be read
        stored = await stored_intraday_frame(session, tenant_id, user, symbol, exchange, timeframe, lookback_days, now=now)
        if stored is not None:
            return BacktestBars(stored, STORED_INTRADAY_SOURCE, "stored", STORED_INTRADAY_WORDS)
        from app.market_lake import own_bars
        if await own_bars.shared_off(session, tenant_id):
            note = own_bars.SHARED_OFF_LINE
        lookback_days = BROKER_INTRADAY_DAYS
    try:
        if broker_fetch is not None:
            df, source = await broker_fetch()
        else:
            # M71Q-2: a reader's backtest reads through his own credential only, never a teammate's
            df, source = await evidence.server_frame(session, tenant_id, symbol, exchange, timeframe, broker=broker,
                                                     lookback_days=lookback_days, user=user, own_only=user is not None)
        return BacktestBars(df, source, "broker", broker_words(source), note)
    except HTTPException as exc:
        broker_error = exc
    if not await flag_enabled(session, FLAG, tenant_id):
        raise broker_error                         # flag off: exactly the answer every path gave before
    if timeframe_kind(timeframe) is None:
        if broker_error.status_code != 409:
            raise broker_error                     # a session exists but its read failed: say what the broker said
        stored = await stored_intraday_frame(session, tenant_id, user, symbol, exchange, timeframe, lookback_days, now=now)
        if stored is not None:                     # M55 T4: a backfilled index's stored intraday history
            return BacktestBars(stored, STORED_INTRADAY_SOURCE, "stored", STORED_INTRADAY_WORDS)
        raise HTTPException(status_code=409, detail=INTRADAY_NEEDS_BROKER)
    if (exchange or "NSE").strip().upper() != "NSE":
        raise HTTPException(status_code=409, detail=STORED_ONLY_NSE.format(exchange=exchange.strip().upper()))
    df = await lake_frame(session, symbol, timeframe, lookback_days, now=now)
    if df.empty:
        raise HTTPException(status_code=409, detail=not_stored(symbol))
    return BacktestBars(df, LAKE_SOURCE, "stored", STORED_WORDS)


def frame_bars(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """The frame as candles for the browser (the Backtesting page draws them and posts them back)."""
    return [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close),
             "volume": float(r.volume)} for ts, r in df.iterrows()]


__all__ = ["FLAG", "LAKE_SOURCE", "from_store", "STORED_INTRADAY_SOURCE", "stored_intraday_tf", "stored_intraday_key", "stored_intraday_frame", "STORED_WORDS", "INTRADAY_NEEDS_BROKER", "BacktestBars", "resolve", "lake_frame", "to_frame",
           "weekly", "window", "timeframe_kind", "not_stored", "broker_words", "frame_bars"]
