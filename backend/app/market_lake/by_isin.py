"""HB1: reading the daily lake by company (ISIN) and by day - the survivorship-free views a screener or backtest needs.

- `isin_candles`: one company's daily history across symbol changes. The lake stores bars under the symbol traded that
  day (`NSE:<SYMBOL>`, exactly as the exchange printed them); `md_isin_symbols` says which symbol an ISIN traded under
  and when, so the bars are taken from each symbol only within that ISIN's own date range (a symbol later reused by
  another company does not leak in), then adjusted (B4) with the actions of every symbol the company used.
- `universe_on`: every cash instrument that has a bar on a given day - delisted and suspended names included as of
  then, because the exchange's file of that day had them.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCandleRecord, MdIsinSymbolRecord
from app.market_lake import adjust
from app.market_lake.asof import candles_as_of
from app.market_lake.bhavcopy_backfill import SOURCE, TIMEFRAME, bar_end
from app.market_lake.ingest import LakeBar


async def symbol_ranges(session: AsyncSession, isin: str, exchange: str = "NSE") -> List[MdIsinSymbolRecord]:
    return sorted(await session.scalars(select(MdIsinSymbolRecord).where(
        MdIsinSymbolRecord.isin == isin.upper(), MdIsinSymbolRecord.exchange == exchange.upper())), key=lambda r: r.first_date)


async def isin_candles(session: AsyncSession, isin: str, start: date, end: date, *, adjusted: bool = True,
                       as_of: Optional[datetime] = None, exchange: str = "NSE") -> List[LakeBar]:
    ranges = await symbol_ranges(session, isin, exchange)
    bars: Dict[datetime, LakeBar] = {}
    actions = []
    for r in ranges:
        lo, hi = max(start, r.first_date), min(end, r.last_date)
        if lo > hi:
            continue
        rows = await candles_as_of(session, f"{exchange.upper()}:{r.symbol}", TIMEFRAME, bar_end(lo) - timedelta(hours=1), bar_end(hi),
                                   as_of=as_of, sources=[SOURCE["cm"]])
        for row in rows:
            ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
            bars[ts] = LakeBar(row.instrument_key, row.timeframe, ts, float(row.open), float(row.high), float(row.low),
                               float(row.close), int(row.volume), row.oi)
        if adjusted:
            actions += await adjust.actions_as_of(session, r.symbol, exchange, as_of=as_of)
    out = [bars[ts] for ts in sorted(bars)]
    return adjust.adjust(out, sorted(actions, key=lambda a: a.ex_date)) if adjusted else out


async def universe_on(session: AsyncSession, day: date) -> List[str]:
    """Instrument keys of every cash instrument in the exchange's file of `day` (as loaded)."""
    return sorted(set(await session.scalars(select(MdCandleRecord.instrument_key).where(
        MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.source == SOURCE["cm"], MdCandleRecord.ts == bar_end(day)))))
