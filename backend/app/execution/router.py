import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from app.execution.tagging import LEG_ENTRY, LEG_STOP, build_order_tag
from app.brokers.base import BrokerInterface
from app.brokers.circuit_breaker import breaker_for, observe_call
from app.brokers.models import BrokerOrderRequest
from app.core.enums import ExecutionMode, SignalDirection, OrderSide
from app.core.models import RiskConfig, Signal, Trade
from app.execution.paper_broker import PaperBroker
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
    ) -> None:
        self.executed = executed
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


class OrderRouter:
    """Routes an approved signal to Paper or Live execution. Every signal passes the Risk Engine first.

    LIVE mode never places an order without an authenticated `broker` (a concrete BrokerInterface
    implementation) being handed in - there is no fallback or silent no-op, per the platform's
    safety rule that live trading must never bypass risk validation or run without a real broker.
    """

    # How long to wait for a live market order's fill to show in the order book before falling
    # back to the signal's entry price as the provisional fill (reconciliation corrects it).
    fill_poll_attempts = 3
    fill_poll_delay_seconds = 0.5

    def __init__(
        self, mode: ExecutionMode, risk_config: RiskConfig, broker: Optional[BrokerInterface] = None,
        exchange: str = "NSE", product: str = "MIS", place_protective_stop: bool = True,
        algo_id: Optional[str] = None,
    ) -> None:
        self.mode = mode
        self.algo_id = algo_id
        self.risk_manager = RiskManager(risk_config)
        self.paper_broker = PaperBroker()
        self.broker = broker
        self.exchange = exchange
        self.product = product
        self.place_protective_stop = place_protective_stop

    async def execute(
        self, signal: Signal, state: TradingDayState, *, contract_spec=None, max_quantity: Optional[float] = None,
        pre_place_check=None,
    ) -> ExecutionResult:
        """`contract_spec` overrides the registry lookup (Phase F3: a resolved option/future
        carries its own lot size); `max_quantity` caps the risk-based size (max lots, margin).
        `pre_place_check(quantity)` (Phase I1) is awaited after sizing and before any fill or
        broker call; it returns (allowed, reasons, notes) from the risk hierarchy."""
        contract_spec = contract_spec or get_contract_spec(signal.symbol)
        decision = self.risk_manager.validate_and_size(signal, state, contract_spec=contract_spec)
        if not decision.approved:
            return ExecutionResult(executed=False, reasons=decision.reasons)
        notes: list[str] = []
        if max_quantity is not None and decision.quantity > max_quantity:
            unit = contract_spec.lot_size if contract_spec is not None else 1
            capped = int(max_quantity // unit) * unit if unit else max_quantity
            if capped <= 0:
                return ExecutionResult(executed=False, reasons=[f"Position cap ({max_quantity:g}) is below one lot ({unit:g})"])
            notes.append(f"Size capped from {decision.quantity:g} to {capped:g} by the deployment/margin limit")
            decision.quantity = capped
        if pre_place_check is not None:
            allowed, reasons, hierarchy_notes = await pre_place_check(decision.quantity)
            notes.extend(hierarchy_notes)
            if not allowed:
                return ExecutionResult(executed=False, reasons=notes + reasons)
        started = time.perf_counter()

        max_tag = getattr(self.broker, "max_tag_length", None) or 20
        entry_tag = build_order_tag(strategy_id=signal.strategy_id, leg=LEG_ENTRY, algo_id=self.algo_id, max_length=max_tag)

        if self.mode == ExecutionMode.PAPER:
            trade = self.paper_broker.open_trade(signal, decision.quantity, datetime.now(timezone.utc))
            trade.expected_price = signal.entry
            trade.entry_latency_ms = int((time.perf_counter() - started) * 1000)
            state.trades_today += 1
            state.open_positions += 1
            return ExecutionResult(executed=True, reasons=notes + ["Paper order filled"], trade=trade, algo_tag=entry_tag)

        if self.broker is None:
            raise LiveTradingNotConfigured(
                "Live trading is disabled until a real broker adapter (BrokerInterface) is authenticated "
                "and passed to OrderRouter. Use ExecutionMode.PAPER until then."
            )

        # Phase G2: the broker's circuit breaker (app/brokers/circuit_breaker.py). Open means the
        # broker has been failing across the platform: no new entry, said so on the trail. A
        # business decision, so REJECTED - the broker was never asked.
        breaker = breaker_for(self.broker.name)
        if not breaker.allow_submission():
            return ExecutionResult(executed=False, reasons=notes + [breaker.refusal_reason()])
        records_own_calls = not getattr(self.broker, "records_circuit", False)

        order_request = BrokerOrderRequest(
            symbol=signal.symbol,
            exchange=self.exchange,
            transaction_type=OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL,
            quantity=decision.quantity,
            order_type="MARKET",
            product=self.product,
            tag=entry_tag,
        )
        try:
            response = await self.broker.place_order(order_request)
        except Exception as exc:
            if records_own_calls:
                observe_call(self.broker.name, "place_order", exc)
            # A "kill broker mid-fill" disaster case (master prompt Section 50's own required
            # test category, confirmed directly: an uncaught exception here previously propagated
            # all the way up through execute_signal_for_user as an unhandled 500, leaving the
            # order stuck at RISK_CHECK forever - never REJECTED, never FAILED, invisible to any
            # "list my open/pending orders" query). Never known whether the broker actually
            # received this order before erroring - system_failure=True routes this to FAILED
            # (not REJECTED) precisely because it's not a business decision either way, and
            # reconciliation (app/reconciliation/) is what actually resolves the ambiguity against
            # the broker's own book.
            return ExecutionResult(
                executed=False, reasons=[f"Broker call failed: {exc}"], system_failure=True,
            )
        if records_own_calls:
            observe_call(self.broker.name, "place_order", None)
        if response.status in ("REJECTED", "CANCELLED"):
            return ExecutionResult(executed=False, reasons=[f"Broker rejected order: {response.message or response.status}"])

        state.trades_today += 1
        state.open_positions += 1
        reasons = notes + [f"Live order placed via {self.broker.name}: {response.order_id}"]

        fill_price, filled_quantity = await self._resolve_fill(response.order_id, fallback=signal.entry)
        if filled_quantity <= 0:
            # Nothing filled yet (or the broker cannot tell us): keep the requested size - the
            # reconciliation engine corrects it against the broker's book - but say so.
            filled_quantity = decision.quantity
            reasons.append("Fill quantity not confirmed by the order book - recorded as requested; reconciliation will correct")
        elif filled_quantity < decision.quantity:
            # Partial fill (safety rule 17): the position is the filled part, never the requested one.
            reasons.append(f"Partial fill: {filled_quantity:g} of {decision.quantity:g} - position and stop sized to the filled quantity")
            decision.quantity = filled_quantity
        trade = Trade(
            symbol=signal.symbol, strategy_id=signal.strategy_id, direction=signal.direction,
            entry_time=datetime.now(timezone.utc), entry_price=round(fill_price, 2), quantity=decision.quantity,
            stop_loss=signal.stop_loss, target1=signal.target1, target2=signal.target2,
            expected_price=signal.entry, entry_latency_ms=int((time.perf_counter() - started) * 1000),
        )

        sl_order_id: Optional[str] = None
        sl_failed = False
        if self.place_protective_stop:
            # The broker-side stop is the platform's safety net for the case this process dies
            # (or loses connectivity) while a live position is open: the exchange then still
            # closes it at the stop. Placed as SL-M on the opposite side at the signal's stop.
            try:
                sl_response = await self.broker.place_stop_loss_order(
                    signal.symbol, self.exchange,
                    OrderSide.SELL if signal.direction == SignalDirection.LONG else OrderSide.BUY,
                    decision.quantity, trigger_price=signal.stop_loss, product=self.product,
                    tag=build_order_tag(strategy_id=signal.strategy_id, leg=LEG_STOP, algo_id=self.algo_id, max_length=max_tag),
                )
                sl_order_id = sl_response.order_id
                reasons.append(f"Protective stop-loss placed: {sl_order_id} @ {signal.stop_loss}")
            except Exception as exc:  # noqa: BLE001 - a failed stop must never undo a real fill
                sl_failed = True
                reasons.append(f"WARNING: protective stop-loss order failed ({exc}) - software stop only")
                logger.error("Protective SL placement failed for %s after live fill %s: %s", signal.symbol, response.order_id, exc)

        return ExecutionResult(
            executed=True, reasons=reasons, trade=trade, broker_order_id=response.order_id,
            sl_order_id=sl_order_id, sl_failed=sl_failed, algo_tag=entry_tag,
        )

    async def _resolve_fill_price(self, order_id: str, fallback: float) -> float:
        price, _ = await self._resolve_fill(order_id, fallback)
        return price

    async def _resolve_fill(self, order_id: str, fallback: float) -> tuple[float, float]:
        """Reads the actual average fill price and filled quantity from the broker's order book, briefly retrying
        while a just-placed market order is still pending. Any broker that can't answer (or an
        order still unfilled after the retries) falls back to the signal entry, and the position
        reconciliation engine corrects the recorded entry against the broker's own book later."""
        for attempt in range(self.fill_poll_attempts):
            try:
                book = await self.broker.get_order_book()
            except Exception as exc:  # noqa: BLE001 - informational path, never fatal
                logger.debug("Order book unavailable for fill price lookup (%s); using signal entry", exc)
                return fallback, 0.0
            match = next((o for o in book if o.order_id == order_id), None)
            if match is not None and match.average_price and match.filled_quantity > 0:
                return float(match.average_price), float(match.filled_quantity)
            if attempt < self.fill_poll_attempts - 1:
                await asyncio.sleep(self.fill_poll_delay_seconds)
        logger.info("Fill price for %s not yet in order book; recording signal entry provisionally", order_id)
        return fallback, 0.0
