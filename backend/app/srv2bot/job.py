"""The worker's step for the 15M SRv2 bot: when the flag `srv2_bot` is ON for the owner's organisation and the private package is
installed, register ATP's IO (io.py) and run the bot's schedule once (the private runner's `tick`, in a thread: the copied code is
synchronous). The schedule itself (engine at each 5M close, the bot every minute at :30, exits every 15 s / 5 s) is the bot's own,
Trade's cron timing. Owner-only; PAPER; nothing here touches ATP's orders, positions or exits."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.srv2bot import io as bot_io
from app.srv2bot import loader

logger = logging.getLogger(__name__)

FLAG = "srv2_bot"


async def owner(session) -> bot_io.Owner | None:
    from app.db.models import User
    from app.platform import live_owner, phase0_lock
    tid = await phase0_lock.owner_tenant_id(session)
    if tid is None:
        return None
    user = await session.get(User, live_owner.owner_user_id())
    return bot_io.Owner(tid, user) if live_owner.is_owner(user) else None


_cal: dict = {}
TICK_SECONDS = 2.0
IDLE_SECONDS = 30.0         # the bot is OFF / not installed / no owner: look again in 30 s


async def calendar_for(session, now: datetime) -> Any:
    """ATP's NSE calendar handed to the engine (as the signal engine runner does), read once a day."""
    from app.engine_provider import sources
    from app.workers.engine_runner import calendar_last
    day = now.date()
    if _cal.get("day") != day:
        first = datetime(now.year - 1, 1, 1).date()
        _cal.update(day=day, cal=await sources.load_calendar(session, "NSE", first, calendar_last(now)))
    return _cal["cal"]


async def run_once(session_factory: Callable, usable_adapter: Callable, now: datetime | None = None) -> list | None:
    """One step. Returns what ran (None when the bot is off / not installed / no owner)."""
    from app.platform.controls import flag_enabled
    bot = loader.load()
    if bot is None:
        return None
    now = now or datetime.now(UTC)
    async with session_factory() as s:
        who = await owner(s)
        if who is None or not await flag_enabled(s, FLAG, who.tenant_id):
            stop_services()
            return None
        cal = await calendar_for(s, now)
    loop = asyncio.get_running_loop()
    bot.host.register(**bot_io.build(loop, session_factory, who, usable_adapter))
    if not _services["on"]:
        await _start_services(bot, session_factory, who)
    from app.market_data.calendar import IST
    ist = now.astimezone(IST).replace(tzinfo=None)
    return await asyncio.to_thread(bot.runner.tick, ist, cal)


_services: dict = {"on": False, "bot": None}


async def _start_services(bot: Any, session_factory: Callable, who: bot_io.Owner) -> None:
    """SRV2 P3 (ADR-0029): once the bot is ON, store the default agent mode (human_confirm, only when none is stored) and start the
    private package's Vision worker and Telegram ✅ service (daemon threads outside `tick`, so exits are never delayed). An older
    private package without them: the bot runs as before."""
    from app.srv2bot import mode as agent_mode
    async with session_factory() as s:
        if await agent_mode.ensure_default(s, getattr(who.user, "id", None)) is not None:
            await s.commit()
    start = getattr(bot.runner, "start_services", None)
    if start is not None:
        await asyncio.to_thread(start)
    _services.update(on=True, bot=bot)


def stop_services() -> None:
    """The bot is OFF (or the worker stops): stop the Vision / Telegram threads if this worker started them."""
    bot = _services["bot"]
    if not _services["on"] or bot is None:
        return
    stop = getattr(bot.runner, "stop_services", None)
    try:
        if stop is not None:
            stop()
    finally:
        _services.update(on=False, bot=None)


async def loop_forever(session_factory: Callable, usable_adapter: Callable) -> None:
    """The worker's background task: one step every TICK_SECONDS (the bot's exits need a 5 s cadence; the worker's cycle is 60 s).
    A failing step is logged and the next one runs."""
    while True:
        ran = None
        try:
            ran = await run_once(session_factory, usable_adapter)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the bot never stops the worker
            logger.exception("srv2 bot step failed")
            ran = []
        await asyncio.sleep(TICK_SECONDS if ran is not None else IDLE_SECONDS)
