"""M68 PR-2 (owner order M68, TS-1 rows TS1-017 to 024): the user's own Position size, read for every new entry.

The rule (M68, binding): ATP never pre-chooses a size. The size of a new entry is the user's own, in this order (the first
one set wins):

1. the order's own size (a ticket's lots / quantity, a request body);
2. the bot's own lots (`strategy_deployments.max_lots`, the bot form's "Lots");
3. the user's Settings > Trade settings > Position size (app/me/trade_settings.py, prefs namespace `trade_settings`):
   Fixed lots, Fixed quantity, % of capital, or Risk-based (his Risk limits' risk per trade);
4. a value he saved earlier in an older store (TS-6, kept exactly): `trading_defaults.max_lots` ("Lots for a new bot").

With none of them, and no risk per trade of his own (risk-based sizing, Settings > Risk limits), ATP does not choose: a
ticket ASKS for the size (TS-3), and a bot / webhook / API / scan / approval entry places nothing and records "Needs your
setting: Lots" (the order trail, the bot card's wait reason and the notification bell show it). Before M68 every such
entry traded one lot (TS1-017).

What stays fixed is only the exchange's fact: a size is a whole number of the contract's lot (a cash share's lot is one
share). A size that comes to less than one lot places nothing and says so - it is never rounded up.

Exits never read this module: an exit closes what is open (ADR-0004).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.execution.order_defaults import NEEDS_PREFIX, _raw
from app.me import trade_settings
from app.risk_engine.risk_manager import CASH_UNIT, NEEDS_LOTS

SECTION = "position_size"
WHERE = "Settings › Trade settings › Position size"
FIXED_LOTS, FIXED_QUANTITY, PCT_CAPITAL, RISK_BASED = "FIXED_LOTS", "FIXED_QUANTITY", "PCT_CAPITAL", "RISK_BASED"
MODES = (FIXED_LOTS, FIXED_QUANTITY, PCT_CAPITAL, RISK_BASED)
FANOUT_MODES = ("MULTIPLIER", "FIXED", "PCT_CAPITAL")       # app/accounts/fanout_plan.py SIZING_MODES
RISK_WHERE = "Settings › Risk limits"


def label(name: str) -> str:
    return trade_settings.BY_KEY[f"{SECTION}.{name}"].label


def needs_setting(name: str) -> str:
    """The plain-words skip / refusal reason for a missing Position size field (the same prefix as Order defaults, so the
    bot card reads it as "needs_setting", app/deployments/why.held_back)."""
    return f"{NEEDS_PREFIX}{label(name)} ({WHERE})"


NEEDS_LOTS_TEXT = NEEDS_LOTS         # the risk engine's own refusal when no size reaches it (one sentence everywhere)


@dataclass(frozen=True)
class PositionSize:
    """The user's own Position size; None = Not set."""
    mode: str | None = None
    lots: int | None = None
    quantity: int | None = None
    capital_pct: float | None = None
    fanout_mode: str | None = None
    fanout_value: float | None = None

    @property
    def any_set(self) -> bool:
        return self.mode is not None or self.lots is not None or self.quantity is not None or self.capital_pct is not None


def _whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _positive(value: Any, high: float = trade_settings.BIG) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) and 0 < v <= high else None


def _choice(value: Any, allowed: tuple[str, ...]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


async def saved(session: AsyncSession, user: User) -> dict[str, Any]:
    """Every Trade setting the user saved himself (validated), for a reader of this section that also needs a value of
    another one (the basket's ready strategies: Options > Spread width) - one reader of the namespace per order path."""
    return trade_settings.clean((await _raw(session, user, trade_settings.NAMESPACE)).get("values"))


async def load(session: AsyncSession, user: User | None) -> PositionSize:
    """The user's own Position size (Trade settings first, then his older "Lots for a new bot"); all None for no user."""
    if user is None or getattr(user, "id", None) is None:
        return PositionSize()
    values = await saved(session, user)
    older = await _raw(session, user, "trading_defaults")

    def own(name: str) -> Any:
        return values.get(f"{SECTION}.{name}")

    return PositionSize(
        mode=_choice(own("mode"), MODES),
        lots=_whole(own("lots")) or _whole(older.get("max_lots")),
        quantity=_whole(own("quantity")),
        capital_pct=_positive(own("capital_pct"), 100.0),
        fanout_mode=_choice(own("fanout_mode"), FANOUT_MODES),
        fanout_value=_positive(own("fanout_value")),
    )


def _lots_text(n: float) -> str:
    return f"{n:g} lot" if n == 1 else f"{n:g} lots"


def quantity_for(ps: PositionSize, *, unit: float, entry: float | None, capital: float | None,
                 risk_set: bool) -> tuple[float | None, str | None]:
    """The size of one new entry from the user's Position size, in units (whole lots of `unit`).

    Returns ``(quantity, None)`` for a size, ``(None, None)`` when risk-based sizing sizes it (his own risk per trade is
    set: Settings > Risk limits), and ``(None, reason)`` when nothing he set gives a size (the entry places nothing and
    the reason says which setting it needs). `entry` None: a value-based size (% of capital) cannot be worked out here.
    """
    lot = float(unit) if unit and unit > 0 else CASH_UNIT
    mode = ps.mode or (FIXED_LOTS if ps.lots is not None else FIXED_QUANTITY if ps.quantity is not None
                       else PCT_CAPITAL if ps.capital_pct is not None else None)
    if mode is None:
        # Nothing of his own in Position size: risk-based sizing when he set a risk per trade (as before M68), else ask.
        return (None, None) if risk_set else (None, NEEDS_LOTS_TEXT)
    if mode == RISK_BASED:
        if not risk_set:
            return None, (f"{NEEDS_PREFIX}Risk per trade ({RISK_WHERE}) - your Position size is risk-based, and no risk "
                          f"per trade is set. Nothing was placed")
        return None, None
    if mode == FIXED_LOTS:
        if ps.lots is None:
            return None, needs_setting("lots")
        return float(ps.lots) * lot, None
    if mode == FIXED_QUANTITY:
        if ps.quantity is None:
            return None, needs_setting("quantity")
        whole = math.floor(ps.quantity / lot + 1e-9)
        if whole < 1:
            return None, (f"Your quantity ({ps.quantity:g}) is less than one lot ({lot:g}) of this contract - nothing was placed. "
                          f"Change it: {WHERE}")
        return whole * lot, None
    # PCT_CAPITAL
    if ps.capital_pct is None:
        return None, needs_setting("capital_pct")
    if capital is None or capital <= 0:
        return None, f"Your capital is not known, so a size as a share of it cannot be worked out - nothing was placed ({WHERE})"
    if entry is None or entry <= 0:
        return None, (f"A share of capital does not size this entry (it has no single price) - set this bot's own lots, or "
                      f"Fixed lots in {WHERE}. Nothing was placed")
    value = capital * ps.capital_pct / 100.0
    whole = math.floor(value / (entry * lot) + 1e-9)
    if whole < 1:
        return None, (f"{ps.capital_pct:g}% of your capital ({value:,.0f}) is less than one lot ({_lots_text(1)} of {lot:g} at "
                      f"{entry:g}) - nothing was placed. Change it: {WHERE}")
    return whole * lot, None


def fanout_sizing(ps: PositionSize, mode: str | None, value: float | None) -> tuple[str | None, float | None]:
    """An extra paper account's sizing: its own (both set), else the user's Position size > Extra paper accounts (both
    set), else Not set (None, None) - that account is skipped with "Needs your setting" (app/accounts/fanout_plan.py)."""
    if mode and value is not None and value > 0:
        return mode, float(value)
    if ps.fanout_mode and ps.fanout_value is not None:
        return ps.fanout_mode, ps.fanout_value
    return None, None


__all__ = ["PositionSize", "load", "saved", "quantity_for", "fanout_sizing", "needs_setting", "label", "NEEDS_LOTS_TEXT", "WHERE", "MODES",
           "FANOUT_MODES", "CASH_UNIT", "FIXED_LOTS", "FIXED_QUANTITY", "PCT_CAPITAL", "RISK_BASED"]
