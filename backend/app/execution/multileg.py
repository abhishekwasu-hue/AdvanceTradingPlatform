"""Phase H2: executing a multi-leg option structure as one position.

The single-leg pipeline (`execute_signal_for_user`) works on one Signal -> one order -> one
trade. A spread is several orders that only make sense together, so this executor:

1. applies the same entry refusals (kill switches, plan, algo id, broker-uncertain flag) and
   the same risk-engine day checks (daily loss, trade count, open positions, cool-down);
2. quotes every leg, computes the structure's economics (`structure_metrics`) and sizes in
   whole lots off **max loss** - `risk per trade / max loss per lot` - capped by `max_lots` and,
   LIVE, by the broker's margin for the short legs (unknown margin = refusal, as in F3);
3. opens one OrderRecord per leg (idempotency `<key>:L<i>`) sharing a `leg_group_id`;
4. PAPER: fills each leg at its premium with the paper slippage. LIVE: places the protective
   long wings first (so the shorts are never naked, and the broker gives the spread margin),
   then the shorts; if any leg fails after another filled, the filled legs are unwound with
   market orders, every order ends FAILED, and the tenant is flagged broker-uncertain;
5. persists one TradeRecord per leg with the group's metrics, and raises one ENTRY notification.

Exits are the position monitor's job (`app/trading/position_monitor.py::_monitor_group`): the
legs are judged together on the spread's value, the short strikes and the square-off time.
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.brokers.circuit_breaker import breaker_for
from app.brokers.models import BrokerOrderRequest
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OrderSide, OrderStatus, SignalDirection
from app.core.models import RiskConfig, Signal, Trade
from app.db.models import OrderRecord, Tenant, TradeRecord, User
from app.execution.contract_execution import MARGIN_SAFETY, ContractExecutionError, contract_ltp
from app.execution.order_persistence import create_order, transition_order
from app.execution.paper_broker import PaperBroker
from app.execution.router import OrderRouter
from app.execution.signal_execution import entry_refusals
from app.execution.tagging import LEG_ENTRY, LEG_EXIT, build_order_tag
from app.instruments.contracts import ContractResolutionError, ContractRules
from app.instruments.models import ContractSpec
from app.core.enums import AssetClass
from app.instruments.spreads import ResolvedStructure, StructureMetrics, structure_metrics
from app.notifications.service import notify
from app.observability.metrics import ORDERS
from app.reconciliation.service import mark_broker_uncertain
from app.risk_engine.risk_manager import RiskManager
from app.risk_engine.routes import get_tenant_risk_config
from app.trading.persistence import build_trading_day_state, persist_trade

logger = logging.getLogger(__name__)


@dataclass
class StructureResult:
    executed: bool
    reasons: List[str]
    orders: List[OrderRecord] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    leg_group_id: Optional[str] = None
    metrics: Optional[StructureMetrics] = None
    system_failure: bool = False


async def _reject_all(session: AsyncSession, orders: List[OrderRecord], reasons: List[str], *, failed: bool = False) -> None:
    for order in orders:
        if order.status in (OrderStatus.REJECTED.value, OrderStatus.FAILED.value):
            continue
        order.reasons_json = json.dumps(reasons)
        await transition_order(session, order, OrderStatus.FAILED if failed else OrderStatus.REJECTED, detail="; ".join(reasons)[:500])
        ORDERS.labels(mode=order.mode, status=order.status).inc()


async def execute_structure(
    session: AsyncSession, user: User, *, mode: str, strategy_id: str, signal: Signal, structure: ResolvedStructure,
    rules: ContractRules, target_credit_pct: Optional[float], stop_credit_pct: Optional[float],
    idempotency_key: str, broker: Optional[BrokerInterface] = None, quote_broker: Optional[BrokerInterface] = None,
    deployment_id: Optional[int] = None, risk_config: Optional[RiskConfig] = None,
) -> StructureResult:
    started = time.perf_counter()
    tenant = await session.get(Tenant, user.tenant_id)
    execution_mode = ExecutionMode(mode)
    price_source = quote_broker or broker

    # One order per leg, up front, so the trail shows the whole structure from the first event.
    orders: List[OrderRecord] = []
    leg_signals: List[Signal] = []
    for i, leg in enumerate(structure.legs):
        leg_signal = signal.model_copy(update={
            "symbol": leg.contract.tradingsymbol, "direction": leg.contract.trade_direction,
            "entry": None, "stop_loss": None, "target1": None, "target2": None, "risk_reward": None,
            "reasons": list(signal.reasons) + [f"{structure.strategy.value} leg {i + 1}/{len(structure.legs)}: {leg.side.value} {leg.contract.tradingsymbol}"],
        })
        order, created = await create_order(session, user, mode=mode, strategy_id=strategy_id, signal=leg_signal,
                                            idempotency_key=f"{idempotency_key}:L{i}")
        if not created:
            return StructureResult(executed=order.status == OrderStatus.POSITION_OPEN.value,
                                   reasons=[f"Structure already handled for this signal (order {order.id} {order.status})"], orders=[order])
        order = await transition_order(session, order, OrderStatus.VALIDATING, detail="Structure leg received")
        orders.append(order)
        leg_signals.append(leg_signal)

    refusals = await entry_refusals(session, tenant, user.tenant_id, mode, strategy_id)
    if refusals:
        await _reject_all(session, orders, refusals)
        await notify(session, user.tenant_id, NotificationType.REJECTION, title=f"Structure rejected: {structure.underlying_symbol}",
                     message="; ".join(refusals), severity=NotificationSeverity.WARNING, user_id=user.id, related_order_id=orders[0].id)
        return StructureResult(executed=False, reasons=refusals, orders=orders)

    if execution_mode == ExecutionMode.LIVE and broker is None:
        reasons = ["Live trading not configured: no authenticated broker adapter for this tenant"]
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders)

    # Quotes -> economics.
    premiums: Dict[str, float] = {}
    try:
        if price_source is None:
            raise ContractExecutionError("No broker session to quote the legs from")
        for leg in structure.legs:
            premiums[leg.contract.tradingsymbol] = await contract_ltp(price_source, leg.contract)
        metrics = structure_metrics(structure, premiums, target_credit_pct=target_credit_pct, stop_credit_pct=stop_credit_pct)
    except (ContractExecutionError, ContractResolutionError) as exc:
        reasons = [str(exc)]
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders)

    for order in orders:
        await transition_order(session, order, OrderStatus.RISK_CHECK, detail="Running risk checks")

    # Sizing: the risk engine's day checks plus lots off max loss. A synthetic signal whose
    # entry-to-stop distance *is* the max loss per unit makes `risk / max_loss` the quantity.
    cfg = risk_config or await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    state = await build_trading_day_state(session, user)
    lot = structure.lot_size
    spec = ContractSpec(symbol=structure.underlying_symbol, exchange=structure.legs[0].contract.exchange, asset_class=AssetClass.INDEX_OPTION,
                        description=f"{structure.strategy.value} on {structure.underlying_symbol}", lot_size=float(lot), tick_size=0.05)
    sizing_signal = signal.model_copy(update={
        "symbol": structure.underlying_symbol, "direction": SignalDirection.SHORT, "entry": metrics.net_credit,
        "stop_loss": round(metrics.net_credit + metrics.max_loss, 2), "target1": None, "target2": None, "risk_reward": None,
    })
    decision = RiskManager(cfg).validate_and_size(sizing_signal, state, contract_spec=spec)
    notes = list(structure.notes) + [
        f"Net credit {metrics.net_credit:g}/unit, max loss {metrics.max_loss:g}/unit ({metrics.max_loss * lot:,.0f}/lot), "
        f"breakeven {', '.join(f'{b:g}' for b in metrics.breakevens)}; exit at value <= {metrics.target_value:g} or >= {metrics.stop_value:g}",
    ]
    if not decision.approved:
        reasons = notes + decision.reasons
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)
    lots = int(decision.quantity // lot)
    if lots < 1:
        reasons = notes + [f"Max loss per lot ({metrics.max_loss * lot:,.0f}) exceeds risk per trade ({cfg.capital * cfg.risk_per_trade_pct / 100:,.0f})"]
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)
    if rules.max_lots:
        lots = min(lots, rules.max_lots)
    if execution_mode == ExecutionMode.LIVE:
        try:
            cap, note = await _live_lot_cap(broker, structure)
        except ContractExecutionError as exc:
            reasons = notes + [str(exc)]
            await _reject_all(session, orders, reasons)
            return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)
        notes.append(note)
        lots = min(lots, cap)
    quantity = lots * lot
    notes.append(f"{lots} lot(s) x {lot} = {quantity} per leg")

    for order in orders:
        order.quantity = quantity

    group_id = uuid.uuid4().hex
    group_meta = metrics.as_dict()
    group_meta.update({"lots": lots, "quantity": quantity, "underlying_symbol": structure.underlying_symbol})
    fills: Dict[str, Tuple[float, Optional[str]]] = {}

    if execution_mode == ExecutionMode.PAPER:
        paper = PaperBroker()
        for leg in structure.legs:
            price = premiums[leg.contract.tradingsymbol]
            fills[leg.contract.tradingsymbol] = (round(paper._slip(price, leg.contract.trade_direction), 2), None)
        notes.append("Paper fills at the quoted premiums")
    else:
        breaker = breaker_for(broker.name)
        if not breaker.allow_submission():
            reasons = notes + [breaker.refusal_reason()]
            await _reject_all(session, orders, reasons)
            return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)
        placed_ok, failure = await _place_live_legs(broker, structure, quantity, strategy_id, tenant, fills, notes)
        if not placed_ok:
            reasons = notes + [failure]
            await _reject_all(session, orders, reasons, failed=True)
            if tenant is not None:
                await mark_broker_uncertain(session, tenant, f"structure {group_id[:8]} FAILED: {failure}", user_id=user.id)
                await session.commit()
            await notify(session, user.tenant_id, NotificationType.SYSTEM_FAILURE, title=f"Structure failed: {structure.underlying_symbol}",
                         message=failure[:1000], severity=NotificationSeverity.CRITICAL, user_id=user.id, related_order_id=orders[0].id)
            return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics, system_failure=True)

    # Book every leg.
    trades: List[TradeRecord] = []
    latency_ms = int((time.perf_counter() - started) * 1000)
    now = datetime.now(timezone.utc)
    for order, leg in zip(orders, structure.legs):
        fill_price, broker_order_id = fills[leg.contract.tradingsymbol]
        expected = premiums[leg.contract.tradingsymbol]
        # Per-leg levels are informational: the group exit is what closes the position.
        stop = round(fill_price * (2.0 if leg.role == "SHORT" else 0.0), 2)
        trade = Trade(symbol=leg.contract.tradingsymbol, strategy_id=strategy_id, direction=leg.contract.trade_direction,
                      entry_time=now, entry_price=fill_price, quantity=quantity, stop_loss=stop,
                      expected_price=expected, entry_latency_ms=latency_ms)
        if broker_order_id:
            order.broker_order_id = broker_order_id
        venue = broker.name if execution_mode == ExecutionMode.LIVE and broker is not None else "paper broker"
        order = await transition_order(session, order, OrderStatus.SUBMITTED, detail=f"Submitted to {venue}")
        order = await transition_order(session, order, OrderStatus.PENDING, detail="Awaiting fill")
        order = await transition_order(session, order, OrderStatus.FILLED, detail=f"{'Paper' if venue == 'paper broker' else 'Live'} fill {broker_order_id or ''}".strip())
        ORDERS.labels(mode=mode, status="FILLED").inc()
        meta = {
            "instrument_kind": "OPTION", "exchange": leg.contract.exchange, "instrument_key": leg.contract.instrument_key,
            "lot_size": lot, "expiry": leg.contract.expiry, "option_position": "WRITE" if leg.role == "SHORT" else "BUY",
            "underlying_symbol": structure.underlying_symbol, "underlying_direction": signal.direction.value,
            "underlying_stop_loss": signal.stop_loss, "underlying_target1": signal.target1, "underlying_target2": signal.target2,
            "leg_group_id": group_id, "leg_role": leg.role, "option_strategy": structure.strategy.value, "group_meta": json.dumps(group_meta),
        }
        record = await persist_trade(session, user, trade, mode=mode, broker_order_id=broker_order_id, deployment_id=deployment_id, contract_meta=meta)
        order.trade_id = record.id
        order.reasons_json = json.dumps(notes)
        await transition_order(session, order, OrderStatus.POSITION_OPEN, detail=f"Position opened (trade #{record.id}, group {group_id[:8]})")
        trades.append(record)

    await notify(
        session, user.tenant_id, NotificationType.ENTRY,
        title=f"{structure.strategy.value.replace('_', ' ').title()} opened on {structure.underlying_symbol}",
        message="; ".join(notes)[:1000], severity=NotificationSeverity.INFO, user_id=user.id, related_trade_id=trades[0].id,
    )
    logger.info("Structure %s opened: %s legs, %s lots, group %s", structure.strategy.value, len(trades), lots, group_id[:8])
    return StructureResult(executed=True, reasons=notes, orders=orders, trades=trades, leg_group_id=group_id, metrics=metrics)


async def _live_lot_cap(broker: BrokerInterface, structure: ResolvedStructure) -> Tuple[int, str]:
    """Lots the account's margin allows: the broker's requirement for one lot of every short
    leg (no spread benefit assumed - conservative) against MARGIN_SAFETY of available margin."""
    per_lot = 0.0
    for leg in structure.short_legs:
        probe = BrokerOrderRequest(
            symbol=leg.contract.instrument_key if "|" in (leg.contract.instrument_key or "") else leg.contract.tradingsymbol,
            exchange=leg.contract.exchange, transaction_type=OrderSide.SELL, quantity=structure.lot_size, order_type="MARKET", product="MIS",
        )
        margin = await broker.get_order_margin(probe)
        if margin is None or margin <= 0:
            raise ContractExecutionError(f"{broker.name} did not report a margin requirement for {leg.contract.tradingsymbol} - refused")
        per_lot += float(margin)
    margins = await broker.get_margins()
    available = float(margins.available_margin or margins.available_cash or 0.0)
    lots = int((available * MARGIN_SAFETY) // per_lot) if per_lot > 0 else 0
    if lots < 1:
        raise ContractExecutionError(f"Insufficient margin for the short legs: {per_lot:,.0f} per lot needed, {available:,.0f} available")
    return lots, f"Margin {per_lot:,.0f}/lot for the short legs, {available:,.0f} available -> at most {lots} lot(s)"


async def _place_live_legs(
    broker: BrokerInterface, structure: ResolvedStructure, quantity: float, strategy_id: str, tenant: Optional[Tenant],
    fills: Dict[str, Tuple[float, Optional[str]]], notes: List[str],
) -> Tuple[bool, str]:
    """Wings first, then shorts. Any failure unwinds what filled. Returns (ok, failure_text)."""
    algo_id = tenant.algo_id if tenant is not None else None
    max_tag = getattr(broker, "max_tag_length", None) or 20
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=RiskConfig(), broker=broker)
    placed: List = []
    ordered = structure.long_legs + structure.short_legs
    for leg in ordered:
        request = BrokerOrderRequest(
            symbol=leg.contract.tradingsymbol, exchange=leg.contract.exchange, transaction_type=leg.side,
            quantity=quantity, order_type="MARKET", product="MIS",
            tag=build_order_tag(strategy_id=strategy_id, leg=LEG_ENTRY, algo_id=algo_id, max_length=max_tag),
        )
        try:
            response = await broker.place_order(request)
            if response.status.upper() in ("REJECTED", "CANCELLED"):
                raise ContractExecutionError(f"{leg.side.value} {leg.contract.tradingsymbol} rejected: {response.message or response.status}")
        except Exception as exc:  # noqa: BLE001
            failure = f"{leg.side.value} {leg.contract.tradingsymbol} failed: {exc}"
            unwound = await _unwind(broker, placed, quantity, strategy_id, algo_id, max_tag)
            return False, failure + (f"; unwound {unwound} filled leg(s)" if placed else "")
        placed.append(leg)
        price, _ = await router._resolve_fill(response.order_id, fallback=0.0)
        fills[leg.contract.tradingsymbol] = (round(price, 2), response.order_id)
        notes.append(f"Live {leg.side.value} {leg.contract.tradingsymbol} via {broker.name}: {response.order_id}")
    return True, ""


async def _unwind(broker: BrokerInterface, placed: List, quantity: float, strategy_id: str, algo_id: Optional[str], max_tag: int) -> int:
    done = 0
    for leg in reversed(placed):
        try:
            await broker.place_order(BrokerOrderRequest(
                symbol=leg.contract.tradingsymbol, exchange=leg.contract.exchange,
                transaction_type=OrderSide.SELL if leg.side == OrderSide.BUY else OrderSide.BUY,
                quantity=quantity, order_type="MARKET", product="MIS",
                tag=build_order_tag(strategy_id=strategy_id, leg=LEG_EXIT, algo_id=algo_id, max_length=max_tag),
            ))
            done += 1
        except Exception as exc:  # noqa: BLE001 - reported; reconciliation resolves the rest
            logger.error("Unwind of %s failed: %s", leg.contract.tradingsymbol, exc)
    return done
