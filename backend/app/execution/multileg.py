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
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.brokers.circuit_breaker import breaker_for
from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest
from app.compliance import fo_limits
from app.compliance.algo_id import order_algo_id
from app.core import config
from app.core.enums import AssetClass, ExecutionMode, NotificationSeverity, NotificationType, OrderSide, OrderStatus, SignalDirection
from app.core.models import RiskConfig, Signal, Trade
from app.db.models import OrderRecord, Tenant, TradeRecord, User
from app.execution import approval as approval_gate
from app.execution.contract_execution import MARGIN_SAFETY, ContractExecutionError, _is_upstox, contract_ltp
from app.execution.multileg_smart import LegsRun, entry_specs, place_legs
from app.execution.order_persistence import create_order, transition_order
from app.execution.order_safety import wing_fill_complete
from app.execution.paper_adapter import PaperExecutionAdapter
from app.execution.paper_broker import PaperBroker
from app.execution.router import OrderRouter
from app.execution.signal_execution import entry_refusals
from app.execution.tagging import LEG_ENTRY, LEG_EXIT, build_order_tag, client_order_id, new_client_order_id
from app.instruments.contracts import ContractResolutionError, ContractRules
from app.instruments.models import ContractSpec
from app.instruments.spreads import ResolvedStructure, StructureMetrics, can_hold_for_days, structure_metrics
from app.notifications.service import notify
from app.observability.metrics import ORDERS
from app.platform.controls import clamp_config, risk_ceilings
from app.reconciliation.service import mark_broker_uncertain
from app.risk_engine import by_default
from app.risk_engine.guardian import GuardianVerdict, apply_multiplier, guard_entry, portfolio_risk_block
from app.risk_engine.hierarchy import RiskContext
from app.risk_engine.hierarchy import evaluate as evaluate_hierarchy
from app.risk_engine.risk_manager import RiskManager, nonfinite_levels
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
    # SE-7 (flag smart_execution): "COMPLETE" / "INCOMPLETE" / "NOTHING_PLACED" on the engine's path, None on the old one.
    state: Optional[str] = None
    # M64 RM-3: a Risk limits refusal as data (app/risk_engine/risk_limits.block) for the block message.
    blocks: list[dict] = field(default_factory=list)

    @property
    def keeps_entry_slot(self) -> bool:
        """An INCOMPLETE structure opened real positions (its filled legs): the day's entry slot stays taken."""
        return bool(self.trades)


async def _reject_all(session: AsyncSession, orders: List[OrderRecord], reasons: List[str], *, failed: bool = False) -> None:
    for order in orders:
        if order.status in (OrderStatus.REJECTED.value, OrderStatus.FAILED.value):
            continue
        order.reasons_json = json.dumps(reasons)
        await transition_order(session, order, OrderStatus.FAILED if failed else OrderStatus.REJECTED, detail="; ".join(reasons)[:500])
        ORDERS.labels(mode=order.mode, status=order.status).inc()


@by_default.releases_unused_entry_slot
async def execute_structure(
    session: AsyncSession, user: User, *, mode: str, strategy_id: str, signal: Signal, structure: ResolvedStructure,
    rules: ContractRules, target_credit_pct: Optional[float], stop_credit_pct: Optional[float],
    idempotency_key: str, broker: Optional[BrokerInterface] = None, quote_broker: Optional[BrokerInterface] = None,
    deployment_id: Optional[int] = None, risk_config: Optional[RiskConfig] = None, account_id: Optional[int] = None,
    underlying_exit: bool = False, holding: str | None = None,
    origin: str = approval_gate.ORIGIN_AUTOMATIC, approval_action_id: int | None = None,
) -> StructureResult:
    """M55 T2: `origin` / `approval_action_id` as on `execute_signal_for_user` - "approval" with the id of an APPROVED
    entry proposal (app/execution/approval.py::execute_entry) is the only way a structure passes approval-only mode;
    the default is the refusing "automatic"."""
    started = time.perf_counter()
    # M68 PR-1 (TS1-008): the holding is the bot's / proposal's, else the user's Trade settings; Not set = refused below.
    from app.execution import order_defaults
    holding = holding or (await order_defaults.load(session, user)).holding
    # M55 T1: only a defined-risk credit structure is held for days (SWING); M68 PR-5 (TS1-041): a written straddle /
    # strangle too when the user switched that on. Anything else stays INTRADAY.
    from app.risk_engine import own_risk
    written = (await own_risk.for_user(session, user)).overnight_short_options if holding == "SWING" else False
    held = holding == "SWING" and can_hold_for_days(structure.strategy, written_overnight=written)
    product = "NRML" if held else "MIS"
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

    refusals = await entry_refusals(session, tenant, user.tenant_id, mode, strategy_id, user=user,
                                    broker_name=getattr(broker, "name", None) if broker is not None else None)
    # Z3 / BT2 + M55 T2: under approval-only mode a structure passes only as an APPROVED entry proposal's execution
    # (origin "approval" with its id, checked against the database); every other caller is refused.
    gate = await approval_gate.gate_refusal(session, user.tenant_id, deployment_id=deployment_id,
                                            origin=origin if origin == approval_gate.ORIGIN_APPROVAL else approval_gate.ORIGIN_AUTOMATIC,
                                            approval_action_id=approval_action_id if origin == approval_gate.ORIGIN_APPROVAL else None)
    if gate:
        refusals.insert(0, gate)
    ban, _ = await fo_limits.entry_refusal(session, signal.symbol, signal.timestamp)   # D7: F&O ban on the underlying
    if ban:
        refusals.append(ban)
    # N2-2 (flag experience_levels): an unhedged short side or a futures leg needs the level, or the day's opt-out.
    from app.paper import experience
    refusals.extend(await experience.entry_refusals(session, user, experience.structure_gates(structure.legs)))
    # #309 review: the underlying levels are stored on every leg's trade after its fill; refuse them before any leg moves.
    bad = nonfinite_levels(signal)
    if bad:
        refusals.append(f"Underlying signal {bad} is not a finite number")
    if holding is None:
        refusals.append(order_defaults.needs_setting("holding"))
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
        metrics = structure_metrics(structure, premiums, target_credit_pct=target_credit_pct, stop_credit_pct=stop_credit_pct,
                                    underlying_exit=underlying_exit)
    except (ContractExecutionError, ContractResolutionError) as exc:
        reasons = [str(exc)]
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders)

    for order in orders:
        await transition_order(session, order, OrderStatus.RISK_CHECK, detail="Running risk checks")

    # Sizing: the risk engine's day checks plus lots off max loss. A synthetic signal whose
    # entry-to-stop distance *is* the max loss per unit makes `risk / max_loss` the quantity.
    if risk_config is not None:
        cfg = risk_config
    else:                                    # DEF-0: limits not set are off; a % reads the user's PAPER balance
        from app.risk_engine.by_default import capital_basis
        cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
        if cfg.capital is None:
            cfg = cfg.with_capital(await capital_basis(session, user))
    # Phase V1: ceilings, then the guardian's entry rules (cool-down, drawdown ladder, events).
    cfg, guardian_notes = clamp_config(cfg, await risk_ceilings(session, user.tenant_id))
    state = await build_trading_day_state(session, user)
    daily_amount = cfg.daily_loss_amount()
    daily_limit = -daily_amount if daily_amount is not None else -math.inf
    # A breached daily-loss limit outranks the guardian (the risk engine's refusal engages the kill switch).
    if mode == ExecutionMode.PAPER.value and await by_default.enabled(session, user.tenant_id):
        # Z5 / P1-d: the user's own PAPER risk cap as well as the organisation's (strictest wins), before the guardian.
        cfg, cap_notes = await by_default.apply_user_cap(session, user, cfg)
        guardian_notes.extend(cap_notes)
    guard = await guard_entry(session, user.tenant_id, mode=mode, underlying=structure.underlying_symbol, cfg=cfg,
                              # no user_id: stored and notified org-wide - number-free VIX wording (BT1b)
                              direction=signal.direction.value, owner_id=user.id) if state.daily_pnl > daily_limit else GuardianVerdict()
    if guard.reasons:
        await _reject_all(session, orders, guard.reasons)
        await notify(session, user.tenant_id, NotificationType.RISK_REJECTION, title=f"Structure refused by the risk guardian: {structure.underlying_symbol}",
                     message="; ".join(guard.reasons), severity=NotificationSeverity.WARNING, user_id=user.id, related_order_id=orders[0].id)
        return StructureResult(executed=False, reasons=guard.reasons, orders=orders, metrics=metrics)
    cfg = apply_multiplier(cfg, guard.size_multiplier)
    guardian_notes.extend(guard.notes)
    lot = structure.lot_size
    spec = ContractSpec(symbol=structure.underlying_symbol, exchange=structure.legs[0].contract.exchange, asset_class=AssetClass.INDEX_OPTION,
                        description=f"{structure.strategy.value} on {structure.underlying_symbol}", lot_size=float(lot), tick_size=0.05)
    # Phase R: `risk_per_unit` is the max loss of a defined-risk credit structure, the debit of a
    # debit structure, or the stop distance of an undefined-risk short straddle/strangle.
    # A ratio spread can be entered for (almost) nothing: the sizer still needs an entry > 0.
    # M64 RM-4: the structure is judged as ONE position - its max loss is the risk per unit (a credit spread with its
    # hedge leg is defined-risk and never reads as a naked short). An undefined-risk structure with no stop carries the
    # "no stop" 0, so risk-based sizing falls back to the trader's own lots with a warning, never a refusal.
    basis = abs(metrics.net_credit) or 0.05
    sizing_signal = signal.model_copy(update={
        "symbol": structure.underlying_symbol, "direction": SignalDirection.SHORT, "entry": basis,
        "stop_loss": round(basis + metrics.risk_per_unit, 2) if metrics.risk_per_unit > 0 else 0.0,
        "target1": None, "target2": None, "risk_reward": None,
    })
    decision = RiskManager(cfg).validate_and_size(sizing_signal, state, contract_spec=spec,
                                                  requested_quantity=float(rules.max_lots * lot) if rules.max_lots else None)
    notes = list(structure.notes) + [_economics_note(metrics, lot)] + guardian_notes + list(decision.warnings)
    if not decision.approved:
        reasons = notes + decision.reasons
        await _log_blocks(session, user, decision.blocks, structure.underlying_symbol, strategy_id, orders[0].id, mode)
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics, blocks=decision.blocks)
    lots = int(decision.quantity // lot)
    if lots < 1:
        reasons = notes + [f"One lot of this structure risks {metrics.risk_per_unit * lot:,.0f}, more than your risk per trade "
                           f"({cfg.risk_amount() or 0:,.0f}). Change limit: Settings > Risk limits"]
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)
    if rules.max_lots:
        lots = min(lots, rules.max_lots)
    if execution_mode == ExecutionMode.LIVE:
        try:
            cap, note = await _live_lot_cap(broker, structure, debit_per_unit=-metrics.net_credit if metrics.debit else None,
                                            share=cfg.margin_share(), product=product)
        except ContractExecutionError as exc:
            reasons = notes + [str(exc)]
            await _reject_all(session, orders, reasons)
            return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)
        notes.append(note)
        lots = min(lots, cap)
    quantity = lots * lot
    hold_margin: float | None = None
    if held and execution_mode == ExecutionMode.PAPER:
        # M55 T1: the margin a spread held overnight blocks, checked at entry (LIVE: the broker's margin above, NRML).
        refusal, note, hold_margin = await _hold_margin_refusal(session, user, structure, premiums, lots, metrics=metrics,
                                                                price_source=price_source)
        if refusal:
            reasons = notes + [refusal]
            await _reject_all(session, orders, reasons)
            await notify(session, user.tenant_id, NotificationType.RISK_REJECTION, title=f"Structure refused: {structure.underlying_symbol}",
                         message=refusal, severity=NotificationSeverity.WARNING, user_id=user.id, related_order_id=orders[0].id)
            return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)
        notes.append(note)
    if held:
        notes.append("Held for days: kept overnight; exits by your own exit rules; settles at expiry")
    ratios = {leg.contract.tradingsymbol: leg.ratio for leg in structure.legs}
    if structure.max_ratio > 1:
        notes.append(f"{lots} lot(s) x {lot} = {quantity} per 1x leg; ratio legs x{structure.max_ratio} = {quantity * structure.max_ratio}")
    else:
        notes.append(f"{lots} lot(s) x {lot} = {quantity} per leg")

    # Phase V1 (R4): open risk at the stops plus this structure's max loss within the cap.
    block, _ = await portfolio_risk_block(session, user.tenant_id, mode=mode, underlying=structure.underlying_symbol, cfg=cfg,
                                          new_max_loss=metrics.risk_per_unit * quantity)
    if block:
        reasons = notes + [block]
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)

    # Phase I1: the risk hierarchy, with the structure's max loss per unit as the risk per unit
    # and the short legs' premium as the order value. Quantity is the largest leg's (Phase U ratios).
    verdict = await evaluate_hierarchy(session, RiskContext(
        tenant_id=user.tenant_id, user_id=user.id, strategy_id=strategy_id, symbol=structure.underlying_symbol,
        quantity=quantity * structure.max_ratio,
        entry=basis, stop_loss=None, capital=cfg.capital, mode=mode, order_id=orders[0].id, account_id=account_id,
        risk_per_unit=metrics.risk_per_unit, deployment_id=deployment_id,
    ), user=user)
    notes.extend(verdict.notes)
    if not verdict.allowed:
        reasons = notes + verdict.reasons
        await _reject_all(session, orders, reasons)
        return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)

    for order, leg in zip(orders, structure.legs):
        order.quantity = quantity * leg.ratio

    group_id = uuid.uuid4().hex
    group_meta = metrics.as_dict()
    # `quantity` is the 1x leg's; the position monitor weights each leg by its own quantity / this.
    group_meta.update({"lots": lots, "quantity": quantity, "underlying_symbol": structure.underlying_symbol, "ratios": ratios})
    if hold_margin is not None:
        group_meta["hold_margin"] = hold_margin     # M68 PR-5: what this held structure blocks (`_held_margin_blocked`)
    fills: Dict[str, Tuple[float, Optional[str]]] = {}

    from app.execution import smart_execution
    from app.platform.controls import flag_enabled
    smart = await flag_enabled(session, smart_execution.FLAG, user.tenant_id)
    run: Optional[LegsRun] = None
    if smart:
        # SE-7: LIVE and PAPER on the smart-execution engine (one code path; PAPER through the PaperExecutionAdapter).
        if execution_mode == ExecutionMode.LIVE:
            breaker = breaker_for(broker.name)
            if not breaker.allow_submission():
                reasons = notes + [breaker.refusal_reason()]
                await _reject_all(session, orders, reasons)
                return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics)
        settings = await smart_execution.settings_for(session, user)
        venue: BrokerInterface
        if execution_mode == ExecutionMode.LIVE:
            venue = broker
        else:
            practice = PaperExecutionAdapter(PaperBroker())
            for leg in structure.legs:
                practice.mark(leg.contract.tradingsymbol, premiums[leg.contract.tradingsymbol], leg.contract.exchange)
            venue = practice
        run = await place_legs(venue, entry_specs(structure, quantity), strategy_id=strategy_id, tenant=tenant,
                               band_pct=(await order_defaults.load(session, user)).price_protection_pct, prices=premiums,
                               live=execution_mode == ExecutionMode.LIVE,
                               product=product)
        notes.extend(run.notes)
        if execution_mode == ExecutionMode.PAPER:
            notes.append("Paper fills at the quoted premiums")
        for key, fill in run.fills.items():
            fills[key] = (fill.price, fill.order_id if execution_mode == ExecutionMode.LIVE else None)
        if run.state != smart_execution.COMPLETE:
            return await _smart_not_complete(
                session, user, tenant, orders, structure, run, notes, metrics, settings, fills=fills, premiums=premiums,
                quantity=quantity, lot=lot, group_meta=group_meta, signal=signal, strategy_id=strategy_id, mode=mode,
                deployment_id=deployment_id, account_id=account_id, stop_credit_pct=stop_credit_pct, started=started,
                idempotency_key=idempotency_key, broker=broker, group_id=group_id, holding="SWING" if held else "INTRADAY")
    elif execution_mode == ExecutionMode.PAPER:
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
        # M55 S4: each leg's entry carries the client order id of its own `orders` row, on the row before anything is sent
        # (the start-up order sweeper finds it in the broker's book by that tag).
        leg_tags: dict[str, str] = {}
        for order, leg in zip(orders, structure.legs):
            tag = leg_entry_tag(broker, tenant, strategy_id, order.id)
            if leg_tags.setdefault(leg.contract.tradingsymbol, tag) == tag:
                order.algo_tag = tag
        await session.commit()
        placed_ok, failure = await _place_live_legs(broker, structure, quantity, strategy_id, tenant, fills, notes,
                                                    leg_tags=leg_tags, product=product)
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
    trades = await _book_legs(session, user, orders, list(structure.legs), fills, premiums, structure=structure, metrics=metrics,
                              quantity=quantity, lot=lot, group_id=group_id, group_meta=group_meta, signal=signal,
                              strategy_id=strategy_id, mode=mode, broker=broker, deployment_id=deployment_id, account_id=account_id,
                              stop_credit_pct=stop_credit_pct, notes=notes, started=started, holding="SWING" if held else "INTRADAY")

    await notify(
        session, user.tenant_id, NotificationType.ENTRY,
        title=f"{structure.strategy.value.replace('_', ' ').title()} opened on {structure.underlying_symbol}",
        message="; ".join(notes)[:1000], severity=NotificationSeverity.INFO, user_id=user.id, related_trade_id=trades[0].id,
    )
    logger.info("Structure %s opened: %s legs, %s lots, group %s", structure.strategy.value, len(trades), lots, group_id[:8])
    return StructureResult(executed=True, reasons=notes, orders=orders, trades=trades, leg_group_id=group_id, metrics=metrics,
                           state=smart_execution.COMPLETE if run is not None else None)


def _leg_meta(leg, *, structure: ResolvedStructure, lot: int, signal: Signal, group_id: str, group_meta: Dict) -> Dict:
    return {
        "instrument_kind": "OPTION", "exchange": leg.contract.exchange, "instrument_key": leg.contract.instrument_key,
        "lot_size": lot, "expiry": leg.contract.expiry, "option_position": "WRITE" if leg.role == "SHORT" else "BUY",
        "underlying_symbol": structure.underlying_symbol, "underlying_direction": signal.direction.value,
        "underlying_stop_loss": signal.stop_loss, "underlying_target1": signal.target1, "underlying_target2": signal.target2,
        "leg_group_id": group_id, "leg_role": leg.role, "option_strategy": structure.strategy.value, "group_meta": json.dumps(group_meta),
        "underlying_entry": signal.entry,   # Z6: read only by the journal (Greeks / IV at entry); not stored on the trade
    }


def leg_stop(fill_price: float, role: str, *, debit: bool, stop_credit_pct: Optional[float]) -> float:
    """Per-leg levels are informational: the group exit is what closes the position (legs are never judged one by one).
    DEF-0 D3: derived from the trader's own structure stop only; with none set a leg carries no level (0 = none)."""
    if stop_credit_pct is None:
        return 0.0
    if role == "SHORT" and not debit:
        return round(fill_price * (1 + stop_credit_pct / 100.0), 2)
    if role == "LONG" and debit:
        return round(fill_price * (1 - min(stop_credit_pct, 100.0) / 100.0), 2)
    return 0.0


async def _book_legs(
    session: AsyncSession, user: User, orders: List[OrderRecord], legs: List, fills: Dict[str, Tuple[float, Optional[str]]],
    premiums: Dict[str, float], *, structure: ResolvedStructure, metrics: StructureMetrics, quantity: float, lot: int, group_id: str,
    group_meta: Dict, signal: Signal, strategy_id: str, mode: str, broker: Optional[BrokerInterface], deployment_id: Optional[int],
    account_id: Optional[int], stop_credit_pct: Optional[float], notes: List[str], started: float,
    quantities: dict[str, float] | None = None, holding: str,
) -> List[TradeRecord]:
    """Books every leg that has a fill (all of them on the old path; the filled ones of an INCOMPLETE run): its order
    through SUBMITTED -> PENDING -> FILLED -> POSITION_OPEN and one TradeRecord in the group. `quantities`: the filled
    quantity per leg where it differs from the order's (a partial fill on the engine's path)."""
    trades: List[TradeRecord] = []
    latency_ms = int((time.perf_counter() - started) * 1000)
    now = datetime.now(timezone.utc)
    live = mode == ExecutionMode.LIVE.value
    for order, leg in zip(orders, legs):
        if leg.contract.tradingsymbol not in fills:
            continue
        fill_price, broker_order_id = fills[leg.contract.tradingsymbol]
        expected = premiums[leg.contract.tradingsymbol]
        stop = leg_stop(fill_price, leg.role, debit=bool(metrics.debit), stop_credit_pct=stop_credit_pct)
        filled_qty = (quantities or {}).get(leg.contract.tradingsymbol, quantity * leg.ratio)
        trade = Trade(symbol=leg.contract.tradingsymbol, strategy_id=strategy_id, direction=leg.contract.trade_direction,
                      entry_time=now, entry_price=fill_price, quantity=filled_qty, stop_loss=stop,
                      expected_price=expected, entry_latency_ms=latency_ms)
        if broker_order_id:
            order.broker_order_id = broker_order_id
        venue = broker.name if live and broker is not None else "paper broker"
        order = await transition_order(session, order, OrderStatus.SUBMITTED, detail=f"Submitted to {venue}")
        order = await transition_order(session, order, OrderStatus.PENDING, detail="Awaiting fill")
        order = await transition_order(session, order, OrderStatus.FILLED, detail=f"{'Paper' if venue == 'paper broker' else 'Live'} fill {broker_order_id or ''}".strip())
        ORDERS.labels(mode=mode, status="FILLED").inc()
        meta = _leg_meta(leg, structure=structure, lot=lot, signal=signal, group_id=group_id, group_meta=group_meta)
        record = await persist_trade(session, user, trade, mode=mode, broker_order_id=broker_order_id, deployment_id=deployment_id, contract_meta=meta,
                                     account_id=account_id, holding=holding)
        order.trade_id = record.id
        order.reasons_json = json.dumps(notes)
        await transition_order(session, order, OrderStatus.POSITION_OPEN, detail=f"Position opened (trade #{record.id}, group {group_id[:8]})")
        trades.append(record)
    return trades


async def _smart_not_complete(
    session: AsyncSession, user: User, tenant: Optional[Tenant], orders: List[OrderRecord], structure: ResolvedStructure, run: LegsRun,
    notes: List[str], metrics: StructureMetrics, settings, *, fills: Dict[str, Tuple[float, Optional[str]]], premiums: Dict[str, float],
    quantity: float, lot: int, group_meta: Dict, signal: Signal, strategy_id: str, mode: str, deployment_id: Optional[int],
    account_id: Optional[int], stop_credit_pct: Optional[float], started: float, idempotency_key: str, broker: Optional[BrokerInterface],
    group_id: str, holding: str,
) -> StructureResult:
    """SE-7 / TM-11: the engine stopped at a leg that did not fill. Nothing is undone by itself: the filled legs are booked
    as the group's positions (INCOMPLETE, the user's two choices and his own "if I do not answer" setting), the legs not
    filled end FAILED / REJECTED on the trail. Nothing filled at all: every order ends FAILED, as before."""
    from app.execution import incomplete_structures, smart_execution
    failure = run.failure or "a leg did not fill"
    reasons = notes + [failure]
    by_key = {leg.contract.tradingsymbol: (order, leg) for order, leg in zip(orders, structure.legs)}
    if tenant is not None and run.uncertain:
        await mark_broker_uncertain(session, tenant, f"structure FAILED half way: {run.uncertain}"[:500], user_id=user.id)
        await session.commit()
    if not run.fills:
        await _reject_all(session, orders, reasons, failed=True)
        await notify(session, user.tenant_id, NotificationType.SYSTEM_FAILURE, title=f"Structure failed: {structure.underlying_symbol}",
                     message=f"Nothing was placed: {failure}"[:1000], severity=NotificationSeverity.CRITICAL, user_id=user.id,
                     related_order_id=orders[0].id)
        return StructureResult(executed=False, reasons=reasons, orders=orders, metrics=metrics, system_failure=True,
                               state=smart_execution.NOTHING_PLACED)
    leg_meta = {}
    for spec in run.pending:
        _, leg = by_key[spec.key]
        leg_meta[spec.key] = _leg_meta(leg, structure=structure, lot=lot, signal=signal, group_id=group_id, group_meta={})
        leg_meta[spec.key].pop("group_meta", None)
        leg_meta[spec.key]["direction"] = leg.contract.trade_direction.value
        expiry = leg_meta[spec.key].get("expiry")
        leg_meta[spec.key]["expiry"] = expiry.isoformat() if hasattr(expiry, "isoformat") else expiry   # JSON on the row
    block = incomplete_structures.new_block(
        user_id=user.id, mode=mode, strategy_id=strategy_id, deployment_id=deployment_id, account_id=account_id,
        idempotency_key=idempotency_key, pending=run.pending, leg_meta=leg_meta, failure=failure, settings=settings,
        premiums={spec.key: premiums.get(spec.key) for spec in run.pending}, debit=bool(metrics.debit), stop_credit_pct=stop_credit_pct)
    meta = dict(group_meta)
    meta["incomplete"] = block
    filled_orders = [by_key[k][0] for k in run.fills]
    filled_legs = [by_key[k][1] for k in run.fills]
    trades = await _book_legs(session, user, filled_orders, filled_legs, fills, premiums, structure=structure, metrics=metrics,
                              quantity=quantity, lot=lot, group_id=group_id, group_meta=meta, signal=signal, strategy_id=strategy_id,
                              mode=mode, broker=broker, deployment_id=deployment_id, account_id=account_id,
                              stop_credit_pct=stop_credit_pct, notes=reasons, started=started,
                              quantities={k: f.quantity for k, f in run.fills.items()}, holding=holding)
    for key, (order, leg) in by_key.items():
        if key in run.fills:
            continue
        why = run.leg_reasons.get(key) or [failure]
        order.reasons_json = json.dumps(notes + why)
        failed = why[0] == failure
        await transition_order(session, order, OrderStatus.FAILED if failed else OrderStatus.REJECTED, detail="; ".join(why)[:500])
        ORDERS.labels(mode=order.mode, status=order.status).inc()
    await session.commit()
    await incomplete_structures.announce(session, trades, block, structure.underlying_symbol, user_id=user.id)
    logger.warning("Structure %s INCOMPLETE: %s of %s legs filled, group %s (%s)", structure.strategy.value, len(trades),
                   len(structure.legs), group_id[:8], failure)
    return StructureResult(executed=False, reasons=reasons + [block["message"]], orders=orders, trades=trades, leg_group_id=group_id,
                           metrics=metrics, state=smart_execution.INCOMPLETE)


def _economics_note(metrics: StructureMetrics, lot: int) -> str:
    be = ", ".join(f"{b:g}" for b in metrics.breakevens) or "n/a"
    exits = metrics.exit_summary()          # DEF-0 D3: exactly the exits the trader set ("no target or stop set")
    undefined = (f"UNDEFINED max loss - sized off the stop ({metrics.risk_per_unit:g}/unit, {metrics.risk_per_unit * lot:,.0f}/lot)"
                 if metrics.risk_per_unit > 0 else "UNDEFINED max loss - no stop set")
    if metrics.payoff:
        # Phase U: payoff-priced structure, exits as P&L per unit.
        side = f"net credit {metrics.net_credit:g}" if metrics.net_credit >= 0 else f"net debit {-metrics.net_credit:g}"
        loss = f"max loss {metrics.max_loss:g}/unit ({metrics.max_loss * lot:,.0f}/lot)" if metrics.max_loss is not None else undefined
        profit = f"max profit {metrics.max_profit:g}/unit" if metrics.max_profit is not None else "profit open-ended"
        return f"{side[0].upper()}{side[1:]}/unit, {loss}, {profit}, breakeven {be}; exits: {exits}"
    if metrics.debit:
        debit = -metrics.net_credit
        return f"Net debit {debit:g}/unit ({debit * lot:,.0f}/lot) = max loss, profit open-ended, breakeven {be}; exits: {exits}"
    if not metrics.defined_risk:
        return f"Net credit {metrics.net_credit:g}/unit, {undefined}, breakeven {be}; exits: {exits}"
    return (f"Net credit {metrics.net_credit:g}/unit, max loss {metrics.max_loss:g}/unit ({metrics.max_loss * lot:,.0f}/lot), "
            f"breakeven {be}; exits: {exits}")


async def _log_blocks(session: AsyncSession, user: User, blocks: list[dict], symbol: str, strategy_id: str, order_id: int | None,
                      mode: str) -> None:
    """M64 RM-3: every Risk limits block of a structure goes to the journal's record (risk_events)."""
    from app.risk_engine import risk_limits
    for item in blocks:
        await risk_limits.log_block(session, tenant_id=user.tenant_id, user_id=user.id, item=item, symbol=symbol, strategy_id=strategy_id,
                                    order_id=order_id, mode=mode, source="structure")


async def _held_margin_blocked(session: AsyncSession, user: User) -> float:
    """M55 T1: the margin his other PAPER structures held for days still block - the margin worked out at its entry
    (`hold_margin` in its group metadata, M68 PR-5: a written straddle / strangle too), else its defined max loss (per
    unit) x its quantity, as the option backtest's span_like margin counts a defined-risk structure."""
    from sqlalchemy import select
    rows = await session.execute(select(TradeRecord.leg_group_id, TradeRecord.group_meta).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None),
        TradeRecord.mode == ExecutionMode.PAPER.value, TradeRecord.holding == "SWING", TradeRecord.leg_group_id.is_not(None)))
    blocked: dict[str, float] = {}
    for group_id, raw in rows:
        if group_id in blocked:
            continue
        try:
            meta = json.loads(raw or "{}")
            if meta.get("hold_margin") is not None:
                blocked[group_id] = max(0.0, float(meta["hold_margin"]))
            else:
                blocked[group_id] = max(0.0, float(meta.get("max_loss") or 0.0)) * float(meta.get("quantity") or 0.0)
        except (TypeError, ValueError):
            blocked[group_id] = 0.0
    return round(sum(blocked.values()), 2)


async def _hold_margin_refusal(session: AsyncSession, user: User, structure: ResolvedStructure, premiums: dict[str, float],
                               lots: int, *, metrics: StructureMetrics, price_source: Optional[BrokerInterface]
                               ) -> tuple[str | None, str, float | None]:
    """M55 T1: a PAPER spread held for days blocks its margin overnight, so it is checked at entry: ATP's labelled
    estimate (`fno_basket.hedge_margin`, the defined max loss) against his practice balance less what his other held
    spreads block. M68 PR-5 (TS1-041): a written straddle / strangle held by his switch has no defined max loss, so its
    margin is the option backtest's span_like rule (`option_structure_margin`: the short legs' underlying notional x the
    short-option %), the underlying quoted from the same price source as the legs. No broker is built here (Rule 0:
    execute_structure never obtains a broker itself; LIVE is checked by the broker's own margin in `_live_lot_cap`).
    (refusal or None, note, the margin it blocks or None)."""
    from app.execution import smart_execution
    from app.paper import fno_basket
    builder = [{"direction": "SELL" if leg.role == "SHORT" else "BUY", "option_type": leg.contract.right,
                "strike": float(leg.contract.strike or 0.0), "premium": float(premiums[leg.contract.tradingsymbol]),
                "lots": int(lots * leg.ratio), "lot_size": int(structure.lot_size), "expiry": leg.contract.expiry.isoformat(), "iv": None}
               for leg in structure.legs]
    estimate = fno_basket.hedge_margin(builder)
    if estimate["with_hedge"]["value"] is None and metrics.max_loss is None and structure.short_legs:
        written = await _written_hold_margin(structure, lots, price_source)
        if written is not None:
            estimate = {**estimate, "with_hedge": {**estimate["with_hedge"], "value": written,
                                                   "basis": "Undefined risk: the short legs' underlying notional x the "
                                                            "short-option % (the option backtest's span_like margin)."}}
    free = await fno_basket.practice_balance(session, user) - await _held_margin_blocked(session, user)
    view = smart_execution.margin_view(None, estimate, free, free_source="practice")
    if view.required is None:
        return "Not entered: the margin to hold this spread for days could not be worked out, so it is not held", "", None
    if view.free_after is None or view.free_after < 0:
        return (f"Not entered: not enough margin to hold this spread for days - it needs {view.required:,.0f}, "
                f"{max(free, 0.0):,.0f} is free in your practice account"), "", None
    return None, f"Margin to hold for days: {view.required:,.0f} ({view.label}); {view.free_after:,.0f} free after", view.required


async def _written_hold_margin(structure: ResolvedStructure, lots: int, price_source: Optional[BrokerInterface]) -> float | None:
    """M68 PR-5 (TS1-041): the span_like margin of a written structure with no defined max loss - the option backtest's
    own rule and numbers (`app.backtest.models.option_structure_margin` with `SpanLikeMargin`), the underlying quoted
    from the legs' price source. None when the underlying has no quote (the caller then refuses; never a made-up spot)."""
    from app.backtest.models import SpanLikeMargin, option_structure_margin
    from app.instruments import master as instrument_master
    from app.option_chain.chain_view import spot_instrument
    if price_source is None:
        return None
    try:
        symbol, exchange = spot_instrument(instrument_master.underlying_of(structure.underlying_symbol))
        spot = float(await price_source.get_ltp_for_symbol(symbol, exchange))
    except Exception as exc:  # noqa: BLE001 - no quote = no margin number = refused by the caller
        logger.warning("Underlying quote for the held-structure margin of %s failed: %s", structure.underlying_symbol, exc)
        return None
    if not math.isfinite(spot) or spot <= 0:
        return None
    short_quantity = sum(lots * structure.lot_size * leg.ratio for leg in structure.short_legs)
    return round(option_structure_margin(SpanLikeMargin(), spot=spot, short_quantity=short_quantity, max_loss=None), 2)


async def _live_lot_cap(broker: BrokerInterface, structure: ResolvedStructure, *, debit_per_unit: Optional[float] = None,
                        share: float = MARGIN_SAFETY, product: str) -> tuple[int, str]:
    """Lots the account's margin allows against `share` of the available margin (M64: the trader's margin safety %
    when he switched it on, else the whole available margin). M64 RM-4: the structure's margin is the broker's margin for
    ALL its legs placed together (the hedge benefit included) when the broker reports one; otherwise the sum of the short
    legs alone (no spread benefit assumed - conservative). A structure with no short leg (Phase R long
    straddle/strangle) blocks exactly its debit, so that is the per-lot number (Phase Q's premium rule)."""
    if not structure.short_legs:
        if debit_per_unit is None or debit_per_unit <= 0:
            raise ContractExecutionError("No short leg and no debit to size the long-only structure's margin from - refused")
        per_lot = float(debit_per_unit) * structure.lot_size
        margins = await broker.get_margins()
        available = float(margins.available_margin or margins.available_cash or 0.0)
        lots = int((available * share) // per_lot)
        if lots < 1:
            raise ContractExecutionError(f"Insufficient funds for the debit: {per_lot:,.0f} per lot needed, {available:,.0f} available")
        return lots, f"Debit {per_lot:,.0f}/lot, {available:,.0f} available -> at most {lots} lot(s)"
    per_lot = 0.0
    basket = await _structure_margin(broker, structure, product)
    source = "for the whole structure"
    for leg in (structure.short_legs if basket is None else []):
        probe = BrokerOrderRequest(
            symbol=leg.contract.instrument_key if _is_upstox(broker) and "|" in (leg.contract.instrument_key or "") else leg.contract.tradingsymbol,
            exchange=leg.contract.exchange, transaction_type=OrderSide.SELL, quantity=structure.lot_size * leg.ratio,
            order_type="MARKET", product=product,
        )
        margin = await broker.get_order_margin(probe)
        if margin is None or margin <= 0:
            raise ContractExecutionError(f"{broker.name} did not report a margin requirement for {leg.contract.tradingsymbol} - refused")
        per_lot += float(margin)
        source = "for the short legs"
    if basket is not None:
        per_lot = basket
    margins = await broker.get_margins()
    available = float(margins.available_margin or margins.available_cash or 0.0)
    lots = int((available * share) // per_lot) if per_lot > 0 else 0
    if lots < 1:
        raise ContractExecutionError(f"Insufficient margin {source}: {per_lot:,.0f} per lot needed, {available:,.0f} available")
    return lots, f"Margin {per_lot:,.0f}/lot {source}, {available:,.0f} available -> at most {lots} lot(s)"


async def _structure_margin(broker: BrokerInterface, structure: ResolvedStructure, product: str) -> float | None:
    """M64 RM-4: the broker's margin for one lot of every leg placed together (hedge benefit included), or None when the
    broker has no basket margin call or it fails - the caller then sums the short legs (never a made-up number)."""
    requests = [BrokerOrderRequest(
        symbol=leg.contract.instrument_key if _is_upstox(broker) and "|" in (leg.contract.instrument_key or "") else leg.contract.tradingsymbol,
        exchange=leg.contract.exchange, transaction_type=OrderSide.SELL if leg in structure.short_legs else OrderSide.BUY,
        quantity=structure.lot_size * leg.ratio, order_type="MARKET", product=product,
    ) for leg in structure.legs]
    try:
        basket = await broker.get_basket_margin(requests)
        required = float(basket.required) if basket is not None else None
    except Exception:  # noqa: BLE001 - no basket number: the short legs' sum below
        return None
    return required if required is not None and math.isfinite(required) and required > 0 else None


def leg_entry_tag(broker: BrokerInterface, tenant: Tenant | None, strategy_id: str, order_id: int | None) -> str:
    """M55 S4: a structure leg's entry tag, with the client order id of the leg's own `orders` row."""
    algo_id = order_algo_id(tenant.algo_id if tenant is not None else None, broker.name)
    return build_order_tag(strategy_id=strategy_id, leg=LEG_ENTRY, algo_id=algo_id,
                           max_length=getattr(broker, "max_tag_length", None) or 20, broker=broker.name,
                           client_id=client_order_id("E", order_id) if order_id is not None else new_client_order_id())


async def _place_live_legs(
    broker: BrokerInterface, structure: ResolvedStructure, quantity: float, strategy_id: str, tenant: Optional[Tenant],
    fills: dict[str, tuple[float, str | None]], notes: list[str], leg_tags: dict[str, str] | None = None, *,
    product: str,
) -> Tuple[bool, str]:
    """Wings first, then shorts - and a short is sent only once every wing's fill is *confirmed* in the broker's
    book (P0.5 / T3), so the account is never short without its protection. A leg that does not fill inside the
    poll window is cancelled and the structure fails; every confirmed leg is unwound. A fill price is recorded only
    when the book reports one, never as 0.0. Returns (ok, failure_text)."""
    algo_id = order_algo_id(tenant.algo_id if tenant is not None else None, broker.name)   # D1: registered, else generic
    max_tag = getattr(broker, "max_tag_length", None) or 20
    router = OrderRouter(mode=ExecutionMode.LIVE, risk_config=RiskConfig(), broker=broker)
    placed: List = []          # (leg, filled quantity) with a confirmed fill - what an unwind must reverse

    async def _fail(leg, text: str, order_id: Optional[str], known_filled: float = 0.0) -> Tuple[bool, str]:
        if order_id:
            try:
                await broker.cancel_order(order_id)      # the unfilled leg must not keep working
            except Exception as exc:  # noqa: BLE001
                text += f"; cancel of {order_id} failed: {exc}"
            # The exchange may have filled it between the last look and the cancel: that fill is a real position
            # and must be unwound with the rest, never orphaned.
            late_price, late_qty, _ = await router._resolve_fill_with_status(order_id, fallback=0.0)
            late_qty = max(late_qty, known_filled)      # an unreadable book must not hide a fill we already saw
            if late_qty > 0:
                placed.append((leg, late_qty))
                text += f"; {leg.contract.tradingsymbol} filled {late_qty:g} during the cancel"
        unwound = await _unwind(broker, placed, quantity, strategy_id, algo_id, max_tag, product=product)
        return False, text + (f"; unwound {unwound} filled leg(s)" if placed else "")

    for phase, legs in (("wing", structure.long_legs), ("short", structure.short_legs)):
        for leg in legs:
            request = BrokerOrderRequest(
                symbol=leg.contract.tradingsymbol, exchange=leg.contract.exchange, transaction_type=leg.side,
                quantity=quantity * leg.ratio, order_type="MARKET", product=product,
                tag=(leg_tags or {}).get(leg.contract.tradingsymbol)
                or build_order_tag(strategy_id=strategy_id, leg=LEG_ENTRY, algo_id=algo_id, max_length=max_tag, broker=broker.name,
                                   client_id=new_client_order_id()),
                intent=ORDER_INTENT_ENTRY,
            )
            try:
                response = await broker.place_order(request)
                if response.status.upper() in ("REJECTED", "CANCELLED"):
                    raise ContractExecutionError(f"{leg.side.value} {leg.contract.tradingsymbol} rejected: {response.message or response.status}")
            except Exception as exc:  # noqa: BLE001
                return await _fail(leg, f"{leg.side.value} {leg.contract.tradingsymbol} failed: {exc}", None)
            price, filled, _ = await router._resolve_fill_with_status(response.order_id, fallback=0.0)
            if filled <= 0 or price <= 0:
                return await _fail(leg, f"{leg.side.value} {leg.contract.tradingsymbol} ({phase}) not filled within the confirmation window", response.order_id)
            if phase == "wing" and config.LIVE_STRICT_WING_FILL and not wing_fill_complete(quantity * leg.ratio, filled):
                # Trade port (order_safety): a partly filled wing protects only part of the short - stop and unwind
                # (`_fail` cancels the remainder and re-reads the book, which adds this wing's fill to the unwind).
                return await _fail(leg, f"wing {leg.contract.tradingsymbol} filled {filled:g} of {quantity * leg.ratio:g} - shorts not sent", response.order_id,
                                   known_filled=filled)
            placed.append((leg, filled))
            fills[leg.contract.tradingsymbol] = (round(price, 2), response.order_id)
            notes.append(f"Live {leg.side.value} {leg.contract.tradingsymbol} via {broker.name}: {response.order_id} filled @ {price:g}")
    return True, ""


async def _unwind(broker: BrokerInterface, placed: List, quantity: float, strategy_id: str, algo_id: Optional[str], max_tag: int,
                  *, product: str) -> int:
    """Reverses every confirmed fill, shorts first, for exactly the quantity that filled (P0.5 / T3)."""
    done = 0
    ordered = sorted(placed, key=lambda item: 0 if item[0].role == "SHORT" else 1)
    for leg, filled in ordered:
        try:
            await broker.place_order(BrokerOrderRequest(
                symbol=leg.contract.tradingsymbol, exchange=leg.contract.exchange,
                transaction_type=OrderSide.SELL if leg.side == OrderSide.BUY else OrderSide.BUY,
                quantity=filled if filled and filled > 0 else quantity * leg.ratio, order_type="MARKET", product=product,
                tag=build_order_tag(strategy_id=strategy_id, leg=LEG_EXIT, algo_id=algo_id, max_length=max_tag, broker=broker.name,
                                    client_id=new_client_order_id()),
            ))
            done += 1
        except Exception as exc:  # noqa: BLE001 - reported; reconciliation resolves the rest
            logger.error("Unwind of %s failed: %s", leg.contract.tradingsymbol, exc)
    return done
