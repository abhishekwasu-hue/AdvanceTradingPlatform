"""The IO the 15M SRv2 bot's copied code asks for (the private host adapter names), done by ATP for the platform owner only:
- reads through the owner organisation's Upstox session and ATP's broker budget (RateLimitedBroker): option chain rows, expiries
  (instrument master), last prices, funds;
- the owner's settings rows (store.py) and LIVE confirmation (none: LIVE is not wired);
- Telegram to the owner's linked chat (the organisation's bot, else the platform bot);
- the owner's Upstox access token (the engine reads its own candles with it, as in Trade);
- candles in Trade's `upstox_api.fetch_candles` shape (the bot's Vision chart, the level gate, the engine service ask for them).
The copied code is synchronous and runs in a worker thread: each callable here runs its coroutine on the worker's event loop
(`run_coroutine_threadsafe`) with its own database session and waits for it (bounded). Reads only: nothing here places, changes or
closes an ATP order or position."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.srv2bot import store

logger = logging.getLogger(__name__)

WAIT_SECONDS = 30.0
UPSTOX = "upstox"
UNDERLYING = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY", "SENSEX": "SENSEX"}


# LIVE is not wired in ATP's port (DECISIONS 18 Nov SRV2): the bot's `live_confirmed` adapter is always False.
LIVE_CONFIRMED = False


def effective_trading_mode(row: dict | None) -> str:
    """The bot's own rule for its trading mode (the private package's stand-in for Trade cloud_db.get_strategy_settings, L1041-1052): the
    stored row's `trading_mode` (none stored = PAPER, Trade's default); a stored LIVE / LIVE_PAPER counts only with the owner's LIVE
    confirmation, which ATP's port never gives (LIVE_CONFIRMED) -> PAPER."""
    mode = str((row or {}).get("trading_mode") or "PAPER").upper()
    if mode in ("LIVE", "LIVE_PAPER") and not LIVE_CONFIRMED:
        return "PAPER"
    return mode


# Trade upstox_api.fetch_candles (L132-255): its lookback defaults, and which intervals ATP's Upstox broker serves directly (the v2
# history / intraday endpoints: 1minute, 30minute, day); the others are resampled from 1-minute bars, as ATP's market-data service does.
CANDLE_LOOKBACK_DAYS = {"1minute": 5, "5minute": 10, "15minute": 20, "30minute": 60, "1hour": 90, "day": 400}
CANDLE_DIRECT = {"1minute": "1min", "30minute": "30min", "day": "day"}
CANDLE_RESAMPLE = {"5minute": "5min", "15minute": "15min", "1hour": "60min"}
CANDLE_CHUNK_DAYS = 28                       # Trade: at most one month of minute bars per history call
CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
SPOT = {"NIFTY": ("NIFTY 50", "NSE"), "BANKNIFTY": ("NIFTY BANK", "NSE"), "SENSEX": ("SENSEX", "BSE")}


def candle_args(args: tuple, kwargs: dict) -> tuple[str, str, int]:
    """(symbol, interval, lookback days) from Trade's call shapes: fetch_candles(token, symbol, current_spot, interval=, lookback_days=)
    (the engine service names the third `underlying_price`). An interval Trade does not take falls back to 30minute, as Trade."""
    symbol = str(args[1] if len(args) > 1 else kwargs.get("symbol") or "NIFTY").upper()
    interval = str(args[3] if len(args) > 3 else kwargs.get("interval") or "30minute")
    if interval not in CANDLE_LOOKBACK_DAYS:
        interval = "30minute"
    days = args[4] if len(args) > 4 else kwargs.get("lookback_days")
    return symbol, interval, int(days if days is not None else CANDLE_LOOKBACK_DAYS[interval])


def candle_frame(bars: list[Any], interval: str) -> Any:
    """Bars (ATP OHLCVBar) -> Trade's frame: timestamp (India time, as Upstox sends it), open, high, low, close, volume, oi; sorted, a
    repeated timestamp keeps the newest fetch (Trade: keep="last"); 5 / 15 minute and 1 hour resampled from 1-minute bars (bars start
    at the session's open, left-labelled). No bars -> an empty frame with those columns."""
    import pandas as pd
    if not bars:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    frame = pd.DataFrame([{"timestamp": b.timestamp, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                           "volume": b.volume, "oi": 0.0} for b in bars])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame = frame.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    for col in CANDLE_COLUMNS[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    rule = CANDLE_RESAMPLE.get(interval)
    if rule:
        frame = (frame.set_index("timestamp").resample(rule, label="left", closed="left", origin="start_day", offset="15min")
                 .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "oi": "last"})
                 .dropna(subset=["open"]).reset_index())
    return frame


class Owner:
    def __init__(self, tenant_id: int, user: Any):
        self.tenant_id, self.user = tenant_id, user


def build(loop: asyncio.AbstractEventLoop, session_factory: Callable, owner: Owner,
          usable_adapter: Callable) -> dict[str, Callable]:
    """The callables for the private host's register(...). `usable_adapter(session, tenant_id, broker, user_id, now)` is the
    worker's own (token check + rate budget)."""

    def call(make):
        fut = asyncio.run_coroutine_threadsafe(make(), loop)
        return fut.result(timeout=WAIT_SECONDS)

    async def _broker(session):
        broker = await usable_adapter(session, owner.tenant_id, UPSTOX, owner.user.id, datetime.now(UTC))
        if broker is None:
            raise RuntimeError("the owner's Upstox session is not usable (log in again)")
        return broker

    async def _data_broker(session):
        """For market-data reads only (option chain, last prices, candles): the logged-in session first; without the daily login, the
        owner's Upstox Analytics Token (one year, data only - CLAUDE.md: the default connection for data and PAPER), as ATP's own data
        reads choose (market_data/candles_routes.py: token_is_usable, else data_is_usable). Never for funds or orders."""
        broker = await usable_adapter(session, owner.tenant_id, UPSTOX, owner.user.id, datetime.now(UTC))
        if broker is not None:
            return broker
        from app.brokers.token_lifecycle import build_adapter, data_is_usable, get_credential_record
        from app.secrets_store.envelope import ensure_tenant_key
        await ensure_tenant_key(session, owner.tenant_id)
        record = await get_credential_record(session, owner.tenant_id, UPSTOX)
        if not data_is_usable(record):
            raise RuntimeError("the owner's Upstox session is not usable and no Analytics Token serves data (log in again)")
        return build_adapter(record)

    def settings(strategy: str, symbol: str) -> dict | None:
        async def go():
            async with session_factory() as s:
                return await store.get_row(s, strategy, symbol)
        return call(go)

    def token() -> str | None:
        """The token the bot's own Upstox reads use (data only: PAPER, LIVE is not wired): the daily session's when it is usable, else
        the Analytics Token's when it serves data (owner VPS 26 Nov: before the daily login the bot had no data at all)."""
        async def go():
            from app.brokers.token_lifecycle import get_credential_record, load_credentials, token_is_usable
            from app.brokers.upstox_analytics import analytics_usable
            from app.secrets_store.envelope import ensure_tenant_key
            async with session_factory() as s:
                await ensure_tenant_key(s, owner.tenant_id)          # a fresh process (the TEST card) has no data key loaded yet
                try:
                    await _broker(s)                               # the worker's check (refreshes the token status)
                except RuntimeError:
                    pass                                           # no daily session: the Analytics Token may still serve data
                record = await get_credential_record(s, owner.tenant_id, UPSTOX)
                if token_is_usable(record):
                    return load_credentials(record).access_token
                if record is not None and analytics_usable(record):
                    return load_credentials(record).analytics_token
                return None
        try:
            return call(go)
        except Exception as exc:  # noqa: BLE001 - Trade: no token -> the bot says so and enters nothing
            logger.info("srv2 bot: no usable Upstox token (%s)", type(exc).__name__)
            return None

    async def _expiries(s, symbol: str) -> list[str]:
        from app.instruments import master
        from app.market_data.calendar import IST
        today = datetime.now(IST).date()
        days = await master.expiries(s, UNDERLYING.get(symbol, symbol), on_or_after=today)
        return [d.isoformat() for d in days]

    def option_expiries(tok: str, symbol: str) -> list[str]:
        async def go():
            async with session_factory() as s:
                return await _expiries(s, symbol)
        return call(go)

    def option_chain(tok: str, symbol: str, expiry_index: int) -> tuple[list | None, str]:
        async def go():
            from datetime import date
            async with session_factory() as s:
                days = await _expiries(s, symbol)
                if len(days) <= expiry_index:
                    return None, "no such expiry in the instrument master"
                broker = await _data_broker(s)
                return await broker.get_option_chain_raw(UNDERLYING.get(symbol, symbol), date.fromisoformat(days[expiry_index])), ""
        try:
            return call(go)
        except Exception as exc:  # noqa: BLE001 - Trade: (None, reason) -> no entry this cycle
            return None, f"{type(exc).__name__}: {exc}"

    def ltp(tok: str, keys: list[str]) -> tuple[dict[str, float], str | None]:
        async def go():
            async with session_factory() as s:
                broker = await _data_broker(s)
                return await broker.get_ltp_by_key(list(keys)), None
        try:
            return call(go)
        except Exception as exc:  # noqa: BLE001 - Trade: ({}, detail) -> that check waits for the next cycle
            return {}, f"{type(exc).__name__}: {exc}"

    def candles(*args: Any, **kwargs: Any) -> Any:
        """Trade upstox_api.fetch_candles: history up to yesterday in month chunks plus today from the intraday endpoint (minute
        intervals), or the daily bars up to today; through the owner's Upstox session and ATP's budget. Never raises: a failure is an
        empty frame with `attrs["failed_chunks"]`, as Trade."""
        from datetime import timedelta

        from app.market_data.calendar import IST
        symbol, interval, days = candle_args(args, kwargs)
        name, exchange = SPOT.get(symbol, SPOT["NIFTY"])          # Trade get_instrument_key: an unknown name reads NIFTY
        step = CANDLE_DIRECT.get(interval, "1min")

        async def go():
            today = datetime.now(IST).date()
            bars: list[Any] = []
            failed = 0
            async with session_factory() as s:
                broker = await _data_broker(s)
                if step == "day":
                    try:
                        bars += await broker.get_historical_data(name, exchange, "day", datetime.combine(today - timedelta(days=days), datetime.min.time()),
                                                                 datetime.combine(today, datetime.min.time()))
                    except Exception:  # noqa: BLE001 - Trade counts a failed chunk and goes on
                        failed += 1
                    return bars, failed
                start, end = today - timedelta(days=max(days, 2)), today - timedelta(days=1)
                while end > start:
                    begin = max(start, end - timedelta(days=CANDLE_CHUNK_DAYS))
                    try:
                        bars += await broker.get_historical_data(name, exchange, step, datetime.combine(begin, datetime.min.time()),
                                                                 datetime.combine(end, datetime.min.time()))
                    except Exception:  # noqa: BLE001
                        failed += 1
                    end = begin - timedelta(days=1)
                try:
                    bars += await broker.get_intraday_candles(name, exchange, step)
                except Exception:  # noqa: BLE001 - outside the session there is no intraday data
                    pass
            return bars, failed
        try:
            bars, failed = call(go)
        except Exception as exc:  # noqa: BLE001 - Trade: an empty frame
            logger.info("srv2 bot: candles unreadable for %s %s (%s)", symbol, interval, type(exc).__name__)
            bars, failed = [], 1
        frame = candle_frame(bars, interval)
        frame.attrs["failed_chunks"] = failed
        return frame

    def funds(tok: str) -> dict | None:
        async def go():
            async with session_factory() as s:
                broker = await _broker(s)
                m = await broker.get_margins()
                return {"available_margin": float(m.available_margin or 0.0), "used_margin": float(m.used_margin or 0.0)}
        try:
            return call(go)
        except Exception as exc:  # noqa: BLE001 - Trade: None
            logger.info("srv2 bot: funds unreadable (%s)", type(exc).__name__)
            return None

    def send(text: str, silent: bool = False, kind: str | None = None) -> bool:
        async def go():
            from app.alerts.channels import TelegramConfig
            from app.alerts.dispatcher import send_telegram
            from app.screener.evidence_store import _telegram_target
            async with session_factory() as s:
                chat, bot_token, why = await _telegram_target(s, owner.user)
            if not chat or not bot_token:
                logger.info("srv2 bot: no Telegram chat for the owner (%s)", why)
                return False
            await send_telegram(TelegramConfig(bot_token=bot_token, chat_id=str(chat)), text, chat_id=str(chat))
            return True
        try:
            return bool(call(go))
        except Exception as exc:  # noqa: BLE001 - Trade: a failed send never stops the bot
            logger.info("srv2 bot: Telegram send failed (%s)", type(exc).__name__)
            return False

    return {"settings": settings, "token": token, "option_expiries": option_expiries, "option_chain": option_chain, "ltp": ltp,
            "funds": funds, "send": send, "candles": candles, "live_confirmed": lambda *a: LIVE_CONFIRMED}
