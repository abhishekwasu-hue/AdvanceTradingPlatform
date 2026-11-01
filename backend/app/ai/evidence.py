"""H-C1 a: approval evidence comes from the server's data, never from the browser.

A backtest or plan that a human approves (an AI draft, an interview option) must run on candles the server fetched
itself - from the tenant's broker session today, from the market-data lake once part B is merged. Candles posted by
the client are still accepted for demos, but they are labelled `sample` whatever the request claims, and sample
evidence cannot approve or deploy anything (AI_EVIDENCE_SERVER_ONLY, on by default). No server data for the window
means 409 - never a silent fallback to client data (OPEN_QUESTIONS H-5).

M71 W2-B (flag `market_store_reads`): the Copilot / AI reads ask the own store first (`store_frame`,
app/market_store): Daily from the NSE files (zero broker calls), intraday from the reader's own stored one-minute bars
plus only the newest ones through his own session. Nothing stored for the window -> `server_frame` exactly as before.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Optional, Tuple

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.models import bars_to_dataframe

SAMPLE = "sample"
SERVER_PREFIXES = ("broker:", "lake")
DAILY_MIN_LOOKBACK_DAYS = 400      # as `MarketDataService._lookback`: a daily read always covers about a year


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
                       broker: Optional[str] = None, lookback_days: int = 30, user: Any = None,
                       own_only: bool = False) -> Tuple[pd.DataFrame, str]:
    """Candles fetched by the server through the tenant's broker session -> (frame, source).

    BT1b (flag `byob_data_tier`): only through `user`'s own session at a confirmed broker, cached for that session
    only (never the platform-wide candle key); otherwise 409 with the reason.
    M72 PR-1a (M71Q-2, `own_only` - the Copilot's tools): with the flag off, only a credential `user` linked himself,
    never a teammate's (409 saying so); a read with no user of this organisation gets none."""
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data import intervals, live_access
    from app.market_data.candles_routes import _pick_record
    from app.market_data.service import MarketDataService
    if await live_access.byob_on(session, tenant_id):
        own = await live_access.user_source(session, user if getattr(user, "tenant_id", None) == tenant_id else None,
                                            [(symbol, exchange)], broker=broker)
        if not own.allows(symbol, exchange):
            raise live_access.LiveDataRefused(own.reason(symbol, exchange))
        record = own.record
        service = own.market_data(lookback_days=lookback_days)
    else:
        if own_only and getattr(user, "tenant_id", None) != tenant_id:
            raise live_access.LiveDataRefused(live_access.NO_USER)
        own_scope = {"user_id": int(user.id)} if own_only else {}
        record = await _pick_record(session, tenant_id, broker, "primary", **own_scope)          # 409 when no session today
        service = MarketDataService(build_adapter(record), lookback_days=lookback_days)
    try:
        # SCR-FIX A2: fetched at the broker's native interval and resampled when it does not serve `timeframe` as it is
        bars = await intervals.get_bars(service, symbol.strip().upper(), exchange.strip().upper(), timeframe)
    except Exception as exc:  # noqa: BLE001 - say what the broker said
        raise HTTPException(status_code=502, detail=f"Could not fetch {symbol} candles from {record.broker_name}: {type(exc).__name__}: {exc}"[:300]) from exc
    if not bars:
        raise HTTPException(status_code=409, detail=f"No server data for {symbol} {timeframe} from {record.broker_name} - nothing to test on")
    df = bars_to_dataframe(bars)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df, f"broker:{record.broker_name}"


@dataclass
class StoredFrame:
    """M71 W2-B: a Copilot read served by the own store - the closed bars, where they came from, the plain gap lines
    (what the store does not hold; never filled with zeros) and the broker history calls the read made."""
    frame: pd.DataFrame
    source: str
    gaps: list[str] = field(default_factory=list)
    rest_calls: int = 0


async def store_on(session: AsyncSession | None, tenant_id: int) -> bool:
    """The flag `market_store_reads` for the tenant (False without a session)."""
    if session is None:
        return False
    from app.market_store.models import FLAG
    from app.platform.controls import flag_enabled
    return bool(await flag_enabled(session, FLAG, tenant_id))


async def store_frame(session: AsyncSession | None, tenant_id: int, symbol: str, exchange: str, timeframe: str, *,
                      lookback_days: int, user: Any = None, broker: str | None = None, now: datetime | None = None,
                      checked: bool = False) -> StoredFrame | None:
    """M71 W2-B (flag `market_store_reads`): the Copilot's bars from the own store (`market_store.read_bars`), or None
    (flag off, or the store holds nothing of the window - the caller then reads exactly as before).
    - Daily on NSE: the NSE daily files over max(`lookback_days`, 400) days - zero broker calls.
    - Intraday: `user`'s own stored one-minute bars built into the timeframe over `lookback_days` calendar days, plus
      only the newest bars through his own session (M71Q-2: never another member's bars or session; a reader of
      another organisation reads nothing). `checked`: the caller already knows the flag is on."""
    from app.market_data.calendar import IST
    from app.market_store import read_bars
    from app.market_store.models import DAILY, WEEKLY, UnknownInterval, canonical
    from app.market_store.sources import own_rest
    if session is None or (not checked and not await store_on(session, tenant_id)):
        return None
    try:
        name = canonical(timeframe)
    except UnknownInterval:
        return None
    sym, ex = symbol.strip().upper(), (exchange or "NSE").strip().upper()
    now = end = now or datetime.now(UTC)
    days = max(1, int(lookback_days))
    rest = None
    if name in (DAILY, WEEKLY):
        if ex != "NSE":
            return None
        days = max(days, DAILY_MIN_LOOKBACK_DAYS)
    else:
        if user is None or getattr(user, "tenant_id", None) != tenant_id:
            return None
        rest = await own_rest(session, user, sym, ex, broker=broker)
    first = end.astimezone(IST).date() - timedelta(days=days)
    start = datetime.combine(first, datetime.min.time(), IST).astimezone(UTC)
    got = await read_bars(session, sym, name, start, end, user=user, purpose="copilot", exchange=ex, rest=rest, now=now)
    if not got.served:
        return None
    return StoredFrame(got.frame, got.source, got.gap_lines(), got.rest_calls)

