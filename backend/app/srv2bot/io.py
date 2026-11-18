"""The IO the 15M SRv2 bot's copied code asks for (the private host adapter names), done by ATP for the platform owner only:
- reads through the owner organisation's Upstox session and ATP's broker budget (RateLimitedBroker): option chain rows, expiries
  (instrument master), last prices, funds;
- the owner's settings rows (store.py) and LIVE confirmation (none: LIVE is not wired);
- Telegram to the owner's linked chat (the organisation's bot, else the platform bot);
- the owner's Upstox access token (the engine reads its own candles with it, as in Trade).
The copied code is synchronous and runs in a worker thread: each callable here runs its coroutine on the worker's event loop
(`run_coroutine_threadsafe`) with its own database session and waits for it (bounded). Reads only: nothing here places, changes or
closes an ATP order or position."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.srv2bot import store

logger = logging.getLogger(__name__)

WAIT_SECONDS = 30.0
UPSTOX = "upstox"
UNDERLYING = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY", "SENSEX": "SENSEX"}


class Owner:
    def __init__(self, tenant_id: int, user: Any):
        self.tenant_id, self.user = tenant_id, user


def build(loop: asyncio.AbstractEventLoop, session_factory: Callable, owner: Owner,
          usable_adapter: Callable) -> Dict[str, Callable]:
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

    def settings(strategy: str, symbol: str) -> Optional[dict]:
        async def go():
            async with session_factory() as s:
                return await store.get_row(s, strategy, symbol)
        return call(go)

    def token() -> Optional[str]:
        async def go():
            from app.brokers.token_lifecycle import get_credential_record, load_credentials, token_is_usable
            async with session_factory() as s:
                await _broker(s)                                   # the worker's check (refreshes the token status)
                record = await get_credential_record(s, owner.tenant_id, UPSTOX)
                if not token_is_usable(record):
                    return None
                return load_credentials(record).access_token
        try:
            return call(go)
        except Exception as exc:  # noqa: BLE001 - Trade: no token -> the bot says so and enters nothing
            logger.info("srv2 bot: no usable Upstox token (%s)", type(exc).__name__)
            return None

    async def _expiries(s, symbol: str) -> List[str]:
        from app.instruments import master
        from app.market_data.calendar import IST
        today = datetime.now(IST).date()
        days = await master.expiries(s, UNDERLYING.get(symbol, symbol), on_or_after=today)
        return [d.isoformat() for d in days]

    def option_expiries(tok: str, symbol: str) -> List[str]:
        async def go():
            async with session_factory() as s:
                return await _expiries(s, symbol)
        return call(go)

    def option_chain(tok: str, symbol: str, expiry_index: int) -> Tuple[Optional[list], str]:
        async def go():
            from datetime import date
            async with session_factory() as s:
                days = await _expiries(s, symbol)
                if len(days) <= expiry_index:
                    return None, "no such expiry in the instrument master"
                broker = await _broker(s)
                return await broker.get_option_chain_raw(UNDERLYING.get(symbol, symbol), date.fromisoformat(days[expiry_index])), ""
        try:
            return call(go)
        except Exception as exc:  # noqa: BLE001 - Trade: (None, reason) -> no entry this cycle
            return None, f"{type(exc).__name__}: {exc}"

    def ltp(tok: str, keys: List[str]) -> Tuple[Dict[str, float], Optional[str]]:
        async def go():
            async with session_factory() as s:
                broker = await _broker(s)
                return await broker.get_ltp_by_key(list(keys)), None
        try:
            return call(go)
        except Exception as exc:  # noqa: BLE001 - Trade: ({}, detail) -> that check waits for the next cycle
            return {}, f"{type(exc).__name__}: {exc}"

    def funds(tok: str) -> Optional[dict]:
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

    def send(text: str, silent: bool = False, kind: Optional[str] = None) -> bool:
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
            "funds": funds, "send": send, "live_confirmed": lambda *a: False}
