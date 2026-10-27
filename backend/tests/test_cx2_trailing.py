"""CX2 trailing (owner, ATP_ORDER_CX_SX.md CX2 / SX2): the optional trailing of a combined exit.

LOCK: once the gain reaches A, the stop moves to keep B. TRAIL: every X of favourable move, the stop moves by Y. For the
₹ MTM and a spot / premium level. Proofs: the stop only ever moves in the trade's favour (pure, through the engine, and
in the database against a stale pass); the lock fires once; a restart mid-exit closes nothing twice and keeps the
trailed stop; the kill switch ON still exits; the API validates the user's numbers and adds none of its own.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

from app.db.models import GroupExitRecord, TradeRecord
from app.execution import group_exits as gx
from app.execution import trigger_engine
from app.trading import position_monitor as pm
from tests.test_auth_api import _session_factory, client
from tests.test_cx_api import BASE, prices  # noqa: F401 - the fixture
from tests.test_cx_api import _spread as _api_spread
from tests.test_cx_group_exits import NOW, _arm, _get, _no_live_exits_left, _org, _pass, _spread, closes  # noqa: F401
from tests.test_fno1_chain_ticket import _org_tenant


def _run(coro):
    return asyncio.run(coro)


def _row(**kw) -> GroupExitRecord:
    base = dict(measure=gx.MTM_INR, sl_value=1000.0, trail_kind=gx.TRAIL, trail_json=json.dumps({"step": 1000, "by": 500}),
                legs_json="[]", sl_side=None, target_side=None, basis_value=None, best_value=None, current_sl=None,
                locked_at=None, trail_log=None, spot_symbol=None, target_value=None)
    base.update(kw)
    return GroupExitRecord(**base)


# --- pure: only ever in favour -----------------------------------------------------------------------------------------------

def test_an_mtm_trail_moves_the_stop_up_by_whole_steps_and_never_back():
    row = _row()
    seen = []
    for value in (400.0, 1200.0, 800.0, -300.0, 2100.0, 1500.0, 3999.0):
        gx.apply_trail(row, value, NOW)
        seen.append(gx.stop_now(row))
    assert seen == [-1000.0, -500.0, -500.0, -500.0, 0.0, 0.0, 500.0]
    assert all(b >= a for a, b in zip(seen, seen[1:]))                            # never back
    assert row.best_value == 3999.0
    assert gx.decide(row, 600.0) is None and gx.decide(row, 500.0) == gx.SL
    assert gx.distance(row, 900.0)["sl"] == 400.0


def test_a_level_trail_follows_the_trade_side_for_a_stop_above_and_a_stop_below():
    above = _row(measure=gx.SPOT, sl_value=24000.0, sl_side=gx.ABOVE, basis_value=23800.0, spot_symbol="NIFTY 50",
                 trail_json=json.dumps({"step": 50, "by": 40}))                   # gains as the spot falls
    for value in (23740.0, 23790.0, 23900.0, 23690.0):
        gx.apply_trail(above, value, NOW)
    assert above.current_sl == 23920.0 and gx.stop_side(above) == gx.ABOVE      # 2 steps down: 24000 - 80
    assert gx.decide(above, 23919.0) is None and gx.decide(above, 23920.0) == gx.SL
    below = _row(measure=gx.NET_PREMIUM, sl_value=40.0, sl_side=gx.BELOW, basis_value=50.0,
                 trail_json=json.dumps({"step": 5, "by": 5}))                     # gains as the premium rises
    for value in (56.0, 52.0, 61.0, 45.0):
        gx.apply_trail(below, value, NOW)
    assert below.current_sl == 50.0 and gx.decide(below, 50.0) == gx.SL


def test_a_lock_keeps_the_amount_once_the_gain_is_reached_and_a_lock_alone_is_a_stop():
    row = _row(sl_value=None, trail_kind=gx.LOCK, trail_json=json.dumps({"after": 2000, "lock": 500}))
    assert not gx.apply_trail(row, 1999.0, NOW) and gx.stop_now(row) is None
    assert gx.apply_trail(row, 2000.0, NOW) and gx.stop_now(row) == 500.0
    assert not gx.apply_trail(row, 5000.0, NOW) and gx.stop_now(row) == 500.0
    assert "Once the combined profit reaches ₹2,000, the stop moves to keep ₹500." in gx.describe(row)
    assert "The stop only moves your way." in gx.describe(row)


def test_the_trailing_numbers_are_the_users_own_and_checked_in_plain_words():
    assert gx.trail_errors(gx.TRAIL_NONE, None, None) == []
    assert gx.trail_errors(gx.TRAIL, {"step": 100, "by": 50}, None) == ["A trailing stop needs a stop-loss to start from"]
    assert "The stop cannot shift further than the move itself" in gx.trail_errors(gx.TRAIL, {"step": 50, "by": 60}, 100.0)
    assert "The gain kept must be less than the gain that moves the stop" in gx.trail_errors(gx.LOCK, {"after": 50, "lock": 50}, None)
    assert gx.trail_errors(gx.LOCK, {}, None) == ["Enter the gain after which the stop moves (more than zero)",
                                                  "Enter how much of the gain to keep (zero or more)"]
    assert gx.trail_errors(gx.LOCK, {"after": 50, "lock": 0}, None) == []      # keep zero: the user's own choice


# --- through the engine -----------------------------------------------------------------------------------------------------

def test_the_engine_trails_the_stored_stop_and_fires_once_on_the_trailed_stop(closes):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "CXT")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0, trail_kind=gx.TRAIL, trail={"step": 1000, "by": 500})
    assert row.trail_kind == gx.TRAIL and "For every ₹1,000 the combined profit rises, the stop moves up ₹500." in gx.describe(row)
    _pass(t, {"CXTSHORT": 70.0, "CXTHEDGE": 40.0})                               # MTM +1500: one step
    assert _get(GroupExitRecord, row.id).current_sl == -500.0
    _pass(t, {"CXTSHORT": 50.0, "CXTHEDGE": 41.0}, now=NOW + timedelta(seconds=5))   # +2550: two steps
    assert _get(GroupExitRecord, row.id).current_sl == 0.0
    _pass(t, {"CXTSHORT": 90.0, "CXTHEDGE": 41.0}, now=NOW + timedelta(seconds=10))  # +550: back, the stop stays
    stored = _get(GroupExitRecord, row.id)
    assert stored.current_sl == 0.0 and stored.status == gx.ARMED and closes == [] and stored.best_value == 2550.0
    _pass(t, {"CXTSHORT": 101.0, "CXTHEDGE": 40.0}, now=NOW + timedelta(seconds=15))  # -50: the trailed stop
    done = _get(GroupExitRecord, row.id)
    assert done.status == gx.DONE and done.fired_kind == gx.SL and closes == [short, hedge]
    for i in range(2):
        _pass(t, {"CXTSHORT": 120.0, "CXTHEDGE": 40.0}, now=NOW + timedelta(seconds=20 + i))
    assert closes == [short, hedge]


def test_a_lock_fires_once_and_the_exit_closes_once(closes):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "CXL")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0, trail_kind=gx.LOCK, trail={"after": 2000, "lock": 500})
    _pass(t, {"CXLSHORT": 50.0, "CXLHEDGE": 40.0})                               # +2500: locked
    first = _get(GroupExitRecord, row.id)
    assert first.current_sl == 500.0 and first.locked_at is not None
    _pass(t, {"CXLSHORT": 30.0, "CXLHEDGE": 40.0}, now=NOW + timedelta(minutes=1))   # +3500: nothing new
    again = _get(GroupExitRecord, row.id)
    assert again.locked_at == first.locked_at and again.current_sl == 500.0
    assert sum("Gain reached" in e["note"] for e in gx.trail(again)) == 1
    _pass(t, {"CXLSHORT": 92.0, "CXLHEDGE": 40.0}, now=NOW + timedelta(minutes=2))   # +400: the locked stop
    assert closes == [short, hedge] and _get(GroupExitRecord, row.id).status == gx.DONE
    _pass(t, {"CXLSHORT": 99.0, "CXLHEDGE": 40.0}, now=NOW + timedelta(minutes=3))
    assert closes == [short, hedge]


def test_a_spot_trail_on_closed_candles_judges_each_candle_on_the_stop_before_it(closes):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "CXC")
    row = _arm(t, [short, hedge], now_value=23800.0, measure=gx.SPOT, sl_value=24000.0, spot_symbol="NIFTY 50",
               spot_exchange="NSE", check_mode=gx.CLOSE, timeframe_minutes=1, trail_kind=gx.TRAIL, trail={"step": 100, "by": 100})
    start = NOW.replace(second=0, microsecond=0) + timedelta(minutes=1)

    async def candles(symbol, exchange):
        values = [23690, 23810, 23905]                                            # moves the stop to 23900, then breaks it
        return [SimpleNamespace(timestamp=start + timedelta(minutes=i), open=c, high=c, low=c, close=c) for i, c in enumerate(values)]
    table = {"NIFTY 50": 23905.0, "CXCSHORT": 110.0, "CXCHEDGE": 42.0}
    _pass(t, table, now=start + timedelta(minutes=2), candles=candles)           # two candles closed
    mid = _get(GroupExitRecord, row.id)
    assert mid.current_sl == 23900.0 and mid.status == gx.ARMED and closes == []   # 23810 is inside the moved stop
    _pass(t, table, now=start + timedelta(minutes=3), candles=candles)           # the third closes beyond 23900
    assert closes == [short, hedge] and _get(GroupExitRecord, row.id).fired_kind == gx.SL


def test_a_stale_pass_never_moves_the_stored_stop_back():
    t = _org()
    short, hedge = _spread(t, "CXS")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0, trail_kind=gx.TRAIL, trail={"step": 1000, "by": 500})

    async def go():
        async with _session_factory() as other:                                  # another pass already moved it to 0
            fresh = await other.get(GroupExitRecord, row.id)
            fresh.current_sl, fresh.best_value = 0.0, 2500.0
            await other.commit()
        async with _session_factory() as session:
            stale = await session.get(GroupExitRecord, row.id)
            before = (None, None, None)                                           # what this pass had read
            stale.best_value, stale.current_sl = 1600.0, -500.0
            await trigger_engine._save_trail(session, stale, before)
            await session.commit()
    _run(go())
    stored = _get(GroupExitRecord, row.id)
    assert stored.current_sl == 0.0 and stored.best_value == 2500.0


def test_a_restart_mid_exit_on_a_trailed_stop_closes_nothing_twice(closes):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "CXR")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0, trail_kind=gx.LOCK, trail={"after": 2000, "lock": 500})
    _pass(t, {"CXRSHORT": 50.0, "CXRHEDGE": 40.0})                               # locked at ₹500
    assert _get(GroupExitRecord, row.id).current_sl == 500.0

    async def crash_after_the_short():
        async with _session_factory() as session:
            armed = await session.get(GroupExitRecord, row.id)
            armed.last_value = 400.0
            assert await trigger_engine._claim_group(session, armed, gx.SL, NOW)
            trade = await session.get(TradeRecord, short)
            assert (await pm.close_position(session, trade, 92.0, "Combined stop-loss", user_id=t["user_id"])).closed
    _run(crash_after_the_short())
    report = _pass(t, {"CXRSHORT": 93.0, "CXRHEDGE": 40.0}, now=NOW + timedelta(seconds=30))
    assert report.filled == 1 and closes == [short, hedge]
    assert _get(GroupExitRecord, row.id).status == gx.DONE and _get(TradeRecord, short).exit_price == 92.0


def test_a_trailed_stop_exits_with_the_kill_switch_on(closes, monkeypatch):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "CXK")
    _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0, trail_kind=gx.TRAIL, trail={"step": 1000, "by": 1000})
    _pass(t, {"CXKSHORT": 60.0, "CXKHEDGE": 40.0})                               # +2000: the stop is at +1000
    asked = []

    async def refuse(*a, **k):
        asked.append("asked")
        return ["Kill switch"]
    from app.execution import approval, signal_execution
    monkeypatch.setattr(signal_execution, "entry_refusals", refuse)
    monkeypatch.setattr(signal_execution, "active_kill_switch_reasons", refuse)
    monkeypatch.setattr(approval, "gate_refusal", refuse)
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "cx2 proof"}).status_code == 200
    try:
        report = _pass(t, {"CXKSHORT": 85.0, "CXKHEDGE": 40.0}, now=NOW + timedelta(seconds=5))   # +750: below +1000
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    assert report.filled == 2 and asked == [] and closes == [short, hedge]


# --- the API ---------------------------------------------------------------------------------------------------------------

def test_the_api_takes_the_trailing_validates_it_and_shows_the_stop_now(prices):  # noqa: F811
    t = _org_tenant("cx2-api")
    short, hedge = _api_spread(t, "CX2A")
    prices.update({"CX2ASHORT": 110.0, "CX2AHEDGE": 42.0})
    body = {"trade_ids": [short, hedge], "measure": "MTM_INR", "sl_value": 1000, "client_ref": uuid.uuid4().hex[:16],
            "trailing": {"kind": "TRAIL", "step": 500, "by": 800}}
    bad = client.post(f"{BASE}/preview", headers=t["headers"], json=body).json()
    assert not bad["ok"] and "The stop cannot shift further than the move itself" in bad["reasons"]
    assert client.post(BASE, headers=t["headers"], json=body).status_code == 422
    assert client.post(BASE, headers=t["headers"], json={**body, "trailing": {"kind": "TRAIL", "step": -1, "by": 1}}).status_code == 422
    body["trailing"] = {"kind": "TRAIL", "step": 500, "by": 250}
    good = client.post(f"{BASE}/preview", headers=t["headers"], json=body).json()
    assert good["ok"] and "For every ₹500 the combined profit rises, the stop moves up ₹250." in good["words"]
    made = client.post(BASE, headers=t["headers"], json=body).json()
    assert made["trail_kind"] == "TRAIL" and made["trailing"] == {"step": 500.0, "by": 250.0} and made["stop_now"] == -1000.0
    kept = client.post(f"{BASE}/{made['id']}/edit", headers=t["headers"], json={"version": 1, "sl_value": 900}).json()
    assert kept["trail_kind"] == "TRAIL" and kept["stop_now"] == -900.0                  # absent: kept
    gone = client.post(f"{BASE}/{made['id']}/edit", headers=t["headers"],
                       json={"version": 2, "sl_value": 900, "trailing": None}).json()
    assert gone["trail_kind"] == "NONE" and gone["trailing"] is None                     # null: removed
    lock = client.post(f"{BASE}/{made['id']}/edit", headers=t["headers"],
                       json={"version": 3, "sl_value": None, "trailing": {"kind": "LOCK", "after": 2000, "lock": 500}}).json()
    assert lock["trail_kind"] == "LOCK" and lock["stop_now"] is None and "the trailing stop is reached" in lock["words"]
