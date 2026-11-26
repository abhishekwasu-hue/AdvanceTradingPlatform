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


async def forward(session: Any, tenant_id: int, update: dict) -> bool:
    """True when the update was handed to the bot (it then answers the press itself); False = ATP handles it as before."""
    from app.platform.controls import flag_enabled
    from app.srv2bot import job, loader
    bot = loader.load()
    enqueue = getattr(getattr(bot, "runner", None), "enqueue_update", None)
    if enqueue is None:
        return False
    who = await job.owner(session)
    if who is None or who.tenant_id != tenant_id or not await flag_enabled(session, job.FLAG, tenant_id):
        return False
    try:
        return bool(await asyncio.to_thread(enqueue, update))
    except Exception:  # noqa: BLE001 - a failed hand-over falls back to ATP's own answer
        logger.exception("srv2 bot: a Telegram update could not be handed to the bot")
        return False
