"""SX (owner, ATP_ORDER_CX_SX.md SX1-SX8): strategy-level stop-loss / target / trailing attached at entry.

SX8 proofs: the two new trailing kinds (lock and trail; trail to break-even) move the stop only in the trade's favour;
the lock fires once; stop-loss and target are one row (OCO: one firing ends the other); the legs exit shorts first; a
restart mid-exit closes nothing twice; the leg's own waiting exit is cancelled when the strategy exit fires (SX4); no
re-entry (SX7). Through the basket: an entry with exits creates one combined exit (origin SX) on the filled legs - ₹,
% of the net premium (converted at the fill) or the spot level; an entry without exits creates none; a repeated tap
sets one; an exit that cannot be set never undoes the entry.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.db.models import GroupExitRecord, NotificationRecord, PaperRestingOrderRecord, TradeRecord
from app.execution import group_exits as gx
from app.execution import resting_book as book
from app.execution import strategy_exits as sx
from app.execution import trigger_engine
from app.trading import position_monitor as pm
from tests.test_auth_api import _session_factory
from tests.test_cx_group_exits import NOW, _arm, _get, _no_live_exits_left, _org, _pass, _spread, closes  # noqa: F401
from tests.test_fno2_basket import _condor_legs, _org_tenant, _post, _prices, condor  # noqa: F401 - the fixture


def _run(coro):
    return asyncio.run(coro)


def _row(**kw) -> GroupExitRecord:
    base = dict(measure=gx.MTM_INR, sl_value=None, trail_kind=gx.LOCK_TRAIL,
                trail_json=json.dumps({"after": 2000, "lock": 500, "step": 1000, "by": 500}), legs_json="[]", sl_side=None,
                target_side=None, basis_value=None, best_value=None, current_sl=None, locked_at=None, trail_log=None,
                spot_symbol=None, target_value=None)
    base.update(kw)
    return GroupExitRecord(**base)


# --- SX2: the two new kinds, pure ---------------------------------------------------------------------------------------

def test_lock_and_trail_locks_once_then_trails_and_never_moves_back():
    row = _row()
    seen = []
    for value in (1500.0, 2000.0, 2900.0, 3000.0, 2000.0, -100.0, 4600.0, 3000.0):
        gx.apply_trail(row, value, NOW)
        seen.append(gx.stop_now(row))
    assert seen == [None, 500.0, 500.0, 1000.0, 1000.0, 1000.0, 1500.0, 1500.0]
    assert all(b >= a for a, b in zip(seen[1:], seen[2:]))                      # only ever in favour
    assert sum("Gain reached" in e["note"] for e in gx.trail(row)) == 1           # the lock is logged once
    assert gx.decide(row, 1501.0) is None and gx.decide(row, 1500.0) == gx.SL
    assert ("Once the combined profit reaches ₹2,000, the stop moves to keep ₹500; then for every further ₹1,000, it moves "
            "up ₹500.") in gx.describe(row)


def test_trail_to_break_even_moves_the_stop_to_zero_once_and_keeps_it():
    row = _row(sl_value=1000.0, trail_kind=gx.BREAK_EVEN, trail_json=json.dumps({"after": 1500}))
    assert gx.stop_now(row) == -1000.0
    assert not gx.apply_trail(row, 1499.0, NOW) and gx.stop_now(row) == -1000.0
    assert gx.apply_trail(row, 1500.0, NOW) and gx.stop_now(row) == 0.0 and row.locked_at is not None
    assert not gx.apply_trail(row, 9000.0, NOW) and not gx.apply_trail(row, -500.0, NOW)
    assert gx.stop_now(row) == 0.0 and gx.decide(row, 0.0) == gx.SL
    assert "the stop moves to break-even (no loss)" in gx.describe(row)


def test_break_even_on_a_spot_level_follows_the_trade_side():
    row = _row(measure=gx.SPOT, sl_value=24000.0, sl_side=gx.ABOVE, basis_value=23800.0, spot_symbol="NIFTY 50",
               trail_kind=gx.BREAK_EVEN, trail_json=json.dumps({"after": 100}))     # gains as the spot falls
    gx.apply_trail(row, 23750.0, NOW)
    assert gx.stop_now(row) == 24000.0
    gx.apply_trail(row, 23700.0, NOW)
    assert gx.stop_now(row) == 23800.0 and gx.stop_side(row) == gx.ABOVE
    gx.apply_trail(row, 23900.0, NOW)
    assert gx.stop_now(row) == 23800.0 and gx.decide(row, 23800.0) == gx.SL


def test_the_new_kinds_take_only_the_users_numbers_in_plain_words():
    assert gx.trail_errors(gx.BREAK_EVEN, {}, None) == ["Enter the gain after which the stop moves to break-even (more than zero)"]
    assert gx.trail_errors(gx.BREAK_EVEN, {"after": 10}, None) == []
    assert gx.trail_errors(gx.LOCK_TRAIL, {"after": 100, "lock": 20, "step": 50, "by": 50}, None) == []
    bad = gx.trail_errors(gx.LOCK_TRAIL, {"after": 100, "lock": 20, "step": 50, "by": 60}, None)
    assert bad == ["The stop cannot shift further than the move itself"]
    assert "Enter the further move that shifts the stop (more than zero)" in gx.trail_errors(gx.LOCK_TRAIL, {"after": 100, "lock": 20}, None)
    ask = gx.GroupExitAsk(client_ref="x", trade_ids=[1], measure=gx.SPOT, spot_symbol="NIFTY 50", spot_exchange="NSE",
                          trail_kind=gx.BREAK_EVEN, trail={"after": 50})
    assert any("which way is yours" in e for e in gx.ask_errors(ask, []))
    assert not any("Enter a stop-loss" in e for e in gx.ask_errors(
        gx.GroupExitAsk(client_ref="x", trade_ids=[1], measure=gx.MTM_INR, trail_kind=gx.LOCK_TRAIL,
                        trail={"after": 100, "lock": 20, "step": 50, "by": 50}), []))


def test_premium_percent_becomes_rupees_on_the_filled_legs():
    legs = [TradeRecord(direction="SHORT", leg_role="SHORT", entry_price=80.0, quantity=75),
            TradeRecord(direction="LONG", leg_role="LONG", entry_price=40.0, quantity=75)]
    assert sx.premium_inr(legs) == 3000.0
    ask = sx.StrategyExitAsk(measure=sx.PREMIUM_PCT, sl_value=100.0, target_value=50.0, trail_kind=gx.BREAK_EVEN,
                             trail={"after": 25.0})
    assert sx.in_rupees(ask, 3000.0) == (3000.0, 1500.0, {"after": 750.0})


# --- SX3 / SX4 / SX6 / SX7 through the engine ----------------------------------------------------------------------------

def _count_trades(tenant_id: int) -> int:
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(TradeRecord).where(TradeRecord.tenant_id == tenant_id)))
    return _run(go())


def test_a_strategy_lock_and_trail_fires_once_shorts_first_and_never_re_enters(closes):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "SXA")
    row = _arm(t, [short, hedge], origin="SX", scope="GROUP", basket_ref="sx-basket-a", measure=gx.MTM_INR, sl_value=1000.0,
               trail_kind=gx.LOCK_TRAIL, trail={"after": 2000, "lock": 500, "step": 1000, "by": 500})
    assert row.origin == "SX" and row.basket_ref == "sx-basket-a"
    before = _count_trades(t["tenant_id"])
    _pass(t, {"SXASHORT": 50.0, "SXAHEDGE": 40.0})                                  # +2500: locked at 500
    first = _get(GroupExitRecord, row.id)
    assert first.current_sl == 500.0 and first.locked_at is not None
    _pass(t, {"SXASHORT": 30.0, "SXAHEDGE": 40.0}, now=NOW + timedelta(seconds=5))  # +3500: one step, 1000
    again = _get(GroupExitRecord, row.id)
    assert again.current_sl == 1000.0 and again.locked_at == first.locked_at
    _pass(t, {"SXASHORT": 80.0, "SXAHEDGE": 40.0}, now=NOW + timedelta(seconds=10))  # +1000: the trailed stop
    done = _get(GroupExitRecord, row.id)
    assert done.status == gx.DONE and done.fired_kind == gx.SL and closes == [short, hedge]     # sold leg first
    for i in range(3):
        _pass(t, {"SXASHORT": 120.0, "SXAHEDGE": 40.0}, now=NOW + timedelta(seconds=20 + i))
    assert closes == [short, hedge] and _count_trades(t["tenant_id"]) == before                  # once; no re-entry


def test_stop_loss_and_target_are_one_row_so_the_target_ends_the_stop(closes):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "SXO")
    row = _arm(t, [short, hedge], origin="SX", scope="GROUP", measure=gx.MTM_INR, sl_value=1000.0, target_value=1500.0)
    _pass(t, {"SXOSHORT": 68.0, "SXOHEDGE": 40.0})                                  # +1600: the target
    done = _get(GroupExitRecord, row.id)
    assert done.status == gx.DONE and done.fired_kind == gx.TARGET and closes == [short, hedge]
    _pass(t, {"SXOSHORT": 150.0, "SXOHEDGE": 40.0}, now=NOW + timedelta(seconds=5))  # the stop level: nothing left armed
    assert closes == [short, hedge] and _get(GroupExitRecord, row.id).fired_kind == gx.TARGET


def test_the_legs_own_waiting_stop_is_cancelled_when_the_strategy_exit_fires_first(closes):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "SXL")

    async def leg_stop():
        async with _session_factory() as session:
            rest = PaperRestingOrderRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], client_ref=uuid.uuid4().hex, mode="PAPER",
                                           purpose=book.EXIT, kind="STOP", trade_id=short, symbol="SXLSHORT", exchange="NFO",
                                           side="BUY", quantity=50, order_type="SL-M", trigger_symbol="SXLSHORT",
                                           trigger_exchange="NFO", trigger_level=200.0, trigger_side="ABOVE", status=book.RESTING)
            session.add(rest)
            await session.commit()
            return rest.id
    stop_id = _run(leg_stop())
    _arm(t, [short, hedge], origin="SX", scope="GROUP", measure=gx.MTM_INR, sl_value=1000.0)
    _pass(t, {"SXLSHORT": 130.0, "SXLHEDGE": 45.0})
    assert closes == [short, hedge] and _get(PaperRestingOrderRecord, stop_id).status == book.CANCELLED


def test_a_restart_mid_strategy_exit_closes_nothing_twice(closes):  # noqa: F811 - the fixture
    t = _org()
    short, hedge = _spread(t, "SXR")
    row = _arm(t, [short, hedge], origin="SX", scope="GROUP", measure=gx.MTM_INR, sl_value=1000.0, trail_kind=gx.BREAK_EVEN,
               trail={"after": 1500})
    _pass(t, {"SXRSHORT": 60.0, "SXRHEDGE": 40.0})                                  # +2000: break-even
    assert _get(GroupExitRecord, row.id).current_sl == 0.0

    async def crash_after_the_short():
        async with _session_factory() as session:
            armed = await session.get(GroupExitRecord, row.id)
            armed.last_value = -50.0
            assert await trigger_engine._claim_group(session, armed, gx.SL, NOW)
            trade = await session.get(TradeRecord, short)
            assert (await pm.close_position(session, trade, 101.0, "Combined stop-loss", user_id=t["user_id"])).closed
    _run(crash_after_the_short())
    report = _pass(t, {"SXRSHORT": 102.0, "SXRHEDGE": 40.0}, now=NOW + timedelta(seconds=30))
    assert report.filled == 1 and closes == [short, hedge]
    assert _get(GroupExitRecord, row.id).status == gx.DONE and _get(TradeRecord, short).exit_price == 101.0


# --- SX1 / SX3 through the basket ----------------------------------------------------------------------------------------

def _exits_of(tenant_id: int) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(GroupExitRecord).where(GroupExitRecord.tenant_id == tenant_id)))
    return _run(go())


def _trades(tenant_id: int) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id)))
    return _run(go())


def test_an_entry_without_exits_creates_no_strategy_exit(condor):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _org_tenant("sx-none")
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex})
    assert out["placed"] == 4 and "exits" not in out
    assert _exits_of(t["tenant_id"]) == []


def test_an_entry_with_exits_creates_one_strategy_exit_on_the_filled_legs(condor):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _org_tenant("sx-inr")
    ref = uuid.uuid4().hex
    body = {"legs": _condor_legs(expiry, legs), "basket_ref": ref,
            "exits": {"measure": "MTM_INR", "sl_value": 5000, "target_value": 3000,
                      "trailing": {"kind": "LOCK_TRAIL", "after": 2000, "lock": 500, "step": 1000, "by": 500}}}
    out = _post(t, "execute", body)
    assert out["placed"] == 4 and out["exits"]["status"] == "ARMED" and out["exits"]["legs"] == 4
    assert "combined loss reaches ₹5,000 or combined profit reaches ₹3,000" in out["exits"]["words"]
    assert _post(t, "execute", body)["exits"]["id"] == out["exits"]["id"]          # a repeated tap sets one
    [row] = _exits_of(t["tenant_id"])
    assert (row.origin, row.scope, row.basket_ref, row.measure, row.status) == ("SX", "GROUP", ref, gx.MTM_INR, gx.ARMED)
    assert sorted(gx.trade_ids(row)) == sorted(tr.id for tr in _trades(t["tenant_id"]))
    assert row.trail_kind == gx.LOCK_TRAIL and float(row.sl_value) == 5000.0


def test_percent_of_the_net_premium_is_set_in_rupees_at_the_fill(condor):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _org_tenant("sx-pct")
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex,
                               "exits": {"measure": "PREMIUM_PCT", "sl_value": 100, "target_value": 50}})
    assert out["exits"]["status"] == "ARMED" and "% of the net premium" in out["exits"]["words"]
    basis = sx.premium_inr(_trades(t["tenant_id"]))
    [row] = _exits_of(t["tenant_id"])
    assert basis > 0 and row.measure == gx.MTM_INR
    assert float(row.sl_value) == round(basis, 2) and float(row.target_value) == round(basis / 2, 2)


def test_a_spot_level_exit_reads_the_underlying_and_its_sides(condor, monkeypatch):  # noqa: F811 - the fixture
    expiry, base, legs = condor
    _prices(monkeypatch, {**{sym: p for _, (_, _, sym, p) in legs.items()}, "NIFTY 50": base})
    t = _org_tenant("sx-spot")
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex,
                               "exits": {"measure": "SPOT", "sl_value": base + 300, "target_value": base - 300}})
    assert out["exits"]["status"] == "ARMED"
    [row] = _exits_of(t["tenant_id"])
    assert (row.measure, row.spot_symbol, row.sl_side, row.target_side) == (gx.SPOT, "NIFTY 50", gx.ABOVE, gx.BELOW)
    assert float(row.basis_value) == base


def test_an_exit_that_cannot_be_set_never_undoes_the_entry_and_is_told(condor):  # noqa: F811 - the fixture
    expiry, base, legs = condor                                                    # no spot price in this lookup
    t = _org_tenant("sx-noprice")
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": uuid.uuid4().hex,
                               "exits": {"measure": "SPOT", "sl_value": base + 300}})
    assert out["placed"] == 4 and out["exits"]["status"] == "NOT_SET" and "No live price" in out["exits"]["reason"]
    assert _exits_of(t["tenant_id"]) == [] and len(_trades(t["tenant_id"])) == 4

    async def told():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord.title).where(NotificationRecord.tenant_id == t["tenant_id"])))
    assert "Strategy exit not set" in _run(told())
