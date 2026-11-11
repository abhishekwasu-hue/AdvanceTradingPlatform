"""M75 W-10 (owner order M75 CW-6 / CW-8; DECISIONS 23 Oct TM-23, 1 Nov M75Q-1): the broker GTT path of trigger plans,
on a fake broker (the PR plan's W-10 row: create / cancel / fill / reject; the kill switch cancels first; the Phase 0
lock refuses the create).

Also proven: ATP's engine stays the default (flag off, his setting Not set, "ask me first", a time window, a plan on the
underlying, and - until the LIVE manual path records a fill with its exits - every plan); a plan waiting at the broker
is never fired by ATP's engine as well (one path, once); a GTT the broker ends returns the plan to ATP's engine; pause /
cancel take the GTT down; a broker GTT is a new entry at the order chokepoint (the wrapper refuses it under the lock).
"""
import asyncio
import json

import pytest

from app.brokers.models import BrokerOrderStatus, GttStatus
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, limits_for
from app.core.enums import KillSwitchScope, OrderSide
from app.db.models import TriggerPlanRecord, User
from app.execution import broker_gtt, trigger_plans
from app.kill_switch import checks
from app.platform import phase0_lock
from tests.live_switch_helpers import set_live
from tests.test_auth_api import _session_factory, client
from tests.test_m75_w8_trigger_plans import SPOT, _body, _create, _orders, _pass, _plan
from tests.test_m75_w8_trigger_plans import setup as w8_setup
from tests.test_trading_worker import _FakeBroker
from tests.test_z5_p1d_risk_by_default import _flag

pytestmark = pytest.mark.usefixtures("owner_switched_live_on")


@pytest.fixture
def setup(monkeypatch):
    """The W-8 setup (`trigger_plans` on, a contract and its price); afterwards the organisation's plans are ended, so no
    armed plan of this module reaches another module's worker cycle."""
    out = w8_setup.__wrapped__(monkeypatch)
    yield out

    async def end():
        from sqlalchemy import update
        async with _session_factory() as session:
            await session.execute(update(TriggerPlanRecord).where(TriggerPlanRecord.tenant_id == out[0]["tenant_id"],
                                                                  TriggerPlanRecord.status.in_(("ARMED", "PAUSED", "TRIGGERED")))
                                  .values(status="CANCELLED"))
            from app.db.models import PaperRestingOrderRecord  # the target a fired plan armed on its position
            await session.execute(update(PaperRestingOrderRecord).where(PaperRestingOrderRecord.tenant_id == out[0]["tenant_id"],
                                                                        PaperRestingOrderRecord.status == "RESTING")
                                  .values(status="CANCELLED"))
            await session.commit()
    _run(end())


class FakeGtt(_FakeBroker):
    """A broker that offers GTT: it keeps each one, and the test moves it (trigger / cancel / reject)."""
    supports_gtt = True

    def __init__(self, reject: bool = False):
        super().__init__()
        self.gtts: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.reject = reject
        self.book: list[BrokerOrderStatus] = []

    async def place_gtt(self, gtt):
        self.calls.append(("place", gtt.client_ref))
        if self.reject:
            return GttStatus(gtt_id="", status="REJECTED", message="Not allowed for this segment")
        gid = f"G{len(self.gtts) + 1}"
        self.gtts[gid] = {"status": "ACTIVE", "order_id": None, "req": gtt}
        return GttStatus(gtt_id=gid, status="ACTIVE")

    async def modify_gtt(self, gtt_id, gtt):
        self.calls.append(("modify", gtt_id))
        return GttStatus(gtt_id=gtt_id, status="ACTIVE")

    async def cancel_gtt(self, gtt_id):
        self.calls.append(("cancel", gtt_id))
        self.gtts[gtt_id]["status"] = "CANCELLED"
        return GttStatus(gtt_id=gtt_id, status="CANCELLED")

    async def get_gtt(self, gtt_id):
        g = self.gtts[gtt_id]
        return GttStatus(gtt_id=gtt_id, status=g["status"], order_id=g["order_id"])

    async def get_order_book(self):
        return list(self.book)

    def fire(self, gid: str, order_id: str = "B1") -> None:
        self.gtts[gid].update(status="TRIGGERED", order_id=order_id)
        req = self.gtts[gid]["req"].order
        self.book.append(BrokerOrderStatus(order_id=order_id, symbol=req.symbol, transaction_type=OrderSide.BUY, quantity=req.quantity,
                                           filled_quantity=req.quantity, order_type="MARKET", status="COMPLETE", average_price=101.5))


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def broker(monkeypatch):
    fake = FakeGtt()

    async def adapter_for(session, user):
        return RateLimitedBroker(fake, RateBudget(limits_for(fake.name)), tenant_id=user.tenant_id)
    monkeypatch.setattr(broker_gtt, "own_adapter", adapter_for)
    return fake


def _his_choice(t, value="BROKER"):
    out = client.put("/api/me/trade-settings", headers=t["headers"], json={"values": {"automation.plan_broker_gtt": value}})
    assert out.status_code == 200, out.text


def _on_contract(plan_id, symbol):
    """The plan watches the ordered contract's own price (what a broker GTT can watch, M75Q-8)."""
    async def go():
        async with _session_factory() as session:
            row = await session.get(TriggerPlanRecord, plan_id)
            row.watch_symbol, row.watch_exchange = symbol, "NFO"
            c = json.loads(row.condition_json)
            c["level"] = 110.0
            row.condition_json = json.dumps(c)
            await session.commit()
    _run(go())


def _ready(t, expiry, strike, symbol, monkeypatch, **body):
    _flag(broker_gtt.FLAG, t["tenant_id"])
    _his_choice(t)
    monkeypatch.setattr(broker_gtt, "FILL_RECORDING", True)
    plan = _create(t, _body(expiry, strike, **body))
    _on_contract(plan["id"], symbol)
    return plan


def _reasons(plan_id):
    async def go():
        async with _session_factory() as session:
            row = await session.get(TriggerPlanRecord, plan_id)
            return await broker_gtt.route_reasons(session, await session.get(User, row.user_id), row)
    return _run(go())


# --- ATP's engine stays the default --------------------------------------------------------------------------------------

def test_atp_engine_is_the_default_and_each_reason_is_named(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike))
    reasons = _reasons(plan["id"])
    assert any("not switched on" in r for r in reasons)                       # the flag
    assert any("keep simple price plans in ATP" in r for r in reasons)       # his setting Not set = ATP
    assert any("watches NIFTY 50" in r for r in reasons)                     # a plan on the underlying (M75Q-8)
    assert any("cannot be recorded" in r for r in reasons)                   # no fill recording until TT-1 (M75Q-9)
    ready = _ready(t, expiry, strike, symbol, monkeypatch)
    assert _reasons(ready["id"]) == []
    ask = _ready(t, expiry, strike, symbol, monkeypatch, approval="APPROVAL")
    assert any("asks you first" in r for r in _reasons(ask["id"]))
    window = _ready(t, expiry, strike, symbol, monkeypatch, condition={"level": 24700.0, "side": "ABOVE", "mode": "TOUCH",
                                                                         "window_start": "09:30", "window_end": "15:00"})
    assert any("no time window" in r for r in _reasons(window["id"]))
    monkeypatch.setattr(broker_gtt, "FILL_RECORDING", False)
    assert any("cannot be recorded" in r for r in _reasons(ready["id"]))


def test_with_the_flag_off_the_broker_is_never_asked(setup, broker):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike))
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    assert broker.calls == [] and _plan(plan["id"]).status == "ARMED"


# --- create / fill: one path, once ----------------------------------------------------------------------------------------

def test_create_once_then_the_broker_fill_ends_the_plan_without_a_second_order(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    assert broker.calls == [("place", f"plan-{plan['id']}-0")]
    g = broker.gtts["G1"]["req"]
    assert g.symbol == symbol and g.trigger_price == 110.0 and g.order.intent == "ENTRY" and g.order.client_ref == f"plan-{plan['id']}-0"
    row = _plan(plan["id"])
    assert row.status == "ARMED" and broker_gtt.gtts(row)[0]["status"] == "ACTIVE" and row.note == "Waiting at your broker as a GTT."
    # the price crosses his level: the broker watches it; ATP's engine does not fire it as well
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 120.0})
    assert _plan(plan["id"]).status == "ARMED" and _orders(t["user_id"], plan["id"]) == [] and len(broker.calls) == 1
    broker.fire("G1")
    report = _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 120.0})
    row = _plan(plan["id"])
    assert report["triggered"] == 1 and row.status == "DONE"
    r = json.loads(row.results_json)["0"]
    assert r["broker_order_id"] == "B1" and r["average_price"] == 101.5 and r["settled"] is True and r["gtt"]["status"] == "TRIGGERED"
    assert _orders(t["user_id"], plan["id"]) == []                         # ATP placed nothing: the broker's order only
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 120.0})
    assert [c[0] for c in broker.calls] == ["place"]                         # no second GTT after a restart / pass


def test_a_rejected_gtt_leaves_the_plan_in_atps_engine(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    broker.reject = True
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    row = _plan(plan["id"])
    assert row.status == "ARMED" and broker_gtt.gtts(row)[0]["status"] == "REJECTED" and "ATP's trigger engine" in row.note
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    assert len(broker.calls) == 1                                            # refused once: not asked again each pass
    report = _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 111.0})       # ATP's engine reads his level and fires
    assert report["triggered"] == 1 and len(_orders(t["user_id"], plan["id"])) == 1


def test_a_gtt_the_broker_ended_returns_the_plan_to_atps_engine(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    broker.gtts["G1"]["status"] = "EXPIRED"
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    row = _plan(plan["id"])
    assert row.status == "ARMED" and "ATP's trigger engine again" in row.note and broker_gtt.active(row) == {}


# --- the chokepoints: Phase 0 lock and the kill switch --------------------------------------------------------------------

def test_the_phase0_lock_refuses_the_create(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    set_live(False)                        # LIVE-1: the lock follows live_trading
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    assert broker.calls == [] and "waits in ATP's trigger engine" in _plan(plan["id"]).note
    # the wrapper refuses it as well (a GTT's order is always a new entry)
    phase0_lock.remember(True, ())
    try:
        wrapped = RateLimitedBroker(broker, RateBudget(limits_for(broker.name)), tenant_id=t["tenant_id"])
        out = _run(wrapped.place_gtt(broker_gtt._request(_plan(plan["id"]), trigger_plans.orders_of(_plan(plan["id"]))[0], 0,  # noqa: SLF001
                                                          type("C", (), {"tradingsymbol": symbol, "exchange": "NFO", "lot_size": 75})(), 100.0)))
    finally:
        set_live(None)
    assert out.status == "REJECTED" and "phase0_paper_only" in (out.message or "") and broker.calls == []


def test_an_engaged_kill_switch_refuses_the_create_and_engaging_one_cancels_gtts_first(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    assert broker_gtt.active(_plan(plan["id"]))
    engaged_at_cancel = []
    original = broker.cancel_gtt

    async def cancel(gtt_id):
        async with _session_factory() as s:                                  # the switch is not stored yet: GTTs go first
            engaged_at_cancel.append(await checks.get_switch(s, KillSwitchScope.TENANT, t["tenant_id"]))
        return await original(gtt_id)
    broker.cancel_gtt = cancel

    async def engage():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            await checks.engage(session, KillSwitchScope.TENANT, t["tenant_id"], user, "test")
    _run(engage())
    row = _plan(plan["id"])
    assert ("cancel", "G1") in broker.calls and broker_gtt.gtts(row)[0]["status"] == "CANCELLED"
    assert engaged_at_cancel == [None] and "kill switch" in row.note
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    assert [c for c in broker.calls if c[0] == "place"] == [("place", f"plan-{plan['id']}-0")]   # no new GTT while it holds

    async def release():
        async with _session_factory() as session:
            await checks.disengage(session, KillSwitchScope.TENANT, t["tenant_id"])
    _run(release())


def test_a_strategy_switch_of_another_strategy_leaves_plans_alone(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})

    async def go():
        async with _session_factory() as session:
            return await broker_gtt.cancel_all(session, t["tenant_id"], "some_other_bot")
    assert _run(go()) == 0 and broker_gtt.active(_plan(plan["id"]))


# --- his taps take the GTT down ---------------------------------------------------------------------------------------------

def test_pause_and_cancel_take_the_gtt_down(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    assert client.post(f"/api/trigger-plans/{plan['id']}/pause", headers=t["headers"]).status_code == 200
    assert ("cancel", "G1") in broker.calls and broker_gtt.active(_plan(plan["id"])) == {}
    assert client.post(f"/api/trigger-plans/{plan['id']}/resume", headers=t["headers"]).status_code == 200
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    assert broker_gtt.active(_plan(plan["id"]))                              # armed again: a new GTT
    assert client.post(f"/api/trigger-plans/{plan['id']}/cancel", headers=t["headers"]).status_code == 200
    assert ("cancel", "G2") in broker.calls and _plan(plan["id"]).status == "CANCELLED"


# --- idempotent: a GTT the broker already sent is never placed again; no GTT outlives his tap ------------------------------

def test_a_gtt_that_triggered_before_his_pause_finishes_the_plan_without_a_second_order(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})
    broker.fire("G1")                                                        # sent at the broker before his tap reached it

    async def too_late(gtt_id):
        broker.calls.append(("cancel", gtt_id))
        return GttStatus(gtt_id=gtt_id, status="TRIGGERED", order_id="B1")
    broker.cancel_gtt = too_late
    out = client.post(f"/api/trigger-plans/{plan['id']}/pause", headers=t["headers"])
    assert out.status_code == 409 and "already triggered" in out.json()["detail"]
    row = _plan(plan["id"])
    r = json.loads(row.results_json)["0"]
    assert row.status == "TRIGGERED" and r["settled"] is True and r["broker_order_id"] == "B1"
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 120.0})
    assert _plan(plan["id"]).status == "DONE" and _orders(t["user_id"], plan["id"]) == []   # the broker's order only
    assert [c[0] for c in broker.calls].count("place") == 1


def test_a_cancel_his_broker_did_not_confirm_leaves_the_plan_unchanged(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})

    async def no_answer(gtt_id):
        raise TimeoutError("no answer")
    broker.cancel_gtt = no_answer
    for action in ("pause", "cancel"):
        out = client.post(f"/api/trigger-plans/{plan['id']}/{action}", headers=t["headers"])
        assert out.status_code == 409 and "did not confirm" in out.json()["detail"]
    row = _plan(plan["id"])
    assert row.status == "ARMED" and broker_gtt.active(row)                  # still watched: never a GTT nobody follows


def test_a_triggered_plan_with_only_an_ended_gtt_is_fired_by_atps_engine_after_a_restart(setup, broker, monkeypatch):
    t, expiry, strike, symbol = setup
    broker.reject = True
    plan = _ready(t, expiry, strike, symbol, monkeypatch)
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})                 # refused at the broker: ATP's engine watches

    async def triggered_then_restart():
        from sqlalchemy import update
        async with _session_factory() as session:                            # ATP's engine moved it, then the worker stopped
            await session.execute(update(TriggerPlanRecord).where(TriggerPlanRecord.id == plan["id"]).values(status="TRIGGERED"))
            await session.commit()
    _run(triggered_then_restart())
    _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 111.0})
    assert _plan(plan["id"]).status == "DONE" and len(_orders(t["user_id"], plan["id"])) == 1   # placed once, by ATP
