"""TM-1 (owner order ATP_SESSION_TERMINAL_MASTER.md, Part 8; approved 23 Oct): PAPER as a broker adapter.

- `PaperExecutionAdapter` implements BrokerInterface over the existing PaperBroker fill and cost model: market, marketable
  limit, triggered stops; no resting order (refused in plain words); partial fills; idempotent client references; books;
- it is registered as an execution adapter, not as a broker account (no connect card);
- the F&O ticket's single-leg PAPER order is the first path moved onto it, and the old path (PaperBroker directly) and
  the new one give IDENTICAL fills, charges, order trail rows and P&L - at the router level over many signals and through
  the whole pipeline (ticket -> order -> trail -> position -> exit);
- kill switch, idempotency and exits (ADR-0004) behave exactly as before."""
import asyncio
import json
import random
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerOrderRequest
from app.brokers.registry import EXECUTION_ADAPTERS, available_brokers, get_execution_adapter
from app.core.enums import AssetClass, ExecutionMode, OrderSide, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import OrderEventRecord, OrderRecord, TradeRecord
from app.execution import india_costs
from app.execution.paper_adapter import PaperExecutionAdapter
from app.execution.paper_broker import PaperBroker
from app.execution.router import OrderRouter
from app.execution.tagging import client_order_id
from app.instruments.models import ContractSpec
from app.paper import fno_ticket
from app.risk_engine.risk_manager import TradingDayState
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import LOT, _body, _contract, _expiry, _org_tenant, _place
from tests.test_z3_approval_entries import _price


def _run(coro):
    return asyncio.run(coro)


def _req(side=OrderSide.BUY, qty=75.0, order_type="MARKET", price=None, trigger=None, ref=None, symbol="NIFTY30OCT2625000CE"):
    return BrokerOrderRequest(symbol=symbol, exchange="NFO", transaction_type=side, quantity=qty, order_type=order_type, price=price,
                              trigger_price=trigger, product="NRML", client_ref=ref)


# --- the adapter --------------------------------------------------------------------------------------------------------

def test_it_is_a_broker_adapter_registered_as_an_execution_venue_not_a_broker_account():
    adapter = get_execution_adapter("paper")
    assert isinstance(adapter, BrokerInterface) and isinstance(adapter, PaperExecutionAdapter)
    assert EXECUTION_ADAPTERS == {"paper": PaperExecutionAdapter}
    assert "paper" not in available_brokers()              # no credentials, no connect card
    with pytest.raises(ValueError):
        get_execution_adapter("nope")


def test_a_market_order_fills_at_the_paper_brokers_slippage_of_the_price():
    pb = PaperBroker()
    adapter = PaperExecutionAdapter(pb)
    adapter.mark("NIFTY30OCT2625000CE", 100.0, "NFO")
    buy = _run(adapter.place_order(_req()))
    sell = _run(adapter.place_order(_req(side=OrderSide.SELL)))
    assert buy.status == sell.status == "COMPLETE"
    assert adapter.order(buy.order_id).average_price == pytest.approx(pb._slip(100.0, SignalDirection.LONG))
    assert adapter.order(sell.order_id).average_price == pytest.approx(pb._slip(100.0, SignalDirection.SHORT))
    assert adapter.charges(buy.order_id) > 0
    trades = _run(adapter.get_trade_book())
    assert [t.order_id for t in trades] == [buy.order_id, sell.order_id]
    assert _run(adapter.get_positions()) == []          # bought and sold the same quantity: flat


def test_charges_are_the_dated_cost_model_of_the_fill():
    adapter = PaperExecutionAdapter()
    adapter.mark("NIFTY30OCT2625000CE", 100.0)
    out = _run(adapter.place_order(_req(side=OrderSide.SELL)))
    fill = adapter.order(out.order_id)
    expected = india_costs.leg_cost(fill.placed_at.astimezone(ZoneInfo("Asia/Kolkata")).date(), "sell",
                                    fill.average_price, 75.0, "OPTION")["total"]
    assert adapter.charges(out.order_id) == pytest.approx(round(expected, 2))


@pytest.mark.parametrize("order_type,side,price,trigger,fills", [
    ("LIMIT", OrderSide.BUY, 101.0, None, True), ("LIMIT", OrderSide.BUY, 99.0, None, False),
    ("LIMIT", OrderSide.SELL, 99.0, None, True), ("LIMIT", OrderSide.SELL, 101.0, None, False),
    ("SL-M", OrderSide.SELL, None, 101.0, True), ("SL-M", OrderSide.SELL, None, 99.0, False),
    ("SL", OrderSide.BUY, 100.5, 99.0, True), ("SL", OrderSide.BUY, 99.5, 99.0, False),
])
def test_only_what_the_price_reaches_now_fills_and_nothing_rests(order_type, side, price, trigger, fills):
    adapter = PaperExecutionAdapter()
    adapter.mark("NIFTY30OCT2625000CE", 100.0)
    out = _run(adapter.place_order(_req(side=side, order_type=order_type, price=price, trigger=trigger)))
    assert (out.status == "COMPLETE") is fills
    if not fills:
        assert out.status == "REJECTED" and "has not reached" in (out.message or "") or "past your limit" in (out.message or "")
        assert _run(adapter.get_trade_book()) == []


def test_a_limit_never_fills_past_its_price():
    adapter = PaperExecutionAdapter(PaperBroker(slippage_pct=1.0))
    adapter.mark("NIFTY30OCT2625000CE", 100.0)
    buy = _run(adapter.place_order(_req(order_type="LIMIT", price=100.0)))
    sell = _run(adapter.place_order(_req(side=OrderSide.SELL, order_type="LIMIT", price=100.0)))
    assert adapter.order(buy.order_id).average_price == 100.0 and adapter.order(sell.order_id).average_price == 100.0


def test_no_price_no_fill_and_a_price_lookup_is_the_users_own_read():
    adapter = PaperExecutionAdapter()
    out = _run(adapter.place_order(_req()))
    assert out.status == "REJECTED" and "No live price" in out.message

    async def lookup(symbol, exchange):
        return 50.0
    adapter = PaperExecutionAdapter(price_lookup=lookup)
    assert _run(adapter.place_order(_req())).status == "COMPLETE"
    assert _run(adapter.get_ltp(["NFO:NIFTY30OCT2625000CE"])) == {"NFO:NIFTY30OCT2625000CE": 50.0}


def test_the_same_reference_never_places_twice():
    adapter = PaperExecutionAdapter()
    adapter.mark("NIFTY30OCT2625000CE", 100.0)
    first = _run(adapter.place_order(_req(ref="tap-1")))
    again = _run(adapter.place_order(_req(ref="tap-1")))
    other = _run(adapter.place_order(_req(ref="tap-2")))
    assert again.order_id == first.order_id and again.raw == {"duplicate": True} and other.order_id != first.order_id
    assert len(_run(adapter.get_trade_book())) == 2


def test_partial_fill_stays_open_until_cancelled_and_positions_net():
    adapter = PaperExecutionAdapter(liquidity=lambda order: 50.0)
    adapter.mark("NIFTY30OCT2625000CE", 100.0, "NFO")
    out = _run(adapter.place_order(_req(qty=75.0)))
    book = adapter.order(out.order_id)
    assert out.status == "OPEN" and book.filled_quantity == 50.0
    assert _run(adapter.modify_order(out.order_id, price=101.0)).status == "OPEN"
    assert _run(adapter.cancel_order(out.order_id)).status == "CANCELLED"
    assert _run(adapter.cancel_order(out.order_id)).message == "The order is already cancelled"
    [pos] = _run(adapter.get_positions())
    assert pos.quantity == 50.0 and pos.product == "NRML" and pos.exchange == "NFO"


def test_an_exit_is_an_order_like_any_other_and_fills():
    """ADR-0004: nothing in the adapter stands in the way of closing a position."""
    adapter = PaperExecutionAdapter()
    adapter.mark("NIFTY30OCT2625000CE", 100.0)
    out = _run(adapter.exit_position("NIFTY30OCT2625000CE", "NSE", 75.0, OrderSide.SELL, product="NRML"))
    assert out.status == "COMPLETE" and adapter.order(out.order_id).transaction_type == OrderSide.BUY


# --- parity: the router, old path vs new path ---------------------------------------------------------------------------

def _spec(symbol: str, lot: float) -> ContractSpec:
    return ContractSpec(symbol=symbol, exchange="NFO", asset_class=AssetClass.INDEX_OPTION, description=symbol, lot_size=lot, tick_size=0.05)


def _route(paper_execution: str, signal: Signal, spec: ContractSpec, max_quantity):
    router = OrderRouter(ExecutionMode.PAPER, RiskConfig(capital=1_000_000.0, risk_per_trade_pct=1.0), exchange="NFO", product="NRML",
                         paper_execution=paper_execution, order_style="MARKET")
    state = TradingDayState()
    result = _run(router.execute(signal, state, contract_spec=spec, max_quantity=max_quantity))
    return result, state


def test_router_parity_old_direct_fill_equals_the_adapter_fill_over_many_signals():
    rng = random.Random(20261023)
    pb = PaperBroker()
    for _ in range(60):
        direction = rng.choice([SignalDirection.LONG, SignalDirection.SHORT])
        entry = round(rng.uniform(5, 900), 2)
        stop = round(entry * (0.7 if direction == SignalDirection.LONG else 1.3), 2)
        lot = float(rng.choice([15, 25, 50, 75, 1]))
        symbol = f"NIFTY30OCT26{rng.randrange(20000, 30000, 50)}{rng.choice(['CE', 'PE'])}"
        signal = Signal(symbol=symbol, strategy_id="manual_fno", strategy_name="x", direction=direction, timestamp=datetime.now(timezone.utc),
                        entry=entry, stop_loss=stop, grade=SignalGrade.VALID)
        cap = rng.choice([None, lot * rng.randint(1, 4)])
        old, old_state = _route("direct", signal, _spec(symbol, lot), cap)
        new, new_state = _route("adapter", signal, _spec(symbol, lot), cap)
        assert (old.executed, old.reasons, old.algo_tag, old.partial_fill) == (new.executed, new.reasons, new.algo_tag, new.partial_fill)
        assert (old_state.trades_today, old_state.open_positions) == (new_state.trades_today, new_state.open_positions)
        if not old.executed:
            continue
        a, b = old.trade, new.trade
        for field in ("symbol", "strategy_id", "direction", "entry_price", "quantity", "stop_loss", "target1", "target2", "expected_price"):
            assert getattr(a, field) == getattr(b, field), field
        exit_price = round(entry * rng.uniform(0.6, 1.4), 2)
        when = a.entry_time
        closed_a = pb.close_trade(a, exit_price, when, "manual")
        closed_b = pb.close_trade(b.model_copy(update={"entry_time": when}), exit_price, when, "manual")
        assert (closed_a.charges, closed_a.pnl, closed_a.exit_price) == (closed_b.charges, closed_b.pnl, closed_b.exit_price)


def test_router_adapter_path_handles_a_partial_fill_as_live_does(monkeypatch):
    from app.execution import paper_adapter
    real = paper_adapter.PaperExecutionAdapter.__init__

    def half(self, *args, **kwargs):
        real(self, *args, **kwargs)
        self.liquidity = lambda order: order.quantity / 2
    monkeypatch.setattr(paper_adapter.PaperExecutionAdapter, "__init__", half)
    signal = Signal(symbol="NIFTY30OCT2625000CE", strategy_id="manual_fno", strategy_name="x", direction=SignalDirection.LONG,
                    timestamp=datetime.now(timezone.utc), entry=100.0, stop_loss=60.0,
                    grade=SignalGrade.VALID)
    result, _ = _route("adapter", signal, _spec(signal.symbol, 50.0), 100.0)
    assert result.executed and result.partial_fill and result.requested_quantity == 100.0 and result.trade.quantity == 50.0
    assert any(r.startswith("Partial fill: 50 of 100") for r in result.reasons)


def test_router_refuses_an_unknown_paper_execution():
    with pytest.raises(ValueError):
        OrderRouter(ExecutionMode.PAPER, RiskConfig(), paper_execution="other", product="MIS", order_style="MARKET")


# --- parity: the whole pipeline (the F&O ticket, the path moved onto the adapter) ---------------------------------------

def _snapshot(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            orders = list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id).order_by(OrderRecord.id)))
            out = []
            for o in orders:
                events = list(await session.scalars(select(OrderEventRecord).where(OrderEventRecord.order_id == o.id).order_by(OrderEventRecord.id)))
                trade = await session.get(TradeRecord, o.trade_id) if o.trade_id else None
                out.append({
                    # M55 S4: the tag's client order id is derived from the row id, which differs per path by design.
                    "order": (o.mode, o.strategy_id, o.status, o.quantity,
                              (o.algo_tag or "").replace(client_order_id("E", o.id), "<id>"), json.loads(o.reasons_json or "[]")),
                    # the trade's own id differs between two organisations; everything else on the trail must match
                    "trail": [(e.from_status, e.to_status, (e.detail or "").replace(f"#{o.trade_id}", "#<trade>")) for e in events],
                    "trade": None if trade is None else (trade.mode, trade.direction, trade.entry_price, trade.quantity, trade.stop_loss,
                                                         trade.instrument_kind, trade.exchange, trade.lot_size, trade.option_position,
                                                         trade.holding, trade.exit_price, trade.exit_reason, trade.charges, trade.pnl),
                })
            return out
    return _run(go())


def _ticket_run(monkeypatch, paper_execution: str, side: str, stop: float, expiry, strike):
    monkeypatch.setattr(fno_ticket, "PAPER_EXECUTION", paper_execution)
    t = _org_tenant(f"tm1-{paper_execution}")
    ref = uuid.uuid4().hex
    placed = _place(t, _body(expiry, strike, side=side, lots=1, stop_loss=stop, product="NRML", client_ref=ref))
    again = _place(t, _body(expiry, strike, side=side, lots=1, stop_loss=stop, product="NRML", client_ref=ref))   # a second tap
    assert again["order_id"] == placed["order_id"]
    if placed.get("trade_id"):
        out = client.post(f"/api/positions/{placed['trade_id']}/exit-now", headers=t["headers"])
        assert out.status_code == 200, out.text
    return placed, _snapshot(t["tenant_id"])


@pytest.mark.parametrize("side,stop", [("BUY", 70.0), ("SELL", 130.0)])
def test_ticket_parity_old_path_and_adapter_path_give_identical_fills_costs_trail_and_pnl(monkeypatch, side, stop):
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    old_placed, old = _ticket_run(monkeypatch, "direct", side, stop, expiry, strike)
    new_placed, new = _ticket_run(monkeypatch, "adapter", side, stop, expiry, strike)
    assert old_placed["status"] == new_placed["status"] == "PLACED"
    assert old_placed["filled"] == new_placed["filled"]
    assert len(old) == len(new) == 1                       # one order each, the second tap placed nothing
    assert old == new                                      # order row, every trail row, the position, its exit, charges, P&L
    assert old[0]["trade"][10] is not None and old[0]["trade"][12] is not None   # exited, with charges


def test_the_ticket_uses_the_adapter_and_a_kill_switch_still_refuses_before_any_fill(monkeypatch):
    assert fno_ticket.PAPER_EXECUTION == "adapter"
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    t = _org_tenant("tm1-kill")
    placed_calls = []
    real = PaperExecutionAdapter.place_order

    async def spy(self, order):
        placed_calls.append(order)
        return await real(self, order)
    monkeypatch.setattr(PaperExecutionAdapter, "place_order", spy)
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "test"}).status_code == 200
    try:
        refused = _place(t, _body(expiry, strike, lots=1, stop_loss=70.0))
        assert refused["status"] == "REFUSED" and placed_calls == []
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    placed = _place(t, _body(expiry, strike, lots=1, stop_loss=70.0))
    assert placed["status"] == "PLACED" and len(placed_calls) == 1 and placed_calls[0].intent == "ENTRY"
    assert placed_calls[0].quantity == float(LOT)
