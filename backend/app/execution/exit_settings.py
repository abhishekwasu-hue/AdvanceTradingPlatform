"""M68 PR-3 (owner order M68, TS-1 rows TS1-026 to 034, TS1-097; owner decision D1 on its provisional default M68Q-1):
the user's own Exits and entry-window settings, read by the worker, the backtests, the scan Auto PAPER window, the
incomplete multi-leg rule, the combined exits and the stop guard.

The rule (M68, binding): ATP never pre-chooses an exit. Each value below is the user's own (Settings > Trade settings,
prefs namespace `trade_settings`); Not set (None) means ATP does nothing by itself:

* `square_off_time` (Exits > Square-off time, D1): Not set - ATP squares off nothing at a clock time. The broker's own
  intraday (MIS) square-off near the close is a broker fact (shown as "Your broker's intraday close"). A user who sets a
  time gets it for every intraday position of his (every bot); a bot's own / an order's own "Close at a time"
  (`exit_rules.time_exit_at`, the ticket's time stop) closes that position earlier.
* `window_start` / `window_end` (Automation > New entries from / until): Not set - no ATP cut-off for new entries (the
  venue's open / close and holidays stay facts, app/market_data/calendar.py).
* `incomplete_legs_action` / `incomplete_legs_answer_minutes` (TS1-030): Not set - an incomplete multi-leg order waits
  for his answer; nothing is closed or kept by a timer.
* `combined_measure` / `combined_check_mode` (TS1-031): the form starts from them; a combined exit on the spot level
  whose check is not chosen asks for it.
* `close_if_broker_stop_rejected` (TS1-032): Not set = off. On (his switch) or the operator's LIVE_EXIT_IF_NO_STOP: a
  LIVE position whose broker stop is clearly refused is closed at once.

Exits are never blocked (ADR-0004): nothing here holds back a stop, a target, Exit now, a group exit or the kill
switch. A missing setting only means ATP adds no exit of its own.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time as dtime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserPrefsRecord
from app.me import trade_settings

EXITS = "exits"
AUTOMATION = "automation"
WHERE = "Settings › Trade settings › Exits"
CLOSE_FILLED, KEEP = "close_filled", "keep"                 # app.execution.incomplete_structures' words
_ACTIONS = {"CLOSE_FILLED": CLOSE_FILLED, "KEEP": KEEP}
_CHECKS = ("TOUCH", "CLOSE")
_MEASURES = ("MTM_INR", "NET_PREMIUM", "SPOT")


def _clock(value: Any) -> dtime | None:
    if not isinstance(value, str) or not trade_settings.TIME.match(value):
        return None
    hh, mm = value.split(":")
    return dtime(int(hh), int(mm))


def in_window(start: dtime | None, end: dtime | None, at: dtime) -> bool:
    """Whether a new entry at `at` is inside the user's own window (each end Not set = open on that side). A start later
    than the end is a window across midnight (a 24x7 venue). Pure."""
    if start is not None and end is not None and start > end:
        return at >= start or at < end
    return (start is None or at >= start) and (end is None or at < end)


@dataclass(frozen=True)
class ExitSettings:
    """The user's own Exits / entry-window settings; None = Not set."""
    square_off_time: dtime | None = None
    window_start: dtime | None = None
    window_end: dtime | None = None
    incomplete_legs_action: str | None = None        # CLOSE_FILLED | KEEP
    incomplete_legs_answer_minutes: int | None = None
    combined_measure: str | None = None
    combined_check_mode: str | None = None
    close_if_broker_stop_rejected: bool | None = None

    # The venue (`family`, app.market_data.calendar.session_family) is passed so a later per-venue setting needs no new
    # call sites; today one time applies to every venue (M68Q-PR3-1).
    def square_off_at(self, family: str) -> dtime | None:
        return self.square_off_time

    def entries_from(self, family: str) -> dtime | None:
        return self.window_start

    def entries_until(self, family: str) -> dtime | None:
        return self.window_end

    def entry_window_reason(self, family: str, at: dtime) -> str | None:
        """Why no new entry is placed at `at` (his own window, in plain words), or None (open)."""
        start, end = self.entries_from(family), self.entries_until(family)
        if in_window(start, end, at):
            return None
        if end is not None and (start is None or at >= end):
            return f"No new entries after {end.strftime('%H:%M')} IST (your setting)"
        return f"No new entries before {start.strftime('%H:%M')} IST (your setting)" if start is not None else None


def from_values(values: dict[str, Any]) -> ExitSettings:
    """The settings from a cleaned `trade_settings` values map (app.me.trade_settings.clean). Pure."""
    def own(section: str, name: str) -> Any:
        return values.get(f"{section}.{name}")
    action = own(EXITS, "incomplete_legs_action")
    minutes = own(EXITS, "incomplete_legs_answer_minutes")
    measure, check, switch = own(EXITS, "combined_measure"), own(EXITS, "combined_check_mode"), own(EXITS, "close_if_broker_stop_rejected")
    return ExitSettings(
        square_off_time=_clock(own(EXITS, "square_off_time")),
        window_start=_clock(own(AUTOMATION, "window_start")),
        window_end=_clock(own(AUTOMATION, "window_end")),
        incomplete_legs_action=_ACTIONS.get(action) if isinstance(action, str) else None,
        incomplete_legs_answer_minutes=minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes >= 1 else None,
        combined_measure=measure if measure in _MEASURES else None,
        combined_check_mode=check if check in _CHECKS else None,
        close_if_broker_stop_rejected=switch if isinstance(switch, bool) else None,
    )


async def load(session: AsyncSession, user: Any = None, *, tenant_id: int | None = None, user_id: int | None = None) -> ExitSettings:
    """The user's own settings (by his User row, or by tenant and user id - a position's owner); all Not set for none."""
    if user is not None:
        tenant_id, user_id = getattr(user, "tenant_id", None), getattr(user, "id", None)
    if tenant_id is None or user_id is None:
        return ExitSettings()
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(
        UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
        UserPrefsRecord.namespace == trade_settings.NAMESPACE))
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return ExitSettings()
    return from_values(trade_settings.clean(data.get("values") if isinstance(data, dict) else None))


async def close_if_stop_rejected(session: AsyncSession, *, tenant_id: int | None, user_id: int | None) -> bool:
    """TS1-032: close a LIVE position whose broker stop was clearly refused - the operator's LIVE_EXIT_IF_NO_STOP, or the
    position owner's own switch. Only ever adds an exit (ADR-0004); how the close is sent is unchanged."""
    from app.core import config
    if config.LIVE_EXIT_IF_NO_STOP:
        return True
    return (await load(session, tenant_id=tenant_id, user_id=user_id)).close_if_broker_stop_rejected is True


def needs_setting(name: str, section: str = EXITS) -> str:
    return f"Needs your setting: {trade_settings.BY_KEY[f'{section}.{name}'].label} ({WHERE})"


__all__ = ["ExitSettings", "load", "from_values", "in_window", "close_if_stop_rejected", "needs_setting", "WHERE",
           "CLOSE_FILLED", "KEEP"]
