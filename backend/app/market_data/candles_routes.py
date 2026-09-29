"""Phase AA: broker candles for the research pages.

The autonomous worker has fetched real candles through each tenant's own broker session since
Phase A2 (`MarketDataService`: history + today's intraday, Redis-cached, resampled from 1-minute
bars anchored to the 09:15 open). The Signals, Scanner, Backtest and Factor Lab pages, however,
still scored candles generated in the browser. This router gives them the same candles:

* `GET  /api/market-data/sources` - the tenant's broker sessions and whether each can serve data
* `POST /api/market-data/candles` - up to 50 symbols at one timeframe through one broker session

The data goes through the tenant's stored credentials (Fernet/envelope-encrypted, never returned),
so a tenant without a logged-in broker gets a 409 that says what to do, not an empty chart. Per-
symbol failures are reported next to the symbols that worked. Fetches are metered per symbol as
`market_data_candles` so plans can price them later; nothing is persisted here.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.billing.service import meter
from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.core.models import OHLCVBar
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session
from app.market_data.service import MarketDataService, build_frames

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-data", tags=["market-data"])

MAX_SYMBOLS = 50
INTRADAY_TIMEFRAMES = ("1min", "3min", "5min", "15min", "30min", "60min")
SUPPORTED_TIMEFRAMES = INTRADAY_TIMEFRAMES + ("day",)
INTRADAY_MAX_LOOKBACK_DAYS = 30      # brokers serve one-minute history about a month per request
DAILY_MAX_LOOKBACK_DAYS = 730


class CandlesBody(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", min_length=2, max_length=10)
    timeframe: str = Field(default="5min")
    lookback_days: int = Field(default=5, ge=1, le=DAILY_MAX_LOOKBACK_DAYS)
    broker: Optional[str] = Field(default=None, description="a stored broker; omitted = the first usable session")
    account_label: str = Field(default="primary", max_length=50)


async def _records(session: AsyncSession, tenant_id: int) -> List[BrokerCredentialRecord]:
    return list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)
                                      .order_by(BrokerCredentialRecord.broker_name, BrokerCredentialRecord.account_label)))


def _source(record: BrokerCredentialRecord) -> dict:
    return {"broker": record.broker_name, "account_label": record.account_label, "token_status": record.token_status,
            "token_expires_at": record.token_expires_at.isoformat() if record.token_expires_at else None, "usable": token_is_usable(record)}


def _frame_to_bars(df) -> List[OHLCVBar]:
    df = df.dropna(subset=["open", "close"])
    return [OHLCVBar(timestamp=ts.to_pydatetime(), open=float(r.open), high=float(r.high), low=float(r.low), close=float(r.close),
                     volume=float(r.volume) if "volume" in df and r.volume == r.volume else 0.0) for ts, r in df.iterrows()]


@router.get("/sources")
async def sources(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    items = [_source(r) for r in await _records(session, user.tenant_id)]
    return {"sources": items, "usable": any(i["usable"] for i in items), "timeframes": list(SUPPORTED_TIMEFRAMES),
            "max_symbols": MAX_SYMBOLS, "intraday_max_lookback_days": INTRADAY_MAX_LOOKBACK_DAYS, "daily_max_lookback_days": DAILY_MAX_LOOKBACK_DAYS}


@router.post("/candles")
async def candles(body: CandlesBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    if body.timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {', '.join(SUPPORTED_TIMEFRAMES)}")
    records = await _records(session, user.tenant_id)
    if body.broker:
        wanted = [r for r in records if r.broker_name == body.broker.lower() and r.account_label == (body.account_label or "primary")]
        if not wanted:
            raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{body.broker}' ({body.account_label})")
        if not token_is_usable(wanted[0]):
            raise HTTPException(status_code=409, detail=f"Broker '{body.broker}' has no valid session token today. Log in to it under Settings > Brokers.")
        record = wanted[0]
    else:
        usable = [r for r in records if token_is_usable(r)]
        if not usable:
            raise HTTPException(status_code=409, detail="No broker session with a valid token. Add the broker's API key under Settings > Brokers "
                                                        "(it is stored encrypted, never in the environment) and log in to it, then try again.")
        record = usable[0]

    warnings: List[str] = []
    daily = body.timeframe == "day"
    lookback = body.lookback_days
    cap = DAILY_MAX_LOOKBACK_DAYS if daily else INTRADAY_MAX_LOOKBACK_DAYS
    if lookback > cap:
        warnings.append(f"lookback clamped to {cap} days for {body.timeframe} bars")
        lookback = cap
    base_interval = "day" if daily else "1min"
    service = MarketDataService(build_adapter(record), lookback_days=lookback)
    exchange = body.exchange.upper()

    out: Dict[str, dict] = {}
    fetched = 0
    for raw in body.symbols:
        symbol = raw.strip().upper()
        if not symbol or symbol in out:
            continue
        try:
            bars = await service.get_candles(symbol, exchange, base_interval)
            if bars and body.timeframe not in ("1min", "day"):
                bars = _frame_to_bars(build_frames(bars, "1min", [body.timeframe])[body.timeframe])
            out[symbol] = {"bars": [b.model_dump(mode="json") for b in bars], "count": len(bars),
                           "first": bars[0].timestamp.isoformat() if bars else None, "last": bars[-1].timestamp.isoformat() if bars else None, "error": None}
            fetched += 1
            if not bars:
                warnings.append(f"{symbol}: the broker returned no candles")
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not lose the others
            logger.info("Candle fetch failed for %s:%s via %s: %s", exchange, symbol, record.broker_name, exc)
            out[symbol] = {"bars": [], "count": 0, "first": None, "last": None, "error": f"{type(exc).__name__}: {exc}"[:300]}
    if fetched:
        await meter(session, user.tenant_id, "market_data_candles", quantity=fetched, source="api",
                    metadata={"broker": record.broker_name, "timeframe": body.timeframe, "exchange": exchange}, commit=True)
    if not fetched:
        warnings.append("every symbol failed - check the symbols are the broker's trading symbols and the session token is valid")
    return {"source": {"broker": record.broker_name, "account_label": record.account_label}, "exchange": exchange, "timeframe": body.timeframe,
            "base_interval": base_interval, "lookback_days": lookback, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "symbols": out, "warnings": warnings,
            "note": "Candles come from your broker's historical API through your own session and are cached for 60 seconds platform-wide."}
