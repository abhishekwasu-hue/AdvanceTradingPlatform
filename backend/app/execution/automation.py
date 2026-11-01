"""M68 PR-6 (owner order M68, TS-1 rows TS1-060 to 063): the user's own Automation settings, read wherever an automatic
entry is decided.

The rule (M68, binding): ATP never pre-chooses whether an entry is placed automatically or waits for the user's Approve,
nor the limits on an approval request or on the scanner's Auto PAPER. Each is the user's own value, in this order (the
first one set wins):

1. the bot's own value (`strategy_deployments.entry_mode`, asked in the bot form);
2. the user's Settings > Trade settings > Automation (app/me/trade_settings.py, prefs namespace `trade_settings`);
3. a value he saved earlier in an older store (TS-6, kept exactly): `trading_defaults.entry_mode`.

The organisation's `approval_only_entries` flag stays an admin control for a team (OFF by default, no longer in the
trader pack): while it is on, every automatic entry of the organisation waits for an Approve, whatever the user chose.

Not set (M68, provisional defaults in docs/OPEN_QUESTIONS.md M68Q-PR6-1..4):

* **New entries** (`approval_mode`): a bot with none of its own and none in his Trade settings places nothing and says
  "Needs your setting: New entries" (bot card, order trail). An alert, an API signal or a scan setup that belongs to no
  bot is unchanged (his "Ask me first" makes it a request; nothing chosen = as before).
* **An entry request waits** (`approval_entry_minutes`): the platform's waiting time (the operator's
  APPROVAL_ENTRY_TTL_MINUTES) still ends the request - a request must end, and an expired request places nothing.
* **New price after a move of** (`requote_drift_pct`): no limit - Approve places at the price then (a price already
  through the stop or target 1 still ends the request: that is the signal's own level, not a setting).
* **New prices offered at most** (`max_requotes`): none offered - a move beyond his own limit ends the request and
  nothing is placed (ATP raises no new request it was not told to).
* **Scanner entries at most / wait per symbol / stays armed for** (`scan_auto_*`): the scanner places no Auto PAPER
  entry (a proposal instead, "Needs your setting: ..."), and arming asks for them.

Exits are never blocked (ADR-0004): nothing here is read by an exit. An exit question ("Exit now?", off since M55 S2)
keeps the operator's waiting time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.execution.order_defaults import NEEDS_PREFIX, _raw
from app.me import trade_settings

SECTION = "automation"
WHERE = "Settings › Trade settings › Automation"
ENTRY_MODES = ("AUTO", "APPROVAL")
SCAN_FIELDS = ("scan_auto_max", "scan_auto_cooldown_minutes", "scan_auto_arming_days")


def label(name: str) -> str:
    return trade_settings.BY_KEY[f"{SECTION}.{name}"].label


def needs_setting(name: str) -> str:
    """The plain-words skip / refusal reason for a missing Automation setting (same prefix as Order defaults, so the bot
    card's "Needs your setting" code reads it too)."""
    return f"{NEEDS_PREFIX}{label(name)} ({WHERE})"


@dataclass(frozen=True)
class Automation:
    """The user's own Automation settings; None = Not set."""
    approval_mode: str | None = None
    approval_entry_minutes: int | None = None
    requote_drift_pct: float | None = None
    max_requotes: int | None = None
    scan_auto_max: int | None = None
    scan_auto_cooldown_minutes: int | None = None
    scan_auto_arming_days: int | None = None

    def scan_missing(self) -> list[str]:
        """The scanner limits he has not set (field names), in the form's order."""
        return [name for name in SCAN_FIELDS if getattr(self, name) is None]


def _whole(value: Any, low: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= low else None


async def load(session: AsyncSession, user: User | None) -> Automation:
    """The user's own Automation settings (Trade settings first, then a value he saved in an older store); all None for
    no user."""
    if user is None or getattr(user, "id", None) is None:
        return Automation()
    values = trade_settings.clean((await _raw(session, user, trade_settings.NAMESPACE)).get("values"))
    legacy = (await _raw(session, user, "trading_defaults")).get("entry_mode")

    def own(name: str) -> Any:
        return values.get(f"{SECTION}.{name}")

    mode = own("approval_mode")
    drift = own("requote_drift_pct")
    return Automation(
        approval_mode=mode if mode in ENTRY_MODES else (legacy if legacy in ENTRY_MODES else None),
        approval_entry_minutes=_whole(own("approval_entry_minutes"), 1),
        requote_drift_pct=float(drift) if isinstance(drift, (int, float)) and not isinstance(drift, bool) and drift > 0 else None,
        max_requotes=_whole(own("max_requotes"), 0),
        scan_auto_max=_whole(own("scan_auto_max"), 1),
        scan_auto_cooldown_minutes=_whole(own("scan_auto_cooldown_minutes"), 0),
        scan_auto_arming_days=_whole(own("scan_auto_arming_days"), 1),
    )


async def load_for(session: AsyncSession, user_id: int | None) -> Automation:
    """`load` for a user id (a proposal's or a template's owner); all None when there is none."""
    if user_id is None:
        return Automation()
    return await load(session, await session.get(User, user_id))


async def entry_mode(session: AsyncSession, deployment: Any, user_id: int | None = None) -> str | None:
    """How this bot's entries go: its own value, else its owner's Trade settings; None = Not set (the bot asks)."""
    own = getattr(deployment, "entry_mode", None) if deployment is not None else None
    if own in ENTRY_MODES:
        return own
    owner = user_id if user_id is not None else getattr(deployment, "created_by", None)
    return (await load_for(session, owner)).approval_mode


__all__ = ["Automation", "load", "load_for", "entry_mode", "needs_setting", "label", "SECTION", "WHERE", "ENTRY_MODES", "SCAN_FIELDS"]
