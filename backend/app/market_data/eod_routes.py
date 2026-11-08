"""M86 D2: the end-of-day reads a user WITHOUT a connected broker sees - `GET /api/market-data/eod/*`.

Owner decision M86: live data for a user comes only from that user's own connected broker; the NSE bhavcopy (EOD) is
shown to everyone. These three reads serve ONLY the shared daily rows of the licensed exchange files
(`bar_source.lake_sources`: `nse_bhav:*`, plus `r2:*` only when BACKTEST_STORED_SOURCES allows it), so they are shared
by design and can never return an intraday bar or a row a broker session fetched:

* `/closes?symbols=` - each symbol's last stored close, the close before it and the date ("Last close <date>").
* `/bars?symbol=&timeframe=day|week` - Daily bars, and Weekly bars built from them (the backtest's `weekly`).
* `/chain?underlying=` - the last stored F&O bhavcopy chain (calls / puts close and OI per strike) of the nearest
  expiry on that day, when the options file was loaded; empty otherwise (the page then shows its connect line).

Read-only: no broker call, no write, no cache shared with any broker path. Nothing here places an order.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import MdCandleRecord, User
from app.db.session import get_session
from app.market_data.calendar import IST

router = APIRouter(prefix="/api/market-data/eod", tags=["market-data"])

eod_rate_limit = user_rate_limit("market_data_eod", limit=60, window_seconds=60)

MAX_SYMBOLS = 60
CLOSE_WINDOW_DAYS = 20                 # the two newest closes are looked for in this many days before the newest one
MAX_LOOKBACK_DAYS = 3650
FO_SOURCE = "nse_bhav:fo"
CHAIN_ROWS = 400


def _clean(symbol: str) -> str:
    return " ".join(symbol.strip().upper().split())


def _day(ts: datetime) -> date:
    ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return ts.astimezone(IST).date()


async def _key_sources(session: AsyncSession, symbol: str) -> tuple[str, list[str]]:
    from app.ai.tools.copilot import lake_key
    from app.backtest import bar_source
    key = lake_key(symbol)
    return key, await bar_source.lake_sources(session, key)


async def _daily(session: AsyncSession, symbol: str, start: datetime, end: datetime) -> list[Any]:
    """The licensed Daily bars of `symbol` in [start, end] (bar ENDs), adjusted for splits / bonuses on record."""
    from app.backtest import bar_source
    from app.market_lake import adjust
    key, sources = await _key_sources(session, symbol)
    if not sources:
        return []
    return await adjust.candles(session, key, bar_source.LAKE_TIMEFRAME, start, end, symbol=key.split(":", 1)[-1],
                                exchange="NSE", kind="INDEX" if key.startswith("NSE_INDEX:") else "EQ", adjusted=True,
                                sources=sources)


daily_bars = _daily     # M88: the journal's trade chart reads the same licensed Daily bars (shared by design, never a broker row)


async def last_close(session: AsyncSession, symbol: str) -> dict[str, Any]:
    """One symbol's last stored close (and the one before it) from the licensed daily files; `missing` when none."""
    from app.backtest import bar_source
    sym = _clean(symbol)
    out: dict[str, Any] = {"symbol": sym, "exchange": "NSE", "close": None, "prev_close": None, "change": None,
                           "change_pct": None, "date": None, "missing": True}
    key, sources = await _key_sources(session, sym)
    if not sources:
        return out
    newest = await session.scalar(select(func.max(MdCandleRecord.ts)).where(
        MdCandleRecord.instrument_key == key, MdCandleRecord.timeframe == bar_source.LAKE_TIMEFRAME,
        MdCandleRecord.source.in_(sources)))
    if newest is None:
        return out
    newest = newest if newest.tzinfo else newest.replace(tzinfo=UTC)
    bars = await _daily(session, sym, newest - timedelta(days=CLOSE_WINDOW_DAYS), newest)
    if not bars:
        return out
    last = bars[-1]
    prev = bars[-2].close if len(bars) > 1 else None
    change = round(last.close - prev, 4) if prev is not None else None
    pct = round(change / prev * 100, 4) if change is not None and prev else None
    out.update(close=last.close, prev_close=prev, change=change, change_pct=pct, date=_day(last.ts).isoformat(), missing=False)
    return out


@router.get("/closes", dependencies=[Depends(eod_rate_limit)])
async def closes(symbols: str = Query(min_length=1, max_length=2000, description="comma-separated NSE symbols or index names"),
                 user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    wanted = list(dict.fromkeys(s for s in (_clean(p) for p in symbols.split(",")) if s))
    if not wanted:
        raise HTTPException(status_code=422, detail="Name at least one symbol.")
    if len(wanted) > MAX_SYMBOLS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_SYMBOLS} symbols per request.")
    items = [await last_close(session, s) for s in wanted]
    dates = [i["date"] for i in items if i["date"]]
    return {"items": items, "as_of": max(dates) if dates else None, "source": "eod"}


@router.get("/bars", dependencies=[Depends(eod_rate_limit)])
async def bars(symbol: str = Query(min_length=1, max_length=40), timeframe: str = Query("day", max_length=8),
               lookback_days: int = Query(730, ge=1, le=MAX_LOOKBACK_DAYS),
               user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    from app.backtest import bar_source
    kind = bar_source.timeframe_kind(timeframe)
    if kind is None:
        raise HTTPException(status_code=422, detail="End-of-day bars are Daily or Weekly; intraday bars need your own broker.")
    sym = _clean(symbol)
    now = datetime.now(UTC)
    rows = await _daily(session, sym, now - timedelta(days=lookback_days), now)
    frame = bar_source.to_frame(rows)
    if kind == "week":
        frame = bar_source.weekly(frame)
    candles = bar_source.frame_bars(frame) if len(frame) else []
    return {"symbol": sym, "exchange": "NSE", "timeframe": kind, "candles": candles,
            "last_date": _day(rows[-1].ts).isoformat() if rows else None, "source": "eod"}


def _chain_key(underlying: str) -> str:
    return underlying.replace(" ", "")


@router.get("/chain", dependencies=[Depends(eod_rate_limit)])
async def chain(underlying: str = Query(min_length=1, max_length=40), expiry: date | None = None,
                user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The last stored option chain from the F&O bhavcopy (closes and OI); `rows` empty when no options file is stored."""
    from app.backtest import bar_source
    name = _clean(underlying)
    prefix = f"NFO:{_chain_key(name)}:"
    calls, puts = f"{prefix}CE:", f"{prefix}PE:"
    base = (MdCandleRecord.timeframe == bar_source.LAKE_TIMEFRAME, MdCandleRecord.source == FO_SOURCE)
    is_option = MdCandleRecord.instrument_key.startswith(calls) | MdCandleRecord.instrument_key.startswith(puts)
    out: dict[str, Any] = {"underlying": name, "date": None, "expiry": None, "expiries": [], "rows": [], "spot": None, "source": "eod"}
    newest = await session.scalar(select(func.max(MdCandleRecord.ts)).where(*base, is_option))
    if newest is None:
        return out
    found = (await session.execute(select(MdCandleRecord.instrument_key, MdCandleRecord.close, MdCandleRecord.oi, MdCandleRecord.version)
                                   .where(*base, is_option, MdCandleRecord.ts == newest))).all()
    latest: dict[str, tuple[float, int | None, int]] = {}
    for key, close, oi, version in found:
        if key not in latest or version > latest[key][2]:
            latest[key] = (float(close), oi, version)
    by_expiry: dict[str, dict[float, dict[str, Any]]] = {}
    for key, (close, oi, _version) in latest.items():
        parts = key.split(":")                       # NFO:<UL>:<CE|PE>:<expiry>:<strike>
        if len(parts) != 5:
            continue
        try:
            strike = float(parts[4])
        except ValueError:
            continue
        side = "call" if parts[2] == "CE" else "put"
        by_expiry.setdefault(parts[3], {}).setdefault(strike, {"strike": strike, "call": None, "put": None})[side] = {"close": close, "oi": oi}
    expiries = sorted(by_expiry)
    if not expiries:
        return out
    pick = expiry.isoformat() if expiry is not None and expiry.isoformat() in by_expiry else expiries[0]
    rows = [by_expiry[pick][k] for k in sorted(by_expiry[pick])][:CHAIN_ROWS]
    spot = await last_close(session, name)
    out.update(date=_day(newest).isoformat(), expiry=pick, expiries=expiries, rows=rows,
               spot=spot["close"] if spot["date"] == _day(newest).isoformat() else None)
    return out


__all__ = ["router", "last_close", "closes", "bars", "chain"]
