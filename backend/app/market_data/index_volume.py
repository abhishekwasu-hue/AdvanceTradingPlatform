"""UI2-4a2: an index chart's volume from its near-month future.

An index has no traded volume of its own (brokers send 0), so a chart of NIFTY 50 or NIFTY BANK shows the volume of the
index's near-month future instead, and says so (`volume_source`).

- **Which future.** `near_future`: the index symbol's F&O underlying (`master.underlying_of`), then the first FUT expiry
  on or after the day in the platform's instrument master (`master.expiries`), then that contract (`master.find_future`)
  - the near month, where the expiry day still counts as the expiring contract's (the roll rule of the futures basis).
  Its tradingsymbol is the master's (Upstox) spelling; the adapter's contract-symbol wrap sends it in the broker's own.
- **Which indices.** Every index in `master.INDEX_SYMBOLS` on its own exchange: NIFTY 50, NIFTY BANK, NIFTY FIN SERVICE,
  NIFTY MID SELECT, NIFTY NEXT 50 (NSE, futures on NFO), SENSEX and BANKEX (BSE, futures on BFO).
- **Which bars.** The future's bars at the chart's base interval, through the same user broker session, API budget and
  entitlement as the index itself (never another session, never sample data); each index bar takes the volume of the
  future's bar at the same timestamp, 0 where the future has none. Only the current near-month contract is read (brokers
  do not serve expired contracts' history): older bars than its first one get 0, and the label says so.
- **When not.** No contract in the master, or its bars could not be loaded: the index's own volume is left as it was and
  `volume_source` is null.
Nothing here places an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import OHLCVBar
from app.instruments import master

MASTER_BROKER = master.UPSTOX_BROKER       # the platform-wide master (public, synced daily); tests point it elsewhere
LISTED_DAYS_BEFORE_EXPIRY = 120            # an index's monthly future is listed about three months before it expires


@dataclass(frozen=True)
class NearFuture:
    underlying: str          # NIFTY
    symbol: str              # the master's tradingsymbol of the contract
    expiry: date
    exchange: str            # NFO, or BFO for SENSEX / BANKEX

    def source(self, first: Optional[date] = None) -> dict:
        """The response's `volume_source`: which contract, and from which day its volume is there."""
        month = self.expiry.strftime("%b %Y").upper()
        since = f", from {first.isoformat()}" if first else ""
        return {"symbol": self.symbol, "expiry": self.expiry.isoformat(), "exchange": self.exchange, "underlying": self.underlying,
                "from": first.isoformat() if first else None,
                "label": f"Volume: {self.underlying} {month} FUT (current near month only{since})"}


def index_underlying(symbol: str, exchange: str) -> Optional[str]:
    """The F&O underlying when `symbol` on `exchange` is an index with futures (`NIFTY 50` on NSE -> NIFTY), else None."""
    key = " ".join((symbol or "").split()).upper()
    underlying = master.UNDERLYING_ALIASES.get(key)
    if underlying is None or underlying not in master.INDEX_SYMBOLS:
        return None
    if (exchange or "").strip().upper() != master.INDEX_EXCHANGE.get(underlying, "NSE"):
        return None
    return underlying


async def near_future(session: AsyncSession, symbol: str, exchange: str, on: date) -> Optional[NearFuture]:
    """The index's near-month future on `on` (the first expiry on or after it), or None (not an index, or the master has
    no such contract)."""
    underlying = index_underlying(symbol, exchange)
    if underlying is None:
        return None
    expiries = await master.expiries(session, underlying, broker=MASTER_BROKER, instrument_type="FUT", on_or_after=on)
    if not expiries:
        return None
    row = await master.find_future(session, underlying, expiries[0], broker=MASTER_BROKER)
    wanted = master.derivatives_exchange(underlying)
    if row is None or (row.exchange or "").upper() != wanted or not row.tradingsymbol:
        return None
    return NearFuture(underlying=underlying, symbol=row.tradingsymbol.upper(), expiry=expiries[0], exchange=wanted)


def _utc(ts: datetime) -> datetime:
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def replace_volume(bars: Iterable[OHLCVBar], future_bars: Iterable[OHLCVBar]) -> List[OHLCVBar]:
    """Each bar with the volume of the future's bar at the same instant (0 where the future has none)."""
    volumes = {_utc(b.timestamp): float(b.volume or 0.0) for b in future_bars}
    return [b.model_copy(update={"volume": volumes.get(_utc(b.timestamp), 0.0)}) for b in bars]
