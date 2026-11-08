"""M88-R S1: the admin's "Shared report symbols" setting and the report's own numbers (platform_controls rows).

`symbols`: what the report covers - an index name from the index catalogue ("NIFTY 50"), "<index name> constituents"
(every member of NSE's official list of that index) or an NSE stock symbol. Empty = no report. `recent_bars`: how many
Daily bars "recent" means (the volume average and the recent high / low). `key_user_id`: the admin whose own AI key the
scheduled run uses (the admin who saved the setting). Admin-only; read by the report job and the admin card.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import User
from app.platform import controls

KEY = "shared_daily_report"
LIST_KEY = "shared_daily_report_list"       # the last good constituent list per index (refreshed on each run)
MAX_ENTRIES = 60
MAX_ENTRY = 60
DEFAULTS: dict[str, Any] = {"symbols": [], "recent_bars": 20, "key_user_id": None}
RECENT_BARS_RANGE = (5, 250)


def clean_entries(raw: list[str]) -> list[str]:
    out: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").strip().upper().split())[:MAX_ENTRY]
        if text and text not in out:
            out.append(text)
    return out[:MAX_ENTRIES]


async def read(session: AsyncSession) -> dict[str, Any]:
    stored = await controls._get(session, KEY)  # noqa: SLF001 - the platform_controls row reader
    out = dict(DEFAULTS)
    if isinstance(stored.get("symbols"), list):
        out["symbols"] = clean_entries(stored["symbols"])
    try:
        bars = int(stored.get("recent_bars", DEFAULTS["recent_bars"]))
        out["recent_bars"] = bars if RECENT_BARS_RANGE[0] <= bars <= RECENT_BARS_RANGE[1] else DEFAULTS["recent_bars"]
    except (TypeError, ValueError):
        pass
    key_user = stored.get("key_user_id")
    out["key_user_id"] = int(key_user) if isinstance(key_user, int) else None
    return out


async def save(session: AsyncSession, user: User, *, symbols: list[str], recent_bars: int) -> dict[str, Any]:
    if not RECENT_BARS_RANGE[0] <= int(recent_bars) <= RECENT_BARS_RANGE[1]:
        raise ValueError(f"Recent bars must be between {RECENT_BARS_RANGE[0]} and {RECENT_BARS_RANGE[1]}.")
    entries = clean_entries(symbols)
    value = {"symbols": entries, "recent_bars": int(recent_bars), "key_user_id": int(user.id)}
    await controls._set(session, KEY, value, user)  # noqa: SLF001 - the platform_controls row writer
    await write_audit_log(session, None, user.id, "shared_report_settings", ", ".join(entries)[:200] or "-")
    await session.commit()
    return await read(session)


async def cached_lists(session: AsyncSession) -> dict[str, Any]:
    return await controls._get(session, LIST_KEY)  # noqa: SLF001


async def cache_list(session: AsyncSession, value: dict[str, Any]) -> None:
    await controls._set(session, LIST_KEY, value, None)  # noqa: SLF001
