"""Owner 26 Nov: pressing ✅ on the 15M SRv2 bot's card answered "This button is not valid for this organisation". With ATP's Telegram
inbound on, the organisation's bot has ATP's webhook set, so Telegram delivers the bot's updates to ATP; the bot's own Telegram service
(getUpdates, in the worker) never sees them. ATP hands the bot's own updates (callback data "v1|...", Trade's signed Vision buttons) to
the private package's inbox; the worker's Telegram service handles them there, where the card was made. Owner tenant only, bot ON.
Nothing here decides anything: it only passes the press on."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

PREFIX = "v1|"


def is_bot_callback(data: str) -> bool:
    return str(data or "").startswith(PREFIX)


async def _bot_for(session: Any, tenant_id: int) -> Any:
    """The private bot's runner for the owner's tenant with the bot ON, else None."""
    from app.platform.controls import flag_enabled
    from app.srv2bot import job, loader
    runner = getattr(loader.load(), "runner", None)
    if getattr(runner, "enqueue_update", None) is None:
        return None
    who = await job.owner(session)
    if who is None or who.tenant_id != tenant_id or not await flag_enabled(session, job.FLAG, tenant_id):
        return None
    return runner


async def forward_text(session: Any, tenant_id: int, update: dict, from_id: str) -> bool:
    """Owner 26 Nov audit: a plain text goes to the bot only while it waits for a typed reply from this user (after ✏️ Modify /
    📝 Correct / ❌ other); otherwise ATP answers it as before. True when handed over."""
    runner = await _bot_for(session, tenant_id)
    wants = getattr(runner, "wants_text", None)
    if runner is None or wants is None or not from_id:
        return False
    try:
        if not await asyncio.to_thread(wants, from_id):
            return False
        return bool(await asyncio.to_thread(runner.enqueue_update, update))
    except Exception:  # noqa: BLE001 - a failed hand-over falls back to ATP's own answer
        logger.exception("srv2 bot: a typed reply could not be handed to the bot")
        return False


async def forward(session: Any, tenant_id: int, update: dict) -> bool:
    """True when the update was handed to the bot (it then answers the press itself); False = ATP handles it as before."""
    runner = await _bot_for(session, tenant_id)
    if runner is None:
        return False
    try:
        return bool(await asyncio.to_thread(runner.enqueue_update, update))
    except Exception:  # noqa: BLE001 - a failed hand-over falls back to ATP's own answer
        logger.exception("srv2 bot: a Telegram update could not be handed to the bot")
        return False
