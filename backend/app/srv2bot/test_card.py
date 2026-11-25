"""Owner 25 Nov ("tyachi atach test ghe, mla telegram war vision through chart yayla pahije"): run Trade's 🧪 TEST card for the 15M SRv2
bot from the worker container -

    python -m app.srv2bot.test_card [--symbol NIFTY] [--wait-min 10]

The card goes through the bot's REAL Vision gate; the worker's Vision service draws the chart and asks Vision, its Telegram service sends
the card with ✅ / ✏️ / ❌ to the owner's own bot and handles the press. It is labelled "🧪 TEST — not a trade", tagged test_only +
dry_run, and NEVER creates a position (the private package's test_card checks the bot's trades and journal are unchanged). It runs only
when the market is closed (the private package refuses 08:30-15:35 IST on a trading day).

This process registers the same host adapters as the worker's loop (app/srv2bot/io.build: settings, token, candles, Telegram) on its
own event loop, for the platform owner only, with the bot ON. Reads only on ATP's side: nothing here places, changes or closes an order.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from datetime import UTC, datetime
from typing import Any

from app.srv2bot import io as bot_io
from app.srv2bot import job, loader

READY_SECONDS = 60.0


async def _usable_adapter(session: Any, tenant_id: int, broker: str, user_id: int | None, now: datetime) -> Any:
    """The worker's check without its shared rate budget (one short run): the owner's Upstox session when its token is usable."""
    from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable, verify_token
    record = await get_credential_record(session, tenant_id, broker)
    if record is None:
        return None
    if not token_is_usable(record, now):
        ok, _message = await verify_token(session, record, user_id=user_id)
        await session.commit()
        if not ok or not token_is_usable(record, now):
            return None
    return build_adapter(record)


async def _ready(loop: asyncio.AbstractEventLoop) -> tuple[dict[str, Any] | None, Any, str]:
    """(the host adapters, ATP's market calendar, "") for the owner with the bot ON, else (None, None, why). The calendar is the one the
    worker's loop hands the engine (job.calendar_for): the TEST card's market-hours refusal reads it (owner VPS 25 Nov: a fresh process
    has no calendar of its own)."""
    from app.db.session import _session_factory
    from app.platform.controls import flag_enabled
    async with _session_factory() as s:
        who = await job.owner(s)
        if who is None:
            return None, None, "no platform owner is set on this server"
        if not await flag_enabled(s, job.FLAG, who.tenant_id):
            return None, None, "the bot is OFF (Admin: srv2_bot)"
        calendar = await job.calendar_for(s, datetime.now(UTC))
    return bot_io.build(loop, _session_factory, who, _usable_adapter), calendar, ""


def _run_card(card: Any, argv: list[str] | None, calendar: Any) -> int:
    """The private card's main with the calendar; an older package's main(argv) without it is called as before."""
    import inspect
    try:
        takes = "calendar" in inspect.signature(card.main).parameters
    except (TypeError, ValueError):
        takes = False
    return int(card.main(argv, calendar=calendar) if takes else card.main(argv))


def main(argv: list[str] | None = None) -> int:
    bot = loader.load()
    if bot is None:
        print(f"❌ the 15M SRv2 bot is not available: {loader.reason()}")
        return 5
    card = getattr(bot, "test_card", None)
    if card is None:
        print("❌ the private package on this server has no TEST card yet (update ATP_PRIVATE_REF and deploy)")
        return 5
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="srv2-test-card", daemon=True)
    thread.start()
    try:
        impls, calendar, why = asyncio.run_coroutine_threadsafe(_ready(loop), loop).result(timeout=READY_SECONDS)
        if impls is None:
            print(f"❌ {why}")
            return 6
        bot.host.register(**impls)
        print(f"{datetime.now(UTC):%H:%M:%S} UTC - TEST card: sending (labelled TEST, never a position)")
        return _run_card(card, argv, calendar)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
