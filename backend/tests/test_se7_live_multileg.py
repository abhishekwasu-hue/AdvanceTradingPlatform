"""SE-7 (owner, 23 Oct): the multi-leg executor on the smart-execution engine (flag `smart_execution`), with the user's
"if I do not answer within N minutes" rule.

Parity (old executor = flag off, new = flag on; fake brokers only):
- success, LIVE: the same orders in the same (safe) order - hedges first, then the sold legs - with the same quantity,
  order type, tag and intent; the same trades (fill, quantity, stop, expected price, role, group economics) and the same
  order trail; a 4-leg iron condor too;
- success, PAPER (through the PaperExecutionAdapter): the same fills, and the same exit / charges / P&L when closed;
- failure (the documented behaviour change): the old path unwinds the filled hedge at once; the new path sends nothing
  more, books the filled hedge and stops INCOMPLETE with "Complete the rest" / "Close the filled legs"; a hedge that
  does not fill in full never lets a sold leg go out.
The rule: past the deadline his choice applies - close the filled legs (an exit: sold legs first, never blocked by a kill
switch, the Phase 0 lock or the broker-uncertain flag) or keep them; audited and notified. Idempotency, the answer
route (flags, 404 / 403 tenancy), "Complete the rest" through the worker's path with the entry refusals, the structure
rules paused while incomplete, and the settings' bounds."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.core.enums import KillSwitchScope, OptionStrategy, SignalDirection, StrikeRule
from app.core.models import RiskConfig
from app.db.models import AuditLogRecord, NotificationRecord, OrderEventRecord, OrderRecord, Tenant, TradeRecord, User
from app.execution import incomplete_structures as inc
from app.execution import smart_execution as se
from app.execution.multileg import execute_structure
from app.instruments.spreads import resolve_structure
from app.kill_switch.checks import disengage, engage
from app.platform import phase0_lock
from app.trading.position_monitor import close_position, monitor_open_positions
from tests.test_auth_api import _session_factory, client
from tests.test_contract_rules import TODAY, _load_master
from tests.test_multileg import SPOT, _OptionBroker, _premium, _rules
from tests.test_trading_worker import _get, _signal, _tenant, _trades
from tests.test_z5_p1d_risk_by_default import _flag

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")

RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_poll_wait(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _load_master()


def _smart(t):
    _flag(se.FLAG, t["tenant_id"])
    _flag("ui_v2", t["tenant_id"])
    return t


class _RejectingBroker(_OptionBroker):
    """Answers REJECTED (a clean refusal, nothing working) for one symbol."""
    def __init__(self, reject: str, **kw):
        super().__init__(**kw)
        self.reject = reject

    async def place_order(self, order):
        if self.reject in order.symbol:
            self.placed.append(order)
            return BrokerOrderResponse(order_id=f"ORD-{len(self.placed)}", status="REJECTED", message="RMS: margin exceeds")
        return await super().place_order(order)

    async def get_order_book(self):
        book = await super().get_order_book()
        return [o.model_copy(update={"status": "REJECTED", "filled_quantity": 0.0, "average_price": None}) if self.reject in o.symbol else o
                for o in book]


class _PartialWingBroker(_OptionBroker):
    """Fills only half of one (bought) leg; the rest never fills."""
    def __init__(self, partial: str, **kw):
        super().__init__(**kw)
        self.partial = partial
        self.cancelled = []

    async def get_order_book(self):
        out = []
        for i, o in enumerate(self.placed):
            filled = o.quantity / 2 if self.partial in o.symbol and o.intent == "ENTRY" else o.quantity
            out.append(BrokerOrderStatus(order_id=f"ORD-{i + 1}", symbol=o.symbol, transaction_type=o.transaction_type, quantity=o.quantity,
                                         filled_quantity=filled, order_type=o.order_type,
                                         status="CANCELLED" if filled < o.quantity and f"ORD-{i + 1}" in self.cancelled else
                                         ("OPEN" if filled < o.quantity else "COMPLETE"), average_price=_premium(o.symbol)))
        return out

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return BrokerOrderResponse(order_id=order_id, status="CANCELLED")


async def _structure(session, strategy=OptionStrategy.BULL_PUT_SPREAD, **kw):
    rules = _rules(**kw)
    return await resolve_structure(session, "NIFTY 50", rules, strategy, SignalDirection.LONG, spread_width=2, spot=SPOT, today=TODAY), rules


def _execute(t, *, mode, key, broker=None, strategy=OptionStrategy.BULL_PUT_SPREAD, **kw):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            structure, rules = await _structure(session, strategy, **kw)
            quote = broker or _OptionBroker()
            return await execute_structure(session, user, mode=mode, strategy_id="s", signal=_signal(), structure=structure, rules=rules,
                                           target_credit_pct=None, stop_credit_pct=None, idempotency_key=key,
                                           broker=broker if mode == "LIVE" else None, quote_broker=quote, risk_config=RISK)
    return _run(go())


def _trail(tenant_id):
    async def go():
        async with _session_factory() as session:
            orders = list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == tenant_id).order_by(OrderRecord.id)))
            out = []
            for o in orders:
                events = list(await session.scalars(select(OrderEventRecord).where(OrderEventRecord.order_id == o.id).order_by(OrderEventRecord.id)))
                out.append((o.symbol, o.status, o.quantity, [(e.from_status, e.to_status) for e in events]))
            return out
    return _run(go())


def _trade_view(tr):
    meta = json.loads(tr.group_meta)
    meta.pop("incomplete", None)
    return (tr.symbol, tr.direction, tr.entry_price, tr.quantity, tr.stop_loss, tr.expected_price, tr.leg_role, tr.mode,
            tr.option_position, tr.option_strategy, bool(tr.broker_order_id), meta)


def _placed_view(broker):
    return [(o.symbol, o.transaction_type.value, o.quantity, o.order_type, o.price, o.product, o.intent, (o.tag or "")[-3:]) for o in broker.placed]


# --- parity: the success path --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("strategy,extra", [(OptionStrategy.BULL_PUT_SPREAD, {}),
                                            (OptionStrategy.IRON_CONDOR, {"strike_rule": StrikeRule.OTM, "strike_offset": 2})])
def test_live_success_parity_same_orders_same_trades_same_trail(strategy, extra):
    old_t, new_t = _tenant(f"se7-old-{strategy.value}@example.com"), _smart(_tenant(f"se7-new-{strategy.value}@example.com"))
    old_b, new_b = _OptionBroker(), _OptionBroker()
    old = _execute(old_t, mode="LIVE", key="p1", broker=old_b, strategy=strategy, **extra)
    new = _execute(new_t, mode="LIVE", key="p1", broker=new_b, strategy=strategy, **extra)
    assert old.executed and new.executed, (old.reasons, new.reasons)
    assert new.state == se.COMPLETE and old.state is None
    # The same orders in the same safe order: every hedge (BUY) before any sold leg.
    assert _placed_view(old_b) == _placed_view(new_b)
    sides = [o.transaction_type.value for o in new_b.placed]
    assert sides == sorted(sides, key=lambda s: 0 if s == "BUY" else 1)
    assert [_trade_view(tr) for tr in _trades(old_t["tenant_id"])] == [_trade_view(tr) for tr in _trades(new_t["tenant_id"])]
    old_trail, new_trail = _trail(old_t["tenant_id"]), _trail(new_t["tenant_id"])
    assert old_trail == new_trail and len(new_trail) == len(new_b.placed) and all(st == "POSITION_OPEN" for _, st, _, _ in new_trail)
    assert old.reasons == new.reasons                                     # the same economics and fill notes on the trail


def test_paper_success_parity_through_the_paper_adapter_including_the_exit():
    old_t, new_t = _tenant("se7-paper-old@example.com"), _smart(_tenant("se7-paper-new@example.com"))
    old = _execute(old_t, mode="PAPER", key="pp1")
    new = _execute(new_t, mode="PAPER", key="pp1")
    assert old.executed and new.executed and new.state == se.COMPLETE
    old_trades, new_trades = _trades(old_t["tenant_id"]), _trades(new_t["tenant_id"])
    assert [_trade_view(tr) for tr in old_trades] == [_trade_view(tr) for tr in new_trades]
    assert not any(tr.broker_order_id for tr in new_trades)               # a practice venue's ids are never a broker's
    assert _trail(old_t["tenant_id"]) == _trail(new_t["tenant_id"])

    async def close(trades):
        async with _session_factory() as session:
            out = []
            for tr in trades:
                row = await session.get(TradeRecord, tr.id)
                outcome = await close_position(session, row, _premium(row.symbol) * 0.5, "test exit")
                out.append((outcome.closed, row.exit_price, row.charges, row.pnl))
            return out
    assert _run(close(old_trades)) == _run(close(new_trades))
    # A replay of the same signal places nothing more on either path (idempotency).
    again = _execute(new_t, mode="PAPER", key="pp1")
    assert again.executed and "already handled" in again.reasons[0] and len(_trades(new_t["tenant_id"])) == 2


# --- the failure path: the documented behaviour change ------------------------------------------------------------------

def test_failed_short_old_unwinds_new_stops_incomplete_and_undoes_nothing():
    old_t, new_t = _tenant("se7-fail-old@example.com"), _smart(_tenant("se7-fail-new@example.com"))
    old_b, new_b = _RejectingBroker("24500 PE"), _RejectingBroker("24500 PE")
    old = _execute(old_t, mode="LIVE", key="f1", broker=old_b)
    new = _execute(new_t, mode="LIVE", key="f1", broker=new_b)
    # Old: the hedge bought, the short refused, the hedge sold back at once; nothing booked; the tenant flagged.
    assert not old.executed and old.system_failure and any("unwound 1 filled leg" in r for r in old.reasons)
    assert [(o.transaction_type.value, o.symbol == old_b.placed[0].symbol) for o in old_b.placed] == [
        ("BUY", True), ("SELL", False), ("SELL", True)]                       # hedge, refused short, hedge sold back
    assert _trades(old_t["tenant_id"]) == []
    # New: the hedge bought and kept, the short refused, nothing undone; INCOMPLETE with both choices.
    assert not new.executed and new.state == se.INCOMPLETE and not new.system_failure
    assert [(o.transaction_type.value, "24400 PE" in o.symbol) for o in new_b.placed] == [("BUY", True), ("SELL", False)]
    trades = _trades(new_t["tenant_id"])
    assert len(trades) == 1 and trades[0].leg_role == "LONG" and trades[0].exit_time is None and trades[0].broker_order_id == "ORD-1"
    block = inc.block_of(trades[0])
    assert block["state"] == inc.INCOMPLETE and block["on_no_answer"] == "close_filled" and block["answer_minutes"] == 5
    assert [p["key"] for p in block["pending"]] == [new_b.placed[1].symbol] and "RMS" in block["failure"]
    assert _get(Tenant, new_t["tenant_id"]).broker_uncertain_since is None   # a clean refusal: nothing is unknown
    statuses = {s: st for s, st, _, _ in _trail(new_t["tenant_id"])}
    assert sorted(statuses.values()) == ["FAILED", "POSITION_OPEN"]

    async def told():
        async with _session_factory() as session:
            note = await session.scalar(select(NotificationRecord).where(NotificationRecord.tenant_id == new_t["tenant_id"],
                                                                         NotificationRecord.event_type == "STRUCTURE_INCOMPLETE"))
            audit = await session.scalar(select(AuditLogRecord).where(AuditLogRecord.tenant_id == new_t["tenant_id"],
                                                                      AuditLogRecord.event == "structure_incomplete"))
            return note, audit
    note, audit = _run(told())
    assert note is not None and note.severity == "CRITICAL" and "Complete the rest" in note.message and audit is not None


def test_a_hedge_that_does_not_fill_in_full_never_lets_a_sold_leg_go():
    t = _smart(_tenant("se7-partial@example.com"))
    broker = _PartialWingBroker("24400 PE")
    result = _execute(t, mode="LIVE", key="w1", broker=broker)
    assert result.state == se.INCOMPLETE
    assert [o.transaction_type.value for o in broker.placed] == ["BUY"]              # no SELL ever sent
    trades = _trades(t["tenant_id"])
    assert len(trades) == 1 and trades[0].quantity == broker.placed[0].quantity / 2   # the filled part is a real position
    pending = inc.block_of(trades[0])["pending"]
    assert [(p["side"], p["quantity"]) for p in pending] == [("BUY", broker.placed[0].quantity / 2), ("SELL", broker.placed[0].quantity)]
    assert _get(Tenant, t["tenant_id"]).broker_uncertain_since is not None           # the cancel was not confirmed clean

    first = _RejectingBroker("24400 PE")                                              # the hedge refused outright
    t2 = _smart(_tenant("se7-nohedge@example.com"))
    result = _execute(t2, mode="LIVE", key="w2", broker=first)
    assert result.state == se.NOTHING_PLACED and [o.transaction_type.value for o in first.placed] == ["BUY"] and _trades(t2["tenant_id"]) == []


# --- the rule at the deadline --------------------------------------------------------------------------------------------

def _incomplete_paper(t, monkeypatch, key="i1"):
    """A PAPER structure stopped INCOMPLETE through the same engine (the practice venue refuses the short)."""
    from app.execution import multileg
    from app.execution.paper_adapter import PaperExecutionAdapter

    class _Refusing(PaperExecutionAdapter):
        async def place_order(self, order):
            if "24500 PE" in order.symbol:
                return BrokerOrderResponse(order_id="", status="REJECTED", message="No practice price")
            return await super().place_order(order)
    monkeypatch.setattr(multileg, "PaperExecutionAdapter", _Refusing)
    result = _execute(t, mode="PAPER", key=key)
    monkeypatch.setattr(multileg, "PaperExecutionAdapter", PaperExecutionAdapter)
    assert result.state == se.INCOMPLETE, result.reasons
    return result


async def _lookup(symbol, exchange="NFO"):
    return _premium(symbol)


def _exits(t, now, broker=None):
    async def go():
        async with _session_factory() as session:
            return await inc.run_exits(session, t["tenant_id"], _lookup, broker=broker, user_id=t["user_id"], now=now)
    return _run(go())


def test_no_answer_closes_the_filled_legs_by_default_even_with_the_kill_switch_and_the_lock_on(monkeypatch):
    t = _smart(_tenant("se7-timer@example.com"))
    result = _incomplete_paper(t, monkeypatch)
    deadline = datetime.fromisoformat(inc.block_of(result.trades[0])["deadline"])
    assert _exits(t, deadline - timedelta(seconds=30)) == 0 and _trades(t["tenant_id"])[0].exit_time is None   # not yet

    async def stop_everything():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            await engage(session, KillSwitchScope.TENANT, t["tenant_id"], user, "test")
    _run(stop_everything())
    _flag(phase0_lock.FLAG, t["tenant_id"])
    try:
        assert _exits(t, deadline + timedelta(seconds=1)) == 1
    finally:
        _flag(phase0_lock.FLAG, t["tenant_id"], on=False)

        async def clear():
            async with _session_factory() as session:
                await disengage(session, KillSwitchScope.TENANT, t["tenant_id"])
        _run(clear())
    leg = _trades(t["tenant_id"])[0]
    assert leg.exit_time is not None and "no answer" in leg.exit_reason and inc.block_of(leg)["state"] == inc.CLOSED
    assert inc.block_of(leg)["answered_by"] == "timer"
    assert _exits(t, deadline + timedelta(minutes=5)) == 0                           # idempotent: nothing twice

    async def audit():
        async with _session_factory() as session:
            return list(await session.scalars(select(AuditLogRecord.event).where(AuditLogRecord.tenant_id == t["tenant_id"])))
    assert "structure_incomplete_timer" in _run(audit())


def test_live_close_is_an_exit_sold_legs_first_through_the_broker(monkeypatch):
    """An iron condor stopped at its second short: the rule buys the filled short back first, then sells the hedges."""
    t = _smart(_tenant("se7-live-close@example.com"))
    broker = _RejectingBroker("24600 CE")
    result = _execute(t, mode="LIVE", key="lc1", broker=broker, strategy=OptionStrategy.IRON_CONDOR,
                      strike_rule=StrikeRule.OTM, strike_offset=2)
    assert result.state == se.INCOMPLETE and [o.transaction_type.value for o in broker.placed] == ["BUY", "BUY", "SELL", "SELL"]
    assert len(result.trades) == 3
    broker.reject = "no-such-symbol"
    deadline = datetime.fromisoformat(inc.block_of(result.trades[0])["deadline"])
    entries = len(broker.placed)
    _flag(phase0_lock.FLAG, t["tenant_id"])
    try:
        closed = _exits(t, deadline + timedelta(seconds=1), broker=broker)
    finally:
        _flag(phase0_lock.FLAG, t["tenant_id"], on=False)
    assert closed == 3
    exits = broker.placed[entries:]
    assert [o.transaction_type.value for o in exits] == ["BUY", "SELL", "SELL"] and all(o.intent != "ENTRY" for o in exits)
    assert all(tr.exit_time is not None and tr.mode == "LIVE" for tr in _trades(t["tenant_id"]))


def test_keep_leaves_the_legs_open_and_the_structure_rules_wait(monkeypatch):
    t = _smart(_tenant("se7-keep@example.com"))
    client.put("/api/me/prefs/smart_execution", headers=t["headers"], json={
        "settlement_days_before": 4, "settlement_band_pct": 2, "settlement_telegram": True, "protection_band_pct": 0.5,
        "on_no_answer": "keep", "answer_minutes": 2})
    result = _incomplete_paper(t, monkeypatch, key="k1")
    block = inc.block_of(result.trades[0])
    assert block["on_no_answer"] == "keep" and block["answer_minutes"] == 2
    deadline = datetime.fromisoformat(block["deadline"])
    assert _exits(t, deadline + timedelta(seconds=1)) == 0
    leg = _trades(t["tenant_id"])[0]
    assert leg.exit_time is None and inc.block_of(leg)["state"] == inc.KEPT

    async def monitor():
        async with _session_factory() as session:
            async def collapsed(symbol, exchange):   # the hedge's price collapses: a whole structure's rule would read a target
                return 1.0
            return await monitor_open_positions(session, t["tenant_id"], collapsed)
    assert _run(monitor()) == [] and _trades(t["tenant_id"])[0].exit_time is None


# --- his answer (the route) ----------------------------------------------------------------------------------------------

def test_answer_route_flags_tenancy_and_idempotency(monkeypatch):
    t = _smart(_tenant("se7-route@example.com"))
    result = _incomplete_paper(t, monkeypatch, key="r1")
    group = result.leg_group_id
    got = client.get("/api/trading/incomplete", headers=t["headers"])
    assert got.status_code == 200
    item = got.json()["items"][0]
    assert item["group_id"] == group and item["actions"] == ["complete_rest", "close_filled", "keep"]
    assert 0 < item["seconds_left"] <= 300 and item["on_no_answer"] == "close_filled" and len(item["pending"]) == 1

    other = _smart(_tenant("se7-route-other@example.com"))
    assert client.post(f"/api/trading/incomplete/{group}/answer", headers=other["headers"], json={"action": "keep"}).status_code == 404
    assert client.get("/api/trading/incomplete", headers=other["headers"]).json()["items"] == []
    off = _tenant("se7-route-off@example.com")
    assert client.get("/api/trading/incomplete", headers=off["headers"]).status_code == 404
    assert client.post(f"/api/trading/incomplete/{group}/answer", headers=t["headers"], json={"action": "nope"}).status_code == 422

    first = client.post(f"/api/trading/incomplete/{group}/answer", headers=t["headers"], json={"action": "complete_rest"})
    assert first.status_code == 200 and first.json()["state"] == inc.COMPLETING
    again = client.post(f"/api/trading/incomplete/{group}/answer", headers=t["headers"], json={"action": "close_filled"})
    assert again.status_code == 200 and again.json()["state"] == inc.COMPLETING and "Already answered" in again.json()["message"]
    assert _trades(t["tenant_id"])[0].exit_time is None


def test_another_member_gets_403(monkeypatch):
    from tests.test_z3b_paper_account import _member
    t = _smart(_tenant("se7-member@example.com"))
    result = _incomplete_paper(t, monkeypatch, key="m1")
    headers = _member(t)["headers"]
    response = client.post(f"/api/trading/incomplete/{result.leg_group_id}/answer", headers=headers, json={"action": "keep"})
    assert response.status_code == 403


def test_close_filled_tap_closes_paper_legs_at_once(monkeypatch):
    t = _smart(_tenant("se7-tap-close@example.com"))
    result = _incomplete_paper(t, monkeypatch, key="c1")
    leg_id = result.trades[0].id

    async def mark():                         # the user's own live price is not configured in tests: the fresh mark stands in
        async with _session_factory() as session:
            row = await session.get(TradeRecord, leg_id)
            row.mark_price, row.mark_time = 70.0, datetime.now(timezone.utc)
            await session.commit()
    _run(mark())
    response = client.post(f"/api/trading/incomplete/{result.leg_group_id}/answer", headers=t["headers"], json={"action": "close_filled"})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == inc.CLOSED and _trades(t["tenant_id"])[0].exit_time is not None


# --- "Complete the rest" (the worker's path) -----------------------------------------------------------------------------

def _complete(t, broker=None, mode_live=False):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            groups = await inc.pending_completions(session, t["tenant_id"])
            out = []
            for legs in groups.values():
                out.append(await inc.complete_rest(session, user, legs, broker=broker, price_lookup=_lookup))
            return out
    return _run(go())


def test_complete_the_rest_places_the_rest_once_and_the_rules_resume(monkeypatch):
    t = _smart(_tenant("se7-complete@example.com"))
    result = _incomplete_paper(t, monkeypatch, key="cr1")
    client.post(f"/api/trading/incomplete/{result.leg_group_id}/answer", headers=t["headers"], json={"action": "complete_rest"})
    outcomes = _complete(t)
    assert [o["state"] for o in outcomes] == [inc.COMPLETED]
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2 and {tr.leg_role for tr in trades} == {"LONG", "SHORT"} and len({tr.leg_group_id for tr in trades}) == 1
    assert all(inc.block_of(tr)["state"] == inc.COMPLETED for tr in trades) and not inc.rules_paused(trades)
    short = next(tr for tr in trades if tr.leg_role == "SHORT")
    assert short.direction == "SHORT" and short.option_position == "WRITE" and json.loads(short.group_meta)["net_credit"] == 40.0
    assert _complete(t) == [] and len(_trades(t["tenant_id"])) == 2              # nothing twice


def test_complete_the_rest_is_an_entry_refused_by_the_kill_switch(monkeypatch):
    t = _smart(_tenant("se7-complete-ks@example.com"))
    result = _incomplete_paper(t, monkeypatch, key="ck1")
    client.post(f"/api/trading/incomplete/{result.leg_group_id}/answer", headers=t["headers"], json={"action": "complete_rest"})

    async def switch(on):
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            if on:
                await engage(session, KillSwitchScope.TENANT, t["tenant_id"], user, "test")
            else:
                await disengage(session, KillSwitchScope.TENANT, t["tenant_id"])
    _run(switch(True))
    try:
        outcomes = _complete(t)
    finally:
        _run(switch(False))
    assert [o["state"] for o in outcomes] == [inc.INCOMPLETE] and "kill switch" in outcomes[0]["message"].lower()
    trades = _trades(t["tenant_id"])
    assert len(trades) == 1 and inc.block_of(trades[0])["state"] == inc.INCOMPLETE   # waits for his answer again


def test_live_complete_the_rest_needs_a_broker_and_the_lock_refuses_it(monkeypatch):
    t = _smart(_tenant("se7-complete-live@example.com"))
    broker = _RejectingBroker("24500 PE")
    result = _execute(t, mode="LIVE", key="cl1", broker=broker)
    client.post(f"/api/trading/incomplete/{result.leg_group_id}/answer", headers=t["headers"], json={"action": "complete_rest"})
    _flag(phase0_lock.FLAG, t["tenant_id"])
    try:
        outcomes = _complete(t, broker=broker)
    finally:
        _flag(phase0_lock.FLAG, t["tenant_id"], on=False)
    assert outcomes[0]["state"] == inc.INCOMPLETE and phase0_lock.ENTRY_REASON in outcomes[0]["message"]
    assert len(broker.placed) == 2                                                       # nothing more was sent
    client.post(f"/api/trading/incomplete/{result.leg_group_id}/answer", headers=t["headers"], json={"action": "complete_rest"})
    broker.reject = "no-such-symbol"
    outcomes = _complete(t, broker=broker)
    assert outcomes[0]["state"] == inc.COMPLETED and [o.transaction_type.value for o in broker.placed[2:]] == ["SELL"]
    assert broker.placed[2].intent == "ENTRY"


# --- the settings ---------------------------------------------------------------------------------------------------------

def test_the_answer_settings_have_bounds_and_a_safe_default():
    t = _tenant("se7-prefs@example.com")
    got = client.get("/api/me/prefs", headers=t["headers"]).json()["prefs"]["smart_execution"]
    assert got["on_no_answer"] == "close_filled" and got["answer_minutes"] == 5
    base = {"settlement_days_before": 4, "settlement_band_pct": 2, "settlement_telegram": True, "protection_band_pct": 0.5}
    for bad in ({"answer_minutes": 0}, {"answer_minutes": 121}, {"on_no_answer": "unwind"}):
        assert client.put("/api/me/prefs/smart_execution", headers=t["headers"], json={**base, **bad}).status_code == 422
    ok = client.put("/api/me/prefs/smart_execution", headers=t["headers"], json={**base, "on_no_answer": "keep", "answer_minutes": 120})
    assert ok.status_code == 200 and ok.json()["value"]["answer_minutes"] == 120


# --- the trading worker runs both: his rule in the exits part, "Complete the rest" in the entries part ---------------------

def _shift_deadline(t, when):
    async def go():
        async with _session_factory() as session:
            for tr in await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"])):
                meta = json.loads(tr.group_meta)
                meta["incomplete"]["deadline"] = when.isoformat()
                tr.group_meta = json.dumps(meta)
            await session.commit()
    _run(go())


def test_the_worker_applies_the_rule_and_sends_the_rest(monkeypatch):
    from tests.test_trading_worker import OPEN_NOW, _worker
    t = _smart(_tenant("se7-worker@example.com"))
    _incomplete_paper(t, monkeypatch, key="wk1")
    broker = _OptionBroker()
    worker = _worker(monkeypatch, broker)
    _shift_deadline(t, OPEN_NOW + timedelta(minutes=10))
    _run(worker.run_cycle(now=OPEN_NOW))                                       # before the deadline: nothing
    assert inc.block_of(_trades(t["tenant_id"])[0])["state"] == inc.INCOMPLETE
    _shift_deadline(t, OPEN_NOW - timedelta(minutes=1))
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))
    leg = _trades(t["tenant_id"])[0]
    assert leg.exit_time is not None and inc.block_of(leg)["state"] == inc.CLOSED and report.positions_closed >= 1

    t2 = _smart(_tenant("se7-worker-2@example.com"))
    result = _incomplete_paper(t2, monkeypatch, key="wk2")
    worker = _worker(monkeypatch, broker)
    client.post(f"/api/trading/incomplete/{result.leg_group_id}/answer", headers=t2["headers"], json={"action": "complete_rest"})
    _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=2)))
    trades = _trades(t2["tenant_id"])
    assert len(trades) == 2 and all(inc.block_of(tr)["state"] == inc.COMPLETED for tr in trades)
