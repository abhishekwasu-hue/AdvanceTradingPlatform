"""Part B4: corporate-action adjustment - adjusted and unadjusted views of lake candles.

A split or bonus changes the share count on its ex-date; prices before it are not comparable with prices after. The
adjusted view multiplies every price before an ex-date by `ratio_old / ratio_new` (a 1:2 split: ratio_new 2, ratio_old 1
-> prices halve) and divides volume by the same factor (volume doubles); factors of several actions compound. Bars on
or after the ex-date are untouched, and so is the stored data - adjustment is a read-time view.
- Only actions known at `as_of` apply (B1), so a backtest replayed before an announcement sees unadjusted history.
- Derivatives are never adjusted (the exchange re-strikes contracts itself): `kind` other than equity -> unadjusted.
- Dividends are not adjusted unless LAKE_ADJUST_DIVIDENDS is on (price minus amount ratio before the ex-date).
"""
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import MdCorporateActionRecord
from app.market_data.calendar import IST
from app.market_lake.asof import candles_as_of
from app.market_lake.ingest import LakeBar

SHARE_ACTIONS = ("SPLIT", "BONUS")
EQUITY_KINDS = ("EQ", "EQUITY", "UNDERLYING")


async def actions_as_of(session: AsyncSession, symbol: str, exchange: str, *, as_of: Optional[datetime] = None) -> List[MdCorporateActionRecord]:
    """Latest visible version of each (ex_date, action)."""
    query = select(MdCorporateActionRecord).where(MdCorporateActionRecord.symbol == symbol.upper(),
                                                  MdCorporateActionRecord.exchange == exchange.upper())
    if as_of is not None:
        query = query.where(MdCorporateActionRecord.ingested_at <= as_of)
    latest = {}
    for row in await session.scalars(query):
        key = (row.ex_date, row.action)
        if key not in latest or row.version > latest[key].version:
            latest[key] = row
    return sorted(latest.values(), key=lambda r: r.ex_date)


def share_factor(actions: Sequence[MdCorporateActionRecord], day: date) -> float:
    """Price multiplier for a bar on `day`: the product of ratio_old/ratio_new over share actions with ex_date > day."""
    factor = 1.0
    for a in actions:
        if a.action in SHARE_ACTIONS and a.ex_date > day and a.ratio_new and a.ratio_old:
            factor *= float(a.ratio_old) / float(a.ratio_new)
    return factor


def dividend_factors(bars: Sequence[LakeBar], actions: Sequence[MdCorporateActionRecord]) -> List[tuple]:
    """(ex_date, factor) per dividend: 1 - amount / the last close before the ex-date (the cum-dividend close)."""
    out = []
    for a in actions:
        if a.action != "DIVIDEND" or not a.amount:
            continue
        before = [b for b in bars if b.ts.astimezone(IST).date() < a.ex_date]
        if before and before[-1].close > 0:
            out.append((a.ex_date, max(0.0, 1 - float(a.amount) / before[-1].close)))
    return out


def adjust(bars: Sequence[LakeBar], actions: Sequence[MdCorporateActionRecord], *, dividends: Optional[bool] = None) -> List[LakeBar]:
    use_div = config.LAKE_ADJUST_DIVIDENDS if dividends is None else dividends
    bars = sorted(bars, key=lambda b: b.ts)
    divs = dividend_factors(bars, actions) if use_div else []
    out: List[LakeBar] = []
    for bar in bars:
        day = bar.ts.astimezone(IST).date()
        shares = share_factor(actions, day)
        f = shares
        for ex_date, df in divs:
            if ex_date > day:
                f *= df
        if f == 1.0:
            out.append(bar)
            continue
        out.append(replace(bar, open=bar.open * f, high=bar.high * f, low=bar.low * f, close=bar.close * f,
                           volume=int(round(bar.volume / shares)) if shares else bar.volume))   # dividends leave volume alone
    return out


async def candles(session: AsyncSession, instrument_key: str, timeframe: str, start: datetime, end: datetime, *,
                  symbol: str, exchange: str, kind: str = "EQ", adjusted: bool = True, as_of: Optional[datetime] = None,
                  sources: Optional[Sequence[str]] = None) -> List[LakeBar]:
    """The lake's candles as of `as_of`, adjusted for splits/bonuses known then (equity only) when `adjusted`."""
    rows = await candles_as_of(session, instrument_key, timeframe, start, end, as_of=as_of, sources=sources)
    bars = [LakeBar(r.instrument_key, r.timeframe, r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc),
                    float(r.open), float(r.high), float(r.low), float(r.close), int(r.volume), r.oi) for r in rows]
    if not adjusted or kind.upper() not in EQUITY_KINDS:
        return bars
    return adjust(bars, await actions_as_of(session, symbol, exchange, as_of=as_of))
