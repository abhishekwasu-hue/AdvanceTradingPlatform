"""CX PR 1 (owner, 24 Oct; review docs/design/CX_SX_REVIEW.md): combined exits in the one trigger engine.

CX7 proofs: a combined exit fires once; the legs close sold legs first, then the hedges; it exits with the account kill
switch ON (and every entry gate refusing); a restart mid-exit closes nothing twice. Plus the review's F1 fix (PAPER holds
the hedges of a short that did not close, as LIVE does), the measures, the plain-word refusals, cancel, no AI import,
and migration b3f5d7e9a1c2 round-trips.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import GroupExitRecord, NotificationRecord, TradeRecord, User
from app.execution import group_exits as gx
from app.execution import trigger_engine
from app.trading import position_monitor as pm
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import OPEN_NOW, STRATEGY, _tenant

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "b3f5d7e9a1c2_cx_group_exits.py"
NOW = OPEN_NOW


def _run(coro):
    return asyncio.run(coro)


def _org():
    return _tenant(f"cx-{uuid.uuid4().hex[:8]}@example.com", with_credentials=False)


def _leg(t, symbol, direction, entry, *, quantity=50, group=None, role=None, underlying="NIFTY") -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id=STRATEGY, symbol=symbol,
                                exchange="NFO", direction=direction, entry_price=entry, quantity=quantity, leg_role=role,
                                stop_loss=entry * (3 if direction == "SHORT" else 0.1),
                                target1=entry * (0.1 if direction == "SHORT" else 3), target2=entry * (0.05 if direction == "SHORT" else 4),
                                leg_group_id=group, instrument_kind="OPTION", underlying_symbol=underlying,
                                entry_time=NOW - timedelta(minutes=30))
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _spread(t, tag):
    group = f"g-{uuid.uuid4().hex[:8]}"
    hedge = _leg(t, f"{tag}HEDGE", "LONG", 40.0, group=group, role="LONG")     # first in DB order on purpose
    short = _leg(t, f"{tag}SHORT", "SHORT", 100.0, group=group, role="SHORT")
    return short, hedge


def _user(t):
    async def go():
        async with _session_factory() as session:
            return await session.get(User, t["user_id"])
    return _run(go())


def _arm(t, trade_ids, now_value=None, **kw) -> GroupExitRecord:
    ask = gx.GroupExitAsk(client_ref=uuid.uuid4().hex, trade_ids=list(trade_ids), **kw)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await gx.create(session, user, ask, now_value=now_value, now=NOW)
    return _run(go())


def _prices(table):
    async def fn(symbol, exchange):
        if symbol not in table:
            raise LookupError("no price")
        return table[symbol]
    return fn


def _pass(t, table, now=NOW, **kw):
    async def go():
        async with _session_factory() as session:
            return await trigger_engine.run_exits(session, t["tenant_id"], _prices(table), now=now, **kw)
    return _run(go())


def _get(model, id_):
    async def go():
        async with _session_factory() as session:
            return await session.get(model, id_)
    return _run(go())


@pytest.fixture
def closes(monkeypatch):
    """Every close_position call, in order (trade id), through the real close."""
    calls = []
    real = pm.close_position

    async def spy(session, trade, *a, **k):
        calls.append(trade.id)
        return await real(session, trade, *a, **k)
    monkeypatch.setattr(pm, "close_position", spy)
    return calls


# --- the measures ---------------------------------------------------------------------------------------------------------

def test_mtm_and_net_premium_are_read_from_every_leg_or_not_at_all():
    legs = [{"trade_id": 1, "role": "SHORT", "quantity": 50, "entry_price": 100.0},
            {"trade_id": 2, "role": "LONG", "quantity": 50, "entry_price": 40.0}]
    assert gx.mtm(legs, {1: 130.0, 2: 45.0}) == -(30 * 50) + 5 * 50           # -1250
    assert gx.net_premium(legs, {1: 130.0, 2: 45.0}) == 85.0                   # short price - long price
    assert gx.mtm(legs, {1: 130.0}) is None and gx.net_premium(legs, {2: 45.0}) is None
    assert gx.side_for(120.0, 100.0) == gx.ABOVE and gx.side_for(80.0, 100.0) == gx.BELOW


def test_the_stop_is_judged_before_the_target_and_mtm_uses_the_rupee_amounts():
    row = GroupExitRecord(measure=gx.MTM_INR, sl_value=1000.0, target_value=500.0)
    assert gx.decide(row, -1000.0) == gx.SL and gx.decide(row, 500.0) == gx.TARGET and gx.decide(row, -999.0) is None
    level = GroupExitRecord(measure=gx.SPOT, sl_value=24000.0, sl_side=gx.ABOVE, target_value=23500.0, target_side=gx.BELOW)
    assert gx.decide(level, 24000.0) == gx.SL and gx.decide(level, 23500.0) == gx.TARGET and gx.decide(level, 23800.0) is None


def test_a_sold_position_without_a_structure_role_is_a_short():
    assert gx.is_short(SimpleNamespace(leg_role=None, direction="SHORT"))
    assert not gx.is_short(SimpleNamespace(leg_role=None, direction="LONG"))
    assert gx.is_short(SimpleNamespace(leg_role="SHORT", direction="LONG"))


# --- CX7: fires once, sold legs first -------------------------------------------------------------------------------------

def test_a_combined_stop_fires_once_and_closes_the_short_before_its_hedge(closes):
    t = _org()
    short, hedge = _spread(t, "CXA")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)
    assert row.status == gx.ARMED and "combined loss reaches ₹1,000" in gx.describe(row) and "sold legs first" in gx.describe(row)
    assert _pass(t, {"CXASHORT": 110.0, "CXAHEDGE": 42.0}).filled == 0          # MTM -400: not yet
    assert closes == [] and _get(GroupExitRecord, row.id).status == gx.ARMED
    report = _pass(t, {"CXASHORT": 130.0, "CXAHEDGE": 45.0})                    # MTM -1250
    assert report.filled == 2 and report.errors == []
    assert closes == [short, hedge]                                              # the short first, then the hedge
    done = _get(GroupExitRecord, row.id)
    assert done.status == gx.DONE and done.fired_kind == gx.SL and done.fired_value == -1250.0
    assert _get(TradeRecord, short).exit_reason == "Combined stop-loss" and _get(TradeRecord, hedge).exit_time is not None
    for _ in range(2):                                                           # later passes: nothing again
        _pass(t, {"CXASHORT": 140.0, "CXAHEDGE": 46.0})
    assert closes == [short, hedge]


def test_two_passes_on_one_reading_fire_the_combined_exit_once(closes):
    t = _org()
    short, hedge = _spread(t, "CXB")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, target_value=500.0)

    async def both():
        async with _session_factory() as a, _session_factory() as b:
            table = {"CXBSHORT": 80.0, "CXBHEDGE": 38.0}                         # MTM +900
            return await asyncio.gather(trigger_engine.run_exits(a, t["tenant_id"], _prices(table), now=NOW),
                                        trigger_engine.run_exits(b, t["tenant_id"], _prices(table), now=NOW))
    _run(both())
    assert sorted(closes) == sorted([short, hedge]) and len(closes) == 2
    assert _get(GroupExitRecord, row.id).status == gx.DONE and _get(GroupExitRecord, row.id).fired_kind == gx.TARGET


def test_a_missing_price_on_any_leg_means_no_decision_this_pass(closes):
    t = _org()
    short, hedge = _spread(t, "CXC")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=100.0)
    _pass(t, {"CXCSHORT": 500.0})                                                # the hedge has no price
    assert closes == [] and _get(GroupExitRecord, row.id).status == gx.ARMED


def test_a_leg_closed_by_hand_counts_at_its_booked_price_and_the_rest_still_exit(closes):
    t = _org()
    short, hedge = _spread(t, "CXP")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)

    async def close_hedge_by_hand():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, hedge)
            assert (await real_close(session, trade, 30.0, "Manual", user_id=t["user_id"])).closed   # booked -500
    real_close = pm.close_position
    _run(close_hedge_by_hand())
    closes.clear()
    _pass(t, {"CXPSHORT": 108.0, "CXPHEDGE": 60.0})        # -400 open + -500 booked = -900; the hedge's live price ignored
    assert closes == [] and _get(GroupExitRecord, row.id).last_value == -900.0
    _pass(t, {"CXPSHORT": 111.0, "CXPHEDGE": 60.0})        # -550 - 500 = -1050
    assert closes == [short] and _get(GroupExitRecord, row.id).status == gx.DONE


def test_an_armed_exit_whose_legs_were_all_closed_another_way_ends(closes):
    t = _org()
    short, hedge = _spread(t, "CXQ")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)

    async def close_both():
        async with _session_factory() as session:
            for tid, price in ((short, 90.0), (hedge, 41.0)):
                assert (await pm.close_position(session, await session.get(TradeRecord, tid), price, "Manual",
                                                user_id=t["user_id"])).closed
    _run(close_both())
    closes.clear()
    _pass(t, {"CXQSHORT": 200.0, "CXQHEDGE": 1.0})
    assert closes == [] and _get(GroupExitRecord, row.id).status == gx.DONE


# --- CX5: never blocked -----------------------------------------------------------------------------------------------------

def test_the_combined_exit_closes_with_the_kill_switch_on_and_every_entry_gate_refusing(closes, monkeypatch):
    t = _org()
    short, hedge = _spread(t, "CXD")
    _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)
    asked = []

    async def refuse(*a, **k):
        asked.append("asked")
        return ["Daily loss limit reached"]
    from app.execution import approval, signal_execution
    monkeypatch.setattr(signal_execution, "entry_refusals", refuse)
    monkeypatch.setattr(signal_execution, "active_kill_switch_reasons", refuse)
    monkeypatch.setattr(approval, "gate_refusal", refuse)
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "cx proof"}).status_code == 200
    try:
        report = _pass(t, {"CXDSHORT": 130.0, "CXDHEDGE": 45.0})
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    assert report.filled == 2 and asked == [] and closes == [short, hedge]


# --- CX5: restart mid-exit ----------------------------------------------------------------------------------------------------

def test_a_restart_after_the_short_closed_closes_only_the_hedge_once(closes):
    t = _org()
    short, hedge = _spread(t, "CXE")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)

    async def crash_after_the_short():                                           # claimed, the short closed, then the crash
        async with _session_factory() as session:
            armed = await session.get(GroupExitRecord, row.id)
            armed.last_value = -1250.0
            assert await trigger_engine._claim_group(session, armed, gx.SL, NOW)
            trade = await session.get(TradeRecord, short)
            assert (await pm.close_position(session, trade, 130.0, "Combined stop-loss", user_id=t["user_id"])).closed
    _run(crash_after_the_short())
    assert _get(GroupExitRecord, row.id).status == gx.FIRING
    report = _pass(t, {"CXESHORT": 135.0, "CXEHEDGE": 45.0}, now=NOW + timedelta(seconds=30))   # the worker again
    assert report.filled == 1 and closes == [short, hedge]                        # the short is never closed twice
    assert _get(GroupExitRecord, row.id).status == gx.DONE
    assert _get(TradeRecord, short).exit_price == 130.0


# --- review F1: PAPER holds the hedges like LIVE ------------------------------------------------------------------------------

def test_paper_keeps_the_hedge_open_while_its_short_has_no_price_and_retries_the_short_first(closes):
    t = _org()
    short, hedge = _spread(t, "CXF")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)

    async def fire_without_the_short_price():
        async with _session_factory() as session:
            armed = await session.get(GroupExitRecord, row.id)
            armed.last_value = -1250.0
            assert await trigger_engine._claim_group(session, armed, gx.SL, NOW)
    _run(fire_without_the_short_price())
    _pass(t, {"CXFHEDGE": 45.0})                                                  # the short has no price this pass
    assert closes == [] and _get(TradeRecord, hedge).exit_time is None           # the hedge is held: never a naked short
    assert _get(GroupExitRecord, row.id).status == gx.FIRING
    _pass(t, {"CXFSHORT": 130.0, "CXFHEDGE": 45.0}, now=NOW + timedelta(seconds=10))
    assert closes == [short, hedge] and _get(GroupExitRecord, row.id).status == gx.DONE


def test_a_short_still_open_after_several_tries_raises_one_critical_notice_and_keeps_trying(closes):
    t = _org()
    short, hedge = _spread(t, "CXG")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)

    async def fire():
        async with _session_factory() as session:
            armed = await session.get(GroupExitRecord, row.id)
            armed.last_value = -1250.0
            assert await trigger_engine._claim_group(session, armed, gx.SL, NOW)
    _run(fire())
    for i in range(gx.STUCK_ALERT_PASSES + 2):
        _pass(t, {"CXGHEDGE": 45.0}, now=NOW + timedelta(seconds=i))

    async def notices():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.title == "Combined exit: legs still open")))
    found = _run(notices())
    assert len(found) == 1 and found[0].severity == "CRITICAL" and "CXGSHORT" in found[0].message
    assert _get(GroupExitRecord, row.id).status == gx.FIRING                     # an exit is never given up


# --- the spot level ------------------------------------------------------------------------------------------------------------

def test_a_spot_level_exit_reads_its_side_from_the_spot_when_it_was_set(closes):
    t = _org()
    short, hedge = _spread(t, "CXH")
    row = _arm(t, [short, hedge], now_value=23800.0, measure=gx.SPOT, sl_value=24000.0, target_value=23500.0,
               spot_symbol="NIFTY 50", spot_exchange="NSE")
    assert row.sl_side == gx.ABOVE and row.target_side == gx.BELOW
    assert "NIFTY 50 spot rises to 24,000.00 (stop-loss)" in gx.describe(row)
    _pass(t, {"NIFTY 50": 23900.0, "CXHSHORT": 110.0, "CXHHEDGE": 42.0})
    assert closes == []
    _pass(t, {"NIFTY 50": 23490.0, "CXHSHORT": 70.0, "CXHHEDGE": 30.0})
    assert closes == [short, hedge] and _get(GroupExitRecord, row.id).fired_kind == gx.TARGET


def test_a_spot_close_check_waits_for_a_closed_candle(closes):
    t = _org()
    short, hedge = _spread(t, "CXI")
    row = _arm(t, [short, hedge], now_value=23800.0, measure=gx.SPOT, sl_value=24000.0, spot_symbol="NIFTY 50",
               spot_exchange="NSE", check_mode=gx.CLOSE, timeframe_minutes=5)
    start = NOW.replace(second=0, microsecond=0)
    start = start - timedelta(minutes=start.minute % 5)

    def bars(closes_by_minute):
        return [SimpleNamespace(timestamp=start + timedelta(minutes=i), open=c, high=c + 5, low=c - 5, close=c)
                for i, c in enumerate(closes_by_minute)]

    async def candles_of(values):
        async def fn(symbol, exchange):
            return bars(values)
        return fn
    table = {"NIFTY 50": 24100.0, "CXISHORT": 130.0, "CXIHEDGE": 45.0}
    still_forming = _run(candles_of([23900, 23950, 24050]))                      # a 5-minute candle not closed yet
    _pass(t, table, now=start + timedelta(minutes=3), candles=still_forming)
    assert closes == [] and _get(GroupExitRecord, row.id).status == gx.ARMED
    closed = _run(candles_of([23900, 23950, 24000, 24020, 24050]))
    _pass(t, table, now=start + timedelta(minutes=5), candles=closed)
    assert closes == [short, hedge] and _get(GroupExitRecord, row.id).fired_kind == gx.SL


# --- setting it: plain refusals, idempotent, cancel -----------------------------------------------------------------------------

def test_refusals_are_plain_words_and_nothing_is_stored():
    t = _org()
    short, hedge = _spread(t, "CXJ")
    with pytest.raises(gx.GroupExitError, match="Enter a stop-loss, a target, or both"):
        _arm(t, [short, hedge], measure=gx.MTM_INR)
    with pytest.raises(gx.GroupExitError, match="Choose the underlying"):
        _arm(t, [short, hedge], now_value=23800.0, measure=gx.SPOT, sl_value=24000.0)
    with pytest.raises(gx.GroupExitError, match="closed-candle check works on the spot level"):
        _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=500.0, check_mode=gx.CLOSE, timeframe_minutes=5)
    with pytest.raises(gx.GroupExitError, match="would fire at once"):
        _arm(t, [short, hedge], now_value=23800.0, measure=gx.SPOT, sl_value=23800.0, spot_symbol="NIFTY 50", spot_exchange="NSE")
    other = _org()
    stranger = _leg(other, "CXJOTHER", "SHORT", 100.0)
    with pytest.raises(gx.GroupExitError, match="not your open positions"):
        _arm(t, [short, stranger], measure=gx.MTM_INR, sl_value=500.0)

    async def count():
        async with _session_factory() as session:
            return len(list(await session.scalars(select(GroupExitRecord).where(GroupExitRecord.tenant_id == t["tenant_id"]))))
    assert _run(count()) == 0


def test_the_same_tap_never_sets_two_and_the_user_can_cancel_an_armed_exit():
    t = _org()
    short, hedge = _spread(t, "CXK")
    ask = gx.GroupExitAsk(client_ref="cxk-once", trade_ids=[short, hedge], measure=gx.MTM_INR, sl_value=800.0)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            first = await gx.create(session, user, ask, now=NOW)
            again = await gx.create(session, user, ask, now=NOW)
            assert first.id == again.id
            cancelled = await gx.cancel(session, user, first.id, now=NOW)
            assert cancelled.status == gx.CANCELLED and gx.trail(cancelled)[-1]["note"] == "Removed by you"
            other = await session.get(User, t["user_id"])
            assert [r.id for r in await gx.list_for_user(session, other)] == []
    _run(go())


def test_an_exit_already_closing_the_legs_cannot_be_cancelled():
    t = _org()
    short, hedge = _spread(t, "CXL")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)

    async def go():
        async with _session_factory() as session:
            armed = await session.get(GroupExitRecord, row.id)
            armed.last_value = -1250.0
            assert await trigger_engine._claim_group(session, armed, gx.SL, NOW)
            user = await session.get(User, t["user_id"])
            with pytest.raises(gx.GroupExitError, match="Already exiting"):
                await gx.cancel(session, user, row.id, now=NOW)
    _run(go())


def test_the_legs_own_waiting_exits_are_cancelled_when_the_combined_exit_fires(closes):
    from app.db.models import PaperRestingOrderRecord
    from app.execution import resting_book as book
    t = _org()
    short, hedge = _spread(t, "CXM")

    async def leg_stop():
        async with _session_factory() as session:
            row = PaperRestingOrderRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], client_ref=uuid.uuid4().hex, mode="PAPER",
                                          purpose=book.EXIT, kind="STOP", trade_id=short, symbol="CXMSHORT", exchange="NFO",
                                          side="BUY", quantity=50, order_type="SL-M", trigger_symbol="CXMSHORT",
                                          trigger_exchange="NFO", trigger_level=200.0, trigger_side="ABOVE", status=book.RESTING)
            session.add(row)
            await session.commit()
            return row.id
    stop_id = _run(leg_stop())
    _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)
    _pass(t, {"CXMSHORT": 130.0, "CXMHEDGE": 45.0})
    assert closes == [short, hedge] and _get(PaperRestingOrderRecord, stop_id).status == book.CANCELLED


def test_one_summary_notification_when_every_leg_is_closed(closes):
    t = _org()
    short, hedge = _spread(t, "CXN")
    row = _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)
    _pass(t, {"CXNSHORT": 130.0, "CXNHEDGE": 45.0})

    async def notes():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.title.like("Combined stop-loss%"))))
    found = _run(notes())
    assert len(found) == 1 and found[0].title == "Combined stop-loss: 2 legs closed"
    assert json.loads(found[0].metadata_json)["group_exit"] == row.id


# --- the tick pass ------------------------------------------------------------------------------------------------------------

def test_the_tick_pass_watches_paper_combined_exits(closes, monkeypatch):
    from app.market_data import hub
    t = _org()
    short, hedge = _spread(t, "CXO")
    _arm(t, [short, hedge], measure=gx.MTM_INR, sl_value=1000.0)
    at = datetime.now(timezone.utc)
    ticks = {"NFO:CXOSHORT": [130.0, at.isoformat()], "NFO:CXOHEDGE": [45.0, at.isoformat()]}
    monkeypatch.setattr(hub, "live_ticks", lambda ns: ticks if ns == hub.org_namespace(t["tenant_id"]) else {})

    async def go():
        async with _session_factory() as session:
            return await trigger_engine.tick_pass(session, now=at)
    _run(go())
    assert closes == [short, hedge]


# --- ADR-0006 and the migration ---------------------------------------------------------------------------------------------

def test_no_ai_module_reaches_the_combined_exits():
    banned = ("group_exits", "run_group_exits")
    offenders = []
    for path in sorted((BACKEND / "app" / "ai").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if any(n.endswith(b) for n in names for b in banned):
                offenders.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    assert offenders == []
    assert "app.ai" not in inspect.getsource(gx)


def test_migration_b3f5d7e9a1c2_is_additive_and_round_trips(tmp_path):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location("cx_migration_run", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "d4a6c8e0b2f1" and module.ROLLBACK_SAFE is True
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    existing = ("tenants", "users", "trades")
    with eng.begin() as conn:
        for name in existing:
            conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, note VARCHAR(10))"))
            conn.execute(sa.text(f"INSERT INTO {name} (id, note) VALUES (1, 'kept')"))
        ctx = MigrationContext.configure(conn)
        for _ in range(2):
            with Operations.context(ctx):
                module.upgrade()
            inspector = sa.inspect(conn)
            assert "group_exits" in inspector.get_table_names()
            assert {i["name"] for i in inspector.get_indexes("group_exits")} >= {"ix_group_exits_tenant_status", "ix_group_exits_user_id"}
            for name in existing:
                assert {c["name"] for c in inspector.get_columns(name)} == {"id", "note"}
            with Operations.context(ctx):
                module.downgrade()
            assert "group_exits" not in sa.inspect(conn).get_table_names()
