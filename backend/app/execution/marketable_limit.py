"""TM-3 (owner decision 23 Oct 2026): a MARKET order where the broker does not take one from an API / algo client.

Kotak Neo's Trade API page says market orders are not allowed for retail algo trading and recommends limit orders. Where
a broker's `BrokerCapabilities.market_orders_allowed` is False, a MARKET order goes as a **marketable LIMIT**: the limit
a protection band past the reference price (above it for a buy, below it for a sell), on the tick. The band is the
user's own setting (the deployment's `market_protection_pct`, carried on the order as `protection_pct`); with none set
the platform's existing stated default for a protected limit (`DEFAULT_PROTECTION_PCT`, the same 0.5% as
app/execution/router.py's PROTECTED_LIMIT) applies. No other rule is added: nothing here decides a quantity, a price
level or whether to trade.

Owner principle "same code path for PAPER and LIVE": `to_venue_order` is the one transform - the Kotak Neo adapter
applies it before an order leaves for the broker, and the PAPER adapter applies the same function when it practises for
a venue that does not take market orders (`PaperExecutionAdapter(venue=...)`), so a practice fill and a real one see
the same order type and limit.

Exits are never blocked (ADR-0004): a marketable limit that the market moves away from is re-priced from a fresh
reference (`reprice_until_filled`) and never cancelled or refused here; after the last attempt the order stays working
at the broker and the caller is told so.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from app.brokers.models import BrokerOrderRequest
from app.core.enums import OrderSide

DEFAULT_PROTECTION_PCT = 0.5     # == app.execution.router.DEFAULT_MARKET_PROTECTION_PCT (a test keeps them equal)
DEFAULT_TICK = 0.05
FILLED = ("COMPLETE", "FILLED", "TRADED", "EXECUTED")
ENDED = ("REJECTED", "CANCELLED", "CANCELED", "EXPIRED", "LAPSED")


def band_pct(order: BrokerOrderRequest, default: float = DEFAULT_PROTECTION_PCT) -> float:
    """The protection band for this order: its own `protection_pct` when set and positive, else `default`."""
    value = order.protection_pct
    if value is None or isinstance(value, bool):
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number > 0 else float(default)


def marketable_limit_price(reference: float, side: OrderSide, protection_pct: float, tick: float = DEFAULT_TICK) -> float:
    """The limit `protection_pct` past `reference` - above for a buy, below for a sell - rounded OUTWARD to the tick (up
    for a buy, down for a sell), so rounding never shrinks the room the order has to fill, and never below one tick."""
    if reference is None or not math.isfinite(float(reference)) or float(reference) <= 0:
        raise ValueError("a marketable limit needs a positive reference price")
    tick = float(tick) if tick and tick > 0 else DEFAULT_TICK
    band = max(0.0, float(protection_pct)) / 100.0
    decimals = max(2, len(f"{tick:.10f}".rstrip("0").split(".")[1]))
    if side == OrderSide.BUY:
        steps = math.ceil(round(float(reference) * (1 + band) / tick, 6))
    else:
        steps = math.floor(round(float(reference) * (1 - band) / tick, 6))
    return round(max(tick, steps * tick), decimals)


def needs_limit(order: BrokerOrderRequest, capabilities: Any) -> bool:
    """True when this order is a MARKET order and the venue does not take market orders."""
    return (order.order_type or "MARKET").upper() == "MARKET" and not bool(getattr(capabilities, "market_orders_allowed", True))


def to_venue_order(order: BrokerOrderRequest, capabilities: Any, reference: Optional[float], *, tick: float = DEFAULT_TICK,
                   default_band: float = DEFAULT_PROTECTION_PCT) -> Tuple[BrokerOrderRequest, str]:
    """(the order the venue takes, a plain note). A MARKET order at a venue without market orders becomes a LIMIT at
    `marketable_limit_price`; every other order is returned unchanged with an empty note. Raises ValueError when a
    limit is needed and there is no usable reference price (the caller reads one first; nothing is guessed)."""
    if not needs_limit(order, capabilities):
        return order, ""
    pct = band_pct(order, default_band)
    limit = marketable_limit_price(float(reference) if reference is not None else 0.0, order.transaction_type, pct, tick)
    return (order.model_copy(update={"order_type": "LIMIT", "price": limit}),
            f"Sent as a limit order at {limit} ({pct:g}% protection band): this broker does not take market orders from an API.")


@dataclass
class RepriceResult:
    order_id: str
    status: str                       # the last status read: COMPLETE, OPEN, REJECTED, ...
    filled: bool
    limits: List[float] = field(default_factory=list)   # every limit sent, the first one included
    note: str = ""


async def reprice_until_filled(adapter: Any, order_id: str, order: BrokerOrderRequest, first_limit: float,
                               reference: Callable[[], Awaitable[Optional[float]]], *, attempts: int, wait_seconds: float,
                               tick: float = DEFAULT_TICK, default_band: float = DEFAULT_PROTECTION_PCT,
                               sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> RepriceResult:
    """Follow a marketable limit until it fills: after each `wait_seconds` read the order book; still working -> a
    fresh reference and a new limit the same band past it (`modify_order`). `attempts` and `wait_seconds` come from the
    caller's settings. Never cancels and never refuses (an exit is never blocked): a missing reference or a failed
    modify is skipped and tried again on the next attempt; after the last attempt the order is left working and the
    result says so. Nothing is placed here."""
    pct = band_pct(order, default_band)
    result = RepriceResult(order_id=order_id, status="OPEN", filled=False, limits=[first_limit])
    for _ in range(max(0, int(attempts))):
        await sleep(max(0.0, float(wait_seconds)))
        status, filled_qty = await _status_of(adapter, order_id)
        result.status = status
        if status in FILLED or (filled_qty is not None and filled_qty + 1e-9 >= float(order.quantity)):
            result.filled, result.status = True, "COMPLETE"
            return result
        if status in ENDED:
            result.note = f"The broker ended the order ({status.lower()})."
            return result
        try:
            ref = await reference()
            limit = marketable_limit_price(float(ref) if ref is not None else 0.0, order.transaction_type, pct, tick)
        except (ValueError, TypeError):
            continue                                     # no price this time: try again on the next attempt
        except Exception:  # noqa: BLE001 - a failed price read never stops the follow-up
            continue
        if limit == result.limits[-1]:
            continue
        try:
            await adapter.modify_order(order_id, price=limit)
            result.limits.append(limit)
        except Exception:  # noqa: BLE001 - the order keeps working at its last limit; the next attempt tries again
            continue
    status, filled_qty = await _status_of(adapter, order_id)
    result.status = status
    if status in FILLED or (filled_qty is not None and filled_qty + 1e-9 >= float(order.quantity)):
        result.filled, result.status = True, "COMPLETE"
    elif status not in ENDED:
        result.note = "The order is still working at the broker at its last limit; it was not cancelled."
    return result


async def _status_of(adapter: Any, order_id: str) -> Tuple[str, Optional[float]]:
    try:
        book = await adapter.get_order_book()
    except Exception:  # noqa: BLE001 - unknown is treated as still working
        return "OPEN", None
    for row in book or []:
        if str(getattr(row, "order_id", "")) == str(order_id):
            return str(getattr(row, "status", "OPEN") or "OPEN").upper(), float(getattr(row, "filled_quantity", 0) or 0)
    return "OPEN", None
