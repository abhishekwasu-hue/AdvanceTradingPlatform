"""M76 MP-1: the trader's warning switches and the "already accepted today" store (docs/design/M76_REVIEW.md sections 8-9).

* Switches: Settings › Trade settings › Warnings (app/me/trade_settings.py, `warnings.<id>` and `warnings.<id>_ask`). A
  switch he never saved uses the OWNER's starting state (amendment 2); every number (`collar_pct`, `mis_minutes`) stays
  Not set until he saves one, and a line that needs it does not exist until then.
* Accepted today: pressing Place with a line shown accepts it. The same type for the same contract is then not shown again
  until the end of that IST trading day (an operational expiry, not a reading rule): Redis
  ``pretrade:acc:{tenant}:{user}:{type}:{instrument}`` with a TTL to midnight IST. Redis down = nothing is suppressed (the
  line shows again; it never hides a line it cannot remember). No table, no migration.

Read-only for trading: nothing here places, refuses, changes or delays an order or an exit.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import client as cache
from app.db.models import User
from app.me import trade_settings as ts

IST = timezone(timedelta(hours=5, minutes=30))
WARNING_IDS = ts.WARNING_IDS
_INSTRUMENT = re.compile(r"[^A-Za-z0-9|.:_-]")


@dataclass(frozen=True)
class WarningSettings:
    on: dict[str, bool]                       # type -> switch (his saved value, else the owner's start state)
    ask: dict[str, bool]                      # type -> "ask me before placing"
    collar_pct: float | None = None           # Not set: no limit-vs-LTP line
    mis_minutes: int | None = None            # Not set: no MIS cut-off line
    square_off_time: str | None = None        # his own Exits › Square-off time (Not set: no MIS cut-off line)
    saved: dict[str, Any] = field(default_factory=dict)

    def enabled(self, wid: str) -> bool:
        return self.on.get(wid, False)


def settings_from(values: dict[str, Any]) -> WarningSettings:
    on = {w: bool(ts.effective(values, f"warnings.{w}")) for w in ts.WARNING_IDS}
    ask = {w: bool(ts.effective(values, f"warnings.{w}_ask")) for w in ts.WARNING_IDS}
    collar = values.get("warnings.collar_pct")
    minutes = values.get("warnings.mis_minutes")
    return WarningSettings(on=on, ask=ask, collar_pct=float(collar) if collar is not None else None,
                           mis_minutes=int(minutes) if minutes is not None else None,
                           square_off_time=values.get("exits.square_off_time"), saved=values)


async def read_settings(session: AsyncSession, user: User) -> WarningSettings:
    return settings_from(await ts.saved_values(user, session))


# --- accepted today -----------------------------------------------------------------------------------------------------

def instrument_key(underlying: str, expiry: str, strike: float | None, right: str) -> str:
    """One contract, as the ticket names it (no lookup): NIFTY|2026-11-03|24500|CE."""
    strike_text = "" if strike is None else (f"{strike:g}")
    return _INSTRUMENT.sub("", f"{underlying.upper()}|{expiry}|{strike_text}|{right.upper()}")[:80]


def seconds_to_ist_midnight(now: datetime) -> int:
    ist = now.astimezone(IST)
    midnight = (ist + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((midnight - ist).total_seconds()))


def _key(user: User, wid: str, instrument: str) -> str:
    return f"pretrade:acc:{user.tenant_id}:{user.id}:{wid}:{instrument}"


async def accept(user: User, types: Iterable[str], instrument: str, now: datetime | None = None) -> list[str]:
    """Records that he pressed Place with these lines shown; returns the types recorded (unknown ids are ignored)."""
    moment = now or datetime.now(UTC)
    ttl = seconds_to_ist_midnight(moment)
    at = moment.astimezone(UTC).replace(microsecond=0).isoformat()
    done: list[str] = []
    for wid in dict.fromkeys(types):
        if wid not in ts.WARNING_IDS:
            continue
        try:
            # M77 SX-6: the value is the time Place was pressed (UTC ISO), for the study's check card; any value = accepted
            await cache._get_client().set(_key(user, wid, instrument), at, ex=ttl)  # noqa: SLF001 - a TTL past the cache cap, by design
            done.append(wid)
        except Exception:  # noqa: BLE001 - fail-open: the line simply shows again
            pass
    return done


async def accepted(user: User, instrument: str) -> set[str]:
    """The types he already accepted today for this contract (Redis down: none)."""
    keys = [_key(user, w, instrument) for w in ts.WARNING_IDS]
    try:
        values = await cache._get_client().mget(keys)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return set()
    return {w for w, v in zip(ts.WARNING_IDS, values) if v}


async def accepted_times(user: User, instrument: str) -> dict[str, str | None]:
    """M77 SX-6: the types he accepted today for this contract -> when Place was pressed (None for a record made before
    the time was kept). Redis down: none."""
    keys = [_key(user, w, instrument) for w in ts.WARNING_IDS]
    try:
        values = await cache._get_client().mget(keys)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str | None] = {}
    for w, v in zip(ts.WARNING_IDS, values):
        if not v:
            continue
        text: str | None = v.decode() if isinstance(v, bytes) else str(v)
        try:
            datetime.fromisoformat(str(text))
        except ValueError:
            text = None
        out[w] = text
    return out


# --- M76 MP-5: accepted lines on the journal entry ----------------------------------------------------------------------

LABELS: dict[str, str] = {name: label for name, label, _help, _on in ts.WARNING_TYPES}


def clean_accepted(types: Iterable[str] | None) -> tuple[str, ...]:
    """The accepted types the order carries: known ids only, each once, in the order sent (an unknown id - say from a
    tab opened before a type was renamed - is dropped, never a reason to refuse the order)."""
    return tuple(w for w in dict.fromkeys(types or ()) if w in ts.WARNING_IDS)[:len(ts.WARNING_IDS)]


async def record_accepted(session: AsyncSession, user: User, trade_ids: Iterable[int], types: Iterable[str],
                          at: str | None) -> None:
    """He pressed Place with these lines shown and the order filled: each type goes on the trade's journal entry
    (`plan_json.accepted_warnings`, with the time Place was pressed). Runs after the order, through the journal's own
    queue (flag `discipline_engine`), only while `trade_warnings` is on. Never raises, never holds back the order."""
    wanted, ids = clean_accepted(types), [int(t) for t in trade_ids if t]
    if not wanted or not ids:
        return
    try:
        from app.journal import capture
        from app.platform.controls import flag_enabled
        if not await flag_enabled(session, "trade_warnings", user.tenant_id):
            return
        await capture.on_accepted_warnings(session, user.tenant_id, ids, [{"id": w, "at": at} for w in wanted])
    except Exception:  # noqa: BLE001 - the journal record never fails or delays the order
        import logging
        logging.getLogger(__name__).warning("Accepted warnings not journaled for user %s", user.id, exc_info=True)
