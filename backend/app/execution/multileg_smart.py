"""SE-7 (owner, 23 Oct): the multi-leg executor's legs on the smart-execution engine - LIVE and PAPER on one code path.

`execute_structure` (app/execution/multileg.py) calls `place_legs` when the flag `smart_execution` is on for the
organisation; the venue is the user's own broker adapter (LIVE, handed in by the trading worker) or the
PaperExecutionAdapter at the quoted premiums (PAPER, TM-1). Without the flag the old executor runs unchanged.

What changes against the old LIVE path (`multileg._place_live_legs`):
* the order is the engine's (`smart_execution.run`): every hedge (BUY) leg first, then the sold legs - the same order as
  before for every structure ATP builds (wings, then shorts);
* TM-11: a sold leg is sent only after every hedge is CONFIRMED filled in full in the broker's book (not just accepted);
  a partly filled leg counts as not filled;
* the first leg that does not fill stops the run, and **nothing is undone by itself**: the filled legs (a partial fill
  too, at its filled quantity) are real positions, booked, and the order is INCOMPLETE with "Complete the rest" / "Close
  the filled legs" for the user's tap (app/execution/incomplete_structures.py). The old path unwound the filled legs at
  once with market orders; now only the user's own "if I do not answer" setting does that, as an exit;
* where the broker takes no market order (TM-3), a marketable limit at the user's own protection band.

Every leg is still an ENTRY order (`intent`), so the Phase 0 lock's broker chokepoint refuses it while the lock holds;
this module never reads or changes `live_trading` or `phase0_paper_only`. The AI never calls it (ADR-0006).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.brokers.base import BrokerInterface
from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest
from app.compliance.algo_id import order_algo_id
from app.core.enums import ExecutionMode, OrderSide
from app.core.models import RiskConfig
from app.execution import smart_execution
from app.execution.tagging import LEG_ENTRY, build_order_tag

logger = logging.getLogger(__name__)

EPS = 1e-9


@dataclass
class LegSpec:
    """One leg to place: what the venue needs, and what the booking needs back. `key` is the leg's tradingsymbol."""
    key: str
    exchange: str
    side: str                      # BUY | SELL
    role: str                      # LONG (hedge / bought) | SHORT (sold)
    quantity: float
    index: int = 0                 # the leg's place in the structure (its order row `:L<index>`)
    instrument_key: Optional[str] = None
    ratio: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "exchange": self.exchange, "side": self.side, "role": self.role, "quantity": self.quantity,
                "index": self.index, "instrument_key": self.instrument_key, "ratio": self.ratio}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LegSpec":
        return cls(key=str(raw["key"]), exchange=str(raw.get("exchange") or "NFO"), side=str(raw["side"]).upper(),
                   role=str(raw.get("role") or ("LONG" if str(raw["side"]).upper() == "BUY" else "SHORT")),
                   quantity=float(raw["quantity"]), index=int(raw.get("index") or 0), instrument_key=raw.get("instrument_key"),
                   ratio=int(raw.get("ratio") or 1))


@dataclass
class LegFill:
    price: float
    quantity: float
    order_id: Optional[str]


@dataclass
class LegsRun:
    """What happened: COMPLETE / INCOMPLETE / NOTHING_PLACED, the fills by leg key (a partial fill too), the legs (and
    remainders) still to place, the first failure in words, and whether the broker's state is uncertain."""
    state: str
    fills: Dict[str, LegFill]
    pending: List[LegSpec]
    failure: str = ""
    notes: List[str] = field(default_factory=list)
    uncertain: Optional[str] = None
    leg_reasons: Dict[str, List[str]] = field(default_factory=dict)


async def place_legs(broker: BrokerInterface, specs: Sequence[LegSpec], *, strategy_id: str, tenant: Any,
                     band_pct: float, prices: Dict[str, float], live: bool, product: str = "MIS") -> LegsRun:
    """Places `specs` through `broker` with the engine: hedges first, no sold leg before every hedge is confirmed filled
    in full, the first leg that does not fill stops the run. Nothing is retried or undone here."""
    from app.execution.router import OrderRouter
    algo_id = order_algo_id(tenant.algo_id if tenant is not None else None, broker.name) if live else None
    max_tag = getattr(broker, "max_tag_length", None) or 20
    router = OrderRouter(mode=ExecutionMode.LIVE if live else ExecutionMode.PAPER, risk_config=RiskConfig(), broker=broker)
    market_ok = bool(getattr(getattr(broker, "capabilities", None), "market_orders_allowed", True))
    fills: Dict[str, LegFill] = {}
    notes: List[str] = []
    failure: Dict[str, str] = {}
    uncertain: List[str] = []

    async def place(index: int, seq: int):
        spec = specs[index]
        what = f"{spec.side} {spec.key}"
        order_type, limit = smart_execution.order_type_for(market_ok, spec.side, float(prices.get(spec.key) or 0.0), band_pct)
        if order_type == "LIMIT" and not prices.get(spec.key):
            return _stop(failure, f"{what}: no price for the protected limit order - not sent")
        request = BrokerOrderRequest(
            symbol=spec.key, exchange=spec.exchange, transaction_type=OrderSide.BUY if spec.side == "BUY" else OrderSide.SELL,
            quantity=spec.quantity, order_type=order_type, price=limit, product=product,
            tag=build_order_tag(strategy_id=strategy_id, leg=LEG_ENTRY, algo_id=algo_id, max_length=max_tag, broker=broker.name) if live else None,
            intent=ORDER_INTENT_ENTRY,
        )
        try:
            response = await broker.place_order(request)
        except Exception as exc:  # noqa: BLE001 - the broker may have taken it: the state is not known
            uncertain.append(f"{what}: the broker did not answer ({type(exc).__name__}: {exc})")
            return _stop(failure, f"{what} failed: {exc}")
        if (response.status or "").upper() in ("REJECTED", "CANCELLED") or not response.order_id:
            return _stop(failure, f"{what} rejected: {response.message or response.status}")
        price, filled, _ = await router._resolve_fill_with_status(response.order_id, fallback=0.0)
        if filled + EPS >= spec.quantity and price > 0:
            fills[spec.key] = LegFill(round(price, 2), float(spec.quantity), response.order_id)
            if live:
                notes.append(f"Live {spec.side} {spec.key} via {broker.name}: {response.order_id} filled @ {price:g}")
            return True, [], fills[spec.key]
        # Not filled in full inside the confirmation window: the rest is cancelled; a fill that lands meanwhile is a real
        # position and is booked (never orphaned), never undone by itself.
        confirmed, late_price, late_qty = await router._cancel_unfilled(response.order_id, fallback=price)
        got = max(float(late_qty), float(filled))
        at = late_price if late_qty > 0 else price
        if not confirmed:
            uncertain.append(f"{what}: the cancel of {response.order_id} is not confirmed in the broker's book")
        if got > EPS and at > 0:
            fills[spec.key] = LegFill(round(at, 2), min(got, float(spec.quantity)), response.order_id)
            return _stop(failure, f"{what} filled {got:g} of {spec.quantity:g} - the rest was cancelled",
                         result=fills[spec.key])
        return _stop(failure, f"{what} not filled within the confirmation window - cancelled")

    outcome = await smart_execution.run([s.side for s in specs], place)
    pending: List[LegSpec] = []
    for leg in outcome.legs:
        spec = specs[leg.index]
        got = fills[spec.key].quantity if spec.key in fills else 0.0
        if got + EPS < spec.quantity:
            pending.append(LegSpec(key=spec.key, exchange=spec.exchange, side=spec.side, role=spec.role,
                                   quantity=round(spec.quantity - got, 6), index=spec.index,
                                   instrument_key=spec.instrument_key, ratio=spec.ratio))
    reasons = {specs[leg.index].key: list(leg.reasons) for leg in outcome.legs if leg.reasons}
    state = outcome.state
    if state == smart_execution.NOTHING_PLACED and fills:
        state = smart_execution.INCOMPLETE          # the first leg part-filled: that part is a real position
    elif state == smart_execution.COMPLETE and pending:
        state = smart_execution.INCOMPLETE
    return LegsRun(state=state, fills=fills, pending=pending, failure=failure.get("text", ""), notes=notes,
                   uncertain="; ".join(uncertain) or None, leg_reasons=reasons)


def _stop(failure: Dict[str, str], text: str, *, result: Optional[LegFill] = None):
    failure.setdefault("text", text)
    return False, [text], result


def entry_specs(structure: Any, quantity: float) -> List[LegSpec]:
    """The structure's legs as specs, in the structure's own order (the engine orders them)."""
    return [LegSpec(key=leg.contract.tradingsymbol, exchange=leg.contract.exchange, side=leg.side.value.upper(), role=leg.role,
                    quantity=float(quantity * leg.ratio), index=i, instrument_key=leg.contract.instrument_key, ratio=int(leg.ratio))
            for i, leg in enumerate(structure.legs)]


__all__ = ["LegSpec", "LegFill", "LegsRun", "place_legs", "entry_specs"]
