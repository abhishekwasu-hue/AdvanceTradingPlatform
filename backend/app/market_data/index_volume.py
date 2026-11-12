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
- **Screens (M93-Q3, owner decision: a missing or 0 volume is no volume data).** An index has no traded volume, so a 0
  in its bars (the NSE files, a broker's index bars, a bar the future has none for) is not volume: `screen_frame`
  turns it into "missing" (NaN) for the screener, which then never treats it as traded volume. Charts keep their 0.
Nothing here places an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd
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


def replace_frame_volume(frame: pd.DataFrame, future_frame: pd.DataFrame | None) -> pd.DataFrame:
    """M99 B1-a: `replace_volume` on a bar frame (indexed by bar start) - a copy whose volume is the future's bar volume
    at the same instant, 0 where the future has no bar (the same rule as the chart and the screener)."""
    if future_frame is None or "volume" not in future_frame.columns:
        volumes = pd.Series(0.0, index=frame.index)
    else:
        fut = future_frame["volume"].astype(float).fillna(0.0)
        fut = fut[~fut.index.duplicated(keep="last")]
        fut.index = _utc_index(fut.index)
        volumes = pd.Series(fut.reindex(_utc_index(frame.index)).fillna(0.0).to_numpy(float), index=frame.index)
    return frame.assign(volume=volumes)


def _utc_index(index: pd.Index) -> pd.DatetimeIndex:
    di = pd.DatetimeIndex(index)
    return di.tz_localize("UTC") if di.tz is None else di.tz_convert("UTC")


def is_index(symbol: str) -> bool:
    """M93-Q3: `symbol` is an index (no traded volume of its own): an F&O index's candle symbol or underlying name
    (`NIFTY 50`, `NIFTY BANK`, `SENSEX`), or a name the platform stores as an index (`NIFTY50` -> NSE_INDEX:NIFTY50)."""
    key = " ".join((symbol or "").split()).upper()
    if not key:
        return False
    if key in master.UNDERLYING_ALIASES:
        return True
    from app.ai.tools.copilot import lake_key
    return lake_key(key).split(":", 1)[0].endswith("_INDEX")


def screen_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """M93-Q3: an index's bars with "no volume data" (NaN) where its volume is 0 - a copy; the frame itself when there
    is no 0 (or no volume column)."""
    if "volume" not in frame.columns:
        return frame
    volume = frame["volume"].astype(float)
    zero = (volume == 0).to_numpy()
    if not zero.any():
        return frame
    return frame.assign(volume=volume.mask(zero, np.nan))
