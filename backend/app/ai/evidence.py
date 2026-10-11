"""H-C1 a: approval evidence comes from the server's data, never from the browser.

A backtest or plan that a human approves (an AI draft, an interview option) must run on candles the server fetched
itself - from the tenant's broker session today, from the market-data lake once part B is merged. Candles posted by
the client are still accepted for demos, but they are labelled `sample` whatever the request claims, and sample
evidence cannot approve or deploy anything (AI_EVIDENCE_SERVER_ONLY, on by default). No server data for the window
means 409 - never a silent fallback to client data (OPEN_QUESTIONS H-5).
"""
from typing import Optional, Tuple

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.models import bars_to_dataframe

SAMPLE = "sample"
SERVER_PREFIXES = ("broker:", "lake")


def is_server(source: Optional[str]) -> bool:
    return bool(source) and str(source).startswith(SERVER_PREFIXES)


def client_source(claimed: Optional[str]) -> str:
    """What client-posted candles are recorded as: `sample` (the request's own label is not trusted)."""
    return SAMPLE if config.AI_EVIDENCE_SERVER_ONLY else (claimed or SAMPLE)


def require_server(source: Optional[str], what: str) -> None:
    if config.AI_EVIDENCE_SERVER_ONLY and not is_server(source):
        raise HTTPException(status_code=400, detail=(
            f"{what} needs evidence from server data; this one ran on {source or 'unknown'} candles. "
            "Run it again without posting candles - the server fetches them from your broker session."))


async def server_frame(session: AsyncSession, tenant_id: int, symbol: str, exchange: str, timeframe: str, *,
                       broker: Optional[str] = None, lookback_days: int = 30) -> Tuple[pd.DataFrame, str]:
    """Candles fetched by the server through the tenant's broker session -> (frame, source)."""
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data.candles_routes import _pick_record
    from app.market_data.service import MarketDataService
    record = await _pick_record(session, tenant_id, broker, "primary")          # 409 when no session today
    adapter = build_adapter(record)
    try:
        bars = await MarketDataService(adapter, lookback_days=lookback_days).get_candles(symbol.strip().upper(), exchange.strip().upper(), timeframe)
    except Exception as exc:  # noqa: BLE001 - say what the broker said
        raise HTTPException(status_code=502, detail=f"Could not fetch {symbol} candles from {record.broker_name}: {type(exc).__name__}: {exc}"[:300]) from exc
    if not bars:
        raise HTTPException(status_code=409, detail=f"No server data for {symbol} {timeframe} from {record.broker_name} - nothing to test on")
    df = bars_to_dataframe(bars)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df, f"broker:{record.broker_name}"
