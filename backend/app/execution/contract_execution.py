"""Phase F3: turning an underlying signal plus a resolved contract into an executable order.

The strategy said "LONG NIFTY 50 at 24512, stop 24460, target 24610". The deployment's rules
resolved that to "buy NIFTY 24500 CE 01 OCT 26" (F2). Everything downstream - risk engine, paper
broker, live router, order trail - works on a `Signal`, so this module builds the *order signal*
on the contract:

* **bought option**: entry = current premium, stop = premium floor (`premium_stop_pct` below),
  direction LONG. Risk per unit is therefore the premium at risk, and the risk engine sizes
  lots off it exactly as it sizes shares off a stop distance.
* **written option**: entry = premium received, stop = premium ceiling above, direction SHORT
  (P&L falls as the premium rises). Sizing is additionally capped by `max_lots` (default one
  lot) and, LIVE, by the broker's margin requirement per lot against available margin.
* **future**: entry = the future's price; stop and targets are the underlying's distances
  transplanted onto it, direction as signalled.

The strategy's own levels are kept on the trade as `underlying_*` so the position monitor can
exit on them (F4); the contract's price levels are what the broker-side SL-M and P&L use.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderRequest
from app.core.enums import AssetClass, InstrumentKind, OptionPosition, OrderSide, SignalDirection
from app.core.models import Signal
from app.instruments.contracts import ContractRules, ResolvedContract
from app.instruments.models import ContractSpec

logger = logging.getLogger(__name__)

MARGIN_SAFETY = 0.8          # never plan to use more than this share of available margin
DEFAULT_WRITE_MAX_LOTS = 1   # a written option with no explicit cap trades one lot


class ContractExecutionError(ValueError):
    """The contract cannot be executed right now (no premium quote, margin unknown) - the order
    trail records it as a rejection with this text."""


@dataclass
class OrderPlan:
    order_signal: Signal
    contract_spec: ContractSpec
    max_quantity: Optional[float]
    meta: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


async def contract_ltp(broker: BrokerInterface, contract: ResolvedContract) -> float:
    """The contract's last price. Upstox resolves by instrument key (`NSE_FO|...` passes through
    its symbol resolver untouched); other brokers by tradingsymbol on the derivatives exchange."""
    attempts = []
    if "|" in (contract.instrument_key or ""):
        attempts.append(contract.instrument_key)
    attempts.append(contract.tradingsymbol)
    last_error: Optional[Exception] = None
    for symbol in attempts:
        try:
            price = float(await broker.get_ltp_for_symbol(symbol, contract.exchange))
            if price > 0:
                return price
        except Exception as exc:  # noqa: BLE001 - try the next identifier
            last_error = exc
    raise ContractExecutionError(f"No quote for {contract.tradingsymbol} on {contract.exchange}" + (f": {last_error}" if last_error else ""))


def build_order_plan(signal: Signal, contract: ResolvedContract, rules: ContractRules, ltp: float) -> OrderPlan:
    if signal.entry is None or signal.stop_loss is None:
        raise ContractExecutionError("Underlying signal has no entry/stop to derive contract levels from")
    spec = ContractSpec(
        symbol=contract.tradingsymbol, exchange=contract.exchange, asset_class=AssetClass.INDEX_OPTION,
        description=f"{contract.underlying} {contract.kind.value.lower()} ({contract.exchange})",
        lot_size=float(contract.lot_size), tick_size=contract.tick_size,
    )
    meta = {
        "instrument_kind": contract.kind.value, "exchange": contract.exchange, "instrument_key": contract.instrument_key,
        "lot_size": contract.lot_size, "expiry": contract.expiry, "option_position": contract.position.value if contract.position else None,
        "premium_stop_pct": rules.premium_stop_pct if contract.kind == InstrumentKind.OPTION else None,
        "underlying_symbol": contract.underlying_symbol, "underlying_direction": signal.direction.value,
        "underlying_stop_loss": signal.stop_loss, "underlying_target1": signal.target1, "underlying_target2": signal.target2,
    }
    notes = [f"{contract.entry_side.value} {contract.tradingsymbol} (lot {contract.lot_size}) for {signal.direction.value} {contract.underlying_symbol} @ {signal.entry}"]
    notes.extend(contract.selection_notes)  # Phase H1: why this strike
    max_quantity = rules.max_lots * contract.lot_size if rules.max_lots else None

    if contract.kind == InstrumentKind.FUTURE:
        stop = round(ltp - (signal.entry - signal.stop_loss), 2)
        target1 = round(ltp + (signal.target1 - signal.entry), 2) if signal.target1 is not None else None
        target2 = round(ltp + (signal.target2 - signal.entry), 2) if signal.target2 is not None else None
        order_signal = signal.model_copy(update={
            "symbol": contract.tradingsymbol, "direction": contract.trade_direction, "entry": ltp,
            "stop_loss": stop, "target1": target1, "target2": target2,
            "reasons": list(signal.reasons) + [f"Future levels transplanted from {contract.underlying_symbol}: stop {stop}, target {target1}"],
        })
        return OrderPlan(order_signal, spec, max_quantity, meta, notes)

    pct = (rules.premium_stop_pct or 30.0) / 100.0
    if contract.position == OptionPosition.WRITE:
        ceiling = round(ltp * (1 + pct), 2)
        if max_quantity is None:
            max_quantity = DEFAULT_WRITE_MAX_LOTS * contract.lot_size
            notes.append(f"Written option without max_lots: capped at {DEFAULT_WRITE_MAX_LOTS} lot")
        order_signal = signal.model_copy(update={
            "symbol": contract.tradingsymbol, "direction": SignalDirection.SHORT, "entry": ltp,
            "stop_loss": ceiling, "target1": None, "target2": None,
            "reasons": list(signal.reasons) + [f"Premium received {ltp}; ceiling {ceiling} (+{pct:.0%}); exits on {contract.underlying_symbol} levels"],
        })
    else:
        floor = round(ltp * (1 - pct), 2)
        order_signal = signal.model_copy(update={
            "symbol": contract.tradingsymbol, "direction": SignalDirection.LONG, "entry": ltp,
            "stop_loss": floor, "target1": None, "target2": None,
            "reasons": list(signal.reasons) + [f"Premium paid {ltp}; floor {floor} (-{pct:.0%}); exits on {contract.underlying_symbol} levels"],
        })
    return OrderPlan(order_signal, spec, max_quantity, meta, notes)


async def written_lot_cap(broker: BrokerInterface, contract: ResolvedContract) -> Tuple[int, str]:
    """LIVE only: how many lots of this written option the account can carry - the broker's
    margin requirement for one lot against `MARGIN_SAFETY` of available margin. Unknown margin
    is a refusal, not a guess."""
    probe = BrokerOrderRequest(
        symbol=contract.instrument_key if "|" in (contract.instrument_key or "") else contract.tradingsymbol,
        exchange=contract.exchange, transaction_type=OrderSide.SELL, quantity=contract.lot_size, order_type="MARKET", product="MIS",
    )
    per_lot = await broker.get_order_margin(probe)
    if per_lot is None or per_lot <= 0:
        raise ContractExecutionError(f"{broker.name} did not report a margin requirement for writing {contract.tradingsymbol} - refused")
    margins = await broker.get_margins()
    available = float(margins.available_margin or margins.available_cash or 0.0)
    lots = int((available * MARGIN_SAFETY) // per_lot)
    if lots < 1:
        raise ContractExecutionError(
            f"Insufficient margin to write {contract.tradingsymbol}: {per_lot:,.0f} per lot needed, {available:,.0f} available"
        )
    return lots, f"Margin {per_lot:,.0f}/lot, {available:,.0f} available -> at most {lots} lot(s)"
