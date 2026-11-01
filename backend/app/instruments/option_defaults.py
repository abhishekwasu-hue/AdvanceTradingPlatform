"""M68 PR-4 (owner order M68, TS-1 rows TS1-035 to 040, 042 to 047, TS1-100): the user's own Options settings.

The rule (M68, binding): ATP never pre-chooses an expiry, a strike, an in / out of the money distance, an option side, a
spread width, a ratio, a delta tolerance, a strike search window, an expiry-day roll, a delivery warning or a risk-free
rate. Each is the user's own value, in this order (the first one set wins):

1. the bot's / backtest's own value (the form or the request body);
2. the user's Settings > Trade settings > Options (app/me/trade_settings.py, prefs namespace `trade_settings`);
3. a value he saved earlier in an older store (TS-6, kept exactly): `trading_defaults` (My trading defaults: expiry,
   strike, distance, side, width), `option_chain.expiry_day_next` and `smart_execution.settlement_*` (only when stored).

Not set at all: ATP does not choose. A form ASKS for the field (TS-3: a 422 at that field, `type: needs_setting`); an
entry of a bot whose row lacks a value places nothing and records "Needs your setting: <field>". Exits never read this
module (ADR-0004).

What a structure is by its own definition stays as it is (TS1-042): a straddle / iron butterfly sells at the money, a
strangle / condor sells out of the money, a credit structure writes and a debit one buys. Those are names, not choices.
The exchange's own facts stay too: only a listed strike or expiry can be traded (TS1-047, checked when the contract is
picked) and the freeze quantity splits a large order (TS1-087).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserPrefsRecord
from app.me import trade_settings

SECTION = "options"
WHERE = "Settings › Trade settings › Options"
NEEDS_PREFIX = "Needs your setting: "
EXPIRY_RULES = ("NEAREST", "NEXT", "MONTHLY")
STRIKE_RULES = ("ATM", "ITM", "OTM")
POSITIONS = ("BUY", "WRITE")


def label(name: str) -> str:
    return trade_settings.BY_KEY[f"{SECTION}.{name}"].label


def needs_setting(name: str) -> str:
    """The plain-words refusal / skip reason for a missing Options setting."""
    return f"{NEEDS_PREFIX}{label(name)} ({WHERE})"


def field_error(loc: tuple[str, ...], name: str) -> dict[str, Any]:
    """One 422 detail item the form shows at the field (TS-3), as PR-1's `needs_setting` items."""
    return {"loc": ["body", *loc], "type": "needs_setting", "msg": needs_setting(name)}


@dataclass(frozen=True)
class OptionDefaults:
    """The user's own Options settings; None = Not set."""
    expiry_rule: str | None = None
    strike_rule: str | None = None
    strike_offset: int | None = None
    option_position: str | None = None
    spread_width: int | None = None
    ratio: int | None = None
    custom_legs: list[dict[str, Any]] | None = None
    delta_tolerance: float | None = None
    strike_search_steps: int | None = None
    expiry_day_roll: bool | None = None
    settlement_warning: bool | None = None
    settlement_days_before: int | None = None
    settlement_band_pct: float | None = None
    risk_free_rate_pct: float | None = None
    builder_pop_pct: float | None = None
    builder_strikes_otm: int | None = None

    @property
    def risk_free_rate(self) -> float | None:
        """The rate as a fraction (7 % -> 0.07), or None."""
        return None if self.risk_free_rate_pct is None else self.risk_free_rate_pct / 100.0


async def _raw(session: AsyncSession, user: User, namespace: str) -> dict[str, Any]:
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(
        UserPrefsRecord.tenant_id == user.tenant_id, UserPrefsRecord.user_id == user.id, UserPrefsRecord.namespace == namespace))
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _choice(value: Any, allowed: tuple[str, ...]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _whole(value: Any, low: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= low else None


def _number(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if low <= v <= high else None


def _switch(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


async def load(session: AsyncSession, user: User | None) -> OptionDefaults:
    """The user's own Options settings (Trade settings first, then a value he saved in an older store); all None for no
    user."""
    if user is None or getattr(user, "id", None) is None:
        return OptionDefaults()
    values = trade_settings.clean((await _raw(session, user, trade_settings.NAMESPACE)).get("values"))
    older = await _raw(session, user, "trading_defaults")
    chain = await _raw(session, user, "option_chain")
    smart = await _raw(session, user, "smart_execution")

    def own(name: str) -> Any:
        return values.get(f"{SECTION}.{name}")

    def own_else(name: str, read: Any, older_value: Any) -> Any:
        """His Trade-settings value when he set one (a set value that does not read is None: he is asked, never the older
        store's value); the older store's value only when the Trade-settings field is Not set."""
        return read(own(name)) if own(name) is not None else read(older_value)

    days = _whole(smart.get("settlement_days_before"), 0)
    band = _number(smart.get("settlement_band_pct"), 0.0, 100.0)
    warning = own("settlement_warning")
    if warning is None and (days is not None or band is not None):
        warning = True                      # TS-6: he saved the delivery warning's numbers before it had a switch
    legs = own("custom_legs")
    return OptionDefaults(
        expiry_rule=_choice(own("expiry_rule"), EXPIRY_RULES) or _choice(older.get("expiry_rule"), EXPIRY_RULES),
        strike_rule=_choice(own("strike_rule"), STRIKE_RULES) or _choice(older.get("strike_rule"), STRIKE_RULES),
        strike_offset=own_else("strike_offset", lambda v: _whole(v, 0), older.get("strike_offset")),
        option_position=_choice(own("option_position"), POSITIONS) or _choice(older.get("option_position"), POSITIONS),
        spread_width=own_else("spread_width", lambda v: _whole(v, 1), older.get("spread_width")),
        ratio=_whole(own("ratio"), 1),
        custom_legs=legs if isinstance(legs, list) else None,
        delta_tolerance=_number(own("delta_tolerance"), 0.0, 1.0),
        strike_search_steps=_whole(own("strike_search_steps"), 1),
        expiry_day_roll=_switch(own("expiry_day_roll")) if own("expiry_day_roll") is not None else _switch(chain.get("expiry_day_next")),
        settlement_warning=_switch(warning),
        settlement_days_before=own_else("settlement_days_before", lambda v: _whole(v, 0), smart.get("settlement_days_before")),
        settlement_band_pct=own_else("settlement_band_pct", lambda v: _number(v, 0.0, 100.0), smart.get("settlement_band_pct")),
        risk_free_rate_pct=_number(own("risk_free_rate_pct"), 0.0, 100.0),
        builder_pop_pct=_number(own("builder_pop_pct"), 0.0, 100.0),
        builder_strikes_otm=_whole(own("builder_strikes_otm"), 0),
    )


def risk_free_rate(own: OptionDefaults) -> float:
    """The rate the Greeks of this user use: his own (Settings > Trade settings > Options > Risk-free rate, Advanced), else
    the operator's model input `RISK_FREE_RATE` (app/core/config.py; a model input, not an order value - M68Q-PR4-2)."""
    from app.core.config import RISK_FREE_RATE
    return own.risk_free_rate if own.risk_free_rate is not None else RISK_FREE_RATE


__all__ = ["OptionDefaults", "load", "needs_setting", "field_error", "label", "risk_free_rate", "NEEDS_PREFIX", "WHERE",
           "EXPIRY_RULES", "STRIKE_RULES", "POSITIONS"]
