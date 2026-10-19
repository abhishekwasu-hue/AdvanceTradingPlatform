"""N1 X2 on a real Postgres: a failure inside the dispatcher's `_paper` never leaves the worker's shared session
aborted. On Postgres a failed statement aborts the whole transaction (InFailedSqlTransaction for everything after it),
so the checks must run in a savepoint and a pipeline failure must be rolled back before the intent is recorded.

Runs only when ATP_PG_RACE_URL names a scratch Postgres database at the Alembic heads (postgresql+asyncpg://...,
`alembic upgrade heads` first), like tests/test_z3_pg_close_race.py. The in-memory suite covers the same claim on SQLite
(tests/test_n1_x2_auto_paper.py::test_a_failure_inside_the_checks_fails_the_intent_and_leaves_the_session_usable)."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("ATP_PG_RACE_URL", "")

pytestmark = pytest.mark.skipif(not URL.startswith("postgresql"), reason="set ATP_PG_RACE_URL to a scratch Postgres database")


def _signal(now):
    from app.core.enums import SignalDirection, SignalGrade
    from app.core.models import Signal
    return Signal(symbol="PGDEMO", strategy_id="scan_then:1", strategy_name="Scan", direction=SignalDirection.LONG, timestamp=now, entry=100.0,
                  stop_loss=98.0, target1=106.0, risk_reward=3.0, score=60, grade=SignalGrade.VALID, reasons=["pg"], timeframe_combo="5m")


async def _seed(factory, now):
    from app.db.models import ExecutionIntentRecord, Tenant, User
    async with factory() as session:
        tenant = Tenant(name=f"x2pg-{uuid.uuid4().hex[:6]}")
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email=f"x2pg-{uuid.uuid4().hex[:8]}@example.com", hashed_password="!", role="OWNER")
        session.add(user)
        await session.flush()
        intent = ExecutionIntentRecord(tenant_id=tenant.id, user_id=user.id, source="scan", source_ref="rule:1:symbol:PGDEMO",
                                       idempotency_key=f"scan:pg:{uuid.uuid4().hex}", requested_mode="AUTO_PAPER", status="CLAIMED",
                                       symbol="PGDEMO", exchange="NSE", direction="LONG", strategy_id="scan_then:1",
                                       signal_json=_signal(now).model_dump_json(), evidence_json="{}", attempts=1, claimed_at=now,
                                       expires_at=now + timedelta(minutes=5))
        session.add(intent)
        await session.commit()
        return tenant.id, user.id, intent.id


@pytest.mark.parametrize("where", ["checks", "pipeline"])
def test_a_failure_inside_paper_never_leaves_the_session_aborted(monkeypatch, where):
    from app.db.models import ExecutionIntentRecord, NotificationRecord, User
    from app.execution import signal_execution
    from app.execution.core import dispatcher, intents
    from app.screener.then import auto_paper

    seen = []

    async def bad_sql(session):
        seen.append(session.in_nested_transaction())
        await session.execute(text("SELECT * FROM no_such_table_x2pg"))          # aborts the transaction on Postgres

    async def open_window(session, now, exchange="NSE"):
        return None                                                            # the session clock is not what is tested here
    monkeypatch.setattr(auto_paper, "window_reason", open_window)
    if where == "checks":
        async def broken_limit(session, intent, now):
            await bad_sql(session)
        monkeypatch.setattr(auto_paper, "limit_reason", broken_limit)
    else:
        async def no_limit(session, intent, now):
            return None
        monkeypatch.setattr(auto_paper, "limit_reason", no_limit)

        async def broken_pipeline(session, user, **kw):
            await bad_sql(session)
        monkeypatch.setattr(signal_execution, "execute_signal_for_user", broken_pipeline)
    from app.ai import worker_bridge

    async def lookup_factory(session, user):
        async def lookup(symbol, exchange="NSE"):
            return 100.0
        return lookup
    monkeypatch.setattr(worker_bridge, "price_lookup_for_user", lookup_factory)

    async def go():
        engine = create_async_engine(URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        try:
            tenant_id, user_id, intent_id = await _seed(factory, now)
            async with factory() as session:                                  # one session, as the worker's cycle has
                row = await session.get(ExecutionIntentRecord, intent_id)
                owner = await session.get(User, user_id)
                status = await dispatcher._paper(session, row, owner, _signal(now), now)
                # The same session goes on working: a read, then a write committed after the failure.
                one = (await session.execute(text("SELECT 1"))).scalar()
                session.add(NotificationRecord(tenant_id=tenant_id, user_id=user_id, event_type="SYSTEM", title="after", message="x2pg",
                                               severity="INFO"))
                await session.commit()
            async with factory() as session:
                saved = await session.get(ExecutionIntentRecord, intent_id)
                after = await session.scalar(select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id,
                                                                              NotificationRecord.title == "after"))
            return status, one, saved, after
        finally:
            await engine.dispose()

    status, one, saved, after = asyncio.run(go())
    assert seen and one == 1 and after is not None
    assert status == intents.FAILED and saved.status == intents.FAILED
    assert ("checks could not run" if where == "checks" else "placement failed") in saved.reason
    if where == "checks":
        assert seen == [True]                                                  # inside the savepoint
