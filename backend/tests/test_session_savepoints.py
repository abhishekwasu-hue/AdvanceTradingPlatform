"""ADR-0004: one failed step on the trading worker's shared cycle session must not take the rest of the cycle with it.

On Postgres a failed statement aborts the transaction (InFailedSqlTransaction) and a failed flush leaves the SQLAlchemy
session needing a rollback (PendingRollbackError) - every later statement fails, the next tenants' exits included.
`app.db.savepoint.guarded` puts each advisory step in a SAVEPOINT. A failed flush reproduces the problem on any backend;
the Postgres test (skipped without one) reproduces the aborted transaction itself."""
import asyncio
import os
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from app.db.models import MarketHolidayRecord, StrategyDeploymentRecord, TradeRecord, WorkerHeartbeatRecord
from app.db.savepoint import guarded
from app.workers import trading_worker as tw
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import OPEN_NOW, STRATEGY, _FakeBroker, _deploy, _tenant, _worker


def _run(coro):
    return asyncio.run(coro)


async def _failed_flush(session, day=date(2031, 1, 1)):
    """Two identical holiday rows: the unique (exchange, date) constraint fails the flush - a real SQL error."""
    session.add_all([MarketHolidayRecord(exchange="ZZTEST", holiday_date=day, description="dup"),
                     MarketHolidayRecord(exchange="ZZTEST", holiday_date=day, description="dup")])
    await session.flush()


def _holidays(exchange):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(MarketHolidayRecord.description).where(MarketHolidayRecord.exchange == exchange)))
    return _run(go())


# --- the helper -----------------------------------------------------------------------------------------------------------

def test_without_a_savepoint_a_failed_step_poisons_the_shared_session():
    async def go():
        async with _session_factory() as session:
            try:
                await _failed_flush(session)
            except IntegrityError:
                pass                                    # the pattern this fix removes: log and carry on
            with pytest.raises(PendingRollbackError):
                await session.execute(select(TradeRecord.id).limit(1))
            await session.rollback()
    _run(go())


def test_guarded_undoes_only_the_failed_step_and_the_session_keeps_working():
    async def go():
        async with _session_factory() as session:
            session.add(MarketHolidayRecord(exchange="ZZKEEP", holiday_date=date(2031, 2, 1), description="other pending work"))
            await session.flush()
            with pytest.raises(IntegrityError):
                async with guarded(session):
                    await _failed_flush(session)
            assert (await session.execute(text("SELECT 1"))).scalar() == 1      # usable
            async with guarded(session):                                         # a later step still runs and succeeds
                session.add(MarketHolidayRecord(exchange="ZZKEEP", holiday_date=date(2031, 2, 2), description="later step"))
            await session.commit()
    _run(go())
    assert sorted(_holidays("ZZKEEP")) == ["later step", "other pending work"] and _holidays("ZZTEST") == []


def test_guarded_after_the_step_committed_keeps_the_commit_and_resets_a_broken_session():
    async def go():
        async with _session_factory() as session:
            with pytest.raises(IntegrityError):
                async with guarded(session):
                    session.add(MarketHolidayRecord(exchange="ZZCOMMIT", holiday_date=date(2031, 3, 1), description="committed"))
                    await session.commit()                                       # the savepoint ends with this commit
                    await _failed_flush(session, date(2031, 3, 2))
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
    _run(go())
    assert _holidays("ZZCOMMIT") == ["committed"]


def test_keep_reloads_an_object_the_failed_step_changed():
    async def go():
        async with _session_factory() as session:
            row = MarketHolidayRecord(exchange="ZZRELOAD", holiday_date=date(2031, 4, 1), description="before")
            session.add(row)
            await session.commit()
            with pytest.raises(RuntimeError):
                async with guarded(session, keep=(row,)):
                    row.description = "changed in the failed step"
                    await session.flush()
                    raise RuntimeError("boom")
            return row.description                       # readable without a lazy load: reloaded
    assert _run(go()) == "before"


def _pg_url():
    url = os.environ.get("DATABASE_URL", "")
    return url if url.startswith("postgresql") else None


@pytest.mark.skipif(_pg_url() is None, reason="needs Postgres at DATABASE_URL (the default unit run uses SQLite)")
def test_on_postgres_a_failed_statement_aborts_the_transaction_and_guarded_keeps_it_usable():
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def go():
        engine = create_async_engine(_pg_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                await session.execute(text("CREATE TEMP TABLE sp_probe (id int PRIMARY KEY)"))
                await session.execute(text("INSERT INTO sp_probe VALUES (1)"))
                try:
                    await session.execute(text("SELECT * FROM no_such_table_sp"))
                except DBAPIError:
                    pass
                with pytest.raises(DBAPIError, match="InFailedSQLTransaction|current transaction is aborted"):
                    await session.execute(text("SELECT 1"))
                await session.rollback()
            async with factory() as session:
                await session.execute(text("CREATE TEMP TABLE sp_probe2 (id int PRIMARY KEY)"))
                await session.execute(text("INSERT INTO sp_probe2 VALUES (1)"))
                with pytest.raises(DBAPIError):
                    async with guarded(session):
                        await session.execute(text("SELECT * FROM no_such_table_sp"))
                assert (await session.execute(text("SELECT count(*) FROM sp_probe2"))).scalar() == 1   # earlier work kept
        finally:
            await engine.dispose()
    _run(go())


# --- the trading worker ---------------------------------------------------------------------------------------------------

def _open_trade(t, dep_id, *, symbol="RELIANCE") -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id=STRATEGY, symbol=symbol,
                                exchange="NSE", deployment_id=dep_id, direction="LONG", entry_price=100.0, quantity=1,
                                stop_loss=98.0, target1=104.0, target2=108.0, entry_time=OPEN_NOW - timedelta(minutes=30))
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _trade(trade_id):
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def _set_active(*dep_ids):
    async def go():
        async with _session_factory() as session:
            for dep_id in dep_ids:
                (await session.get(StrategyDeploymentRecord, dep_id)).status = "ACTIVE"
            await session.commit()
    _run(go())


def test_a_failed_step_on_one_tenant_never_blocks_the_next_tenants_exit(monkeypatch):
    a = _tenant("sp-tenant-a@example.com")
    b = _tenant("sp-tenant-b@example.com")             # (_tenant stops every deployment; both are re-activated below)
    dep_a, dep_b = _deploy(a), _deploy(b)
    _set_active(dep_a, dep_b)
    trade_b = _open_trade(b, dep_b)
    real = tw.ensure_tenant_key

    async def poisoned(session, tenant_id):
        if tenant_id == a["tenant_id"]:
            await _failed_flush(session, date(2031, 5, 1))   # tenant A's cycle hits a SQL error
        return await real(session, tenant_id)
    monkeypatch.setattr(tw, "ensure_tenant_key", poisoned)
    report = _run(_worker(monkeypatch, _FakeBroker(ltp=97.0)).run_cycle(now=OPEN_NOW))      # below B's stop
    assert any(f"tenant {a['tenant_id']}" in e for e in report.errors)
    assert not any("rolled back due to a previous exception" in e for e in report.errors), report.errors
    closed = _trade(trade_b)
    assert closed.exit_time is not None and "stop" in (closed.exit_reason or "").lower()       # B's exit still ran
    assert report.positions_closed >= 1


def test_a_failed_housekeeping_step_leaves_the_cycle_session_working(monkeypatch):
    t = _tenant("sp-housekeeping@example.com")
    _deploy(t)

    async def failing_oi_banner(self, session, now):
        await _failed_flush(session, date(2031, 6, 1))
    monkeypatch.setattr(tw.TradingWorker, "_oi_banner", failing_oi_banner)
    news_reads = []

    async def enabled(session):
        news_reads.append((await session.execute(select(TradeRecord.id).limit(1))).all())   # the next step reads the same session
        return False
    monkeypatch.setattr(tw.news_feed, "enabled", enabled)

    async def cycles():
        async with _session_factory() as session:
            hb = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
            return hb.cycle_count if hb is not None else 0
    before = _run(cycles())
    report = _run(_worker(monkeypatch, _FakeBroker(ltp=101.0)).run_cycle(now=OPEN_NOW))
    assert any("oi banner" in e for e in report.errors)
    assert news_reads and not any("news feed" in e for e in report.errors)   # the step after it still read the session
    assert not any("rolled back due to a previous exception" in e or "InFailedSqlTransaction" in e for e in report.errors), report.errors
    assert _run(cycles()) == before + 1                  # and the cycle reached its heartbeat


# --- review fixes: per-position savepoints, commits right after a broker accepted an order ----------------------------------

class _StopBroker(_FakeBroker):
    """Stops stay TRIGGER PENDING (the base fake fills every order it is given)."""
    async def get_order_book(self):
        book = await super().get_order_book()
        for o in book:
            if o.order_type in ("SL-M", "SL"):
                o.status, o.filled_quantity, o.average_price = "TRIGGER PENDING", 0, None
        return book


async def _rearm_case(factory, tag):
    """Two LIVE positions without a standing stop. The guard re-arms the first; the second's step hits a SQL error. Run as
    the worker runs it (inside guarded): the first position's new stop id must be committed and stay."""
    from app.db.models import Tenant, User
    from app.trading import stop_guard
    async with factory() as session:
        tenant = Tenant(name=f"sp-{tag}", plan="business")
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email=f"sp-{tag}@example.com", hashed_password="!", is_active=True)
        session.add(user)
        await session.flush()
        ids = []
        for sym in ("SPGA", "SPGB"):
            trade = TradeRecord(tenant_id=tenant.id, user_id=user.id, mode="LIVE", strategy_id=STRATEGY, symbol=sym, exchange="NSE",
                                direction="LONG", entry_price=100.0, quantity=1, stop_loss=98.0, target1=104.0, target2=108.0,
                                sl_order_id=f"GONE-{sym}", entry_time=OPEN_NOW - timedelta(minutes=30))
            session.add(trade)
            await session.flush()
            ids.append(trade.id)
        await session.commit()
        tenant_id, first, second = tenant.id, ids[0], ids[1]

    real_notify = stop_guard.notify

    async def notify(session, tid, kind, **kw):
        if kw.get("related_trade_id") == second:
            await session.execute(text("SELECT * FROM no_such_table_sp"))     # a SQL error on the second position's step
        return await real_notify(session, tid, kind, **kw)
    stop_guard.notify = notify
    try:
        async with factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            try:
                async with guarded(session, keep=(tenant,)):
                    counts = await stop_guard.verify_protective_stops(session, tenant, _StopBroker(ltp=100.0), user_id=None)
            except Exception:  # noqa: BLE001 - the worker logs it
                counts = None
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
    finally:
        stop_guard.notify = real_notify
    async with factory() as session:
        rows = {t.id: t.sl_order_id for t in await session.scalars(select(TradeRecord).where(TradeRecord.id.in_([first, second])))}
    return counts, rows[first], rows[second]


def test_a_rearmed_stop_id_survives_a_later_positions_error():
    counts, first_id, second_id = _run(_rearm_case(_session_factory, "rearm-sqlite"))
    assert first_id not in (None, "GONE-SPGA") and first_id.startswith("ORD-")   # the new stop - never the old id back
    assert second_id not in (None, "GONE-SPGB")                                   # committed before its own error too
    assert counts is not None and counts["failed"] == 1 and counts["rearmed"] >= 1   # the pass went on past the error


@pytest.mark.skipif(_pg_url() is None, reason="needs Postgres at DATABASE_URL (the default unit run uses SQLite)")
def test_on_postgres_a_rearmed_stop_id_survives_a_later_positions_error():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.db.models import Base

    async def go():
        engine = create_async_engine(_pg_url())
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _rearm_case(async_sessionmaker(engine, expire_on_commit=False), f"rearm-pg-{os.getpid()}")
        finally:
            await engine.dispose()
    counts, first_id, second_id = _run(go())
    assert first_id.startswith("ORD-") and second_id not in (None, "GONE-SPGB")
    assert counts is not None and counts["failed"] == 1 and counts["rearmed"] >= 1   # the pass went on past the error


def test_one_accounts_sql_error_never_blocks_the_other_accounts_reconciliation(monkeypatch):
    from types import SimpleNamespace
    from app.db.models import Tenant
    from app.reconciliation import service as recon
    t = _tenant("sp-recon@example.com")
    seen = []

    async def run_reconciliation(session, tenant, broker_name, adapter, *, account, **kw):
        seen.append(account.account_label)
        if account.account_label == "first":
            await _failed_flush(session, date(2031, 8, 1))
        await session.execute(select(TradeRecord.id).limit(1))                # the session still works for the next one
        return SimpleNamespace(broker_name=broker_name, account_label=account.account_label, items=[], mismatched_count=0,
                               accounts=[account.account_label])
    monkeypatch.setattr(recon, "run_reconciliation", run_reconciliation)
    accounts = [SimpleNamespace(broker_name="upstox", account_label=label) for label in ("first", "second")]

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            reports = await recon.reconcile_accounts(session, tenant, [(a, None) for a in accounts], source="worker")
            await session.execute(select(TradeRecord.id).limit(1))
            return reports
    reports = _run(go())
    assert seen == ["first", "second"] and [r.account_label for r in reports] == ["second"]


def test_one_positions_close_error_never_ends_the_sweep_or_the_square_off(monkeypatch):
    from app.trading import position_monitor as pm
    t = _tenant("sp-sweep@example.com")
    dep = _deploy(t)
    _set_active(dep)
    first, second = _open_trade(t, dep, symbol="SPSWA"), _open_trade(t, dep, symbol="SPSWB")
    real_close = pm.close_position

    async def close(session, trade, *args, **kwargs):
        if trade.id == first:
            await _failed_flush(session, date(2031, 9, 1))                       # the first position's close hits a SQL error
        return await real_close(session, trade, *args, **kwargs)
    monkeypatch.setattr(pm, "close_position", close)
    monkeypatch.setattr(tw, "close_position", close)
    report = _run(_worker(monkeypatch, _FakeBroker(ltp=97.0)).run_cycle(now=OPEN_NOW))              # both below their stop
    assert _trade(first).exit_time is None and _trade(second).exit_time is not None                # the sweep went on
    assert not any("rolled back due to a previous exception" in e for e in report.errors), report.errors

    third = _open_trade(t, dep, symbol="SPSWC")                                  # square-off: the same isolation
    late = datetime(2026, 9, 25, 15, 16, tzinfo=OPEN_NOW.tzinfo)
    _run(_worker(monkeypatch, _FakeBroker(ltp=100.5, intraday_bars=400)).run_cycle(now=late))
    assert _trade(first).exit_time is None and _trade(third).exit_reason == "Intraday square-off"


def test_a_live_exit_order_id_is_committed_before_the_booking(monkeypatch):
    from app.execution import paper_broker
    from app.trading import position_monitor as pm
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("sp-exit-id@example.com")
    dep = _deploy(t)

    async def seed():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="LIVE", strategy_id=STRATEGY, symbol="SPEXIT",
                                exchange="NSE", deployment_id=dep, direction="LONG", entry_price=100.0, quantity=1, stop_loss=98.0,
                                target1=104.0, target2=108.0, entry_time=OPEN_NOW - timedelta(minutes=30))
            session.add(trade)
            await session.commit()
            return trade.id
    trade_id = _run(seed())

    def boom(*args, **kwargs):
        raise RuntimeError("booking failed after the exit order went out")
    monkeypatch.setattr(paper_broker.PaperBroker, "estimate_round_trip_costs", boom)

    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            with pytest.raises(RuntimeError):
                async with guarded(session, keep=(trade,)):
                    await pm.close_position(session, trade, 97.0, "Stop loss", broker=_FakeBroker(ltp=97.0), user_id=t["user_id"])
    _run(go())
    assert (_trade(trade_id).exit_order_id or "").startswith("ORD-")             # the broker took it: recorded for good
