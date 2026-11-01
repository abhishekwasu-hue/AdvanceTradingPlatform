import json
import logging
import math
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.compliance import algo_id as algo_ids
from app.compliance import fo_limits
from app.core import config
from app.core.enums import ExecutionMode, NotificationSeverity, NotificationType, OptionPosition, OrderSide, OrderStatus, SignalDirection
from app.core.logging_config import bind_log_context, update_log_context
from app.core.models import RiskConfig, Signal
from app.db.models import OrderRecord, Tenant, User
from app.execution import approval as approval_gate
from app.execution import order_defaults
from app.execution.contract_execution import ContractExecutionError, build_order_plan, contract_ltp, written_lot_cap
from app.execution.order_persistence import create_order, execution_result_from_order, transition_order
from app.execution.prechecks import PreCheckRefusal, live_margin_cap, margin_probe_for, validate_instrument
from app.execution.products import product_for
from app.execution.router import ExecutionResult, OrderRouter
from app.instruments.contracts import ContractRules, ResolvedContract
from app.instruments.models import ContractSpec
from app.instruments.registry import get_contract_spec
from app.kill_switch.checks import active_kill_switch_reasons
from app.notifications.service import notify
from app.observability.metrics import ORDER_ENTRY_LATENCY, ORDERS
from app.plans.limits import live_allowed_for, tenant_is_active
from app.platform.controls import clamp_config, risk_ceilings
from app.platform.controls import entry_blocks as platform_entry_blocks
from app.reconciliation.service import broker_uncertain_reason, mark_broker_uncertain
from app.risk_engine.by_default import releases_unused_entry_slot as by_default_releases_slot
from app.risk_engine.guardian import GuardianVerdict, apply_multiplier, guard_entry, portfolio_risk_block
from app.risk_engine.hierarchy import RiskContext
from app.risk_engine.hierarchy import evaluate as evaluate_hierarchy
from app.risk_engine.routes import get_tenant_risk_config
from app.trading.persistence import build_trading_day_state, persist_trade

logger = logging.getLogger(__name__)


async def entry_refusals(session: AsyncSession, tenant: Optional[Tenant], tenant_id: int, mode: str, strategy_id: str, *,
                         user: Optional[User] = None, broker_name: Optional[str] = None, reserve: bool = True) -> list:
    """Every reason a *new entry* must be refused before any sizing or routing: kill switches,
    platform controls (maintenance mode, disabled broker, per-user trading disable - Phase M),
    a suspended organisation, plan limits, the SEBI algo id and the broker-uncertain flag
    (safety rule 8). Shared by the single-leg pipeline and the multi-leg executor (Phase H2)."""
    reasons = await active_kill_switch_reasons(session, tenant_id, strategy_id)
    reasons.extend(await platform_entry_blocks(session, user=user, mode=mode, broker_name=broker_name))
    if mode == ExecutionMode.PAPER.value and user is not None:
        # Z3-b: the user's PAPER-scope pause (Telegram /pause, web) - new PAPER entries only; exits never come here.
        from app.paper.service import paused_reason
        paused = await paused_reason(session, user.id)
        if paused:
            reasons.append(paused)
        # Z5 / P1-d (flag risk_by_default): the user's daily loss lockout and entries-per-day limit - new PAPER entries
        # only; exits never come here (ADR-0004).
        from app.risk_engine import by_default
        if await by_default.enabled(session, user.tenant_id):
            # `reserve`: the order pipelines take the day's entry slot atomically (given back if the entry is not placed).
            reasons.extend(await by_default.entry_refusals(session, user, reserve=reserve and not reasons))
    if tenant is not None and not tenant_is_active(tenant):
        reasons.append(f"Organisation is {tenant.status}: no new orders")
    if mode == ExecutionMode.LIVE.value and not await live_allowed_for(session, tenant):
        reasons.append("Plan does not include live trading - order refused")
    if mode == ExecutionMode.LIVE.value and config.ALGO_ID_REQUIRED_FOR_LIVE:
        # D1: the registered id, or the broker's generic one while the OPS throttle keeps us under the threshold.
        problem = algo_ids.live_problem(tenant.algo_id if tenant else None, broker_name)
        if problem:
            reasons.append(problem)
    if mode == ExecutionMode.LIVE.value:
        # Phase G1 (safety rule 8): a FAILED live order leaves the broker's book unknown; no
        # new LIVE entry until reconciliation says the books agree. Exits are unaffected.
        uncertain = broker_uncertain_reason(tenant)
        if uncertain:
            reasons.append(uncertain)
    return reasons


@by_default_releases_slot
async def execute_signal_for_user(
    session: AsyncSession, user: User, *, mode: str, strategy_id: str, signal: Signal,
    idempotency_key: Optional[str] = None, risk_config: Optional[RiskConfig] = None,
    broker: Optional[BrokerInterface] = None, deployment_id: Optional[int] = None,
    contract: Optional[ResolvedContract] = None, rules: Optional[ContractRules] = None,
    quote_broker: Optional[BrokerInterface] = None, account_id: Optional[int] = None, exit_rules: Optional[str] = None,
    holding: str | None = None, order_style: str | None = None, market_protection_pct: float | None = None,
    journal_source: Optional[str] = None,
    origin: str = approval_gate.ORIGIN_AUTOMATIC, approval_action_id: Optional[int] = None,
    max_quantity: Optional[float] = None, risk_cap_amount: Optional[float] = None, exchange: Optional[str] = None,
    manual_contract_spec: Optional[ContractSpec] = None, manual_contract_meta: Optional[dict] = None,
    paper_execution: str = "direct", freeze_quantity: Optional[int] = None,
) -> Tuple[ExecutionResult, OrderRecord]:
    """The full logged-in execution path a pre-formed `Signal` goes through, regardless of where
    it came from (the platform's own strategy engine via /paper-execute, a TradingView webhook
    alert, or the autonomous worker acting on a deployment): create the formal order record,
    check kill switches, run the risk engine, route to the paper broker or - for mode "LIVE" with
    an authenticated `broker` adapter - the real broker, persist the fill (with the broker's entry
    and protective stop-loss order ids), and notify. One sequence, one state machine, for every
    caller. A LIVE request without a broker is REJECTED on the order trail rather than raised:
    the worker's token gate normally prevents it, but if it ever happens it must be visible.

    Z3 / BT2: `origin` says who asks - "approval" (an APPROVED entry proposal, whose id is `approval_action_id`) or
    "automatic" (the default: worker, webhook, public API, console). Under approval-only mode any entry without an
    approved proposal is REJECTED here (app/execution/approval.py::gate_refusal). TT-2: "manual" is the user's own order,
    confirmed by him on the F&O ticket / basket routes (the only callers that pass it); his confirmation is the approval.
    It never carries a deployment - a deployment entry stays refused without an approved proposal.

    Z5 / P1-d: `max_quantity` caps the size (a PAPER ticket's checked quantity); `risk_cap_amount` is the user's checked
    cap for this entry (a ticket's, an override included). With the `risk_by_default` flag on, a PAPER entry is sized
    within the user's own cap (that amount, or their settings') as well as the organisation's, strictest wins.

    BK2-8: `exchange` is the caller's venue (the deployment's exchange). For an instrument with no contract and no registry
    entry it is the order's exchange when it is another session family than NSE (a currency deployment orders on CDS);
    an NSE / BSE / NFO name keeps the NSE default. The position records the order's exchange, so its exits follow that
    venue's clock.

    FNO-1: `manual_contract_spec` / `manual_contract_meta` are a manual order on a contract the user picked himself (the
    option chain's order ticket, app/paper/fno_ticket.py): the signal is already on the contract (its premium levels),
    the spec carries the instrument master's lot and tick size, and the meta is stored on the position (kind, exchange,
    expiry, lot size). No underlying levels are stored, so the position exits on its own premium stop or by hand.

    TM-1: `paper_execution` "adapter" fills a PAPER entry through the PaperExecutionAdapter (a BrokerInterface over the
    same fill and cost model) instead of PaperBroker directly; the default "direct" keeps every other path as it was.
    """
    ticket_quantity = max_quantity
    # Phase F3: when the deployment trades a derived contract, the order goes on the contract
    # (premium/future price levels) while the strategy's levels stay on the underlying. The order
    # signal is built up front so the order trail's symbol is the contract from the first event;
    # a missing quote is recorded as a REJECTED order below, never raised.
    underlying_signal = signal
    plan = None
    plan_error: Optional[str] = None
    contract_spec = manual_contract_spec
    max_quantity = ticket_quantity
    if contract is not None:
        price_source = quote_broker or broker
        try:
            if price_source is None:
                raise ContractExecutionError("No broker session to quote the contract from")
            ltp = await contract_ltp(price_source, contract)
            plan = build_order_plan(underlying_signal, contract, rules or ContractRules(kind=contract.kind, position=contract.position), ltp)
            signal = plan.order_signal
            contract_spec = plan.contract_spec
            max_quantity = plan.max_quantity if ticket_quantity is None else (
                ticket_quantity if plan.max_quantity is None else min(ticket_quantity, plan.max_quantity))
        except ContractExecutionError as exc:
            plan_error = str(exc)
            signal = underlying_signal.model_copy(update={"symbol": contract.tradingsymbol, "direction": contract.trade_direction})

    with bind_log_context(
        tenant_id=user.tenant_id, strategy_id=strategy_id,
        signal_ref=f"{underlying_signal.symbol}@{underlying_signal.timestamp.isoformat()}",
    ):
        order, was_newly_created = await create_order(
            session, user, mode=mode, strategy_id=strategy_id, signal=signal, idempotency_key=idempotency_key,
        )
        update_log_context(order_id=order.id)
        if not was_newly_created:
            # A genuine race: another concurrent request for this same idempotency_key won and
            # already committed its own order before this one's pre-check could see it (see
            # create_order's own docstring). `order` here is that winning row, already fully
            # decided one way or another - replay its outcome rather than attempting to run this
            # already-settled order through the pipeline a second time.
            logger.info("Idempotency race detected - returning existing order's outcome")
            return execution_result_from_order(order), order

        logger.info("Order created (%s, %s)", signal.direction.value, mode)
        order = await transition_order(session, order, OrderStatus.VALIDATING, detail="Signal received")

        tenant = await session.get(Tenant, user.tenant_id)
        kill_switch_reasons = await entry_refusals(session, tenant, user.tenant_id, mode, strategy_id, user=user,
                                                   broker_name=getattr(broker, "name", None) if broker is not None else None)
        # TT-2: "manual" counts only for a manual order (journal source MANUAL, no deployment); anything else is automatic.
        manual = origin == approval_gate.ORIGIN_MANUAL and deployment_id is None and journal_source == "MANUAL"
        gate_origin = origin if origin != approval_gate.ORIGIN_MANUAL or manual else approval_gate.ORIGIN_AUTOMATIC
        gate = await approval_gate.gate_refusal(session, user.tenant_id, deployment_id=deployment_id, origin=gate_origin,
                                                approval_action_id=approval_action_id)
        if gate:
            kill_switch_reasons.insert(0, gate)
        fo_notes: list = []
        if contract is not None:
            # D7: a derivative on an underlying in the exchange's F&O ban period is not a new trade the market allows.
            ban, ban_note = await fo_limits.entry_refusal(session, underlying_signal.symbol, underlying_signal.timestamp)
            if ban:
                kill_switch_reasons.append(ban)
            elif ban_note:
                fo_notes.append(ban_note)
            # N2-2 (flag experience_levels): futures / naked short options open with the user's experience level or a
            # logged opt-out for the day. New entries only - exits never come here (ADR-0004).
            from app.paper import experience
            kill_switch_reasons.extend(await experience.entry_refusals(session, user, experience.contract_gates(contract)))
        else:
            # N2-2 review M1: a raw symbol (webhook, public API, console, approval, underlying deployment) is classified
            # from the instrument master / registry / its shape - a future, an MCX commodity or a sold option is gated too.
            from app.paper import experience
            kill_switch_reasons.extend(await experience.symbol_entry_refusals(session, user, signal.symbol, signal.direction))
            if manual_contract_meta and manual_contract_meta.get("underlying_symbol"):
                # FNO-1: a manual order on a contract - the F&O ban period applies as to a resolved one (D7).
                ban, ban_note = await fo_limits.entry_refusal(session, str(manual_contract_meta["underlying_symbol"]), signal.timestamp)
                if ban:
                    kill_switch_reasons.append(ban)
                elif ban_note:
                    fo_notes.append(ban_note)
        # M68 PR-1 (TS1-005 / 008 / 010 / 011): the holding, the order type and the price protection are the user's own -
        # the caller's (order / bot / proposal) value, else his Trade settings. Not set: nothing is placed, and the order
        # trail says which setting it needs (ATP does not choose). An exit never comes here (ADR-0004).
        mine = await order_defaults.load(session, user)
        holding = holding or mine.holding
        order_style = order_style or mine.bot_order_style
        if market_protection_pct is None:
            market_protection_pct = mine.price_protection_pct
        if holding is None:
            kill_switch_reasons.append(order_defaults.needs_setting("holding"))
        if order_style is None:
            kill_switch_reasons.append(order_defaults.needs_setting("order_type"))
        elif order_style.upper() == "PROTECTED_LIMIT" and market_protection_pct is None:
            kill_switch_reasons.append(order_defaults.needs_setting("price_protection_pct"))
        if kill_switch_reasons:
            order.reasons_json = json.dumps(kill_switch_reasons)
            order = await transition_order(
                session, order, OrderStatus.REJECTED, detail="; ".join(kill_switch_reasons)
            )
            logger.warning("Order rejected by kill switch: %s", "; ".join(kill_switch_reasons))
            ORDERS.labels(mode=mode, status=order.status).inc()
            await notify(
                session, user.tenant_id, NotificationType.REJECTION,
                title=f"Order rejected: {signal.symbol}", message="; ".join(kill_switch_reasons),
                severity=NotificationSeverity.WARNING, user_id=user.id, related_order_id=order.id,
            )
            return ExecutionResult(executed=False, reasons=kill_switch_reasons), order

        if plan_error is not None:
            order.reasons_json = json.dumps([plan_error])
            order = await transition_order(session, order, OrderStatus.REJECTED, detail=plan_error)
            logger.warning("Order rejected - contract not executable: %s", plan_error)
            ORDERS.labels(mode=mode, status=order.status).inc()
            return ExecutionResult(executed=False, reasons=[plan_error]), order

        order = await transition_order(session, order, OrderStatus.RISK_CHECK, detail="Running risk checks")

        effective_risk_config = risk_config
        if effective_risk_config is None:          # DEF-0: limits not set are off; a % reads the user's PAPER balance
            from app.risk_engine.by_default import capital_basis
            effective_risk_config = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
            if effective_risk_config.capital is None:
                effective_risk_config = effective_risk_config.with_capital(await capital_basis(session, user))

        execution_mode = ExecutionMode(mode)
        if execution_mode == ExecutionMode.LIVE and broker is None:
            reason = "Live trading not configured: no authenticated broker adapter for this tenant"
            order.reasons_json = json.dumps([reason])
            order = await transition_order(session, order, OrderStatus.REJECTED, detail=reason)
            logger.error("LIVE order rejected - no broker adapter supplied")
            ORDERS.labels(mode=mode, status=order.status).inc()
            return ExecutionResult(executed=False, reasons=[reason]), order

        # Phase Q / section 17: the instrument must exist and be tradable today, and - LIVE - the
        # account's margin must cover at least one unit. Both are business decisions (REJECTED).
        pre_notes: list = []
        pre_notes.extend(fo_notes)

        # Phase V1: the platform ceilings clamp the tenant's settings, then the Risk Guardian's
        # entry rules run before sizing - cool-down after a stop-out (R10), the drawdown ladder
        # (P2/P3) and event blackouts / size cuts (M8). A refusal is a REJECTED order; a size cut
        # is a smaller risk per trade for every sizer downstream.
        effective_risk_config, ceiling_notes = clamp_config(effective_risk_config, await risk_ceilings(session, user.tenant_id))
        pre_notes.extend(ceiling_notes)
        if mode == ExecutionMode.PAPER.value:
            from app.risk_engine import by_default
            if await by_default.enabled(session, user.tenant_id):
                # Z5 / P1-d: the user's own PAPER cap (or the ticket's checked one) and the organisation's - strictest wins.
                effective_risk_config, cap_notes = await by_default.apply_user_cap(session, user, effective_risk_config, amount=risk_cap_amount)
                pre_notes.extend(cap_notes)
        # A breached daily-loss limit (R5) outranks the guardian: the risk engine's own refusal
        # below engages the kill switch and raises DAILY_LOSS_LIMIT, which a guardian refusal
        # must not pre-empt.
        day_state = await build_trading_day_state(session, user)
        daily_amount = effective_risk_config.daily_loss_amount()
        daily_limit = -daily_amount if daily_amount is not None else -math.inf
        guard = await guard_entry(session, user.tenant_id, mode=mode, underlying=underlying_signal.symbol, cfg=effective_risk_config,
                                  # no user_id: these reasons are stored on the order trail and notified org-wide, so
                                  # the VIX gate words them without India VIX's values (BT1b)
                                  direction=underlying_signal.direction.value, owner_id=user.id) if day_state.daily_pnl > daily_limit else GuardianVerdict()
        if guard.reasons:
            order.reasons_json = json.dumps(guard.reasons)
            order = await transition_order(session, order, OrderStatus.REJECTED, detail="; ".join(guard.reasons))
            logger.warning("Order rejected - risk guardian: %s", "; ".join(guard.reasons))
            ORDERS.labels(mode=mode, status=order.status).inc()
            await notify(
                session, user.tenant_id, NotificationType.RISK_REJECTION,
                title=f"Order refused by the risk guardian: {signal.symbol}", message="; ".join(guard.reasons),
                severity=NotificationSeverity.WARNING, user_id=user.id, related_order_id=order.id,
            )
            return ExecutionResult(executed=False, reasons=guard.reasons), order
        effective_risk_config = apply_multiplier(effective_risk_config, guard.size_multiplier)
        pre_notes.extend(guard.notes)
        if contract_spec is None:
            contract_spec = get_contract_spec(signal.symbol)
        from app.trading.position_monitor import venue_beyond_nse  # local: the monitor imports the execution layer
        order_exchange = contract.exchange if contract is not None else (
            contract_spec.exchange if contract_spec else (venue_beyond_nse(exchange) or "NSE"))
        refusals, notes = await validate_instrument(
            session, symbol=signal.symbol, exchange=order_exchange, mode=mode,
            broker_name=getattr(broker, "name", None) if broker is not None else None, contract=contract,
        )
        pre_notes.extend(notes)
        if refusals:
            order.reasons_json = json.dumps(refusals)
            order = await transition_order(session, order, OrderStatus.REJECTED, detail="; ".join(refusals))
            logger.warning("Order rejected - instrument check: %s", "; ".join(refusals))
            ORDERS.labels(mode=mode, status=order.status).inc()
            await notify(
                session, user.tenant_id, NotificationType.REJECTION,
                title=f"Order rejected: {signal.symbol}", message="; ".join(refusals),
                severity=NotificationSeverity.WARNING, user_id=user.id, related_order_id=order.id,
            )
            return ExecutionResult(executed=False, reasons=refusals), order

        is_write = contract is not None and contract.position == OptionPosition.WRITE
        # Phase AS: a swing position is a delivery / carry-forward one, entry and stop alike. M68 TS1-009: the margin
        # probes ask the margin of the order's own product (NRML margin differs from MIS); `holding` is set here.
        entry_product = product_for(holding, contract.kind.value if contract is not None else "UNDERLYING", order_exchange)
        if execution_mode == ExecutionMode.LIVE and not is_write:
            probe = margin_probe_for(
                contract, symbol=signal.symbol, exchange=order_exchange,
                side=OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL,
                unit=float(contract_spec.lot_size) if contract_spec is not None else 1.0, unit_price=signal.entry,
            )
            try:
                margin_cap, margin_note = await live_margin_cap(broker, **probe, product=entry_product,
                                                                share=effective_risk_config.margin_share())
            except PreCheckRefusal as exc:
                reason = str(exc)
                order.reasons_json = json.dumps([reason])
                order = await transition_order(session, order, OrderStatus.REJECTED, detail=reason)
                logger.warning("Order rejected - margin: %s", reason)
                ORDERS.labels(mode=mode, status=order.status).inc()
                await notify(
                    session, user.tenant_id, NotificationType.RISK_REJECTION,
                    title=f"Order rejected: {signal.symbol}", message=reason,
                    severity=NotificationSeverity.WARNING, user_id=user.id, related_order_id=order.id,
                )
                return ExecutionResult(executed=False, reasons=[reason]), order
            pre_notes.append(margin_note)
            if margin_cap is not None:
                max_quantity = margin_cap if max_quantity is None else min(max_quantity, margin_cap)

        if is_write and execution_mode == ExecutionMode.LIVE:
            # Writing needs the broker's margin number; an unknown requirement is a refusal.
            try:
                cap_lots, note = await written_lot_cap(broker, contract, effective_risk_config.margin_share(), product=entry_product)
            except ContractExecutionError as exc:
                reason = str(exc)
                order.reasons_json = json.dumps([reason])
                order = await transition_order(session, order, OrderStatus.REJECTED, detail=reason)
                ORDERS.labels(mode=mode, status=order.status).inc()
                return ExecutionResult(executed=False, reasons=[reason]), order
            cap_quantity = cap_lots * contract.lot_size
            max_quantity = cap_quantity if max_quantity is None else min(max_quantity, cap_quantity)
            if plan is not None:
                plan.notes.append(note)

        state = await build_trading_day_state(session, user)
        order_router = OrderRouter(
            mode=execution_mode, risk_config=effective_risk_config, broker=broker,
            exchange=order_exchange,
            algo_id=algo_ids.order_algo_id(tenant.algo_id if tenant is not None else None, broker.name if broker is not None else None),
            product=entry_product,
            # P0.5 / T5 + T2: the deployment's entry style; the stop's order type follows what the broker accepts
            # for the instrument kind actually traded.
            order_style=order_style, market_protection_pct=market_protection_pct,
            is_option=(contract.kind.value == "OPTION") if contract is not None else None,
            paper_execution=paper_execution, freeze_quantity=freeze_quantity,
            # DEF-0 D3: an Autopilot option with no premium stop of the trader's own gets no broker stop at its premium
            place_protective_stop=not (plan is not None and plan.meta.get("instrument_kind") == "OPTION"
                                       and plan.meta.get("premium_stop_pct") is None),
            order_ref=order.id,
        )
        # M55 S4: the tag (with this row's client order id) is on the row before anything is sent, so the start-up order
        # sweeper (app/execution/order_sweeper.py) can find the order in the broker's book by it after a crash.
        order.algo_tag = order_router.entry_tag(signal.strategy_id)
        await session.commit()
        async def hierarchy_check(quantity: float):
            # Phase I1: every applicable risk limit (GLOBAL -> TENANT -> USER -> ACCOUNT ->
            # STRATEGY -> INSTRUMENT), strictest wins, each check recorded as a risk event.
            ctx = RiskContext(
                tenant_id=user.tenant_id, user_id=user.id, strategy_id=strategy_id, symbol=signal.symbol, quantity=quantity,
                entry=signal.entry, stop_loss=signal.stop_loss, capital=effective_risk_config.capital, mode=mode,
                order_id=order.id, account_id=account_id, deployment_id=deployment_id,
            )
            verdict = await evaluate_hierarchy(session, ctx, user=user)
            # Phase V1 (R4): the loss if every open stop hits, plus this trade's, within the cap.
            block, _ = await portfolio_risk_block(session, user.tenant_id, mode=mode, underlying=underlying_signal.symbol,
                                                  cfg=effective_risk_config, new_max_loss=abs(signal.entry - signal.stop_loss) * quantity)
            reasons = list(verdict.reasons) + ([block] if block else [])
            return verdict.allowed and block is None, reasons, verdict.notes

        with order_defaults.stop_band(mine.stop_limit_band_pct):     # M68 TS1-014: the protective stop's gap is his own
            result: ExecutionResult = await order_router.execute(signal, state, contract_spec=contract_spec, max_quantity=max_quantity,
                                                                pre_place_check=hierarchy_check)
        if plan is not None:
            result.reasons = plan.notes + result.reasons
        if pre_notes:
            result.reasons = pre_notes + result.reasons

        order.reasons_json = json.dumps(result.reasons)
        order.algo_tag = result.algo_tag
        if not result.executed:
            if result.system_failure:
                # The broker call itself errored (see OrderRouter.execute) - not a business
                # decision either way, so this is FAILED, not REJECTED, and a CRITICAL
                # SYSTEM_FAILURE notification (the same category app/reconciliation/ and
                # app/brokers/routes.py already use for a broker-side exception), not a routine
                # rejection notice.
                order = await transition_order(session, order, OrderStatus.FAILED, detail="; ".join(result.reasons))
                logger.error("Order failed - broker call raised: %s", "; ".join(result.reasons))
                if tenant is not None and mode == ExecutionMode.LIVE.value:
                    await mark_broker_uncertain(
                        session, tenant, f"order {order.id} ({signal.symbol}) FAILED: {'; '.join(result.reasons)}", user_id=user.id,
                    )
                    await session.commit()
                ORDERS.labels(mode=mode, status=order.status).inc()
                await notify(
                    session, user.tenant_id, NotificationType.SYSTEM_FAILURE,
                    title=f"Order failed: {signal.symbol}", message="; ".join(result.reasons),
                    severity=NotificationSeverity.CRITICAL, user_id=user.id, related_order_id=order.id,
                )
                return result, order

            if result.blocks:
                # M64 RM-3: every Risk limits block to the journal's record, and the trail / notice say where to change it.
                from app.risk_engine import risk_limits
                for item in result.blocks:
                    await risk_limits.log_block(session, tenant_id=user.tenant_id, user_id=user.id, item=item, symbol=signal.symbol,
                                                strategy_id=strategy_id, order_id=order.id, mode=mode, source=journal_source or origin)
                texts = {b["text"]: risk_limits.as_text(b) for b in result.blocks}
                result.reasons = [texts.get(r, r) for r in result.reasons]
                order.reasons_json = json.dumps(result.reasons)
            order = await transition_order(session, order, OrderStatus.REJECTED, detail="; ".join(result.reasons))
            is_daily_loss = any("daily loss limit" in r.lower() for r in result.reasons)
            logger.warning("Order rejected by risk engine: %s", "; ".join(result.reasons))
            ORDERS.labels(mode=mode, status=order.status).inc()
            await notify(
                session, user.tenant_id,
                NotificationType.DAILY_LOSS_LIMIT if is_daily_loss else NotificationType.RISK_REJECTION,
                title=f"Order rejected: {signal.symbol}", message="; ".join(result.reasons),
                severity=NotificationSeverity.CRITICAL if is_daily_loss else NotificationSeverity.WARNING,
                user_id=user.id, related_order_id=order.id,
            )
            return result, order

        if result.trade is not None:
            order.quantity = result.trade.quantity
        if result.broker_order_id:
            order.broker_order_id = result.broker_order_id
        if result.broker_uncertain and tenant is not None and mode == ExecutionMode.LIVE.value:
            # P0.5 / T1: the fill could not be confirmed or an unfilled remainder could not be cancelled - the
            # book is only trustworthy again after reconciliation, so LIVE entries pause now.
            await mark_broker_uncertain(session, tenant, f"order {order.id} ({signal.symbol}): " + "; ".join(
                r for r in result.reasons if "reconciliation" in r)[:400], user_id=user.id)
        venue = broker.name if (execution_mode == ExecutionMode.LIVE and broker is not None) else "paper broker"
        if execution_mode == ExecutionMode.LIVE and broker is not None:
            # D1: the tag the broker received, in the hash-chained audit log next to its order id (committed below).
            await algo_ids.audit_order(session, user.tenant_id, user.id, leg="ENTRY", broker_name=broker.name, symbol=signal.symbol,
                                       tag=result.algo_tag, broker_order_id=result.broker_order_id,
                                       ref=f"order {order.id}" + (f", stop {result.sl_order_id}" if result.sl_order_id else ""))
        order = await transition_order(session, order, OrderStatus.SUBMITTED, detail=f"Submitted to {venue}")
        order = await transition_order(session, order, OrderStatus.PENDING, detail="Awaiting fill")
        if result.partial_fill:
            # Safety rule 17 on the order trail too: the fill was smaller than requested. The
            # position (and its stop) already use the filled quantity (OrderRouter); the order
            # settles as FILLED on that quantity - no remainder is left working at the broker
            # for a market order.
            order = await transition_order(
                session, order, OrderStatus.PARTIAL_FILL,
                detail=f"Partial fill: {result.trade.quantity:g} of {result.requested_quantity:g}" if result.trade is not None else "Partial fill",
            )
        order = await transition_order(
            session, order, OrderStatus.FILLED,
            detail="Paper fill" if execution_mode == ExecutionMode.PAPER else f"Live fill {result.broker_order_id}",
        )
        logger.info("Order filled")
        ORDERS.labels(mode=mode, status="FILLED").inc()
        if result.trade is not None and result.trade.entry_latency_ms is not None:
            ORDER_ENTRY_LATENCY.labels(mode=mode).observe(result.trade.entry_latency_ms / 1000.0)

        if result.trade is not None:
            # BK2-8: the position carries the exchange its order went to (a contract plan names its own), so its exits
            # follow that venue's session - never the NSE default a row without an exchange used to fall back to.
            trade_meta = {"exchange": order_exchange, **(plan.meta if plan is not None else {}), **(manual_contract_meta or {})}
            if not trade_meta.get("exchange"):
                trade_meta["exchange"] = order_exchange
            trade_record = await persist_trade(
                session, user, result.trade, mode=execution_mode.value, broker_order_id=result.broker_order_id,
                sl_order_id=result.sl_order_id, deployment_id=deployment_id,
                contract_meta=trade_meta, exit_rules=exit_rules, account_id=account_id,
                holding=holding, journal_source=journal_source,
            )
            order.trade_id = trade_record.id
            update_log_context(trade_id=trade_record.id)
            order = await transition_order(
                session, order, OrderStatus.POSITION_OPEN, detail=f"Position opened (trade #{trade_record.id})"
            )
            logger.info("Position opened")
            await notify(
                session, user.tenant_id, NotificationType.ENTRY,
                title=f"{result.trade.direction.value} entry filled: {result.trade.symbol}",
                message="; ".join(result.reasons), severity=NotificationSeverity.INFO,
                user_id=user.id, related_trade_id=trade_record.id, related_order_id=order.id,
            )
            if result.sl_failed:
                # A live position with no broker-side stop is the single most dangerous state
                # this platform can be in - CRITICAL, immediately, to the whole tenant.
                closed_note = ""
                if config.LIVE_EXIT_IF_NO_STOP and broker is not None:
                    from app.trading.stop_guard import exit_unprotected  # local: stop_guard imports the monitor
                    try:
                        done = await exit_unprotected(session, trade_record, broker, "the protective stop failed at entry",
                                                      rejected=result.sl_rejected, broker_uncertain=result.broker_uncertain,
                                                      alert=False)
                    except Exception as exit_exc:  # noqa: BLE001 - the CRITICAL below must still go out
                        logger.error("No-stop exit errored for trade %s: %s", trade_record.id, exit_exc)
                        done = None
                    if done is not None:
                        closed_note = (" It was closed at once with a market exit (LIVE_EXIT_IF_NO_STOP)." if done else
                                       " The immediate market exit failed too - close it at the broker by hand.")
                        result.reasons.append("Closed at once: no broker-side stop" if done
                                              else "WARNING: immediate exit without a broker-side stop failed")
                await notify(
                    session, user.tenant_id, NotificationType.SYSTEM_FAILURE,
                    title=f"No broker-side stop-loss on {result.trade.symbol}",
                    message=("The entry filled but the protective stop order failed." + closed_note) if closed_note else
                            ("The entry filled but the protective stop order failed. The position monitor will "
                             "enforce the stop in software; place a manual stop at the broker as a backup."),
                    severity=NotificationSeverity.CRITICAL, user_id=user.id,
                    related_trade_id=trade_record.id, related_order_id=order.id,
                )

        return result, order
