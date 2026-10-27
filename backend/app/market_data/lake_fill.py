"""DH2 / DH6: the last closed session's 1-minute bars from the reader's OWN market lake when the broker's history does not
have them yet.

Seen on production (27 Oct 2026, 03:55 IST): Upstox's 1-minute history ended at Friday 23 Oct 15:29 although Monday 26
Oct had closed (its Daily bar was there) and the intraday endpoint answers nothing before the next open - so every
1-minute chart, the chain page's own close and the Copilot read Friday's close. The owner's nightly pass
(app/market_lake/index_history.py, DH2) had already stored Monday's 1-minute bars from the intraday endpoint after the
close; this fills that gap from them.

Data terms (HB2 / TM-7, docs/DATA_SOURCES.md): only the reader's own private bars (`own_bars.scope_for` - his tenant and
member id, the holder rule) - never another member's or organisation's. A filled result is never written to a cache
another reader could be served from (MarketDataService.get_candles). Closing-auction flags (DH3) are applied by
`own_bars.fetch_rows`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import OHLCVBar

logger = logging.getLogger(__name__)

LakeFill = Callable[[str, str, datetime, datetime], Awaitable[List[OHLCVBar]]]
MAX_BARS = 3000                  # one session is 375 1-minute bars; a gap of a few sessions at most
MINUTE = timedelta(minutes=1)


def for_reader(session: AsyncSession, user: Any) -> LakeFill:
    """A fill function bound to this request's database session and reader (his own private bars only)."""

    async def fill(symbol: str, exchange: str, start: datetime, end: datetime) -> List[OHLCVBar]:
        from app.market_lake import own_bars
        from app.market_lake.index_history import lake_key_for
        try:
            key = lake_key_for(symbol)
            scope = await own_bars.scope_for(session, user, key)
            if scope is None:
                return []
            # The lake stores a bar by its END; the chart by its start: (start, end] of starts = (start + 1m, end + 1m] of ends.
            rows = await own_bars.fetch_rows(session, scope, key, "1m", start + MINUTE, end + MINUTE, limit=MAX_BARS)
        except Exception:  # noqa: BLE001 - the fill is a bonus: the broker's bars are shown as they are without it
            logger.exception("Last-session fill from the own lake failed for %s:%s", exchange, symbol)
            return []
        return [OHLCVBar(timestamp=_utc(r[0]) - MINUTE, open=r[2], high=r[3], low=r[4], close=r[5], volume=float(r[6] or 0))
                for r in rows]

    return fill


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
