"""Z6 review fixes: the journal never sits on the trade path, and concurrent writers never collide.

* H1 - a capture hook reads the `discipline_engine` flag through the caller's own connection (cached) and only queues
  its work, so a fill or an exit never waits for a second pool connection: with a two-connection pool, two concurrent
  `persist_trade` calls finish at once (before the fix each waited for `pool_timeout`), the flag OFF takes no second
  connection at all, and a stuck journal job neither delays the trade / the exit nor runs past its timeout.
* M1 - entries and tags are created with INSERT ... ON CONFLICT DO NOTHING (a user's tag with DO UPDATE), and the
  price range is widened atomically, so concurrent refreshes / hooks never raise a duplicate-key error or lose data.

These run on a file SQLite database with a real connection pool (the suite's shared in-memory engine has a single
connection). Set `Z6_TEST_PG_URL` (e.g. postgresql+asyncpg://postgres:postgres@localhost:5432/z6_test) to run the
same tests against PostgreSQL too.
"""
import asyncio
import json
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.enums import SignalDirection
from app.core.models import Trade
from app.db.base import Base
from app.db.models import JournalEntryRecord, JournalTagRecord, Tenant, TradeRecord, User
from app.journal import capture, service
from app.journal import settings as js
from app.platform import controls

POOL_TIMEOUT = 3


@pytest.fixture(autouse=True)
def _forget_flag():
    yield
    capture.reset_flag_cache()   # the cached flag of these private databases must not leak into the shared test engine


@pytest.fixture(params=["sqlite", "postgresql"])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'journal.db'}"
    url = os.environ.get("Z6_TEST_PG_URL")
    if not url:
        pytest.skip("Z6_TEST_PG_URL not set")
    return url


async def _setup(url: str, *, flag_on: bool, pool_size: int = 2):
    engine = create_async_engine(url, poolclass=AsyncAdaptedQueuePool, pool_size=pool_size, max_overflow=0, pool_timeout=POOL_TIMEOUT)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        tenant = Tenant(name="z6 pool")
        s.add(tenant)
        await s.flush()
        user = User(tenant_id=tenant.id, email=f"z6-pool-{uuid.uuid4().hex[:10]}@example.com", hashed_password="x")
        s.add(user)
        current = await controls._get(s, controls.KEY_FEATURE_FLAGS)
        flags = dict(current.get("flags") or {})
        flags["discipline_engine"] = {"on": flag_on, "tenants": []}
        await controls._set(s, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
        await s.commit()
    capture.reset_flag_cache()
    return engine, factory, user


def _trade(**kw):
    base = dict(symbol="NIFTY", strategy_id="s", direction=SignalDirection.LONG, entry_time=datetime.now(timezone.utc), entry_price=100.0,
                quantity=1, stop_loss=98.0, target1=104.0)
    base.update(kw)
    return Trade(**base)


# --- H1: never on the trade path ---------------------------------------------------------------------------------------

def test_concurrent_fills_never_wait_for_a_second_connection(db_url):
    from app.trading.persistence import persist_trade

    async def go():
        engine, factory, user = await _setup(db_url, flag_on=True, pool_size=2)
        try:
            async def one():
                async with factory() as s:
                    t0 = time.perf_counter()
                    rec = await persist_trade(s, user, _trade())
                    return rec.id, time.perf_counter() - t0
            results = await asyncio.gather(one(), one())
            # Before the fix each hook opened its own session while the caller still held its connection: with a
            # two-connection pool both fills waited the whole pool_timeout. Now they never wait on the journal.
            assert all(seconds < POOL_TIMEOUT - 1 for _, seconds in results), results
            await capture.drain()
            async with factory() as s:
                ids = [trade_id for trade_id, _ in results]
                entries = list(await s.scalars(select(JournalEntryRecord).where(JournalEntryRecord.trade_id.in_(ids))))
                assert sorted(e.trade_id for e in entries) == sorted(ids)      # captured all the same, off the path
        finally:
            await engine.dispose()
    asyncio.run(go())


def test_flag_off_takes_no_second_connection_and_queues_nothing(db_url, monkeypatch):
    from app.trading.persistence import persist_trade
    from app.trading.position_monitor import close_position
    queued = []
    monkeypatch.setattr(capture, "_submit", lambda *a, **k: queued.append(a))

    async def go():
        engine, factory, user = await _setup(db_url, flag_on=False, pool_size=2)
        out = {"now": 0, "peak": 0}

        def checkout(*_):
            out["now"] += 1
            out["peak"] = max(out["peak"], out["now"])

        def checkin(*_):
            out["now"] -= 1
        event.listen(engine.sync_engine.pool, "checkout", checkout)
        event.listen(engine.sync_engine.pool, "checkin", checkin)
        try:
            async with factory() as s:
                rec = await persist_trade(s, user, _trade())
                await capture.on_marks(s, user.tenant_id, {rec.id: 101.0})
                await capture.on_stop_change(s, rec, 98.0, 99.0)
                outcome = await close_position(s, rec, 101.0, "Exit now")
                assert outcome.closed
            assert out["peak"] == 1, out                                        # only the caller's own connection
            assert queued == []
        finally:
            await engine.dispose()
    asyncio.run(go())


def test_a_stuck_journal_job_never_delays_a_fill_or_an_exit(db_url, monkeypatch):
    from app.trading.persistence import persist_trade
    from app.trading.position_monitor import close_position
    monkeypatch.setattr(capture, "HOOK_TIMEOUT_SECONDS", 0.3)

    async def stuck(*a, **k):
        await asyncio.sleep(30)
    monkeypatch.setattr(capture, "ensure_entries", stuck)

    async def go():
        engine, factory, user = await _setup(db_url, flag_on=True, pool_size=1)   # one connection: any wait would show
        try:
            async with factory() as s:
                t0 = time.perf_counter()
                rec = await persist_trade(s, user, _trade())
                outcome = await close_position(s, rec, 99.0, "Emergency Exit")
                assert outcome.closed and time.perf_counter() - t0 < 1.0
            t1 = time.perf_counter()
            await capture.drain()
            assert time.perf_counter() - t1 < 3.0                               # each stuck job was cancelled at 0.3 s
            async with factory() as s:
                row = await s.get(TradeRecord, rec.id)
                assert row.exit_reason == "Emergency Exit" and row.exit_time is not None
        finally:
            await engine.dispose()
    asyncio.run(go())


# --- M1: concurrent writers --------------------------------------------------------------------------------------------

def test_concurrent_refreshes_never_collide(db_url):
    async def go():
        engine, factory, user = await _setup(db_url, flag_on=True, pool_size=5)
        try:
            _, start, _ = service.week_bounds(None)
            async with factory() as s:
                for i in range(3):
                    t0 = start + timedelta(hours=4 + i)
                    s.add(TradeRecord(tenant_id=user.tenant_id, user_id=user.id, mode="PAPER", strategy_id="s", symbol="NIFTY", direction="LONG",
                                      entry_time=t0, entry_price=100.0, quantity=10 * (i + 1), stop_loss=0.0, initial_stop_loss=0.0, target1=104.0,
                                      exit_time=t0 + timedelta(minutes=10), exit_price=98.0, exit_reason="Closed by hand", pnl=-20.0))
                await s.commit()

            async def one():
                async with factory() as s:
                    me = await s.get(User, user.id)
                    try:
                        await service.refresh_week(s, me)
                        return "ok"
                    except Exception as exc:  # noqa: BLE001 - the assertion below prints it
                        return f"{type(exc).__name__}: {exc}"
            for _ in range(5):
                async with factory() as s:
                    for model in (JournalTagRecord, JournalEntryRecord):
                        for row in await s.scalars(select(model).where(model.user_id == user.id)):
                            await s.delete(row)
                    await s.commit()
                assert await asyncio.gather(one(), one(), one()) == ["ok", "ok", "ok"]
            async with factory() as s:
                entries = list(await s.scalars(select(JournalEntryRecord).where(JournalEntryRecord.user_id == user.id)))
                tags = list(await s.scalars(select(JournalTagRecord).where(JournalTagRecord.user_id == user.id)))
                assert len(entries) == 3 and len({(t.trade_id, t.tag) for t in tags}) == len(tags) and tags
        finally:
            await engine.dispose()
    asyncio.run(go())


def test_upserts_keep_greeks_source_extremes_and_decisions(db_url):
    """The outcomes of a lost race, made deterministic: the entry already exists (a refresh created it first) when the
    entry hook runs; a stale ORM copy is written after an atomic widen; a user tag lands on an AUTO tag."""
    async def go():
        engine, factory, user = await _setup(db_url, flag_on=True, pool_size=3)
        try:
            async with factory() as s:
                trade = TradeRecord(tenant_id=user.tenant_id, user_id=user.id, mode="PAPER", strategy_id="s", symbol="NIFTY 24500 CE 29 OCT 26",
                                    direction="LONG", entry_time=datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc), entry_price=150.0, quantity=75,
                                    stop_loss=100.0, target1=200.0, instrument_kind="OPTION", expiry=date(2026, 10, 29))
                s.add(trade)
                await s.commit()
                await capture.ensure_entries(s, [trade])                        # the "refresh" got there first
                await s.commit()
                await capture.on_entry(s, trade, source="WEBHOOK", underlying_price=24480.0)
            await capture.drain()
            async with factory() as s:
                entry = await s.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == trade.id))
                assert entry.source == "WEBHOOK" and entry.iv_entry and json.loads(entry.greeks_entry_json)["right"] == "CE"

            # a stale copy (loaded before the widen) writing its trade-derived columns never undoes the widen
            async with factory() as stale, factory() as other:
                old = await stale.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == trade.id))
                await capture.widen(other, trade.id, [171.5])
                await capture.widen(other, trade.id, [131.25])
                await other.commit()
                row = await stale.get(TradeRecord, trade.id)
                capture.apply_trade(old, row, source="WEBHOOK")
                old.fills_json = "[]"                                           # force an UPDATE from the stale copy
                await stale.commit()
            async with factory() as s:
                entry = await s.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == trade.id))
                assert float(entry.high_price) == 171.5 and float(entry.low_price) == 131.25

            # a duplicate entry insert, a duplicate AUTO tag and a user tag over an AUTO tag are all quiet
            async with factory() as s:
                me = await s.get(User, user.id)
                row = await s.get(TradeRecord, trade.id)
                await capture.ensure_entries(s, [row])
                det = service.Detection(trade.id, "no_stop", {"why": "test"})
                await service._sync_tags(s, me, [trade.id], [det])
                await s.commit()
                await service._sync_tags(s, me, [trade.id], [det])
                await s.commit()
                confirmed = await service.add_user_tag(s, me, row, "no_stop", "mine")
                assert confirmed.status == "CONFIRMED" and confirmed.decided_at is not None
                again = await service.add_user_tag(s, me, row, "no_stop", None)
                assert again.id == confirmed.id
                assert await js.save(s, me, {"fomo_chase_r": 0.5}) and await js.save(s, me, {"fomo_chase_r": 0.4})
                assert (await js.load(s, me)).fomo_chase_r == 0.4
        finally:
            await engine.dispose()
    asyncio.run(go())


def test_iv_at_exit_is_written_from_the_underlying_quote(db_url):
    from app.trading.position_monitor import close_position

    async def go():
        engine, factory, user = await _setup(db_url, flag_on=True, pool_size=2)
        try:
            async with factory() as s:
                trade = TradeRecord(tenant_id=user.tenant_id, user_id=user.id, mode="PAPER", strategy_id="s", symbol="NIFTY 24500 CE 29 OCT 26",
                                    direction="LONG", entry_time=datetime.now(timezone.utc) - timedelta(minutes=30), entry_price=150.0, quantity=75,
                                    stop_loss=100.0, target1=200.0, instrument_kind="OPTION", expiry=date.today() + timedelta(days=14))
                s.add(trade)
                await s.commit()
                outcome = await close_position(s, trade, 160.0, "Target 1", underlying_price=24510.0)
                assert outcome.closed
            await capture.drain()
            async with factory() as s:
                entry = await s.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == trade.id))
                assert entry is not None and entry.iv_exit is not None and 0 < entry.iv_exit < 3
        finally:
            await engine.dispose()
    asyncio.run(go())
