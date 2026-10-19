"""N2-4: what the decision tools read from the shared daily lake (HB1 NSE bhavcopy history) and the NSE classification.
Reference data only: the same for every user, nothing per tenant, nothing from a broker session (so no BT1b entitlement
check is involved).

- `lake_key`: an index underlying ('NIFTY 50', 'NIFTY', 'BANKNIFTY') -> `NSE_INDEX:<catalogue code>`; anything else ->
  `NSE:<SYMBOL>`.
- `daily_closes`: {trade date: close} over the last `days` calendar days. A share with a known ISIN reads the
  split-adjusted company history (`by_isin.isin_candles`); otherwise the raw bars under the key.
- `sectors`: the NSE sector of each share (the open classification row of its ISIN); indices read "Index".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClassificationRecord, SecurityRecord
from app.market_data.calendar import IST
from app.market_lake.asof import candles_as_of

TIMEFRAME = "day"
INDEX_SECTOR = "Index"
UNCLASSIFIED = "Unclassified"
MARKET = "NIFTY 50"


def index_name(name: str) -> Optional[str]:
    """The index display name ('NIFTY 50') for an index underlying in either spelling, else None."""
    from app.instruments.master import INDEX_SYMBOLS
    upper = " ".join(name.strip().upper().split())
    if upper in INDEX_SYMBOLS:
        return INDEX_SYMBOLS[upper]
    return upper if upper in set(INDEX_SYMBOLS.values()) else None


def lake_key(name: str) -> str:
    from app.universe.fo_eod import code_for
    index = index_name(name)
    return f"NSE_INDEX:{code_for(index)}" if index else f"NSE:{name.strip().upper()}"


def _day(ts: datetime) -> date:
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(IST).date()


async def _isin(session: AsyncSession, symbol: str) -> Optional[str]:
    return await session.scalar(select(SecurityRecord.isin).where(SecurityRecord.symbol == symbol.strip().upper()).limit(1))


async def daily_closes(session: AsyncSession, name: str, *, days: int, today: Optional[date] = None) -> Dict[date, float]:
    today = today or datetime.now(timezone.utc).astimezone(IST).date()
    start = today - timedelta(days=days)
    if index_name(name) is None:
        isin = await _isin(session, name)
        if isin:
            from app.market_lake.by_isin import isin_candles
            bars = await isin_candles(session, isin, start, today)
            if bars:
                return {_day(b.ts): float(b.close) for b in bars}
    lo = datetime.combine(start, datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    hi = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    rows = await candles_as_of(session, lake_key(name), TIMEFRAME, lo, hi)
    return {_day(r.ts): float(r.close) for r in rows}


async def sectors(session: AsyncSession, names: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    shares = []
    for name in names:
        if index_name(name):
            out[name] = INDEX_SECTOR
        else:
            shares.append(name)
    if shares:
        isins = dict((await session.execute(select(SecurityRecord.symbol, SecurityRecord.isin).where(
            SecurityRecord.symbol.in_([s.upper() for s in shares])))).all())
        rows = await session.execute(select(ClassificationRecord.isin, ClassificationRecord.sector).where(
            ClassificationRecord.isin.in_(list(isins.values()) or ["-"]), ClassificationRecord.scheme == "NSE",
            ClassificationRecord.valid_to.is_(None)))
        by_isin = {isin: sector for isin, sector in rows if sector}
        for name in shares:
            out[name] = by_isin.get(isins.get(name.upper(), ""), UNCLASSIFIED)
    return out
