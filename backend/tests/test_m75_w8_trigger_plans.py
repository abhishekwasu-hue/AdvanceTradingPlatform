"""M75 W-8 (owner order M75 CW-6 / CW-7 / CW-8; DECISIONS 1 Nov M75Q-1 / M75Q-2): trigger plans.

Proofs (the PR plan's W-8 row and CW-8): nothing is pre-chosen (no plan without his place / ask choice); the plan fires
exactly once on a double pass; its exits follow the fill (the target armed on the position, the stop on the order);
a restart while TRIGGERED never places a second order; the approval-only organisation turns the fire into the existing
ENTRY proposal and only his Approve places it; validity, pause / resume / cancel and his time window; a candle that
closed before the plan was armed never fires it (no look-ahead); the routes are his own and 404 while the flag is off.
"""
import asyncio
import json
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import AiActionRecord, OrderRecord, PaperRestingOrderRecord, TradeRecord, TriggerPlanRecord, User
from app.execution import approval, trigger_engine, trigger_plans
from app.trading.trigger_rules import ABOVE, BELOW
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _contract, _expiry, _org_tenant, _price
from tests.test_z5_p1d_risk_by_default import _flag

SPOT = ("NIFTY 50", "NSE")


def _run(coro):
    return asyncio.run(coro)


def _ref() -> str:
    return uuid.uuid4().hex[:16]


@pytest.fixture
def setup(monkeypatch):
    t = _org_tenant("w8")
    _flag(trigger_plans.FLAG, t["tenant_id"])
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


def _prices(table):
    async def lookup(symbol, exchange="NFO"):
        if symbol not in table:
            raise LookupError(symbol)
        return float(table[symbol])
    return lookup


def _body(expiry, strike, **kw):
    order = {"expiry": expiry.isoformat(), "strike": strike, "right": "CE", "side": "BUY", "lots": 1, "order_type": "MARKET",
             "product": "MIS", "stop_loss": 60.0, "target": 150.0}
    order.update(kw.pop("order", {}))
    body = {"underlying": "NIFTY", "client_ref": _ref(), "condition": {"level": 24700.0, "side": ABOVE, "mode": "TOUCH"},
            "orders": [order], "approval": "AUTO", "valid_until": (datetime.now(UTC) + timedelta(days=2)).isoformat()}
    body.update(kw)
    return body


def _create(t, body, status=200):
    out = client.post("/api/trigger-plans", headers=t["headers"], json=body)
    assert out.status_code == status, out.text
    return out.json()


def _pass(tenant_id, table, now=None, candles=None):
    async def go():
        async with _session_factory() as session:
            return await trigger_plans.run(session, tenant_id, _prices(table), candles=candles, now=now)
    return _run(go())


def _plan(plan_id) -> TriggerPlanRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TriggerPlanRecord, plan_id)
    return _run(go())


def _orders(user_id, plan_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.idempotency_key.like(f"fno:{user_id}:plan-{plan_id}-%"))))
    return _run(go())


def _targets(trade_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(PaperRestingOrderRecord).where(PaperRestingOrderRecord.trade_id == trade_id,
                                                                                    PaperRestingOrderRecord.kind == "TARGET")))
    return _run(go())


# --- nothing pre-chosen; the routes ---------------------------------------------------------------------------------------

def test_routes_are_404_while_the_flag_is_off(setup):
    t, expiry, strike, _ = setup
    _flag(trigger_plans.FLAG, t["tenant_id"], on=False)
    assert client.get("/api/trigger-plans", headers=t["headers"]).status_code == 404
    assert client.post("/api/trigger-plans", headers=t["headers"], json=_body(expiry, strike)).status_code == 404


def test_no_plan_without_his_place_or_ask_choice_and_a_second_tap_is_the_same_plan(setup):
    t, expiry, strike, _ = setup
    asked = _create(t, _body(expiry, strike, approval=None), status=422)
    assert "place at once, or ask me first" in asked["detail"]                  # Not set anywhere: the server asks
    body = _body(expiry, strike)
    first = _create(t, body)
    assert first["status"] == "ARMED" and first["approval"] == "AUTO" and first["watch_symbol"] == "NIFTY 50"
    assert _create(t, body)["id"] == first["id"]                                # the client reference: no second plan
    assert [p["id"] for p in client.get("/api/trigger-plans", headers=t["headers"]).json()].count(first["id"]) == 1


def test_his_own_values_only(setup):
    t, expiry, strike, _ = setup
    bad = _create(t, _body(expiry, strike, order={"order_type": "LIMIT"}), status=422)
    assert "needs its price" in bad["detail"]
    close = _create(t, _body(expiry, strike, condition={"level": 24700.0, "side": ABOVE, "mode": "CLOSE"}), status=422)
    assert "timeframe" in close["detail"]


# --- fires once; exits follow; restart ------------------------------------------------------------------------------------

def test_fires_exactly_once_on_a_double_pass_and_the_target_follows_the_fill(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike))
    assert _pass(t["tenant_id"], {SPOT[0]: 24650.0, symbol: 100.0})["triggered"] == 0        # not reached: still armed
    assert _plan(plan["id"]).status == "ARMED"
    report = _pass(t["tenant_id"], {SPOT[0]: 24701.0, symbol: 100.0})
    assert report["triggered"] == 1 and report["done"] == 1
    row = _plan(plan["id"])
    assert row.status == "DONE" and row.trigger_price == 24701.0
    result = json.loads(row.results_json)["0"]
    assert result["status"] == "PLACED" and result["target_armed"] is True
    assert _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0})["triggered"] == 0        # a second pass: nothing
    assert len(_orders(t["user_id"], plan["id"])) == 1
    targets = _targets(result["trade_id"])
    assert len(targets) == 1 and targets[0].trigger_level == 150.0 and targets[0].status == "RESTING"

    async def trade():
        async with _session_factory() as session:
            return await session.get(TradeRecord, result["trade_id"])
    assert _run(trade()).stop_loss == 60.0                                                     # his stop on the position


def test_two_plans_of_one_organisation_in_one_pass(setup):
    t, expiry, strike, symbol = setup
    one = _create(t, _body(expiry, strike))
    two = _create(t, _body(expiry, strike, condition={"level": 24600.0, "side": ABOVE, "mode": "TOUCH"}))
    report = _pass(t["tenant_id"], {SPOT[0]: 24701.0, symbol: 100.0})
    assert report["triggered"] == 2 and report["done"] == 2
    assert _plan(one["id"]).status == "DONE" and _plan(two["id"]).status == "DONE"
    assert len(_orders(t["user_id"], one["id"])) == 1 and len(_orders(t["user_id"], two["id"])) == 1


def test_a_restart_while_triggered_never_places_a_second_order(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike))
    _pass(t["tenant_id"], {SPOT[0]: 24701.0, symbol: 100.0})
    assert len(_orders(t["user_id"], plan["id"])) == 1

    async def crash_after_send():
        # the worker stopped after the order went out but before the plan's results were saved
        async with _session_factory() as session:
            row = await session.get(TriggerPlanRecord, plan["id"])
            row.status, row.results_json, row.finished_at = "TRIGGERED", None, None
            await session.commit()
    _run(crash_after_send())
    _pass(t["tenant_id"], {SPOT[0]: 24600.0, symbol: 100.0})                                   # the next pass after a restart
    row = _plan(plan["id"])
    assert row.status == "DONE"
    assert len(_orders(t["user_id"], plan["id"])) == 1                                         # still ONE order
    assert len(_targets(json.loads(row.results_json)["0"]["trade_id"])) == 1                   # and ONE target


def test_the_trigger_engine_entry_pass_runs_the_plans_and_counts_their_organisation(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike, condition={"level": 24500.0, "side": BELOW, "mode": "TOUCH"}))

    async def go():
        async with _session_factory() as session:
            assert t["tenant_id"] in await trigger_engine.tenants_with_resting(session)
            await trigger_engine.run_entries(session, t["tenant_id"], _prices({SPOT[0]: 24490.0, symbol: 100.0}), paused=False)
    _run(go())
    assert _plan(plan["id"]).status == "DONE"


def test_nothing_fires_while_entries_are_paused(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike))

    async def go():
        async with _session_factory() as session:
            await trigger_engine.run_entries(session, t["tenant_id"], _prices({SPOT[0]: 24800.0, symbol: 100.0}), paused=True)
    _run(go())
    assert _plan(plan["id"]).status == "ARMED"


# --- ask me first / approval-only ---------------------------------------------------------------------------------------

def test_an_approval_only_organisation_turns_the_fire_into_a_proposal_and_only_his_approve_places(setup):
    t, expiry, strike, symbol = setup
    _flag("approval_only_entries", t["tenant_id"])
    plan = _create(t, _body(expiry, strike))                                   # he chose "place at once": the org wins
    _pass(t["tenant_id"], {SPOT[0]: 24701.0, symbol: 100.0})
    row = _plan(plan["id"])
    assert row.status == "TRIGGERED" and _orders(t["user_id"], plan["id"]) == []              # nothing placed
    proposal_id = json.loads(row.results_json)["0"]["proposal_id"]
    _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0})                                   # waiting: no second request

    async def approve():
        async with _session_factory() as session:
            assert len(list(await session.scalars(select(AiActionRecord).where(AiActionRecord.idempotency_key == f"plan:{plan['id']}:0")))) == 1
            action = await session.get(AiActionRecord, proposal_id)
            action.status = "APPROVED"
            await session.commit()
            user = await session.get(User, t["user_id"])
            return await approval.execute_entry(session, action, user, price_lookup=_prices({symbol: 100.0, SPOT[0]: 24800.0}))
    done = _run(approve())
    assert done.status == "EXECUTED", done.result
    _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0})
    row = _plan(plan["id"])
    result = json.loads(row.results_json)["0"]
    assert row.status == "DONE" and result["status"] == "PLACED" and result["target_armed"] is True
    assert len(_orders(t["user_id"], plan["id"])) == 1


def test_ask_me_first_on_the_plan_itself(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike, approval="APPROVAL"))
    _pass(t["tenant_id"], {SPOT[0]: 24701.0, symbol: 100.0})
    assert json.loads(_plan(plan["id"]).results_json)["0"]["status"] == "PROPOSED"
    assert _orders(t["user_id"], plan["id"]) == []


# --- validity, pause / resume / cancel, window, no look-ahead ------------------------------------------------------------

def test_validity_ends_the_plan(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike))
    later = datetime.now(UTC) + timedelta(days=3)
    assert _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0}, now=later)["expired"] == 1
    assert _plan(plan["id"]).status == "EXPIRED" and _orders(t["user_id"], plan["id"]) == []


def test_pause_resume_cancel_and_another_member_cannot_touch_it(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike))
    assert client.post(f"/api/trigger-plans/{plan['id']}/pause", headers=t["headers"]).json()["status"] == "PAUSED"
    _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0})
    assert _plan(plan["id"]).status == "PAUSED"                                                # a paused plan never fires
    assert client.post(f"/api/trigger-plans/{plan['id']}/resume", headers=t["headers"]).json()["status"] == "ARMED"
    other = _org_tenant("w8-other")
    _flag(trigger_plans.FLAG, other["tenant_id"])
    assert client.post(f"/api/trigger-plans/{plan['id']}/cancel", headers=other["headers"]).status_code == 404
    assert client.post(f"/api/trigger-plans/{plan['id']}/cancel", headers=t["headers"]).json()["status"] == "CANCELLED"
    assert client.post(f"/api/trigger-plans/{plan['id']}/resume", headers=t["headers"]).status_code == 409


def test_his_time_window(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike, condition={"level": 24700.0, "side": ABOVE, "mode": "TOUCH", "window_start": "10:00", "window_end": "11:00"}))
    outside = datetime.now(UTC).replace(hour=7, minute=0) + timedelta(days=1)                 # 12:30 IST
    _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0}, now=outside)
    assert _plan(plan["id"]).status == "ARMED"
    inside = outside.replace(hour=5, minute=0)                                                 # 10:30 IST
    _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0}, now=inside)
    assert _plan(plan["id"]).status == "DONE"


def test_a_candle_closed_before_the_plan_was_armed_never_fires_it(setup):
    t, expiry, strike, symbol = setup
    plan = _create(t, _body(expiry, strike, condition={"level": 24700.0, "side": ABOVE, "mode": "CLOSE", "timeframe_minutes": 5}))
    armed = trigger_plans._utc(_plan(plan["id"]).armed_at)
    start = armed.replace(second=0, microsecond=0) - timedelta(minutes=30)

    def bar(i, close):
        return type("Bar", (), {"timestamp": start + timedelta(minutes=i), "open": close, "high": close, "low": close, "close": close})()

    old = [bar(i, 24800.0) for i in range(25)]                                                 # closed above, but before arming

    async def candles_old(symbol_, exchange):
        return old
    _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0}, now=armed + timedelta(seconds=1), candles=candles_old)
    assert _plan(plan["id"]).status == "ARMED"
    new = old + [bar(i, 24750.0) for i in range(25, 45)]

    async def candles_new(symbol_, exchange):
        return new
    _pass(t["tenant_id"], {SPOT[0]: 24800.0, symbol: 100.0}, now=start + timedelta(minutes=46), candles=candles_new)
    assert _plan(plan["id"]).status == "DONE"


def test_his_journal_source_is_plan(setup):
    from app.db.models import JournalEntryRecord
    from app.journal import capture
    t, expiry, strike, symbol = setup
    _flag("discipline_engine", t["tenant_id"])
    capture.reset_flag_cache()
    plan = _create(t, _body(expiry, strike))

    async def go():
        async with _session_factory() as session:
            await trigger_plans.run(session, t["tenant_id"], _prices({SPOT[0]: 24701.0, symbol: 100.0}))
            await capture.drain()
        async with _session_factory() as other:
            trade_id = json.loads((await other.get(TriggerPlanRecord, plan["id"])).results_json)["0"]["trade_id"]
            return await other.scalar(select(JournalEntryRecord.source).where(JournalEntryRecord.trade_id == trade_id))
    try:
        assert _run(go()) == "PLAN"
    finally:
        _flag("discipline_engine", t["tenant_id"], on=False)
        capture.reset_flag_cache()
