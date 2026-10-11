"""HB1: reading the daily lake by company (ISIN) and by day - the survivorship-free views a screener or backtest needs.

- `isin_candles`: one company's daily history across symbol changes AND ISIN changes. The lake stores bars under the
  symbol traded that day (`NSE:<SYMBOL>`, exactly as the exchange printed them); `md_isin_symbols` holds one row per
  contiguous run of (ISIN, symbol). A company is the chain of runs reachable from its ISIN: its own runs, plus a run of
  ANOTHER ISIN under the same symbol that starts within LINK_DAYS after one of the chain's runs ends (or ends just before
  one starts) - a face-value split usually issues a new ISIN while the symbol continues on the next trading day. A
  symbol reused by a different company months later is not linked. Bars are taken from each symbol only inside its runs,
  and B4 adjusts the stitched series with only the actions whose ex-date falls inside a run of that symbol (or within
  LINK_DAYS after it ends) - another company's later split under a reused symbol never reaches this history.
- `universe_on`: every cash instrument that has a bar on a given day - delisted and suspended names included as of
  then, because the exchange's file of that day had them.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdCandleRecord, MdIsinSymbolRecord
from app.market_lake import adjust
from app.market_lake.asof import candles_as_of
from app.market_lake.bhavcopy_backfill import SOURCE, TIMEFRAME, bar_end
from app.market_lake.ingest import LakeBar

LINK_DAYS = 4          # the next trading day after a weekend and one holiday


@dataclass(frozen=True)
class Run:
    isin: str
    symbol: str
    first_date: date
    last_date: date


async def symbol_ranges(session: AsyncSession, isin: str, exchange: str = "NSE") -> List[MdIsinSymbolRecord]:
    """The ISIN's own runs, oldest first."""
    return sorted(await session.scalars(select(MdIsinSymbolRecord).where(
        MdIsinSymbolRecord.isin == isin.upper(), MdIsinSymbolRecord.exchange == exchange.upper())), key=lambda r: r.first_date)


def _touches(a: Run, b: Run) -> bool:
    """b continues a (or a continues b) under the same symbol within LINK_DAYS."""
    if a.symbol != b.symbol:
        return False
    return (timedelta(0) < b.first_date - a.last_date <= timedelta(days=LINK_DAYS)
            or timedelta(0) < a.first_date - b.last_date <= timedelta(days=LINK_DAYS))


async def company_runs(session: AsyncSession, isin: str, exchange: str = "NSE") -> List[Run]:
    """Every run of the company the ISIN belongs to (its own, plus ISIN changes the symbol carried across), oldest first."""
    exchange = exchange.upper()
    seen_isins: Set[str] = set()
    runs: Dict[Tuple[str, str, date], Run] = {}
    todo = [isin.upper()]
    while todo:
        current = todo.pop()
        if current in seen_isins:
            continue
        seen_isins.add(current)
        own = [Run(r.isin, r.symbol, r.first_date, r.last_date) for r in await symbol_ranges(session, current, exchange)]
        for run in own:
            runs[(run.isin, run.symbol, run.first_date)] = run
        symbols = sorted({r.symbol for r in own})
        if not symbols:
            continue
        others = await session.scalars(select(MdIsinSymbolRecord).where(
            MdIsinSymbolRecord.exchange == exchange, MdIsinSymbolRecord.symbol.in_(symbols), MdIsinSymbolRecord.isin != current))
        for rec in others:
            candidate = Run(rec.isin, rec.symbol, rec.first_date, rec.last_date)
            if any(_touches(run, candidate) for run in own):
                todo.append(rec.isin)
    return sorted(runs.values(), key=lambda r: (r.first_date, r.symbol))


async def isin_candles(session: AsyncSession, isin: str, start: date, end: date, *, adjusted: bool = True,
                       as_of: Optional[datetime] = None, exchange: str = "NSE") -> List[LakeBar]:
    runs = await company_runs(session, isin, exchange)
    bars: Dict[datetime, LakeBar] = {}
    for r in runs:
        lo, hi = max(start, r.first_date), min(end, r.last_date)
        if lo > hi:
            continue
        rows = await candles_as_of(session, f"{exchange.upper()}:{r.symbol}", TIMEFRAME, bar_end(lo) - timedelta(hours=1), bar_end(hi),
                                   as_of=as_of, sources=[SOURCE["cm"]])
        for row in rows:
            ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
            bars[ts] = LakeBar(row.instrument_key, row.timeframe, ts, float(row.open), float(row.high), float(row.low),
                               float(row.close), int(row.volume), row.oi)
    out = [bars[ts] for ts in sorted(bars)]
    if not adjusted:
        return out
    actions = []
    for symbol in sorted({r.symbol for r in runs}):
        windows = [(r.first_date, r.last_date + timedelta(days=LINK_DAYS)) for r in runs if r.symbol == symbol]
        actions += [a for a in await adjust.actions_as_of(session, symbol, exchange, as_of=as_of)
                    if any(lo <= a.ex_date <= hi for lo, hi in windows)]
    return adjust.adjust(out, sorted(actions, key=lambda a: a.ex_date))


async def universe_on(session: AsyncSession, day: date) -> List[str]:
    """Instrument keys of every cash instrument in the exchange's file of `day` (as loaded)."""
    return sorted(set(await session.scalars(select(MdCandleRecord.instrument_key).where(
        MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.source == SOURCE["cm"], MdCandleRecord.ts == bar_end(day)))))
