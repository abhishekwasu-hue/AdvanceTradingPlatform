"""The owner's settings rows for the bot - Trade's `strategy_settings` rows (strategy_name, symbol) -> JSON, e.g.
("15m_dynamic_sr", "NIFTY"), ("__signal_symbols__", "ALL"), ("__global_kill_switch__", "ALL") - kept in one platform_controls row
(owner-only feature; no migration). The values are the owner's own (copied from his Trade dashboard, then edited on his page)."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlatformControlRecord

KEY = "srv2_bot_rows"


def _key(strategy: str, symbol: str) -> str:
    return f"{strategy}|{symbol}"


async def rows(session: AsyncSession) -> Dict[str, Dict[str, Any]]:
    rec = await session.get(PlatformControlRecord, KEY)
    if rec is None or not rec.value_json:
        return {}
    data = json.loads(rec.value_json)
    return dict(data.get("rows") or {}) if isinstance(data, dict) else {}


async def get_row(session: AsyncSession, strategy: str, symbol: str) -> Optional[Dict[str, Any]]:
    return (await rows(session)).get(_key(strategy, symbol))


async def save_row(session: AsyncSession, strategy: str, symbol: str, values: Dict[str, Any], *, by: Optional[int] = None,
                   merge: bool = True) -> Dict[str, Any]:
    """Trade's save_strategy_settings: a JSON merge into the stored row (merge=False replaces it). The caller commits."""
    all_rows = await rows(session)
    k = _key(strategy, symbol)
    row = {**(all_rows.get(k) or {}), **values} if merge else dict(values)
    all_rows[k] = row
    rec = await session.get(PlatformControlRecord, KEY)
    text = json.dumps({"rows": all_rows}, ensure_ascii=False, default=str)
    if rec is None:
        session.add(PlatformControlRecord(key=KEY, value_json=text, updated_by=by))
    else:
        rec.value_json, rec.updated_by = text, by
    return row
