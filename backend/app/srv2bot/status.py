"""The 15M SRv2 bot's status for the owner's Terminal (owner 25 Nov: "Terminal var SRV2 bot status dakhav"): whether it is ON, whether
its loop is stepping, and whether his Telegram (✅ cards) and his AI key (Vision) reach it. The worker's loop writes a small heartbeat
on its settings refresh (every REFRESH_SECONDS while the bot is ON); the API reads it. Names only, never a token, a key or a chat id.
Read-only for the page: nothing here starts, stops or changes the bot."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

STATE_KEY = "srv2_bot_status"
STALE_AFTER = timedelta(minutes=12)          # two missed refreshes (5 min each) plus a margin: the loop is not stepping


async def beat(session: Any, names: tuple[str, ...], now: datetime) -> None:
    """The loop's heartbeat: when it last stepped and which of his settings reached it (names only)."""
    from app.db.models import PlatformControlRecord
    row = await session.get(PlatformControlRecord, STATE_KEY)
    if row is None:
        row = PlatformControlRecord(key=STATE_KEY)
        session.add(row)
    row.value_json = json.dumps({"at": now.astimezone(UTC).isoformat(), "telegram": "TELEGRAM_BOT_TOKEN" in names,
                                 "vision": "ANTHROPIC_API_KEY" in names})
    row.updated_by = None


async def _beat(session: Any) -> dict[str, Any]:
    from app.db.models import PlatformControlRecord
    row = await session.get(PlatformControlRecord, STATE_KEY)
    try:
        value = json.loads(row.value_json) if row is not None and row.value_json else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


async def read(session: Any, tenant_id: int | None, now: datetime | None = None) -> dict[str, Any]:
    """{installed, on, running, last_step_at, telegram, vision, state}: `state` is the one word the Terminal shows -
    not_installed / off / not_running / no_telegram / running."""
    from app.platform.controls import flag_enabled
    from app.srv2bot import job, loader
    now = now or datetime.now(UTC)
    installed = loader.load() is not None
    on = installed and await flag_enabled(session, job.FLAG, tenant_id)
    beat_ = await _beat(session)
    at = beat_.get("at")
    try:
        last = datetime.fromisoformat(at) if isinstance(at, str) else None
    except ValueError:
        last = None
    running = bool(on and last is not None and now - last <= STALE_AFTER)
    telegram, vision = bool(running and beat_.get("telegram")), bool(running and beat_.get("vision"))
    state = ("not_installed" if not installed else "off" if not on else "not_running" if not running
             else "no_telegram" if not telegram else "running")
    return {"installed": installed, "on": on, "running": running, "last_step_at": last.isoformat() if last else None,
            "telegram": telegram, "vision": vision, "state": state}
