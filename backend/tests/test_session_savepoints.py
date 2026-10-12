"""ADR-0004: one failed step on the trading worker's shared cycle session must not take the rest of the cycle with it.

On Postgres a failed statement aborts the transaction (InFailedSqlTransaction) and a failed flush leaves the SQLAlchemy
session needing a rollback (PendingRollbackError) - every later statement fails, the next tenants' exits included.
`app.db.savepoint.guarded` puts each advisory step in a SAVEPOINT. A failed flush reproduces the problem on any backend;
the Postgres test (skipped without one) reproduces the aborted transaction itself."""
import asyncio
import os
from datetime import date, timedelta

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
