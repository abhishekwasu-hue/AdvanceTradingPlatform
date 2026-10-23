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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

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

    def info(self) -> Dict[str, Any]:
        first = day_words(self.frame.index[0]) if len(self.frame) else None
        last = day_words(self.frame.index[-1]) if len(self.frame) else None
        line = f"Prices from {self.words}, {first} to {last}." if first and last else f"Prices from {self.words}."
        return {"source": self.source, "kind": self.kind, "words": self.words, "from_day": first, "to_day": last,
                "bars": int(len(self.frame)), "line": line}


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


async def resolve(session: AsyncSession, tenant_id: int, symbol: str, exchange: str, timeframe: str, *, lookback_days: int = 30,
                  user: Any = None, broker: Optional[str] = None, now: Optional[datetime] = None,
                  broker_fetch: Optional[Callable[[], Awaitable[Tuple[pd.DataFrame, str]]]] = None) -> BacktestBars:
    """The bars a backtest runs on: the broker session when usable, else stored Daily history (flag on), else a plain
    409 saying what is missing. `broker_fetch`: the caller's own broker read (the Copilot tools' `_frame`), so a path with a
    broker session reads exactly what it read before; default `evidence.server_frame`."""
    from app.ai import evidence
    from app.platform.controls import flag_enabled
    try:
        if broker_fetch is not None:
            df, source = await broker_fetch()
        else:
            df, source = await evidence.server_frame(session, tenant_id, symbol, exchange, timeframe, broker=broker,
                                                     lookback_days=lookback_days, user=user)
        return BacktestBars(df, source, "broker", broker_words(source))
    except HTTPException as exc:
        broker_error = exc
    if not await flag_enabled(session, FLAG, tenant_id):
        raise broker_error                         # flag off: exactly the answer every path gave before
    if timeframe_kind(timeframe) is None:
        if broker_error.status_code != 409:
            raise broker_error                     # a session exists but its read failed: say what the broker said
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


__all__ = ["FLAG", "LAKE_SOURCE", "STORED_WORDS", "INTRADAY_NEEDS_BROKER", "BacktestBars", "resolve", "lake_frame", "to_frame",
           "weekly", "window", "timeframe_kind", "not_stored", "broker_words", "frame_bars"]
