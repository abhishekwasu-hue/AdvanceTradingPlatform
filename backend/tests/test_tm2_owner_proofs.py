"""TM-2 (#496, order path) - the owner's six points, each proven here (DECISIONS 24 Oct, "TM-2 (#496, order path)"):

1. LIVE stays OFF: a resting LIVE entry is stored but never reaches a broker or the order pipeline (live_trading OFF,
   phase0_paper_only ON), on the cycle pass and on the tick pass.
2. Exits are never blocked: an armed exit fires on its price with the account kill switch on, approval-only on, the
   PAPER lock on and every entry check refusing - none of them is even asked - and the worker runs the engine's exits
   before every entry gate of the same cycle.
3. The account kill switch: a resting ENTRY reached while it is on is not placed - it ends REJECTED with the reason on
   its trail (and a notification); an exit in the same pass still fills.
4. AI never places, blocks or changes orders: no app/ai module imports or calls the resting book or the trigger engine.
5. Migration 7e2a4c6b8d10 is additive and round-trips (upgrade -> downgrade -> upgrade).
6. Restart: resting orders are rows (not memory); two passes over the same tick and a claim race fire once; an order a
   stopped worker left half-way (FIRING) is looked up at its broker by its client id on worker start (TM2-4), exits
   first: found -> its state (no second order); not found -> sent once with the same id; while running, after the ack
   timeout. PAPER and a LIVE broker session take the same path."""
import ast
import importlib.util
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import OrderRecord, PaperRestingOrderRecord, TradeRecord, User
from app.execution import resting_book, trigger_engine
from app.market_data import hub
from app.platform import controls, phase0_lock
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _contract, _expiry, _org_tenant, _price
from tests.test_tm2_trigger_engine import _arm, _entries, _exits, _open_trade, _ref, _rest, _row, _run, _trade
from tests.test_z5_p1d_risk_by_default import _flag

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "7e2a4c6b8d10_tm2_paper_resting_orders.py"


@pytest.fixture
def setup(monkeypatch):
    """The same organisation, contract and price as tests/test_tm2_trigger_engine.py."""
    import random
    t = _org_tenant("tm2-owner")
    expiry = _expiry()
    strike = float(random.randrange(30000, 40000, 50))
    symbol = _contract(expiry, strike)
    _price(monkeypatch, 100.0)
    return t, expiry, strike, symbol


def _orders(tenant_id) -> int:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == tenant_id))
    return _run(go())


def _set_flags(**values):
    """Stores platform flags {name: on}; returns the previous flags to restore."""
    async def go():
        async with _session_factory() as session:
            current = dict((await controls._get(session, controls.KEY_FEATURE_FLAGS)).get("flags") or {})
            flags = dict(current)
            for name, on in values.items():
                flags[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
            return current
    return _run(go())


def _restore_flags(previous):
    async def go():
        async with _session_factory() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": previous}, None)
            await session.commit()
    _run(go())
    phase0_lock.reset_cache()


# --- 1. LIVE stays OFF ---------------------------------------------------------------------------------------------------

def test_point1_a_resting_live_entry_is_stored_but_never_reaches_a_broker(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    previous = _set_flags(live_trading=False, phase0_paper_only=True)
    try:
        async def store():
            async with _session_factory() as session:
                user = await session.get(User, t["user_id"]) if "user_id" in t else await session.scalar(
                    select(User).where(User.tenant_id == t["tenant_id"]))
                row = await resting_book.rest_entry(
                    session, user, client_ref=f"fno:{_ref()}", symbol=symbol, exchange="NFO", side="BUY", quantity=75.0,
                    order_type="LIMIT", price=95.0, trigger_price=None, ask={}, origin="manual", mode="LIVE")
                await session.commit()
                return row.id
        row_id = _run(store())
        assert _row(row_id).status == "RESTING" and _row(row_id).mode == "LIVE"          # stored (survives, see point 6)
        sent = []

        async def no_place(*a, **k):
            sent.append("fno_ticket.place")
            raise AssertionError("a LIVE resting entry reached the order pipeline")

        async def no_broker(*a, **k):
            sent.append("place_order")
            raise AssertionError("a LIVE resting entry reached a broker")
        from app.execution.paper_adapter import PaperExecutionAdapter
        from app.paper import fno_ticket
        monkeypatch.setattr(fno_ticket, "place", no_place)
        monkeypatch.setattr(PaperExecutionAdapter, "place_order", no_broker)
        before = _orders(t["tenant_id"])
        now = datetime.now(timezone.utc)
        monkeypatch.setattr(hub, "live_ticks", lambda ns: {f"NFO:{symbol}": [94.0, now.isoformat()]}
                            if ns == hub.org_namespace(t["tenant_id"]) else {})

        async def tick():
            async with _session_factory() as session:
                return await trigger_engine.tick_pass(session, now)
        _run(tick())                                                    # the tick pass ...
        _entries(t["tenant_id"], {symbol: 94.0})                         # ... and the cycle pass, price reached
        row = _row(row_id)
        assert sent == [] and _orders(t["tenant_id"]) == before
        assert row.status == "REJECTED" and row.order_id is None and "not switched on" in row.status_note
        assert [s["status"] for s in resting_book.trail(row)] == ["RESTING", "REJECTED"]
    finally:
        _restore_flags(previous)


def test_point1_the_engine_and_the_book_cannot_reach_a_live_broker_themselves():
    """No broker factory / registry / session in either module: the only broker an exit uses is the one the worker
    hands in for a LIVE position that already exists (and the PAPER adapter for a PAPER fill)."""
    for module in (resting_book, trigger_engine):
        tree = ast.parse(inspect.getsource(module))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not {m for m in imported if m.startswith("app.brokers") and m not in ("app.brokers.base", "app.brokers.models")}, imported
    assert "place_order" not in inspect.getsource(resting_book)
    source = inspect.getsource(trigger_engine._evaluate_entry)
    assert "broker" not in source.replace("reaches a broker", "")      # an entry is never given a broker
    assert "adapter.place_order" in inspect.getsource(trigger_engine._paper_fill)          # the PAPER adapter only


# --- 2. exits are never blocked ------------------------------------------------------------------------------------------

def test_point2_an_armed_exit_fires_with_every_gate_refusing_and_none_is_asked(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    asked = []

    async def refuse_list(*a, **k):
        asked.append("entry check")
        return ["Daily loss limit reached"]

    async def refuse_gate(*a, **k):
        asked.append("approval-only")
        return "Approval-only mode"
    from app.execution import approval, signal_execution
    from app.risk_engine.risk_manager import RiskManager

    def refuse_risk(*a, **k):
        asked.append("risk")
        raise AssertionError("risk asked on an exit")
    monkeypatch.setattr(signal_execution, "entry_refusals", refuse_list)
    monkeypatch.setattr(signal_execution, "active_kill_switch_reasons", refuse_list)
    monkeypatch.setattr(approval, "gate_refusal", refuse_gate)
    monkeypatch.setattr(RiskManager, "validate_and_size", refuse_risk)
    _flag(approval.FLAG, t["tenant_id"])
    previous = _set_flags(phase0_paper_only=True, live_trading=False)
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "owner proof"}).status_code == 200
    try:
        report = _exits(t["tenant_id"], {symbol: 79.0})
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
        _restore_flags(previous)
    assert report.filled == 1 and report.errors == [] and asked == []
    assert _trade(trade_id).exit_time is not None and _row(stop["id"]).status == "FILLED"


def test_point2_the_worker_runs_the_engine_exits_before_every_entry_gate():
    from app.workers.trading_worker import TradingWorker
    source = inspect.getsource(TradingWorker._process_tenant)
    exits = source.index("trigger_engine.run_exits(")
    for gate in ("no new entries", "tenant_is_active(tenant)", "WAITING_FOR_CONFIRMED_DATA",
                 "trigger_engine.run_entries(", "active = [d for d in deployments"):
        assert gate in source, gate
        assert exits < source.index(gate), f"the engine's exits run after {gate!r}"
    head = source[:exits]
    assert "return" not in head.split("# Exits before entries.")[1].replace("return report", "")   # no early return before


# --- 3. the account kill switch stops resting entries -------------------------------------------------------------------

def test_point3_kill_switch_on_a_reached_entry_is_rejected_with_its_reason_and_exits_still_fill(setup):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    entry_id = _rest(t, expiry, strike)["resting_order"]["id"]
    before = _orders(t["tenant_id"])
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "owner proof"}).status_code == 200
    try:
        assert _entries(t["tenant_id"], {symbol: 96.0}).filled == 0 and _row(entry_id).status == "RESTING"   # held until reached
        exits = _exits(t["tenant_id"], {symbol: 79.0})
        entries = _entries(t["tenant_id"], {symbol: 79.0})
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    entry = _row(entry_id)
    assert exits.filled == 1 and _row(stop["id"]).status == "FILLED"                   # the exit still works
    assert entries.filled == 0 and entries.rejected == 1 and entry.status == "REJECTED" and entry.order_id is None
    last = resting_book.trail(entry)[-1]
    assert last["status"] == "REJECTED" and "kill switch" in last["note"].lower(), last
    assert _orders(t["tenant_id"]) - before <= 1                        # at most the refused ticket's own record, no fill
    assert _entries(t["tenant_id"], {symbol: 79.0}).filled == 0          # never fires later either


# --- 4. AI never places, blocks or changes orders -----------------------------------------------------------------------

def test_point4_no_ai_module_reaches_the_resting_book_or_the_trigger_engine():
    banned = ("app.execution.resting_book", "app.execution.trigger_engine", "resting_book", "trigger_engine")
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
    for module in (resting_book, trigger_engine):
        source = inspect.getsource(module)
        assert "app.ai" not in source and "import ai" not in source


# --- 5. the migration is additive and round-trips -----------------------------------------------------------------------

def test_point5_migration_7e2a4c6b8d10_upgrade_downgrade_upgrade(tmp_path):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location("tm2_migration_run", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "3c5e7a9b1d24" and module.ROLLBACK_SAFE is True
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    existing = ("tenants", "users", "trades", "orders")
    with eng.begin() as conn:
        for name in existing:
            conn.execute(sa.text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, note VARCHAR(10))"))
            conn.execute(sa.text(f"INSERT INTO {name} (id, note) VALUES (1, 'kept')"))
        ctx = MigrationContext.configure(conn)
        for _ in range(2):                                              # upgrade -> downgrade -> upgrade -> downgrade
            with Operations.context(ctx):
                module.upgrade()
            inspector = sa.inspect(conn)
            assert "paper_resting_orders" in inspector.get_table_names()
            assert {i["name"] for i in inspector.get_indexes("paper_resting_orders")} >= {
                "ix_resting_orders_tenant_status", "ix_paper_resting_orders_user_id", "ix_paper_resting_orders_trade_id"}
            for name in existing:                                       # additive: nothing existing changed
                assert {c["name"] for c in inspector.get_columns(name)} == {"id", "note"}
                assert conn.execute(sa.text(f"SELECT note FROM {name}")).scalar() == "kept"
            with Operations.context(ctx):
                module.downgrade()
            inspector = sa.inspect(conn)
            assert "paper_resting_orders" not in inspector.get_table_names()
            assert set(existing) <= set(inspector.get_table_names())


# --- 6. restart: stored, never double-fired ------------------------------------------------------------------------------

def _restart():
    """What a worker restart loses: the engine's in-process state. The book itself is rows."""
    trigger_engine._last_run = None
    trigger_engine._last_beat = 0.0
    trigger_engine._stall_alerted.clear()


def test_point6_two_passes_on_one_tick_and_a_claim_race_fire_once(setup):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    before = _orders(t["tenant_id"])

    async def race():
        async with _session_factory() as a, _session_factory() as b:
            row_a = await a.get(PaperRestingOrderRecord, row_id)
            row_b = await b.get(PaperRestingOrderRecord, row_id)        # both passes saw it RESTING
            now = datetime.now(timezone.utc)
            won_a = await trigger_engine._claim(a, row_a, now)
            won_b = await trigger_engine._claim(b, row_b, now)
            await trigger_engine._back_to_waiting(a, row_a, "test: released", now)
            return won_a, won_b
    assert _run(race()) == (True, False)
    first = _entries(t["tenant_id"], {symbol: 94.0})
    second = _entries(t["tenant_id"], {symbol: 94.0})
    _restart()
    third = _entries(t["tenant_id"], {symbol: 94.0})
    assert (first.filled, second.filled, third.filled) == (1, 0, 0)
    assert _orders(t["tenant_id"]) - before == 1 and _row(row_id).status == "FILLED"


def test_point6_a_resting_order_survives_a_restart_and_fills_once(setup):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    _restart()
    assert _entries(t["tenant_id"], {symbol: 96.0}).filled == 0 and _row(row_id).status == "RESTING"
    _restart()
    assert _entries(t["tenant_id"], {symbol: 94.0}).filled == 1
    _restart()
    assert _entries(t["tenant_id"], {symbol: 94.0}).filled == 0 and _row(row_id).status == "FILLED"


def _claim_only(row_id, at):
    """A pass that claimed the order (FIRING) and then the worker stopped (nothing else done)."""
    async def go():
        async with _session_factory() as session:
            row = await session.get(PaperRestingOrderRecord, row_id)
            assert await trigger_engine._claim(session, row, at)
    _run(go())


def _start(now):
    """A worker restart: the engine's in-process state is gone, then the start-up reconciliation (TM2-4)."""
    _restart()

    async def go():
        async with _session_factory() as session:
            return await trigger_engine.reconcile_on_start(session, now)
    return _run(go())


def _spy_paper_sends(monkeypatch):
    from app.execution.paper_adapter import PaperExecutionAdapter
    sent = []
    original = PaperExecutionAdapter.place_order

    async def spy(self, order):
        sent.append(order.client_ref)
        return await original(self, order)
    monkeypatch.setattr(PaperExecutionAdapter, "place_order", spy)
    return sent


# PAPER broker ----------------------------------------------------------------------------------------------------------

def test_tm24_paper_crash_mid_exit_fills_the_exit_once_right_after_restart(setup):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    now = datetime.now(timezone.utc)
    _claim_only(stop["id"], now)                                         # the worker stopped right after the claim
    _start(now)                                                          # looked up: not at the paper exchange
    row = _row(stop["id"])
    assert row.status == "FIRING" and row.fired_at is None and "same id" in row.status_note
    first = _exits(t["tenant_id"], {symbol: 79.0}, now=now)              # the first pass sends it once
    second = _exits(t["tenant_id"], {symbol: 79.0}, now=now)
    assert (first.filled, second.filled) == (1, 0) and _trade(trade_id).exit_time is not None
    assert _row(stop["id"]).status == "FILLED"


def test_tm24_paper_crash_after_the_exit_was_accepted_closes_nothing_twice(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    now = datetime.now(timezone.utc)
    finish = trigger_engine._finish

    async def crash(session, row, status, note, at):
        if row.id == stop["id"] and status == "FILLED":
            raise SystemExit("worker stopped")                          # after the paper exchange filled and the trade closed
        await finish(session, row, status, note, at)
    monkeypatch.setattr(trigger_engine, "_finish", crash)
    with pytest.raises(SystemExit):
        _exits(t["tenant_id"], {symbol: 79.0}, now=now)
    monkeypatch.setattr(trigger_engine, "_finish", finish)
    closed = _trade(trade_id)
    assert closed.exit_time is not None and _row(stop["id"]).status == "FIRING"
    sent = _spy_paper_sends(monkeypatch)
    _start(now)
    row = _row(stop["id"])
    assert row.status == "FILLED" and row.fill_price == pytest.approx(closed.exit_price) and sent == []   # found: adopted
    assert _exits(t["tenant_id"], {symbol: 79.0}, now=now).filled == 0 and sent == []
    assert _trade(trade_id).exit_price == pytest.approx(closed.exit_price)


def test_tm24_paper_crash_after_the_entry_was_accepted_places_no_second_order(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    now = datetime.now(timezone.utc)
    finish = trigger_engine._finish
    crashed = []

    async def crash(session, row, status, note, at):
        if not crashed and row.id == row_id:
            crashed.append(row.order_id)
            raise SystemExit("worker stopped")                          # not an Exception: nothing in the pass catches it
        await finish(session, row, status, note, at)
    monkeypatch.setattr(trigger_engine, "_finish", crash)
    with pytest.raises(SystemExit):
        _entries(t["tenant_id"], {symbol: 94.0}, now=now)
    monkeypatch.setattr(trigger_engine, "_finish", finish)
    assert _row(row_id).status == "FIRING" and crashed[0]
    before = _orders(t["tenant_id"])
    _start(now)
    row = _row(row_id)
    assert row.status == "FILLED" and row.order_id == crashed[0]          # found by its client id: adopted at start-up
    assert _entries(t["tenant_id"], {symbol: 94.0}, now=now).filled == 0 and _orders(t["tenant_id"]) == before


def test_tm24_paper_crash_mid_entry_sends_it_once_with_the_same_id(setup):
    t, expiry, strike, symbol = setup
    row_id = _rest(t, expiry, strike)["resting_order"]["id"]
    now = datetime.now(timezone.utc)
    before = _orders(t["tenant_id"])
    _claim_only(row_id, now)
    _start(now)
    assert _row(row_id).status == "FIRING"
    assert _entries(t["tenant_id"], {symbol: 94.0}, now=now).filled == 1
    assert _entries(t["tenant_id"], {symbol: 94.0}, now=now).filled == 0
    assert _orders(t["tenant_id"]) - before == 1 and _row(row_id).status == "FILLED"


def test_tm24_the_paper_adapter_answers_the_lookup_by_client_id():
    from app.brokers.models import BrokerOrderRequest, BrokerOrderStatus
    from app.core.enums import OrderSide
    from app.execution.paper_adapter import PaperExecutionAdapter
    durable = BrokerOrderStatus(order_id="EXIT-1", symbol="X", transaction_type=OrderSide.SELL, quantity=1, filled_quantity=1,
                                order_type="MARKET", status="COMPLETE", average_price=10.0, tag="exit:abc")

    async def book(wanted):
        return durable if wanted == "exit:abc" else None

    async def go():
        adapter = PaperExecutionAdapter(book=book)
        adapter.mark("X", 11.0, "NFO")
        found = await adapter.find_order_by_client_id("exit:abc")
        missing = await adapter.find_order_by_client_id("exit:new")
        again = await adapter.place_order(BrokerOrderRequest(symbol="X", exchange="NFO", transaction_type=OrderSide.SELL,
                                                             quantity=1, order_type="MARKET", client_ref="exit:abc"))
        fresh = await adapter.place_order(BrokerOrderRequest(symbol="X", exchange="NFO", transaction_type=OrderSide.SELL,
                                                             quantity=1, order_type="MARKET", client_ref="exit:new"))
        mine = await adapter.find_order_by_client_id("exit:new")
        return found, missing, again, fresh, mine
    found, missing, again, fresh, mine = _run(go())
    assert found is durable and missing is None
    assert again.order_id == "EXIT-1" and again.raw.get("duplicate")       # the same id never fills twice
    assert fresh.status == "COMPLETE" and mine is not None and mine.order_id == fresh.order_id


# LIVE broker (a fake broker session; LIVE switches untouched - an exit of an existing LIVE position) ------------------

def _live_setup():
    from tests.test_reexit_guard import _Book, _live_trade
    from tests.test_trading_worker import _tenant
    t = _tenant(f"tm24-{_ref()}@example.com")
    return t, _Book, _live_trade


def _arm_live(t, trade_id):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            trade = await session.get(TradeRecord, trade_id)
            row = await resting_book.arm_exit(session, user, trade, client_ref=f"exit:{_ref()}", kind="STOP", order_type="SL-M",
                                              level=98.0)
            await session.commit()
            return row.id
    return _run(go())


def _live_exits(t, broker, now):
    async def go():
        async with _session_factory() as session:
            return await trigger_engine.run_exits(session, t["tenant_id"], _prices({"RXG": 97.0}), broker=broker, now=now)
    return _run(go())


def _prices(table):
    async def lookup(symbol, exchange="NFO"):
        if symbol not in table:
            raise LookupError(symbol)
        return float(table[symbol])
    return lookup


def test_tm24_live_crash_mid_exit_fills_the_exit_once_right_after_restart():
    t, _Book, _live_trade = _live_setup()
    trade_id = _live_trade(t, exit_order_id=None)
    row_id = _arm_live(t, trade_id)
    now = datetime.now(timezone.utc)
    _claim_only(row_id, now)
    _start(now)                                                          # no broker session at start-up: sent on the cycle
    assert _row(row_id).status == "FIRING" and _row(row_id).fired_at is None
    broker = _Book(ltp=97.0)
    first = _live_exits(t, broker, now)
    second = _live_exits(t, broker, now)
    assert len(broker.placed) == 1 and broker.placed[0].transaction_type.value == "SELL"
    assert (first.filled, second.filled) == (1, 0) and _trade(trade_id).exit_time is not None and _row(row_id).status == "FILLED"


def test_tm24_live_crash_after_the_broker_accepted_sends_no_second_order():
    from app.brokers.models import BrokerOrderStatus
    from app.core.enums import OrderSide
    t, _Book, _live_trade = _live_setup()
    trade_id = _live_trade(t, exit_order_id="OLD-1")                    # the exit ledger recorded the broker's id ...
    row_id = _arm_live(t, trade_id)
    now = datetime.now(timezone.utc)
    _claim_only(row_id, now)                                             # ... then the worker stopped before booking it
    broker = _Book(ltp=97.0)
    broker.orders["OLD-1"] = BrokerOrderStatus(order_id="OLD-1", symbol="RXG", transaction_type=OrderSide.SELL, quantity=1,
                                               filled_quantity=1, order_type="MARKET", status="COMPLETE", average_price=96.5)
    _start(now)
    report = _live_exits(t, broker, now)
    trade = _trade(trade_id)
    assert broker.placed == [] and report.filled == 1                   # found at the broker: adopted, nothing sent
    assert trade.exit_time is not None and trade.exit_price == pytest.approx(96.5) and _row(row_id).status == "FILLED"
    assert _live_exits(t, broker, now).filled == 0 and broker.placed == []


def test_tm24_the_live_lookup_reads_the_brokers_book_by_tag():
    from app.brokers.models import BrokerOrderStatus
    from app.core.enums import OrderSide
    _t, _Book, _x = _live_setup()
    broker = _Book()
    broker.max_tag_length = 8
    broker.orders["A"] = BrokerOrderStatus(order_id="A", symbol="RXG", transaction_type=OrderSide.SELL, quantity=1,
                                           order_type="MARKET", status="OPEN", tag="exit:abc")
    assert _run(broker.find_order_by_client_id("exit:abcdef")).order_id == "A"      # cut to the broker's tag length
    assert _run(broker.find_order_by_client_id("exit:zzz")) is None
    broker.book_error = RuntimeError("book down")
    with pytest.raises(RuntimeError):                                    # unknown is never "not there"
        _run(broker.find_order_by_client_id("exit:abc"))


# Order and the running timeout -----------------------------------------------------------------------------------------

def test_tm24_exits_are_reconciled_before_entries(setup, monkeypatch):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    entry_id = _rest(t, expiry, strike)["resting_order"]["id"]
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    now = datetime.now(timezone.utc)
    _claim_only(entry_id, now)                                           # the entry was claimed first ...
    _claim_only(stop["id"], now)
    seen = []
    original = trigger_engine._reconcile

    async def spy(session, row, *a, **k):
        if row.id in (entry_id, stop["id"]):
            seen.append(row.purpose)
        return await original(session, row, *a, **k)
    monkeypatch.setattr(trigger_engine, "_reconcile", spy)
    _start(now)
    assert seen == ["EXIT", "ENTRY"]                                     # ... the exit is settled first


def test_tm24_an_order_still_sending_after_the_ack_timeout_is_settled_while_running(setup):
    t, expiry, strike, symbol = setup
    trade_id = _open_trade(t, expiry, strike, symbol)
    stop = _arm(t, trade_id, kind="STOP", order_type="SL-M", level=80.0)
    now = datetime.now(timezone.utc)
    _claim_only(stop["id"], now)
    assert _exits(t["tenant_id"], {symbol: 79.0}, now=now).filled == 0  # within the ack timeout: left to its pass
    assert _row(stop["id"]).status == "FIRING" and _trade(trade_id).exit_time is None
    later = now + timedelta(seconds=trigger_engine.TRIGGER_ACK_TIMEOUT_SECONDS + 1)
    assert _exits(t["tenant_id"], {symbol: 79.0}, now=later).filled == 1   # no restart: looked up, sent once
    assert _exits(t["tenant_id"], {symbol: 79.0}, now=later).filled == 0
    assert _trade(trade_id).exit_time is not None and _row(stop["id"]).status == "FILLED"
