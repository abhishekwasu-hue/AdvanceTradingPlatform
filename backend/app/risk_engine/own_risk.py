"""M68 PR-5 (owner order M68, TS1-041 / 056 / 057 / 058): the user's own Risk limits choices from Settings > Trade settings.

The rule (M68, binding): ATP never pre-chooses, caps or silently changes a trading choice. These were ATP's own rules
or numbers until now; each is now the user's setting (app/me/trade_settings.py, section `risk_limits`), Not set = OFF:

* `overnight_short_options` (TS1-041): a bot that carries forward may hold only a defined-risk (winged) credit
  structure for days; with this switch on, a written straddle / strangle may be held too.
* `vix_gate` + `vix_change_pct` / `vix_level` / `vix_size_cut_pct` (TS1-056): the India VIX check on new entries,
  with his own numbers. Off (Not set) = the check never pauses or cuts anything. A number Not set = that part is off.
* `experience_trades` / `experience_sessions` (TS1-057): the Advanced experience level (naked short options) opens at
  his own numbers when he turned "Limit me by my experience level" on.
* `event_default_action` (TS1-058): what a market event day he adds does, as the form's starting choice.

The order-time checks with their own switches (Settings > Risk limits, app/risk_engine/risk_limits.py) and the
organisation's choices there (the pause end, opposite positions, the drawdown cut) are not here. Exits never read this
module (ADR-0004): every value here acts on NEW entries only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserPrefsRecord
from app.me import trade_settings

SECTION = "risk_limits"
WHERE = "Settings › Trade settings › Risk limits"


@dataclass(frozen=True)
class OwnRisk:
    """The user's own Risk limits choices; None / False = Not set (off)."""
    overnight_short_options: bool = False
    vix_gate: bool = False
    vix_change_pct: float | None = None
    vix_level: float | None = None
    vix_size_cut_pct: float | None = None
    experience_trades: int | None = None
    experience_sessions: int | None = None
    event_default_action: str | None = None


def needs_setting(name: str) -> str:
    """The plain-words reason for a Risk limits choice that is Not set."""
    return f"Needs your setting: {trade_settings.BY_KEY[f'{SECTION}.{name}'].label} ({WHERE})"


def of_values(values: dict[str, Any]) -> OwnRisk:
    """The choices from a cleaned `trade_settings` values map. Pure."""
    def own(name: str) -> Any:
        return values.get(f"{SECTION}.{name}")

    def num(name: str) -> float | None:
        v = own(name)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def whole(name: str) -> int | None:
        v = own(name)
        return int(v) if isinstance(v, int) and not isinstance(v, bool) else None

    action = own("event_default_action")
    return OwnRisk(
        overnight_short_options=own("overnight_short_options") is True, vix_gate=own("vix_gate") is True,
        vix_change_pct=num("vix_change_pct"), vix_level=num("vix_level"), vix_size_cut_pct=num("vix_size_cut_pct"),
        experience_trades=whole("experience_trades"), experience_sessions=whole("experience_sessions"),
        event_default_action=action if action in ("BLOCK", "SIZE_CUT") else None,
    )


async def load(session: AsyncSession, tenant_id: int | None, user_id: int | None) -> OwnRisk:
    """The user's own choices (all Not set for no user)."""
    if tenant_id is None or user_id is None:
        return OwnRisk()
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(
        UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
        UserPrefsRecord.namespace == trade_settings.NAMESPACE))
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return OwnRisk()
    return of_values(trade_settings.clean(data.get("values") if isinstance(data, dict) else None))


async def for_user(session: AsyncSession, user: Any) -> OwnRisk:
    return await load(session, getattr(user, "tenant_id", None), getattr(user, "id", None))


__all__ = ["OwnRisk", "load", "for_user", "of_values", "needs_setting", "SECTION", "WHERE"]
