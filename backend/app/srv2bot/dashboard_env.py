"""Owner 24 Nov ("he sarv info dashbord warun set keli aahech"): the 15M SRv2 bot's Telegram ✅ cards and its Vision take what the
owner set in ATP's dashboard, so nothing has to be typed into the server's .env:

* Telegram: the organisation's own Telegram bot (Settings > Alerts, its token) and the owner's chat linked to it - the chat that
  gets the cards and the only one whose ✅ counts. ATP only SENDS with that bot (it never reads its updates), so the bot's
  Telegram service may read the taps. A chat linked only through the platform bot ("Connect Telegram") cannot carry buttons:
  the platform bot's updates go to ATP's webhook.
* The buttons' signing secret: made by this worker process (Trade's own service drops the cards still pending at a restart).
* Vision: the owner's own Anthropic key in Settings > AI and the model he picked there.

A value set in .env (SRV2_* -> the worker's TELEGRAM_* / VISION_* / ANTHROPIC_API_KEY, docker-compose.yml) wins over the
dashboard. Values are put in this worker's environment, where the copied Trade code reads them; never logged, only names.
Owner only; nothing here places or changes an order."""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any

logger = logging.getLogger(__name__)

NAMES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_APPROVER_IDS", "VISION_CALLBACK_SECRET", "VISION_SIGNAL_MODEL",
         "ANTHROPIC_API_KEY")
FROM_ENV = frozenset(n for n in NAMES if os.environ.get(n))          # what .env set when the worker started: never overwritten
_SECRET = secrets.token_hex(32)


async def _telegram(session: Any, tenant_id: int, user_id: int) -> dict[str, str]:
    from app.screener.evidence_store import _bot, own_chat
    token = await _bot(session, tenant_id)
    chat = await own_chat(session, tenant_id, user_id)
    if not token or not chat:
        return {}
    return {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat, "TELEGRAM_APPROVER_IDS": chat, "VISION_CALLBACK_SECRET": _SECRET}


async def _vision(session: Any, user: Any) -> dict[str, str]:
    from app.ai import byok
    record = await byok.get_key(session, user, "anthropic")
    if record is None:
        return {}
    try:
        key = byok.open_key(record, user=user)
    except Exception:  # noqa: BLE001 - a key that cannot be opened is no key: Vision says "unavailable", the card still goes
        logger.info("srv2 bot: the owner's Anthropic key could not be opened")
        return {}
    model = byok.effective_model(record, "anthropic")
    return {"ANTHROPIC_API_KEY": key, "VISION_SIGNAL_MODEL": model} if key and model else {}


async def values(session: Any, owner: Any) -> dict[str, str]:
    """What the dashboard gives for the owner (names -> values); empty parts are left out. A part that cannot be read is
    logged by name and left out - it never stops the bot's step."""
    out: dict[str, str] = {}
    for part, read in (("telegram", lambda: _telegram(session, owner.tenant_id, owner.user.id)), ("vision", lambda: _vision(session, owner.user))):
        try:
            out.update(await read())
        except Exception as exc:  # noqa: BLE001
            logger.warning("srv2 bot: the dashboard's %s settings could not be read (%s)", part, type(exc).__name__)
    return out


def apply(found: dict[str, str]) -> tuple[str, ...]:
    """Puts the dashboard values into this process's environment (a name .env set is left as it is; a dashboard value that
    is gone is removed). Returns the names now set from either place, for the log and to see a change."""
    for name in NAMES:
        if name in FROM_ENV:
            continue
        if found.get(name):
            os.environ[name] = found[name]
        else:
            os.environ.pop(name, None)
    return tuple(n for n in NAMES if os.environ.get(n))


__all__ = ["NAMES", "FROM_ENV", "values", "apply"]
