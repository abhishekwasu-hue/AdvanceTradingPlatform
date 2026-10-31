"""M68 PR-1 (owner order M68, TS-1 rows TS1-001 to 016): the user's own Order defaults, read for every order path.

The rule (M68, binding): ATP never pre-chooses a product, an order type, a holding, a price-protection band, a stop-limit
gap or a trigger. Each is the user's own value, in this order (the first one set wins):

1. the order's own value (a ticket field, a request body);
2. the bot's / strategy's own value (`strategy_deployments.holding` / `.order_style`, a proposal's evidence);
3. the user's Settings > Trade settings > Order defaults (app/me/trade_settings.py, prefs namespace `trade_settings`);
4. a value he saved earlier in an older store (TS-6, kept exactly): `trading_defaults.ticket.product` / `.order_type`
   (the chain ticket's own defaults) and `smart_execution.protection_band_pct` (only when stored).

Not set at all: ATP does not choose. A ticket ASKS for the field (TS-3: a field error the ticket shows at that field); a
bot / webhook / API / scan / approval entry places nothing and records "Needs your setting: <field>" (the order trail,
the bot card's wait reason and the notification bell show it).

Exits are never blocked (ADR-0004). An exit never reads this module for a value it needs: it closes the position with
the position's own product (`app.execution.products.product_for_trade` - the record of how it was opened) as a market
order. Where the broker takes no market order (a broker fact, TS1-094) the close goes as a marketable limit inside
`EXIT_CLOSE_BAND_PCT` unless the user saved his own band; where the exchange takes no SL-M on options (a fact, TS1-092)
a protective stop goes as SL with the user's gap, else `EXIT_STOP_LIMIT_BAND_PCT` (or the operator's STOP_LIMIT_BAND_PCT)
- both only so that closing a position can never wait for a setting (the TS-7 allow-list reason: "exit never
blocked").
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserPrefsRecord
from app.me import trade_settings

SECTION = "order_defaults"
WHERE = "Settings › Trade settings › Order defaults"
# Exit-only bands (allow-list, ADR-0004): used ONLY to close a position or to protect an open one when the user saved no
# band of his own and the broker / exchange takes no market / SL-M order. Never used for a new entry.
EXIT_CLOSE_BAND_PCT = 0.5        # == app.execution.marketable_limit.DEFAULT_PROTECTION_PCT (exit fallback, a test keeps them equal)
EXIT_STOP_LIMIT_BAND_PCT = 1.0   # == the 1 % gap of app.brokers.base.stop_order_params (exit fallback)
HOLDINGS = ("INTRADAY", "SWING")
PRODUCTS = ("MIS", "NRML")
TICKET_ORDER_TYPES = ("MARKET", "LIMIT", "SL", "SL-M")
BOT_ORDER_STYLES = ("MARKET", "PROTECTED_LIMIT")     # what an automated entry sends (app/execution/router.py ORDER_STYLES)
TRIGGER_MODES = ("TOUCH", "CLOSE")
TRIGGER_SOURCES = ("PREMIUM", "SPOT")

# The user's own stop-limit gap for the protective stop being placed now (None: not saved). Set around a placement by
# `stop_band`; read by app.brokers.base.BrokerInterface.stop_order_params (wrappers delegate to it, so they read it too).
STOP_BAND: ContextVar[float | None] = ContextVar("m68_stop_limit_band_pct", default=None)


def label(name: str) -> str:
    return trade_settings.BY_KEY[f"{SECTION}.{name}"].label


NEEDS_PREFIX = "Needs your setting: "


def needs_setting(name: str) -> str:
    """The plain-words skip / refusal reason for a missing Order default."""
    return f"{NEEDS_PREFIX}{label(name)} ({WHERE})"


@dataclass(frozen=True)
class OrderDefaults:
    """The user's own Order defaults; None = Not set."""
    holding: str | None = None
    product: str | None = None
    order_type: str | None = None
    price_protection_pct: float | None = None
    stop_limit_band_pct: float | None = None
    trigger_mode: str | None = None
    trigger_source: str | None = None

    @property
    def bot_order_style(self) -> str | None:
        """The order type as an automated entry sends it; a ticket-only type (LIMIT / SL / SL-M) is not one a signal
        entry can send at the signal's moment, so it reads as Not set for a bot (the bot form asks)."""
        return self.order_type if self.order_type in BOT_ORDER_STYLES else None

    @property
    def ticket_order_type(self) -> str | None:
        return self.order_type if self.order_type in TICKET_ORDER_TYPES else None


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


def _pct(value: Any, low: float = 0.0, high: float = 100.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if low < v <= high else None


async def load(session: AsyncSession, user: User | None) -> OrderDefaults:
    """The user's own Order defaults (Trade settings first, then a value he saved in an older store); all None for no user."""
    if user is None or getattr(user, "id", None) is None:
        return OrderDefaults()
    values = trade_settings.clean((await _raw(session, user, trade_settings.NAMESPACE)).get("values"))
    ticket = (await _raw(session, user, "trading_defaults")).get("ticket")
    ticket = ticket if isinstance(ticket, dict) else {}
    smart = await _raw(session, user, "smart_execution")

    def own(name: str) -> Any:
        return values.get(f"{SECTION}.{name}")

    return OrderDefaults(
        holding=_choice(own("holding"), HOLDINGS),
        product=_choice(own("product"), PRODUCTS) or _choice(ticket.get("product"), PRODUCTS),
        order_type=own("order_type") or _choice(ticket.get("order_type"), TICKET_ORDER_TYPES),
        price_protection_pct=_pct(own("price_protection_pct")) or _pct(smart.get("protection_band_pct"), 0.0, 5.0),
        stop_limit_band_pct=_pct(own("stop_limit_band_pct")),
        trigger_mode=_choice(own("trigger_mode"), TRIGGER_MODES),
        trigger_source=_choice(own("trigger_source"), TRIGGER_SOURCES),
    )


def holding_of_product(product: str | None) -> str | None:
    """The ticket's product as the position's holding (NRML carries forward); None stays None (never a guess)."""
    if product is None:
        return None
    return "SWING" if product.upper() == "NRML" else "INTRADAY"


@contextmanager
def stop_band(pct: float | None) -> Iterator[None]:
    """The user's own stop-limit gap for the protective stops placed inside this block (None: his gap is not saved)."""
    token = STOP_BAND.set(pct)
    try:
        yield
    finally:
        STOP_BAND.reset(token)


__all__ = ["OrderDefaults", "load", "needs_setting", "NEEDS_PREFIX", "label", "holding_of_product", "stop_band", "STOP_BAND", "WHERE",
           "EXIT_CLOSE_BAND_PCT", "EXIT_STOP_LIMIT_BAND_PCT", "HOLDINGS", "PRODUCTS", "TICKET_ORDER_TYPES", "BOT_ORDER_STYLES"]
