"""Z3 review M3 on a real Postgres: two closes of one position racing in parallel (separate connections) book exactly
once. Runs only when ATP_PG_RACE_URL names a scratch Postgres database at the Alembic head (postgresql+asyncpg://...,
`alembic upgrade head` first); the test adds one organisation, user and position there. The in-memory suite covers the same claim sequentially
(tests/test_z3_approval_entries.py::test_two_closes_of_one_position_book_once)."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("ATP_PG_RACE_URL", "")

pytestmark = pytest.mark.skipif(not URL.startswith("postgresql"), reason="set ATP_PG_RACE_URL to a scratch Postgres database")


def test_parallel_closes_book_once_on_postgres():
    from app.db.models import NotificationRecord, Tenant, TradeRecord, User
    from app.trading.position_monitor import close_position

    async def go():
        engine = create_async_engine(URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                tenant = Tenant(name=f"race-{uuid.uuid4().hex[:6]}")
                session.add(tenant)
                await session.flush()
                user = User(tenant_id=tenant.id, email=f"race-{uuid.uuid4().hex[:8]}@example.com", hashed_password="!", role="OWNER")
                session.add(user)
                await session.flush()
                trade = TradeRecord(tenant_id=tenant.id, user_id=user.id, mode="PAPER", symbol="RELIANCE", strategy_id="s", direction="LONG",
                                    entry_time=datetime.now(timezone.utc) - timedelta(minutes=5), entry_price=100.0, quantity=10.0,
                                    stop_loss=95.0, target1=110.0, exchange="NSE")
                session.add(trade)
                await session.commit()
                trade_id = trade.id

            async def close(price, reason):
                async with factory() as session:
                    row = await session.get(TradeRecord, trade_id)
                    return await close_position(session, row, price, reason)
            outcomes = await asyncio.gather(close(103.0, "Target 1"), close(97.0, "Exit now (web, live price)"))
            async with factory() as session:
                notes = await session.scalar(select(func.count()).select_from(NotificationRecord).where(
                    NotificationRecord.related_trade_id == trade_id, NotificationRecord.event_type == "EXIT"))
                row = await session.get(TradeRecord, trade_id)
            return outcomes, notes, row
        finally:
            await engine.dispose()

    outcomes, notes, row = asyncio.run(go())
    assert sorted(o.closed for o in outcomes) == [False, True]
    winner = next(o for o in outcomes if o.closed)
    assert notes == 1 and row.exit_price == winner.exit_price and row.exit_reason == winner.exit_reason
