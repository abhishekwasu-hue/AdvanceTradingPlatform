"""B2 (UI-H bug lane, owner 24 Oct): what an option chain answer stands on - its time and its spot.

* **B2a, `data_as_of`.** While the underlying's exchange is open, the chain is read now (the broker's chain, or the data
  hub's copy of the last few seconds), so its capture time is the read time. While it is closed, the numbers are the
  last session's: `data_as_of` is that session's close by the market calendar (holidays included) - never the request
  time, so a Saturday page reads "as of Fri 23 Oct, 15:30 IST", not "13:39".
* **B2b, the spot.** After hours some broker answers carry an underlying price rounded to a strike, or none. While the
  market is closed and the broker's spot is missing or equals one of the chain's strikes exactly, the spot is the last
  close of the index from our own candles (the same shared, budgeted one-minute candles a chart reads, through the
  caller's own session and entitlement), labelled `spot_source: "last_close"` ("Spot: last close" on the page). With no
  candle either, the broker's value stays (`"broker"`). Nothing here is ever a guess.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChain
from app.db.models import User
from app.market_data.calendar import family_holidays, last_session_close, session_status
from app.option_chain import chain_view

logger = logging.getLogger(__name__)

SPOT_BROKER = "broker"
SPOT_LAST_CLOSE = "last_close"


@dataclass(frozen=True)
class AsOf:
    at: datetime          # the chain's capture time (open) or the last session close (closed)
    closed: bool


def as_of_for(now: datetime, holidays, exchange: str) -> AsOf:
    """Pure: the read time while the exchange is open, else its last session close."""
    if session_status(now, holidays, exchange).is_open:
        return AsOf(now, False)
    close = last_session_close(now, holidays, exchange)
    return AsOf(close if close is not None else now, close is not None)


async def chain_as_of(session: AsyncSession, underlying: str, now: datetime) -> AsOf:
    exchange = chain_view.spot_instrument(underlying)[1]
    return as_of_for(now, await family_holidays(session, exchange), exchange)


def strike_rounded(spot: Optional[float], chain: OptionChain) -> bool:
    """True when the spot is missing (or not a price) or equals one of the chain's strikes exactly."""
    if spot is None or not spot > 0:
        return True
    return any(abs(r.strike - spot) < 1e-9 for r in chain.rows)


async def own_last_close(user: User, session: AsyncSession, underlying: str, broker: Optional[str] = None,
                         account_label: str = "primary") -> Optional[Tuple[float, datetime]]:
    """(close, bar time) of the index's newest one-minute bar from our own candles - the chart's read, through the
    caller's own session, entitlement and budget; None when it cannot be read."""
    from fastapi import HTTPException

    from app.market_data.candles_routes import CandlesBody, candles
    symbol, exchange = chain_view.spot_instrument(underlying)
    try:
        body = await candles(CandlesBody(symbols=[symbol], exchange=exchange, timeframe="1min", lookback_days=5, broker=broker,
                                         account_label=account_label or "primary"), user=user, session=session)
    except HTTPException:
        return None
    except Exception as exc:  # noqa: BLE001 - no candle: the broker's spot stays
        logger.info("Last index close not read for %s: %s", underlying, type(exc).__name__)
        return None
    entry = (body.get("symbols") or {}).get(symbol.upper()) or {}
    bars = entry.get("bars") or []
    if entry.get("error") or not bars:
        return None
    last = bars[-1]
    try:
        return float(last["close"]), datetime.fromisoformat(str(last["timestamp"]))
    except (KeyError, TypeError, ValueError):
        return None


async def resolve_spot(user: User, session: AsyncSession, underlying: str, chain: OptionChain, spot: Optional[float], as_of: AsOf,
                       broker: Optional[str] = None, account_label: str = "primary") -> Tuple[Optional[float], str]:
    """(spot, source): the broker's spot, or - closed market, spot missing or strike-rounded - our own last close."""
    if not as_of.closed or not strike_rounded(spot, chain):
        return spot, SPOT_BROKER
    own = await own_last_close(user, session, underlying, broker, account_label)
    if own is None:
        return spot, SPOT_BROKER
    return own[0], SPOT_LAST_CLOSE
