"""N1 F1: the inputs of the portfolio risk model (`risk.py`) for one user: their own open PAPER book, the bucket of
each underlying, its beta to NIFTY 50, and their capital basis.

BT1b: only the caller's own trades (`trades.user_id`) are read, valued at their own marks; no broker session is used,
so nothing of another user's data or session reaches the view. Betas and sectors come from the shared lake and the
U1-b classification (public market data).

Betas: daily closes known at yesterday's close (bar END <= today 09:15 IST) from the end-of-day sources only - the
company's adjusted history by ISIN (`by_isin.isin_candles`), else the symbol's own bars (unadjusted, said so), and
`NSE_INDEX:<code>` for the index - computed once per underlying and IST day, in memory (LRU).
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradeRecord, User
from app.market_data.calendar import IST
from app.portfolio import risk as R

FLAG = "portfolio_risk"
MODE = "PAPER"
BETA_INDEX = "NIFTY"
INDEX_KEYS = {"NIFTY": "NSE_INDEX:NIFTY50", "BANKNIFTY": "NSE_INDEX:NIFTYBANK", "FINNIFTY": "NSE_INDEX:NIFTYFINSERVICE",
              "MIDCPNIFTY": "NSE_INDEX:NIFTYMIDSELECT", "NIFTYNXT50": "NSE_INDEX:NIFTYNEXT50"}
BETA_WINDOW = 250                     # daily returns (about a year)
BETA_MIN_SESSIONS = 120
LOOKBACK_DAYS = 400                   # calendar days read for BETA_WINDOW sessions
MAX_CACHE = 512
ERROR_RETRY_SECONDS = 300             # a failed beta read is tried again after this (review L2), not the next day
DERIVATIVE = re.compile(r"\d.*(CE|PE|FUT)$")

_BETAS: "OrderedDict[Tuple[str, date], R.Beta]" = OrderedDict()
_BETA_FAILED: Dict[Tuple[str, date], float] = {}


def clear_cache() -> None:
    _BETAS.clear()
    _BETA_FAILED.clear()


async def resolve_underlying(session: AsyncSession, symbol: str, underlying: Optional[str] = None) -> Optional[str]:
    """The underlying a symbol's risk counts under (review L1): a cash or index symbol is its own (`underlying_of`); a
    derivative contract (`...CE`, `...PE`, `...FUT` with digits) is resolved through the instrument master, else to the
    index name it starts with; None when that cannot be told."""
    from app.db.models import InstrumentRecord
    from app.instruments.master import underlying_of
    from app.risk_engine.guardian import INDEX_UNDERLYINGS
    sym = (symbol or "").strip().upper()
    known = underlying_of(underlying or sym)
    if not DERIVATIVE.search(sym) or (underlying and not DERIVATIVE.search(underlying.strip().upper())):
        return known
    row = await session.scalar(select(InstrumentRecord.underlying).where(InstrumentRecord.tradingsymbol == sym,
                                                                         InstrumentRecord.underlying.is_not(None)).limit(1))
    if row:
        return underlying_of(row)
    for index in sorted(INDEX_UNDERLYINGS, key=len, reverse=True):
        if sym.startswith(index):
            return index
    return None


def _today(now: Optional[datetime]) -> date:
    now = now or datetime.now(timezone.utc)
    return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(IST).date()


async def open_book(session: AsyncSession, user: User) -> List[TradeRecord]:
    """The user's own open PAPER trades."""
    return list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == MODE,
        TradeRecord.exit_time.is_(None)).order_by(TradeRecord.id)))


async def bucket_of(session: AsyncSession, underlying: str, day: date) -> str:
    """INDEX for an index; else the NSE sector of the underlying's ISIN on `day`; else UNCLASSIFIED:<name>."""
    from app.risk_engine.guardian import INDEX_UNDERLYINGS
    if underlying in INDEX_UNDERLYINGS:
        return R.INDEX_BUCKET
    from app.universe.asof import isin_for
    from app.universe.indices import classification_on
    isin = await isin_for(session, underlying, day)
    cls = await classification_on(session, isin, day) if isin else None
    return R.bucket_label(underlying, (cls or {}).get("sector"), INDEX_UNDERLYINGS)


def _by_day(rows: Iterable[Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for r in rows:
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
        out[str(ts.astimezone(IST).date())] = float(r.close)
    return out


async def _index_closes(session: AsyncSession, key: str, start: datetime, end: datetime) -> Dict[str, float]:
    from app.market_lake.asof import candles_as_of
    from app.market_lake.bhavcopy_backfill import SOURCE, TIMEFRAME
    return _by_day(await candles_as_of(session, key, TIMEFRAME, start, end, sources=[SOURCE["index"]]))


async def _stock_closes(session: AsyncSession, underlying: str, day: date, start: datetime, end: datetime) -> Tuple[Dict[str, float], str]:
    from app.market_lake.asof import candles_as_of
    from app.market_lake.bhavcopy_backfill import SOURCE, TIMEFRAME
    from app.market_lake.by_isin import isin_candles
    from app.universe.asof import isin_for
    isin = await isin_for(session, underlying, day)
    if isin:
        bars = await isin_candles(session, isin, (start + timedelta(days=1)).date(), day - timedelta(days=1), adjusted=True)
        closes = _by_day(b for b in bars if b.ts <= end)
        if closes:
            return closes, "adjusted"
    return _by_day(await candles_as_of(session, f"NSE:{underlying}", TIMEFRAME, start, end, sources=[SOURCE["cm"]])), "unadjusted"


async def beta_of(session: AsyncSession, underlying: str, day: date) -> R.Beta:
    """Beta of `underlying` to NIFTY 50 known at the close before `day` (cached per IST day)."""
    hit = _BETAS.get((underlying, day))
    if hit is not None:
        _BETAS.move_to_end((underlying, day))
        return hit
    failed_at = _BETA_FAILED.get((underlying, day))
    if failed_at is not None and time.monotonic() - failed_at < ERROR_RETRY_SECONDS:
        return R.Beta(1.0, 0, False, "error (tried again in a few minutes)")
    if underlying == BETA_INDEX:
        beta = R.Beta(1.0, 0, True, "definition")
    else:
        end = datetime.combine(day, dtime(9, 15), IST).astimezone(timezone.utc)
        start = end - timedelta(days=LOOKBACK_DAYS)
        try:
            async with session.begin_nested():       # review M1: a failed read rolls back its savepoint only
                index = await _index_closes(session, INDEX_KEYS[BETA_INDEX], start, end)
                from app.risk_engine.guardian import INDEX_UNDERLYINGS
                if underlying in INDEX_UNDERLYINGS:
                    key = INDEX_KEYS.get(underlying)
                    own, how = (await _index_closes(session, key, start, end), "index") if key else ({}, "no NSE index series")
                else:
                    own, how = await _stock_closes(session, underlying, day, start, end)
                beta = R.beta_from_closes(own, index, BETA_WINDOW, BETA_MIN_SESSIONS)
                if beta.known:
                    beta = R.Beta(beta.value, beta.sessions, True, f"lake ({how})")
                elif not own:
                    beta = R.Beta(1.0, 0, False, how if how.startswith("no ") else "no Daily bars in the lake")
        except Exception as exc:  # noqa: BLE001 - unknown beta, never a failed view; not cached for the day (review L2)
            _BETA_FAILED[(underlying, day)] = time.monotonic()
            return R.Beta(1.0, 0, False, f"error: {type(exc).__name__}")
    _BETA_FAILED.pop((underlying, day), None)
    _BETAS[(underlying, day)] = beta
    while len(_BETAS) > MAX_CACHE:
        _BETAS.popitem(last=False)
    return beta


async def risk_view(session: AsyncSession, user: User, now: Optional[datetime] = None, *,
                    extra: Optional[List[R.Position]] = None) -> Dict[str, Any]:
    """The user's own risk view (`extra`: hypothetical positions added to the book, e.g. a proposed entry - F2)."""
    from app.risk_engine import by_default
    day = _today(now)
    positions = R.positions_of(await open_book(session, user)) + list(extra or [])
    for p in positions:                                  # review L1: a contract counts under its underlying
        p.underlying = await resolve_underlying(session, p.underlying, p.underlying) or p.underlying
    underlyings = sorted({p.underlying for p in positions})
    buckets = {u: await bucket_of(session, u, day) for u in underlyings}
    betas = {u: await beta_of(session, u, day) for u in underlyings}
    capital = await by_default.capital_basis(session, user, now)
    out = R.view(positions, buckets, betas, capital or 0.0)       # M68 PR-7: Not set -> no percentages (view leaves them out)
    if capital is None:
        out["capital"] = None
    out.update(as_of=str(day), mode=MODE, capital_source="PAPER account: starting capital + realised P&L before today")
    return out
