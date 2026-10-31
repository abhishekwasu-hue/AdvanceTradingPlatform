"""M55 S4 + S5: a client order id on every LIVE order, reconcile by it, the start-up order sweeper, persisted exit
counters, and the per-account exit broker of the trigger engine.

- every adapter with a tag / reference field sends a unique id per order, inside its own length limit;
- an exit is found again in the broker's book by its own id (no time window), and right after a send that errored;
  an order without the id still matches by tag + time window (the fallback);
- the sweeper settles a stuck RISK_CHECK / SUBMITTED row from the broker's book (or the paper venue), for an
  organisation with no open position, and never places an order;
- the stop guard's counters and cool-downs survive a restart;
- a LIVE resting exit of an organisation with two broker accounts goes out through its own account's session."""
import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.core.enums import OrderSide
from app.db.models import BrokerCredentialRecord, OrderRecord, RuntimeCounterRecord, TradeRecord
from app.execution import order_sweeper, tagging, trigger_engine
from app.execution.tagging import (
    CLIENT_ID_LENGTH,
    CLIENT_ID_MIN_LENGTH,
    LEG_ENTRY,
    LEG_EXIT,
    LEG_STOP,
    build_order_tag,
    client_order_id,
    has_client_id,
)
from app.trading import position_monitor as pm
from app.trading import stop_state
from tests.test_auth_api import _session_factory
from tests.test_reexit_guard import _Book, _close, _live_trade, _lookalike
from tests.test_trading_worker import STRATEGY, _tenant


def _run(coro):
    return asyncio.run(coro)


# --- the id and the tag ------------------------------------------------------------------------------------------------

def test_two_orders_get_two_ids_and_the_same_order_the_same_id():
    assert client_order_id("E", 1) == client_order_id("E", 1)
    ids = {client_order_id("E", n) for n in range(2000)}
    assert len(ids) == 2000
    assert client_order_id("E", 7) != client_order_id("S", 7) != client_order_id("X", 7, 0)
    assert all(len(i) == CLIENT_ID_LENGTH and i.isalnum() and i.upper() == i for i in ids)


@pytest.mark.parametrize("algo_id", [None, "NSE777", "ALGO12345"])
@pytest.mark.parametrize("limit", [20, 25, 36, 64])
def test_the_tag_carries_the_id_within_the_limit_keeping_algo_id_and_leg(algo_id, limit):
    a, b = client_order_id("E", 101), client_order_id("E", 102)
    for leg in (LEG_ENTRY, LEG_STOP, LEG_EXIT):
        ta = build_order_tag(strategy_id="ema_rsi_scalper_1m", leg=leg, algo_id=algo_id, max_length=limit, client_id=a)
        tb = build_order_tag(strategy_id="ema_rsi_scalper_1m", leg=leg, algo_id=algo_id, max_length=limit, client_id=b)
        assert ta != tb and len(ta) <= limit and len(tb) <= limit
        assert ta.endswith("-" + leg) and has_client_id(ta, a) and not has_client_id(ta, b)
        if algo_id:
            assert ta.startswith(algo_id + "-")


def test_no_room_leaves_the_id_out_and_the_tag_as_before():
    algo = "A" * tagging.ALGO_ID_MAX_SAVED                       # the longest algo id the Team page saves
    cid = client_order_id("E", 5)
    tag = build_order_tag(strategy_id="s", leg=LEG_EXIT, algo_id=algo, client_id=cid)
    assert tag == build_order_tag(strategy_id="s", leg=LEG_EXIT, algo_id=algo) and not has_client_id(tag, cid)
    short = build_order_tag(strategy_id="s", leg=LEG_EXIT, algo_id="A" * 10, client_id=cid)     # 4 characters of room
    assert len(short) == 20 and has_client_id(short, cid) and short == f"{'A' * 10}-{cid[:CLIENT_ID_MIN_LENGTH]}-EXIT"
    assert build_order_tag(strategy_id="s", leg=LEG_ENTRY) == "s-ENT"                          # no id: unchanged


def test_every_adapter_has_a_tag_limit_the_id_fits_in():
    from app.brokers import angel_one, coindcx, dhan, fyers, groww, kotak_neo, shoonya, upstox, zerodha
    classes = [zerodha.ZerodhaBroker, upstox.UpstoxBroker, angel_one.AngelOneBroker, fyers.FyersBroker, dhan.DhanBroker,
               kotak_neo.KotakNeoBroker, groww.GrowwBroker, shoonya.ShoonyaBroker, coindcx.CoinDCXBroker]
    for cls in classes:
        limit = cls.max_tag_length
        tags = {build_order_tag(strategy_id=STRATEGY, leg=LEG_EXIT, max_length=limit, broker=cls.name if hasattr(cls, "name") else None,
                                client_id=client_order_id("E", n)) for n in range(50)}
        assert len(tags) == 50 and all(len(t) <= limit for t in tags), cls


def _adapter_payloads():
    """Two stops through each adapter whose test server records the order payload: (field sent, value)."""
    from tests import test_phase_ac_angel_one as angel
    from tests import test_phase_ae_fyers as fy
    from tests import test_phase_af_dhan as dh
    tags = [build_order_tag(strategy_id=STRATEGY, leg=LEG_STOP, client_id=client_order_id("S", n)) for n in (1, 2)]
    out = {}
    server = dh._Server()
    broker = dh._broker(server)
    out["dhan"] = []
    for tag in tags:
        _run(broker.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 10, 2950.0, tag=tag))
        out["dhan"].append(server.last["place"]["correlationId"])
    server = fy._Server()
    broker = fy._broker(server)
    _run(broker.authenticate())
    out["fyers"] = []
    for tag in tags:
        _run(broker.place_stop_loss_order("SBIN", "NSE", OrderSide.SELL, 10, 790.0, tag=tag))
        out["fyers"].append(server.last["POST"]["orderTag"])
    server = angel._Server()
    broker = angel._broker(server)
    _run(broker.authenticate())
    out["angel_one"] = []
    for tag in tags:
        _run(broker.place_stop_loss_order("RELIANCE", "NSE", OrderSide.SELL, 10, 2950.0, product="MIS", tag=tag))
        out["angel_one"].append(server.last_order["ordertag"])
    return tags, out


def test_adapters_send_the_unique_id_in_their_own_tag_field(monkeypatch):
    import app.brokers.dhan as dhan_module
    from tests import test_phase_af_dhan as dh
    monkeypatch.setattr(dhan_module, "date", dh._Before28Oct)
    tags, sent = _adapter_payloads()
    for name, values in sent.items():
        assert values == tags and values[0] != values[1], name


# --- reconcile by the id -----------------------------------------------------------------------------------------------

def _exit_trade(sent_at):
    return SimpleNamespace(id=4242, strategy_id=STRATEGY, symbol="RXG", direction="LONG", quantity=1, broker_order_id=None,
                           sl_order_id=None, exit_order_id="OLD-1", exit_sent_at=sent_at, product=None, exchange="NFO",
                           holding=None, instrument_kind="UNDERLYING")


def test_an_exit_is_adopted_by_its_own_id_without_the_time_window():
    broker = _Book()
    sent = datetime.now(UTC) - timedelta(hours=3)
    trade = _exit_trade(sent)
    tag = pm.expected_exit_tag(trade, broker, None)
    assert has_client_id(tag, pm.exit_client_id(trade))
    far = _lookalike("MINE", "OPEN", tag=tag, placed_at=sent + timedelta(hours=2))          # outside any claim window
    assert pm.adoptable_exit(trade, [far], tag=tag, legacy_tag=pm.legacy_exit_tag(trade, broker, None)) is far
    other = _exit_trade(sent + timedelta(seconds=200))                                     # another attempt: another id
    assert pm.adoptable_exit(other, [far], tag=pm.expected_exit_tag(other, broker, None),
                             legacy_tag=pm.legacy_exit_tag(other, broker, None)) is None


def test_an_order_without_the_id_still_matches_by_tag_and_time_window():
    broker = _Book()
    sent = datetime.now(UTC) - timedelta(minutes=10)
    trade = _exit_trade(sent)
    tag, legacy = pm.expected_exit_tag(trade, broker, None), pm.legacy_exit_tag(trade, broker, None)
    inside = _lookalike("OLD-TAG", "OPEN", tag=legacy, placed_at=sent + timedelta(seconds=3))
    outside = _lookalike("OLD-TAG-2", "OPEN", tag=legacy, placed_at=sent + timedelta(hours=1))
    assert pm.adoptable_exit(trade, [inside], tag=tag, legacy_tag=legacy) is inside
    assert pm.adoptable_exit(trade, [outside], tag=tag, legacy_tag=legacy) is None


def test_a_send_that_errors_is_found_at_once_by_its_id_and_never_sent_twice():
    t = _tenant(f"m55-id-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None)

    class _Lost(_Book):
        async def place_order(self, order):
            self.placed.append(order)
            self.orders["LOST-1"] = BrokerOrderStatus(order_id="LOST-1", symbol=order.symbol, transaction_type=order.transaction_type,
                                                      quantity=order.quantity, filled_quantity=order.quantity, order_type="MARKET",
                                                      status="COMPLETE", average_price=96.5, tag=order.tag, placed_at=datetime.now(UTC))
            raise TimeoutError("read timed out")
    broker = _Lost()
    outcome = _close(trade_id, broker, t["user_id"])
    assert len(broker.placed) == 1 and outcome.closed and outcome.exit_price == 96.5

    async def row():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    assert _run(row()).exit_order_id == "LOST-1"


# --- the start-up order sweeper ----------------------------------------------------------------------------------------

class _BookBroker:
    name = "fakebook"
    max_tag_length = 20

    def __init__(self, orders=(), error=None):
        self.orders, self.error, self.placed = list(orders), error, []

    async def get_order_book(self):
        if self.error is not None:
            raise self.error
        return list(self.orders)

    async def place_order(self, order):                       # the sweeper must never call this
        self.placed.append(order)
        return BrokerOrderResponse(order_id="NEVER", status="OPEN")


def _order_row(t, status, *, mode="LIVE", age_minutes=30, symbol="SWP", trade_id=None, tag_id=True, broker_order_id=None):
    async def go():
        async with _session_factory() as session:
            row = OrderRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, strategy_id=STRATEGY, symbol=symbol,
                              direction="LONG", quantity=1, status=status, trade_id=trade_id, broker_order_id=broker_order_id)
            session.add(row)
            await session.flush()
            if tag_id:
                row.algo_tag = build_order_tag(strategy_id=STRATEGY, leg=LEG_ENTRY, client_id=client_order_id("E", row.id))
            old = datetime.now(UTC) - timedelta(minutes=age_minutes)
            row.created_at = row.updated_at = old
            await session.commit()
            await session.execute(OrderRecord.__table__.update().where(OrderRecord.id == row.id).values(updated_at=old))
            await session.commit()
            return row.id, row.algo_tag
    return _run(go())


def _status(order_id):
    async def go():
        async with _session_factory() as session:
            return (await session.get(OrderRecord, order_id)).status
    return _run(go())


def _credential(t, monkeypatch, broker):
    async def go():
        async with _session_factory() as session:
            rec = BrokerCredentialRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], broker_name="upstox",
                                         account_label=f"a{uuid.uuid4().hex[:4]}", encrypted_payload="x", token_status="VALID",
                                         token_expires_at=datetime.now(UTC) + timedelta(hours=6))
            session.add(rec)
            await session.commit()
            return rec.id
    rec_id = _run(go())
    import app.brokers.token_lifecycle as tl
    monkeypatch.setattr(tl, "build_adapter", lambda record, client=None: broker)
    return rec_id


def _sweep(t):
    async def go():
        async with _session_factory() as session:
            return await order_sweeper.sweep_orders(session, tenant_ids={t["tenant_id"]})
    return _run(go())


def test_the_sweeper_settles_stuck_rows_from_the_broker_book_for_an_organisation_with_no_open_trades(monkeypatch):
    t = _tenant(f"m55-sweep-{uuid.uuid4().hex[:6]}@example.com")
    rejected, rejected_tag = _order_row(t, "RISK_CHECK")
    cancelled, cancelled_tag = _order_row(t, "SUBMITTED")
    absent, _ = _order_row(t, "RISK_CHECK")
    filled, filled_tag = _order_row(t, "SUBMITTED")
    fresh, _ = _order_row(t, "RISK_CHECK", age_minutes=0)                 # may be being placed right now: untouched
    book = [
        BrokerOrderStatus(order_id="B1", symbol="SWP", transaction_type=OrderSide.BUY, quantity=1, order_type="MARKET", status="REJECTED",
                          tag=rejected_tag),
        BrokerOrderStatus(order_id="B2", symbol="SWP", transaction_type=OrderSide.BUY, quantity=1, order_type="MARKET", status="CANCELLED",
                          tag=cancelled_tag),
        BrokerOrderStatus(order_id="B3", symbol="SWP", transaction_type=OrderSide.BUY, quantity=1, filled_quantity=1,
                          order_type="MARKET", status="COMPLETE", tag=filled_tag),
    ]
    broker = _BookBroker(book)
    _credential(t, monkeypatch, broker)
    report = _sweep(t)
    assert _status(rejected) == "REJECTED" and _status(cancelled) == "CANCELLED" and _status(absent) == "CANCELLED"
    assert _status(filled) == "FILLED" and filled in report.flagged             # filled with no position: flagged
    assert _status(fresh) == "RISK_CHECK"
    assert broker.placed == []                                                   # read only: never an order

    async def flagged():
        from app.db.models import Tenant
        async with _session_factory() as session:
            return (await session.get(Tenant, t["tenant_id"])).broker_uncertain_since
    assert _run(flagged()) is not None


def test_the_sweeper_leaves_rows_when_the_book_cannot_be_read_and_settles_paper_and_recorded_positions(monkeypatch):
    t = _tenant(f"m55-sweep2-{uuid.uuid4().hex[:6]}@example.com")
    live, _ = _order_row(t, "SUBMITTED")
    paper, _ = _order_row(t, "RISK_CHECK", mode="PAPER")
    early, _ = _order_row(t, "VALIDATING")
    trade_id = _live_trade(t, exit_order_id=None, symbol="SWQ")
    recorded, _ = _order_row(t, "PENDING", trade_id=trade_id)
    _credential(t, monkeypatch, _BookBroker(error=ConnectionError("down")))
    report = _sweep(t)
    assert _status(live) == "SUBMITTED" and live in report.unchecked            # unknown is never "not there"
    assert _status(paper) == "CANCELLED" and _status(early) == "CANCELLED"
    assert _status(recorded) == "POSITION_OPEN"


# --- persisted counters --------------------------------------------------------------------------------------------------

def test_the_counters_and_cooldowns_survive_a_restart():
    t = _tenant(f"m55-ctr-{uuid.uuid4().hex[:6]}@example.com")
    trade_id = _live_trade(t, exit_order_id=None, symbol="CTR")
    stop_state.exit_attempts[trade_id] = 2
    stop_state.rearm_rejects[trade_id] = 1
    stop_state.rearmed_order[trade_id] = "SL-9"
    stop_state.last_failure_alert[trade_id] = time.monotonic() - 60          # alerted a minute ago

    async def save():
        async with _session_factory() as session:
            return await stop_state.save(session)
    assert _run(save()) >= 4
    for table in (stop_state.exit_attempts, stop_state.rearm_rejects, stop_state.rearmed_order, stop_state.last_failure_alert):
        dict.pop(table, trade_id, None)                                        # a restart: memory is empty

    async def load():
        async with _session_factory() as session:
            return await stop_state.load(session)
    assert _run(load()) >= 4
    assert stop_state.exit_attempts[trade_id] == 2 and stop_state.rearm_rejects[trade_id] == 1
    assert stop_state.rearmed_order[trade_id] == "SL-9"
    assert 50 <= time.monotonic() - stop_state.last_failure_alert[trade_id] <= 120      # the cool-down keeps running

    stop_state.forget(trade_id)                                                # the position closed: its rows go too
    _run(save())

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(RuntimeCounterRecord).where(RuntimeCounterRecord.key == str(trade_id))))
    assert _run(rows()) == []


def test_a_failed_save_never_raises():
    class _Broken:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

        async def rollback(self):
            pass
    stop_state.exit_waits[987654321] = 1
    assert _run(stop_state.save(_Broken())) == 0
    assert 987654321 in stop_state.exit_waits.dirty                            # written on the next pass
    stop_state.forget(987654321)


# --- S5: the trigger engine's exit broker, per account -----------------------------------------------------------------

def test_a_live_resting_exit_goes_out_through_its_own_accounts_broker(monkeypatch):
    brokers = {1: SimpleNamespace(name="acct-1"), 2: SimpleNamespace(name="acct-2")}

    async def broker_for_trade(session, trade):
        return brokers.get(trade.broker_account_id)
    monkeypatch.setattr(pm, "broker_for_trade", broker_for_trade)
    sent = []

    async def send_exit(session, row, trade, price, broker, now, report):
        sent.append((trade.id, broker))
    monkeypatch.setattr(trigger_engine, "_send_exit", send_exit)

    async def reached(*a, **k):
        return True

    async def claim(*a, **k):
        return True
    monkeypatch.setattr(trigger_engine, "_reached", reached)
    monkeypatch.setattr(trigger_engine, "_claim", claim)
    trades = {11: SimpleNamespace(id=11, mode="LIVE", exit_time=None, broker_account_id=1),
              12: SimpleNamespace(id=12, mode="LIVE", exit_time=None, broker_account_id=2),
              13: SimpleNamespace(id=13, mode="LIVE", exit_time=None, broker_account_id=99)}       # no usable session

    class _Session:
        info: dict = {}

        async def get(self, model, key):
            return trades[key]

    class _Prices:
        async def get(self, symbol, exchange):
            return 100.0

    async def go():
        report = trigger_engine.EngineReport()
        for trade_id in trades:
            row = SimpleNamespace(trade_id=trade_id, order_type="MARKET", symbol="X", exchange="NFO", side="SELL", price=None)
            await trigger_engine._evaluate_exit(_Session(), row, _Prices(), None, None, datetime.now(UTC), report)  # broker None
        return report
    report = _run(go())
    assert sent == [(11, brokers[1]), (12, brokers[2])]                        # each its own account, never waiting forever
    assert report.waiting == 1                                                 # only the one with no usable session waits

    handed = SimpleNamespace(name="single")
    assert _run(trigger_engine.exit_broker_for(None, trades[12], handed)) is handed   # a single-account cycle: unchanged
    paper = SimpleNamespace(mode="PAPER")
    assert _run(trigger_engine.exit_broker_for(None, paper, None)) is None


# --- the migration -------------------------------------------------------------------------------------------------------

def test_migration_f9c2a4e6b801_is_additive_and_round_trips(tmp_path):
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "f9c2a4e6b801_runtime_counters.py"
    spec = importlib.util.spec_from_file_location("m55_counters_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "ef28ae662a93" and module.ROLLBACK_SAFE is True
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE trades (id INTEGER PRIMARY KEY, note VARCHAR(10))"))
        conn.execute(sa.text("INSERT INTO trades (id, note) VALUES (1, 'kept')"))
        ctx = MigrationContext.configure(conn)
        for _ in range(2):
            with Operations.context(ctx):
                module.upgrade()
            inspector = sa.inspect(conn)
            assert {c["name"] for c in inspector.get_columns("runtime_counters")} == {"id", "name", "key", "value", "updated_at"}
            assert [u["column_names"] for u in inspector.get_unique_constraints("runtime_counters")] == [["name", "key"]]
            with Operations.context(ctx):
                module.downgrade()
            assert "runtime_counters" not in sa.inspect(conn).get_table_names()
            assert conn.execute(sa.text("SELECT note FROM trades")).scalar() == "kept"
