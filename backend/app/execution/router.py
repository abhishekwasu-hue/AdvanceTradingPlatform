import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.brokers.base import BrokerInterface, round_stop_trigger
from app.brokers.circuit_breaker import breaker_for, observe_call
from app.brokers.exceptions import is_clear_rejection
from app.brokers.models import ORDER_INTENT_ENTRY, BrokerOrderRequest
from app.compliance.order_policy import apply_entry_policy
from app.core.enums import ExecutionMode, OrderSide, SignalDirection
from app.core.models import RiskConfig, Signal, Trade
from app.execution.paper_charges import STATUTORY_ONLY, PaperCharges
from app.execution.tagging import LEG_ENTRY, LEG_STOP, build_order_tag, client_order_id
from app.instruments.registry import get_contract_spec
from app.risk_engine.risk_manager import RiskManager, TradingDayState

logger = logging.getLogger(__name__)


class LiveTradingNotConfigured(RuntimeError):
    """Raised whenever LIVE mode is requested without a real, authenticated broker adapter wired in.

    This is intentional: the platform must never place a live order through a stub. A concrete
    BrokerInterface implementation (Zerodha/Upstox/Angel One/Fyers/Dhan/...) has to be authenticated
    and passed to OrderRouter before ExecutionMode.LIVE can do anything.
    """


class ExecutionResult:
    def __init__(
        self, executed: bool, reasons: list[str], trade: Optional[Trade] = None,
        broker_order_id: Optional[str] = None, system_failure: bool = False,
        sl_order_id: Optional[str] = None, sl_failed: bool = False, algo_tag: Optional[str] = None,
        partial_fill: bool = False, requested_quantity: Optional[float] = None, broker_uncertain: bool = False,
        sl_rejected: bool = False, blocks: list | None = None,
    ) -> None:
        self.executed = executed
        # M64 RM-3: the Risk limits refusals as data (app/risk_engine/risk_limits.block) for the block message and the log.
        self.blocks = list(blocks or [])
        # G-LIVE: the stop failed because the broker clearly declined it (not a timeout) - no stop can be standing.
        self.sl_rejected = sl_rejected
        # P0.5 / T1: the broker's book could not confirm what filled (book unsupported, cancel of an unfilled
        # order failed) - the position is recorded as requested and the tenant must be reconciled before the
        # next LIVE entry (signal_execution marks it broker-uncertain).
        self.broker_uncertain = broker_uncertain
        # Phase Q: the broker filled less than was sent (safety rule 17). `requested_quantity`
        # is what the risk engine sized; `trade.quantity` is what filled.
        self.partial_fill = partial_fill
        self.requested_quantity = requested_quantity
        # The order tag sent to the broker for the entry leg (Phase D1), or the tag a paper order
        # would have carried, so the order trail is identical in both modes.
        self.algo_tag = algo_tag
        self.reasons = reasons
        self.trade = trade
        self.broker_order_id = broker_order_id
        # LIVE only: the broker-side protective stop-loss order placed right after the fill, and
        # whether placing it failed (position is then open *without* a broker-side stop - the
        # position monitor still enforces the software stop, but the operator must be told).
        self.sl_order_id = sl_order_id
        self.sl_failed = sl_failed
        # Distinguishes "the broker was reachable and explicitly declined this order" (a business
        # rejection - REJECTED) from "the broker call itself errored/timed out, so this platform
        # never got a real answer" (a system failure - FAILED) - see OrderRouter.execute's broker
        # exception handling below. Always False for PAPER fills/rejections, which never make a
        # real network call that can fail this way.
        self.system_failure = system_failure


_FILLED_STATUSES = {"COMPLETE", "FILLED", "TRADED", "EXECUTED"}
_REJECTED_STATUSES = {"REJECTED", "CANCELLED", "CANCELED", "EXPIRED", "LAPSED"}
# M68 PR-1 (TS1-011): NOT a default for an entry any more - an entry's protection band is the user's own (his bot's or his
# Trade settings, app/execution/order_defaults.py); with none, an entry that needs one is not placed. The value stays as
# the broker adapters' exit-only fallback (app/execution/marketable_limit.py DEFAULT_PROTECTION_PCT, ADR-0004).
DEFAULT_MARKET_PROTECTION_PCT = 0.5
ORDER_STYLES = ("MARKET", "PROTECTED_LIMIT")
PAPER_EXECUTIONS = ("direct", "adapter")
_TICK = 0.05


class _BandNotSet(Exception):
    """An entry needs a marketable limit and the user saved no price protection (M68 PR-1)."""


def protected_limit_price(reference: float, side: OrderSide, protection_pct: float) -> float:
    """P0.5 / T5: the limit of a marketable limit order - `protection_pct` above the reference for a buy,
    below for a sell - on the exchange tick."""
    band = max(0.0, float(protection_pct)) / 100.0
    raw = reference * (1 + band) if side == OrderSide.BUY else reference * (1 - band)
    return max(_TICK, round(round(raw / _TICK) * _TICK, 2))


_LEG_FILLED, _LEG_NONE, _LEG_REJECTED, _LEG_FAILED = "FILLED", "NONE", "REJECTED", "FAILED"


@dataclass
class _Leg:
    """M55 S3: what one order at the venue came to - FILLED (all or part; `uncertain` when the book could not confirm),
    NONE (nothing filled, nothing working), REJECTED (declined) or FAILED (the call errored)."""
    kind: str
    order_id: str | None = None
    reasons: list = field(default_factory=list)
    filled: float = 0.0
    price: float = 0.0
    uncertain: bool = False


class OrderRouter:
    """Routes an approved signal to Paper or Live execution. Every signal passes the Risk Engine first.

    LIVE mode never places an order without an authenticated `broker` (a concrete BrokerInterface
    implementation) being handed in - there is no fallback or silent no-op, per the platform's
    safety rule that live trading must never bypass risk validation or run without a real broker.

    M55 S3 (A6): PAPER and LIVE take ONE path after the risk checks (`_route`); only the venue differs (the paper venue
    or the user's broker). The LIVE entry style is unchanged by it: MARKET by default; a marketable LIMIT only when the
    deployment's `order_style` is PROTECTED_LIMIT or the broker's order-type policy maps MARKET to a limit
    (compliance/order_policy.py, `map_to_limit`); a venue that takes no market order (its capabilities) gets the same
    marketable limit, the protection band past the checked price (TM-3, as its own adapter would send it).
    """

    # How long to wait for a live market order's fill to show in the order book before falling
    # back to the signal's entry price as the provisional fill (reconciliation corrects it).
    fill_poll_attempts = 3
    fill_poll_delay_seconds = 0.5

    def __init__(
        self, mode: ExecutionMode, risk_config: RiskConfig, broker: Optional[BrokerInterface] = None,
        exchange: str = "NSE", product: str | None = None, place_protective_stop: bool = True,
        algo_id: str | None = None, order_style: str | None = None, market_protection_pct: float | None = None,
        is_option: Optional[bool] = None, paper_execution: str = "direct", freeze_quantity: Optional[int] = None,
        order_ref: int | None = None, paper_charges: PaperCharges | None = None,
    ) -> None:
        self.mode = mode
        # M55 S4: the `orders` row this execution belongs to - its entry and protective stop carry client order ids
        # derived from it (app/execution/tagging.py), so the start-up sweeper finds them in the broker's book by id.
        self.order_ref = order_ref
        # TM-1: how a PAPER order fills - "direct" (PaperBroker.open_trade, every path not yet moved) or "adapter" (the
        # PaperExecutionAdapter, a BrokerInterface over the same fill and cost model). Paths move one at a time, each with
        # an old-vs-new parity test (tests/test_tm1_paper_adapter.py). LIVE ignores it.
        if paper_execution not in PAPER_EXECUTIONS:
            raise ValueError(f"paper_execution must be one of {PAPER_EXECUTIONS}")
        self.paper_execution = paper_execution
        # M55 S3 (A6): deprecated - both values now take the ONE entry path (`_route`); the PAPER fill is always the paper
        # venue's. Kept (and still checked) so callers that pass it keep working.
        # Part 7 smart execution (flag smart_execution): the exchange's freeze quantity for this contract; an adapter
        # order above it goes as several orders of whole lots below it. None = not known / not asked: one order.
        self.freeze_quantity = freeze_quantity
        self.algo_id = algo_id
        # P0.5 / T5: MARKET or PROTECTED_LIMIT - a marketable limit `market_protection_pct` past the signal price.
        # M68 PR-1 (TS1-009 / 010 / 011): the product, the style and the band are the caller's (the user's own values,
        # app/execution/order_defaults.py); None = Not set, and an entry that needs it is not placed (no MIS / MARKET /
        # 0.5 % chosen here). P0.5 / T2: `is_option` tells the stop which order type the broker accepts (None = infer
        # from the symbol).
        self.order_style = order_style.upper() if order_style else None
        self.market_protection_pct = market_protection_pct
        self.is_option = is_option
        self.risk_manager = RiskManager(risk_config)
        # M68 PR-7 (TS1-069): a PAPER fill pays the trader's own brokerage / slippage (None: statutory charges only).
        self.paper_broker = (paper_charges or STATUTORY_ONLY).broker()
        # M55 S3: the paper venue a PAPER entry fills on (None: a PaperExecutionAdapter over `paper_broker` per entry).
        self.paper_venue: BrokerInterface | None = None
        self.broker = broker
        self.exchange = exchange
        self.product = product
        self.place_protective_stop = place_protective_stop

    def entry_tag(self, strategy_id: str) -> str:
        """The tag this router's entry goes out with (M55 S4: with the order row's client order id when it has one)."""
        return build_order_tag(strategy_id=strategy_id, leg=LEG_ENTRY, algo_id=self.algo_id,
                               max_length=getattr(self.broker, "max_tag_length", None) or 20,
                               broker=getattr(self.broker, "name", None),
                               client_id=client_order_id("E", self.order_ref) if self.order_ref is not None else None)

    async def execute(
        self, signal: Signal, state: TradingDayState, *, contract_spec=None, max_quantity: Optional[float] = None,
        pre_place_check=None,
    ) -> ExecutionResult:
        """`contract_spec` overrides the registry lookup (Phase F3: a resolved option/future
        carries its own lot size); `max_quantity` caps the risk-based size (max lots, margin).
        `pre_place_check(quantity)` (Phase I1) is awaited after sizing and before any fill or
        broker call; it returns (allowed, reasons, notes) from the risk hierarchy."""
        contract_spec = contract_spec or get_contract_spec(signal.symbol)
        # DEF-0: with no risk per trade set the size is the caller's cap (the trader's lots / max lots), else one lot.
        decision = self.risk_manager.validate_and_size(signal, state, contract_spec=contract_spec, requested_quantity=max_quantity)
        if not decision.approved:
            return ExecutionResult(executed=False, reasons=decision.reasons, blocks=decision.blocks)
        notes: list[str] = list(decision.warnings)      # M64: e.g. the no-stop fallback's WARNING
        if max_quantity is not None and decision.quantity > max_quantity:
            unit = contract_spec.lot_size if contract_spec is not None else 1
            capped = int(max_quantity // unit) * unit if unit else max_quantity
            if capped <= 0:
                return ExecutionResult(executed=False, reasons=[f"The size allowed ({max_quantity:g}) is less than one lot ({unit:g})."])
            notes.append(f"Size capped from {decision.quantity:g} to {capped:g} by the deployment/margin limit")
            decision.quantity = capped
        if pre_place_check is not None:
            allowed, reasons, hierarchy_notes = await pre_place_check(decision.quantity)
            notes.extend(hierarchy_notes)
            if not allowed:
                return ExecutionResult(executed=False, reasons=notes + reasons)
        started = time.perf_counter()

        entry_tag = self.entry_tag(signal.strategy_id)
        return await self._route(signal, state, decision.quantity, notes, entry_tag, started, contract_spec=contract_spec)

    async def _route(self, signal: Signal, state: TradingDayState, quantity: float, notes: list[str],
                     entry_tag: str | None, started: float, *, contract_spec=None) -> ExecutionResult:
        """M55 S3 (A6): ONE entry path for PAPER and LIVE, for an already sized and checked `quantity`. The venue is the
        only difference: PAPER fills on the paper venue (the PaperExecutionAdapter over PaperBroker's fill and cost model,
        never a broker), LIVE at the user's own authenticated broker. The circuit breaker, the protected limit, the
        order-type policy, the fill poll, the partial-fill handling and the freeze-quantity slicing run the same for both.
        Only LIVE places the broker-side protective stop (the paper venue keeps no order between calls: a PAPER stop is
        the position monitor's and the trigger engine's, as before)."""
        venue = self._venue_for(signal)
        practice = bool(getattr(venue, "paper", False))
        max_tag = getattr(self.broker, "max_tag_length", None) or 20

        # M68 PR-1: the user's own product / order type / band, checked before the broker is asked anything.
        from app.execution import order_defaults
        if self.product is None:
            return ExecutionResult(executed=False, reasons=notes + [order_defaults.needs_setting("holding")])
        if self.order_style is None:
            return ExecutionResult(executed=False, reasons=notes + [order_defaults.needs_setting("order_type")])
        band = self.market_protection_pct
        needs_band = order_defaults.needs_setting("price_protection_pct")

        def limit_for(side: OrderSide) -> float:
            if band is None:
                raise _BandNotSet
            return protected_limit_price(signal.entry, side, band)

        venue_caps = getattr(venue, "capabilities", None)
        if band is None and (self.order_style == "PROTECTED_LIMIT"
                             or (venue_caps is not None and not venue_caps.market_orders_allowed)):
            return ExecutionResult(executed=False, reasons=notes + [needs_band])

        # Phase G2: the broker's circuit breaker (app/brokers/circuit_breaker.py). Open means the
        # broker has been failing across the platform: no new entry, said so on the trail. A
        # business decision, so REJECTED - the broker was never asked.
        breaker = breaker_for(venue.name)
        if not breaker.allow_submission():
            return ExecutionResult(executed=False, reasons=notes + [breaker.refusal_reason()])
        records_own_calls = not getattr(venue, "records_circuit", False)
        order_request = BrokerOrderRequest(
            symbol=signal.symbol,
            exchange=self.exchange,
            transaction_type=OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL,
            quantity=quantity,
            order_type="MARKET",
            product=self.product,
            tag=entry_tag,
            intent=ORDER_INTENT_ENTRY,
        )
        if self.order_style == "PROTECTED_LIMIT" and band is not None:
            limit = protected_limit_price(signal.entry, order_request.transaction_type, band)
            order_request = order_request.model_copy(update={"order_type": "LIMIT", "price": limit})
            notes.append(f"Protected limit entry at {limit} ({band:g}% past {signal.entry})")
        # Part D3: the broker's order-type policy for algo entries (rule-set data; the default changes nothing). The paper
        # venue practising for a broker (`venue`) follows that broker's policy, the plain paper venue the default one.
        try:
            policed, policy_note = apply_entry_policy(
                order_request, getattr(venue, "venue", None) or venue.name, lambda: limit_for(order_request.transaction_type))
        except _BandNotSet:
            return ExecutionResult(executed=False, reasons=notes + [needs_band])
        if policed is None:
            return ExecutionResult(executed=False, reasons=notes + [policy_note])
        order_request = policed
        if policy_note:
            notes.append(policy_note)
        caps = getattr(venue, "capabilities", None)
        if order_request.order_type.upper() == "MARKET" and caps is not None and not caps.market_orders_allowed:
            # TM-3: where the venue takes no market order, a marketable limit the protection band past the checked price
            # (one price rule for every path: app/execution/smart_execution.order_type_for).
            from app.execution.smart_execution import order_type_for
            if band is None:      # the broker takes no market order (a fact): the entry needs his own band (TS1-013)
                return ExecutionResult(executed=False, reasons=notes + [needs_band])
            order_type, limit = order_type_for(False, order_request.transaction_type.value, signal.entry, band)
            order_request = order_request.model_copy(update={"order_type": order_type, "price": limit})

        # Part 7 smart execution: above the exchange's freeze quantity the order goes as several orders of whole lots
        # (only when the caller states the freeze quantity; none = one order).
        lot_size = int(contract_spec.lot_size) if contract_spec is not None and contract_spec.lot_size else 1
        parts: list[float] = [quantity]
        if self.freeze_quantity:
            from app.execution.smart_execution import slice_units
            parts = [float(u) for u in slice_units(int(round(quantity)), lot_size, self.freeze_quantity)] or [quantity]

        reasons = list(notes)
        filled = value = 0.0
        prices: list[float] = []
        broker_uncertain = stopped = False
        first_order_id: str | None = None
        for n, part in enumerate(parts, start=1):
            request = order_request if len(parts) == 1 else order_request.model_copy(
                update={"quantity": part, "client_ref": f"slice-{n}"})
            leg = await self._place_one(venue, request, signal.entry, records_own_calls, practice)
            if filled <= 0 and leg.kind != _LEG_FILLED:
                # Nothing is open yet: the same answers as a single order (FAILED / REJECTED / not filled).
                if leg.kind == _LEG_FAILED:
                    return ExecutionResult(executed=False, reasons=leg.reasons, system_failure=True)
                if leg.kind == _LEG_REJECTED:
                    return ExecutionResult(executed=False, reasons=leg.reasons)
                return ExecutionResult(executed=False, broker_order_id=None if practice else leg.order_id,
                                       reasons=reasons + leg.reasons)
            if leg.kind != _LEG_FILLED:
                # A later slice did not fill: the earlier slices are a real position - booked, never dropped.
                reasons.extend(f"Order {n}: {text}" for text in leg.reasons)
                broker_uncertain = broker_uncertain or leg.kind == _LEG_FAILED
                stopped = True
                break
            reasons.extend(leg.reasons)
            first_order_id = first_order_id or leg.order_id
            broker_uncertain = broker_uncertain or leg.uncertain
            filled += leg.filled
            value += leg.filled * leg.price
            prices.append(leg.price)
            if leg.uncertain:
                stopped = n < len(parts)          # the venue could not confirm this order: nothing more is sent
                break
            if leg.filled + 1e-9 < part:
                stopped = n < len(parts)          # no further slice after one that did not fill in full
                break
        if practice:
            reasons.insert(len(notes), "Paper order filled")
        if len(parts) > 1:
            reasons.append(f"Sent as {len(parts)} orders below the exchange's freeze limit ({self.freeze_quantity:g} qty)"
                           + (" (the later slices were not sent)" if stopped else ""))
            if filled + 1e-9 < quantity:
                reasons.append(f"Partial fill: {filled:g} of {quantity:g} - position and stop sized to the filled quantity")
        partial_fill = filled + 1e-9 < quantity
        fill_price = prices[0] if len(prices) == 1 else value / filled
        state.trades_today += 1
        state.open_positions += 1
        trade = Trade(
            symbol=signal.symbol, strategy_id=signal.strategy_id, direction=signal.direction,
            entry_time=datetime.now(timezone.utc), entry_price=round(fill_price, 2), quantity=filled,
            stop_loss=signal.stop_loss, target1=signal.target1, target2=signal.target2,
            expected_price=signal.entry, entry_latency_ms=int((time.perf_counter() - started) * 1000),
        )

        sl_order_id: Optional[str] = None
        sl_failed = sl_rejected = False
        # DEF-0 D7: no stop set (0) - no broker stop either
        if self.place_protective_stop and not practice and signal.stop_loss is not None and signal.stop_loss > 0:
            # The broker-side stop is the platform's safety net for the case this process dies
            # (or loses connectivity) while a live position is open: the exchange then still
            # closes it at the stop. Placed as SL-M on the opposite side at the signal's stop.
            try:
                stop_side = OrderSide.SELL if signal.direction == SignalDirection.LONG else OrderSide.BUY
                stop_trigger = round_stop_trigger(signal.stop_loss, stop_side, symbol=signal.symbol, exchange=self.exchange)   # on the tick, away from the market
                stop_type, stop_limit = self.broker.stop_order_params(signal.symbol, stop_side, stop_trigger, is_option=self.is_option)
                sl_response = await self.broker.place_stop_loss_order(
                    signal.symbol, self.exchange, stop_side,
                    filled, trigger_price=stop_trigger, product=self.product,
                    tag=build_order_tag(strategy_id=signal.strategy_id, leg=LEG_STOP, algo_id=self.algo_id, max_length=max_tag,
                                        broker=getattr(self.broker, "name", None),
                                        client_id=client_order_id("S", self.order_ref) if self.order_ref is not None else None),
                    **({"is_option": self.is_option} if self.is_option is not None else {}),
                )
                sl_order_id = sl_response.order_id
                reasons.append(f"Protective stop-loss placed: {sl_order_id} @ {stop_trigger:g}" + (f" ({stop_type}, limit {stop_limit})" if stop_type != "SL-M" else ""))
            except Exception as exc:  # noqa: BLE001 - a failed stop must never undo a real fill
                sl_failed = True
                sl_rejected = is_clear_rejection(exc)
                reasons.append(f"WARNING: protective stop-loss order failed ({exc}) - software stop only")
                logger.error("Protective SL placement failed for %s after live fill %s: %s", signal.symbol, first_order_id, exc)

        return ExecutionResult(
            executed=True, reasons=reasons, trade=trade, broker_order_id=None if practice else first_order_id,
            sl_order_id=sl_order_id, sl_failed=sl_failed, algo_tag=entry_tag,
            partial_fill=partial_fill, requested_quantity=quantity, broker_uncertain=broker_uncertain,
            sl_rejected=sl_rejected,
        )

    def _venue_for(self, signal: Signal) -> BrokerInterface:
        """M55 S3: where this entry is sent. PAPER: the paper venue - the broker handed in when it is itself a paper venue
        (`paper` True), else `paper_venue` (a test's), else a PaperExecutionAdapter over this router's PaperBroker - marked
        at the price the caller checked (`signal.entry`). A real broker handed in with PAPER is never sent an order. LIVE:
        the authenticated broker, else LiveTradingNotConfigured (never a stub and never the paper venue)."""
        if self.mode == ExecutionMode.PAPER:
            if self.broker is not None and getattr(self.broker, "paper", False):
                venue = self.broker
            else:
                from app.execution.paper_adapter import PaperExecutionAdapter
                venue = self.paper_venue or PaperExecutionAdapter(self.paper_broker)
            if hasattr(venue, "mark"):
                venue.mark(signal.symbol, signal.entry, self.exchange)
            return venue
        if self.broker is None or getattr(self.broker, "paper", False):
            raise LiveTradingNotConfigured(
                "Live trading is disabled until a real broker adapter (BrokerInterface) is authenticated "
                "and passed to OrderRouter. Use ExecutionMode.PAPER until then."
            )
        return self.broker

    async def _place_one(self, venue: BrokerInterface, request: BrokerOrderRequest, fallback: float,
                         records_own_calls: bool, practice: bool) -> "_Leg":
        """One order at the venue, read back from its book - the LIVE rules (P0.5 / T1) for both modes: a call that
        errored is FAILED (never known whether it arrived); a declined one REJECTED; nothing filled in the poll window -
        cancelled and confirmed in the book, else recorded as requested and marked uncertain; a partial fill keeps the
        filled part and cancels the remainder. Sent once: nothing here retries a placement."""
        try:
            response = await venue.place_order(request)
        except Exception as exc:
            if records_own_calls:
                observe_call(venue.name, "place_order", exc)
            # A "kill broker mid-fill" disaster case (master prompt Section 50's own required
            # test category): never known whether the broker actually received this order before
            # erroring - FAILED (not REJECTED) precisely because it's not a business decision either
            # way, and reconciliation (app/reconciliation/) resolves it against the broker's own book.
            return _Leg(_LEG_FAILED, reasons=[f"Broker call failed: {exc}"])
        if records_own_calls:
            observe_call(venue.name, "place_order", None)
        if response.status in ("REJECTED", "CANCELLED"):
            what = "Paper order not filled" if practice else "Broker rejected order"
            return _Leg(_LEG_REJECTED, order_id=response.order_id, reasons=[f"{what}: {response.message or response.status}"])

        leg = _Leg(_LEG_FILLED, order_id=response.order_id)
        if not practice:
            leg.reasons.append(f"Live order placed via {venue.name}: {response.order_id}")
        fill_price, filled_quantity, book_entry = await self._resolve_fill_with_status(response.order_id, fallback=fallback, broker=venue)
        if filled_quantity <= 0:
            # P0.5 / T1: nothing filled inside the poll window. Never leave an unconfirmed order working while the
            # platform books a position against the signal price.
            book_status = (book_entry.status if book_entry is not None else "").upper()
            if book_status in _REJECTED_STATUSES:
                leg.kind = _LEG_NONE
                leg.reasons.append(f"Order {response.order_id} ended {book_status} at the broker with no fill")
                return leg
            if book_entry is None and not self._book_readable:
                # The broker cannot show us its book: cancelling blind could close a fill we cannot see.
                filled_quantity = request.quantity
                leg.uncertain = True
                leg.reasons.append("Order book unavailable - fill not confirmed; recorded as requested, LIVE entries paused until reconciliation")
            else:
                cancelled, late_price, late_quantity = await self._cancel_unfilled(response.order_id, fallback=fallback, broker=venue)
                if late_quantity > 0:
                    fill_price, filled_quantity = late_price, late_quantity   # filled while we were cancelling
                    leg.reasons.append(f"Order filled during the cancel attempt: {late_quantity:g} @ {late_price}")
                elif cancelled:
                    leg.kind = _LEG_NONE
                    leg.reasons.append(
                        f"No fill within {self.fill_poll_attempts * self.fill_poll_delay_seconds:g}s - order {response.order_id} cancelled at the broker (book confirms), no position opened")
                    return leg
                else:
                    filled_quantity = request.quantity
                    leg.uncertain = True
                    leg.reasons.append("Unfilled order could not be cancelled - recorded as requested, LIVE entries paused until reconciliation")
        if 0 < filled_quantity < request.quantity:
            # Partial fill (safety rule 17): the position is the filled part, never the requested one, and the
            # remainder must not keep working at the broker (P0.5 / T1).
            leg.reasons.append(f"Partial fill: {filled_quantity:g} of {request.quantity:g} - position and stop sized to the filled quantity")
            remainder_status = (book_entry.status or "").upper() if book_entry is not None else ""
            if remainder_status not in (_FILLED_STATUSES | _REJECTED_STATUSES):      # a terminal remainder needs no cancel
                try:
                    await venue.cancel_order(response.order_id)
                    leg.reasons.append(f"Unfilled remainder {request.quantity - filled_quantity:g} cancelled" + ("" if practice else " at the broker"))
                except Exception as exc:  # noqa: BLE001
                    leg.uncertain = True
                    leg.reasons.append(f"Could not cancel the unfilled remainder ({exc}) - LIVE entries paused until reconciliation")
        leg.price, leg.filled = float(fill_price), float(filled_quantity)
        return leg

    async def _execute_paper_adapter(self, signal: Signal, state: TradingDayState, quantity: float, notes: list[str],
                                     entry_tag: Optional[str], started: float, *, lot_size: int = 1) -> ExecutionResult:
        """Deprecated (M55 S3): a thin shim - PAPER and LIVE share one path (`_route`) now."""
        return await self._route(signal, state, quantity, notes, entry_tag, started)

    async def _resolve_fill_price(self, order_id: str, fallback: float) -> float:
        price, _ = await self._resolve_fill(order_id, fallback)
        return price

    async def _resolve_fill(self, order_id: str, fallback: float) -> tuple[float, float]:
        price, quantity, _ = await self._resolve_fill_with_status(order_id, fallback)
        return price, quantity

    _book_readable = True

    async def _resolve_fill_with_status(self, order_id: str, fallback: float, broker: BrokerInterface | None = None):
        """Reads the actual average fill price and filled quantity from the broker's order book, briefly retrying
        while a just-placed order is still pending, and returns the last book entry seen (None when the broker
        cannot show its book - `_book_readable` is then False). An order still unfilled after the retries comes
        back with quantity 0 and the caller decides (P0.5 / T1: cancel it, never book a phantom position)."""
        venue = broker or self.broker
        match = None
        for attempt in range(self.fill_poll_attempts):
            try:
                book = await venue.get_order_book()
            except Exception as exc:  # noqa: BLE001 - informational path, never fatal
                logger.debug("Order book unavailable for fill price lookup (%s); using signal entry", exc)
                self._book_readable = False
                return fallback, 0.0, None
            self._book_readable = True
            match = next((o for o in book if o.order_id == order_id), None)
            if match is not None and match.average_price and match.filled_quantity > 0:
                return float(match.average_price), float(match.filled_quantity), match
            if match is not None and (match.status or "").upper() in _REJECTED_STATUSES:
                return fallback, 0.0, match
            if attempt < self.fill_poll_attempts - 1:
                await asyncio.sleep(self.fill_poll_delay_seconds)
        logger.info("Fill for %s not yet in order book after %s looks", order_id, self.fill_poll_attempts)
        return fallback, 0.0, match

    async def _cancel_unfilled(self, order_id: str, fallback: float,
                               broker: BrokerInterface | None = None) -> tuple[bool, float, float]:
        """Cancels an order the book still shows unfilled. Returns (cancel confirmed, late fill price, late fill
        quantity). A cancel *request* that the adapter accepted proves nothing - every adapter answers "CANCELLED"
        to an accepted request and Kite/Upstox cancels are asynchronous - so the cancel counts only once the
        broker's book shows the order terminal with nothing filled. A fill that lands meanwhile is honoured; an
        order the book cannot show as terminal stays unconfirmed (the caller records it and pauses LIVE entries)."""
        venue = broker or self.broker
        try:
            await venue.cancel_order(order_id)
        except Exception as exc:  # noqa: BLE001 - "already executed" is the common cause: the book decides below
            logger.error("Cancel of unfilled order %s failed: %s", order_id, exc)
        for attempt in range(self.fill_poll_attempts):
            try:
                book = await venue.get_order_book()
            except Exception:  # noqa: BLE001
                return False, fallback, 0.0
            match = next((o for o in book if o.order_id == order_id), None)
            if match is not None and match.average_price and match.filled_quantity > 0:
                return False, float(match.average_price), float(match.filled_quantity)
            if match is not None and (match.status or "").upper() in _REJECTED_STATUSES:
                return True, fallback, 0.0
            if attempt < self.fill_poll_attempts - 1:
                await asyncio.sleep(self.fill_poll_delay_seconds)
        return False, fallback, 0.0
        logger.info("Fill price for %s not yet in order book; recording signal entry provisionally", order_id)
        return fallback, 0.0
