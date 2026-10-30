"""M55 S3: one entry path and one exit cadence for PAPER and LIVE (the owner principle: no paper / live split).

- A6: the same signal through the router gives the same order records and states in PAPER (the paper venue) and LIVE
  (a fake broker over the same fill model) - a full fill, a partial fill and a circuit-breaker refusal; a real broker
  handed in with PAPER is never sent an order, and LIVE never falls back to the paper venue;
- A12: a LIVE resting exit fires on the tick pass, at the session of the account holding the position, exactly once, with
  the PAPER-only lock on (an exit is never blocked); with no session it waits;
- A17: "all open positions" collects the LIVE positions when LIVE is selected and the combined exit closes them on the
  tick pass; "Exit part" works for LIVE where the broker can do it and refuses in plain words where it cannot.
LIVE switches are never changed for a LIVE entry here: every LIVE position is a fake broker's existing position."""
import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app.brokers.circuit_breaker import breaker_for
from app.core.enums import AssetClass, ExecutionMode, SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.db.models import GroupExitRecord, PaperRestingOrderRecord, TradeRecord, User
from app.execution import group_exits as gx
from app.execution import trigger_engine
from app.execution.paper_adapter import PaperExecutionAdapter
from app.execution.paper_broker import PaperBroker
from app.execution.router import LiveTradingNotConfigured, OrderRouter
from app.instruments.registry import ContractSpec
from app.market_data import hub
from app.risk_engine.risk_manager import TradingDayState
from app.trading import position_monitor as pm
from tests.test_auth_api import _session_factory


def _run(coro):
    return asyncio.run(coro)


# --- A6: one entry path ------------------------------------------------------------------------------------------------

class _FakeLiveBroker(PaperExecutionAdapter):
    """A LIVE broker for the test: the paper fill model behind an adapter that is NOT a paper venue."""
    name = "fake_s3_broker"
    paper = False

    def __init__(self, *args, raise_on_place: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0
        self.raise_on_place = raise_on_place

    async def place_order(self, order):
        self.calls += 1
        if self.raise_on_place:
            raise ConnectionError("broker down")
        return await super().place_order(order)


def _signal(entry=100.0, direction=SignalDirection.LONG) -> Signal:
    return Signal(symbol="NIFTY30OCT2625000CE", strategy_id="manual_fno", strategy_name="x", direction=direction,
                  timestamp=datetime.now(UTC), entry=entry, stop_loss=entry * 0.6, grade=SignalGrade.VALID)


def _spec(lot=50.0) -> ContractSpec:
    return ContractSpec(symbol="NIFTY30OCT2625000CE", exchange="NFO", asset_class=AssetClass.INDEX_OPTION, description="x",
                        lot_size=lot, tick_size=0.05)


def _routers(liquidity=None):
    risk = RiskConfig(capital=1_000_000.0, risk_per_trade_pct=1.0)
    paper = OrderRouter(ExecutionMode.PAPER, risk, exchange="NFO", product="NRML")
    paper.paper_venue = PaperExecutionAdapter(paper.paper_broker, liquidity=liquidity)
    broker = _FakeLiveBroker(PaperBroker(), liquidity=liquidity)
    live = OrderRouter(ExecutionMode.LIVE, risk, broker=broker, exchange="NFO", product="NRML", place_protective_stop=False)
    live.fill_poll_delay_seconds = 0
    return paper, live, broker


def _record(result, state):
    """What the order trail books from a result, with the venue's own words (paper / broker name) taken out."""
    reasons = [r.replace(" at the broker", "").replace("fake_s3_broker", "BROKER").replace("paper", "BROKER")
               for r in result.reasons if r != "Paper order filled" and not r.startswith("Live order placed via")]
    trade = result.trade
    return {"executed": result.executed, "reasons": reasons, "partial": result.partial_fill,
            "requested": result.requested_quantity, "system_failure": result.system_failure,
            "uncertain": result.broker_uncertain, "state": (state.trades_today, state.open_positions),
            "trade": None if trade is None else (trade.symbol, trade.direction, trade.entry_price, trade.quantity,
                                                 trade.stop_loss, trade.expected_price)}


def _both(signal, liquidity=None, max_quantity=100.0, before=None):
    paper, live, broker = _routers(liquidity)
    broker.mark(signal.symbol, signal.entry, "NFO")
    if before is not None:
        before(paper, live)
    out = []
    for router in (paper, live):
        state = TradingDayState()
        result = _run(router.execute(signal, state, contract_spec=_spec(), max_quantity=max_quantity))
        out.append((result, state))
    return out, broker


@pytest.mark.parametrize("direction", [SignalDirection.LONG, SignalDirection.SHORT])
def test_a6_paper_and_live_book_the_same_order_and_state_for_the_same_signal(direction):
    (paper, paper_state), (live, live_state) = _both(_signal(123.45, direction))[0]
    assert paper.executed and live.executed
    assert _record(paper, paper_state) == _record(live, live_state)
    assert paper.trade.entry_price == round(PaperBroker()._slip(123.45, direction), 2)     # the paper venue's fill model
    assert paper.broker_order_id is None and live.broker_order_id is not None              # a paper order has no broker id
    assert "Paper order filled" in paper.reasons and any(r.startswith("Live order placed via") for r in live.reasons)


def test_a6_a_partial_fill_is_the_same_in_paper_and_live():
    (paper, paper_state), (live, live_state) = _both(_signal(), liquidity=lambda order: order.quantity / 2)[0]
    assert _record(paper, paper_state) == _record(live, live_state)
    assert paper.partial_fill and paper.trade.quantity == 50.0 and paper.requested_quantity == 100.0
    assert any(r.startswith("Partial fill: 50 of 100") for r in paper.reasons)
    assert any(r.startswith("Unfilled remainder 50 cancelled") for r in paper.reasons)


def test_a6_nothing_filled_is_cancelled_and_refused_the_same_way():
    (paper, paper_state), (live, live_state) = _both(_signal(), liquidity=lambda order: 0.0,
                                                     before=lambda p, lv: setattr(p, "fill_poll_delay_seconds", 0))[0]
    assert not paper.executed and not live.executed
    assert _record(paper, paper_state) == _record(live, live_state)
    assert paper_state.open_positions == 0 and any("cancelled" in r for r in paper.reasons)


def test_a6_a_circuit_breaker_refusal_is_the_same_in_paper_and_live():
    def open_both(paper, live):
        for name in ("paper", _FakeLiveBroker.name):
            breaker = breaker_for(name)
            breaker._open(breaker._clock(), "test outage")
    try:
        (paper, paper_state), (live, live_state) = _both(_signal(), before=open_both)[0]
        assert not paper.executed and not live.executed and not live.system_failure
        assert _record(paper, paper_state) == _record(live, live_state)
        assert "Broker circuit open" in paper.reasons[-1] and paper_state.trades_today == 0
    finally:
        breaker_for("paper").reset()
        breaker_for(_FakeLiveBroker.name).reset()


def test_a6_a_failed_broker_call_is_failed_and_the_order_is_sent_once():
    risk = RiskConfig(capital=1_000_000.0, risk_per_trade_pct=1.0)
    broker = _FakeLiveBroker(PaperBroker(), raise_on_place=True)
    live = OrderRouter(ExecutionMode.LIVE, risk, broker=broker, exchange="NFO", product="NRML", place_protective_stop=False)
    state = TradingDayState()
    result = _run(live.execute(_signal(), state, contract_spec=_spec(), max_quantity=100.0))
    try:
        assert not result.executed and result.system_failure and broker.calls == 1 and state.trades_today == 0
    finally:
        breaker_for(_FakeLiveBroker.name).reset()


def test_a6_paper_never_sends_to_a_real_broker_and_live_never_falls_back_to_paper():
    risk = RiskConfig(capital=1_000_000.0, risk_per_trade_pct=1.0)
    real = _FakeLiveBroker(PaperBroker())
    real.mark("NIFTY30OCT2625000CE", 100.0, "NFO")
    paper = OrderRouter(ExecutionMode.PAPER, risk, broker=real, exchange="NFO", product="NRML")
    result = _run(paper.execute(_signal(), TradingDayState(), contract_spec=_spec(), max_quantity=100.0))
    assert result.executed and real.calls == 0                    # filled on the paper venue, the broker untouched
    for broker in (None, PaperExecutionAdapter()):
        live = OrderRouter(ExecutionMode.LIVE, risk, broker=broker)
        with pytest.raises(LiveTradingNotConfigured):
            _run(live.execute(_signal(), TradingDayState(), contract_spec=_spec(), max_quantity=100.0))


def test_a6_the_freeze_slices_run_on_the_one_path_for_both_modes():
    risk = RiskConfig(capital=1_000_000.0, risk_per_trade_pct=1.0)
    paper = OrderRouter(ExecutionMode.PAPER, risk, exchange="NFO", product="NRML", freeze_quantity=100)
    broker = _FakeLiveBroker(PaperBroker())
    broker.mark("NIFTY30OCT2625000CE", 100.0, "NFO")
    live = OrderRouter(ExecutionMode.LIVE, risk, broker=broker, exchange="NFO", product="NRML", freeze_quantity=100,
                       place_protective_stop=False)
    out = []
    for router in (paper, live):
        state = TradingDayState()
        out.append((_run(router.execute(_signal(), state, contract_spec=_spec(), max_quantity=250.0)), state))
    (p, ps), (lv, ls) = out
    assert _record(p, ps) == _record(lv, ls) and p.trade.quantity == 250.0 and broker.calls == 5
    assert any(r.startswith("Sent as 5 orders") for r in p.reasons) and ps.trades_today == 1


# --- A12: one exit cadence -----------------------------------------------------------------------------------------------

def _live_setup():
    from tests.test_reexit_guard import _Book, _live_trade
    from tests.test_trading_worker import _tenant
    return _tenant(f"m55s3-{uuid.uuid4().hex[:8]}@example.com"), _Book, _live_trade


def _arm_stop(t, trade_id, level=98.0):
    from app.execution import resting_book

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            trade = await session.get(TradeRecord, trade_id)
            row = await resting_book.arm_exit(session, user, trade, client_ref=f"exit:{uuid.uuid4().hex[:12]}", kind="STOP",
                                              order_type="SL-M", level=level)
            await session.commit()
            return row.id
    return _run(go())


def _get(model, id_):
    async def go():
        async with _session_factory() as session:
            return await session.get(model, id_)
    return _run(go())


def _tick(t, table, now, monkeypatch):
    ticks = {f"NFO:{symbol}": [price, now.isoformat()] for symbol, price in table.items()}
    monkeypatch.setattr(hub, "live_ticks", lambda ns: ticks if ns == hub.org_namespace(t["tenant_id"]) else {})

    async def go():
        async with _session_factory() as session:
            return await trigger_engine.tick_pass(session, now)
    return _run(go())


@pytest.fixture
def paper_lock_on():
    """The Phase 0 PAPER-only lock on and live trading off - the platform's state; restored afterwards."""
    from tests.test_tm2_owner_proofs import _restore_flags, _set_flags
    previous = _set_flags(live_trading=False, phase0_paper_only=True)
    yield
    _restore_flags(previous)


def test_a12_a_live_resting_exit_fires_on_the_tick_pass_once_and_the_lock_never_blocks_it(monkeypatch, paper_lock_on):
    t, _Book, _live_trade = _live_setup()
    trade_id = _live_trade(t, exit_order_id=None)
    row_id = _arm_stop(t, trade_id)
    broker = _Book(ltp=97.0)
    seen = []

    async def account_session(session, trade):
        seen.append(trade.id)
        return broker
    monkeypatch.setattr(pm, "broker_for_trade", account_session)
    now = datetime.now(UTC)
    assert _tick(t, {"RXG": 99.0}, now, monkeypatch).filled == 0 and broker.placed == []      # not reached yet
    report = _tick(t, {"RXG": 97.0}, now, monkeypatch)
    assert report.filled == 1 and len(broker.placed) == 1 and broker.placed[0].transaction_type.value == "SELL"
    assert trade_id in seen                                                                 # the holding account's session
    trade = _get(TradeRecord, trade_id)
    assert trade.exit_time is not None and _get(PaperRestingOrderRecord, row_id).status == "FILLED"
    assert _tick(t, {"RXG": 96.0}, now, monkeypatch).filled == 0 and len(broker.placed) == 1   # never a second exit


def test_a12_a_live_resting_exit_without_a_session_waits_and_sends_nothing(monkeypatch):
    t, _Book, _live_trade = _live_setup()
    trade_id = _live_trade(t, exit_order_id=None)
    row_id = _arm_stop(t, trade_id)

    async def no_session(session, trade):
        return None
    monkeypatch.setattr(pm, "broker_for_trade", no_session)
    try:
        report = _tick(t, {"RXG": 97.0}, datetime.now(UTC), monkeypatch)
        assert report.filled == 0 and _get(PaperRestingOrderRecord, row_id).status == "RESTING"
        assert _get(TradeRecord, trade_id).exit_time is None
    finally:
        async def cancel():
            async with _session_factory() as session:
                await session.execute(update(PaperRestingOrderRecord).where(PaperRestingOrderRecord.id == row_id).values(status="CANCELLED"))
                await session.commit()
        _run(cancel())


# --- A17: template / combined exits act on PAPER and LIVE alike ----------------------------------------------------------

def test_a17_all_open_collects_the_selected_mode_and_the_combined_exit_closes_live_positions(monkeypatch, paper_lock_on):
    t, _Book, _live_trade = _live_setup()
    ids = [_live_trade(t, symbol=symbol, exit_order_id=None) for symbol in ("RXA", "RXB")]
    broker = _Book(ltp=90.0)

    async def account_session(session, trade):
        return broker
    monkeypatch.setattr(pm, "broker_for_trade", account_session)

    async def collect():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return (await gx.all_open_ids(session, user), await gx.all_open_ids(session, user, "PAPER"),
                    await gx.all_open_ids(session, user, "LIVE"))
    every, paper_ids, live_ids = _run(collect())
    assert every == live_ids == sorted(ids) and paper_ids == []          # every mode the user holds, not PAPER only
    with pytest.raises(gx.GroupExitError):
        _run(gx.all_open_ids(None, None, "BOTH"))

    async def arm():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            ask = gx.GroupExitAsk(client_ref=uuid.uuid4().hex, trade_ids=live_ids, measure=gx.MTM_INR, sl_value=5.0,
                                  scope="ALL_OPEN")
            return await gx.create(session, user, ask)
    row = _run(arm())
    try:
        assert row.mode == "LIVE"
        report = _tick(t, {"RXA": 90.0, "RXB": 90.0}, datetime.now(UTC), monkeypatch)     # MTM -20: stop reached
        assert report.errors == [] and len(broker.placed) == 2
        assert {o.symbol for o in broker.placed} == {"RXA", "RXB"} and all(o.transaction_type.value == "SELL" for o in broker.placed)
        assert all(_get(TradeRecord, i).exit_time is not None for i in ids)
        assert _get(GroupExitRecord, row.id).status == gx.DONE
    finally:
        async def end():
            async with _session_factory() as session:
                await session.execute(update(GroupExitRecord).where(GroupExitRecord.id == row.id,
                                                                    GroupExitRecord.status.in_(gx.LIVE_STATUSES)).values(status=gx.CANCELLED))
                await session.commit()
        _run(end())


def _exit_part(t, trade_id, lots, monkeypatch, broker):
    from app.execution import approval
    from app.paper import fno_position_actions as actions

    async def price(session, leg, lookup):
        return 97.0, "live price"

    async def account_session(session, trade):
        return broker
    monkeypatch.setattr(approval, "exit_price", price)
    monkeypatch.setattr(pm, "broker_for_trade", account_session)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await actions.exit_part(session, user, trade_id, lots=lots, client_ref=uuid.uuid4().hex)
    return _run(go())


def test_a17_live_exit_part_goes_to_the_broker_where_it_can_and_refuses_in_plain_words_where_it_cannot(monkeypatch):
    from app.paper import fno_position_actions as actions
    t, _Book, _live_trade = _live_setup()
    trade_id = _live_trade(t, exit_order_id=None, quantity=2)
    with pytest.raises(HTTPException) as no_session:
        _exit_part(t, trade_id, 1, monkeypatch, None)
    assert no_session.value.status_code == 409 and no_session.value.detail == actions.LIVE_NO_SESSION
    broker = _Book(ltp=97.0)
    out = _exit_part(t, trade_id, 1, monkeypatch, broker)
    assert out["status"] == "CLOSED" and out["remaining_lots"] == 1
    assert len(broker.placed) == 1 and broker.placed[0].quantity == 1 and broker.placed[0].transaction_type.value == "SELL"
    assert _get(TradeRecord, trade_id).exit_time is None and float(_get(TradeRecord, trade_id).quantity) == 1.0

    stopped = _live_trade(t, exit_order_id=None, quantity=2, sl_order_id="SL-1")
    with pytest.raises(HTTPException) as covers:
        _exit_part(t, stopped, 1, monkeypatch, _Book(ltp=97.0))
    assert covers.value.detail == actions.LIVE_STOP_COVERS_ALL and _get(TradeRecord, stopped).exit_time is None

    live = _get(TradeRecord, trade_id)
    with pytest.raises(HTTPException) as reverse:
        actions._open_checks(live)
    assert reverse.value.status_code == 409 and reverse.value.detail == actions.LIVE_NO_REVERSE
