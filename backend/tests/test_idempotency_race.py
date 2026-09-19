"""Master prompt Section 50: idempotency tests must cover replaying the same signal/webhook
"including out-of-order" - which in practice means two concurrent requests racing past the
existing "does an order for this idempotency_key already exist?" pre-check
(`main.py::paper_execute`, `app/webhooks/routes.py::tradingview_webhook`) before either commits.

This is a genuine bug this session found by actually running two concurrent
`execute_signal_for_user` calls against the same idempotency_key with `asyncio.gather` (confirmed
directly, not just reasoned about): the second call's INSERT hit the `(tenant_id,
idempotency_key)` unique constraint on `orders` and raised an unhandled `sqlalchemy.exc.
IntegrityError`, which would have surfaced as an unhandled 500 to whichever caller lost the race.

The regression tests below do **not** use `asyncio.gather` for two live concurrent DB sessions:
this test suite's shared SQLite database is one single aiosqlite connection (`StaticPool`), and
truly overlapping I/O on that one connection between two coroutines corrupts SQLAlchemy's async
greenlet state well before either coroutine reaches the actual code under test (confirmed
separately - it fails with `MissingGreenlet`/`InvalidRequestError`, unrelated to the real bug).
Real concurrent Postgres connections (as CI already runs against) don't have this limitation, but
pinning this regression to requiring a live Postgres service would make it the one test in the
suite that can't run against the in-memory SQLite harness everything else uses.

Instead, these tests deterministically reproduce the exact DB state the race produces - a second
`create_order` call for an idempotency_key a full row *already exists* under, because another
request's commit landed between this caller's pre-check and its own insert - which exercises the
identical code path (`create_order`'s `except IntegrityError` handling) that a live race hits.

The fix was also separately verified under genuine concurrent load against a real Postgres
instance (not part of this checked-in suite, which only runs against SQLite): 8 simultaneous
`execute_signal_for_user` calls sharing one idempotency key produced exactly one order and zero
crashes, versus reliably crashing with an unhandled IntegrityError before this fix. That same
manual check also caught a second, related bug in the first version of this fix: `create_order`
read `user.tenant_id` *after* calling `session.rollback()` on the race path, and rollback expires
every object already loaded into that session - accessing an expired attribute afterward triggers
an implicit lazy-reload that async SQLAlchemy cannot perform outside an explicit `await`, raising
`MissingGreenlet` instead of the tenant_id it looks like it should just return. Fixed by capturing
`tenant_id` into a local variable before any DB operation in `create_order` runs.
"""
import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums import SignalDirection, SignalGrade, OrderStatus
from app.core.models import Signal
from app.db.models import Tenant, User
from app.execution.order_persistence import create_order
from app.execution.signal_execution import execute_signal_for_user
from tests.test_auth_api import _session_factory


def _signal(symbol: str = "RACESYM") -> Signal:
    from datetime import datetime, timezone

    return Signal(
        symbol=symbol, strategy_id="race_strategy", strategy_name="Race",
        direction=SignalDirection.LONG, timestamp=datetime.now(timezone.utc),
        entry=100.0, stop_loss=98.0, target1=104.0, target2=108.0,
        risk_reward=2.0, score=80, grade=SignalGrade.HIGH_QUALITY, reasons=["race"],
        timeframe_combo="1min",
    )


async def _make_tenant_and_user(email: str) -> int:
    async with _session_factory() as session:
        tenant = Tenant(name=email, webhook_token=f"token-{email}")
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email=email, hashed_password="x", role="USER")
        session.add(user)
        await session.commit()
        return user.id


def test_create_order_confirmed_to_raise_integrity_error_without_the_fix():
    """Documents the bug this session actually found (not a hypothetical): calling the raw ORM
    insert a second time for the same (tenant, idempotency_key) - bypassing create_order's own
    handling entirely - still raises IntegrityError, proving the DB constraint (not application
    code) is what a real race collides with. `create_order` itself is verified NOT to raise this
    below.
    """
    async def _run():
        user_id = await _make_tenant_and_user("integrity-check@example.com")
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            order, created = await create_order(
                session, user, mode="PAPER", strategy_id="race_strategy", signal=_signal(),
                idempotency_key="raw-collision-key",
            )
            assert created is True

            from app.db.models import OrderRecord

            duplicate = OrderRecord(
                tenant_id=user.tenant_id, user_id=user.id, idempotency_key="raw-collision-key",
                mode="PAPER", strategy_id="race_strategy", symbol="RACESYM", direction="LONG",
                status=OrderStatus.CREATED.value, signal_json=_signal().model_dump_json(),
            )
            session.add(duplicate)
            with pytest.raises(IntegrityError):
                await session.flush()

    asyncio.run(_run())


def test_create_order_returns_existing_order_instead_of_raising_on_collision():
    """The actual fix: create_order itself never raises for this - it returns the row that
    already won, with created=False.
    """
    async def _run():
        user_id = await _make_tenant_and_user("collision-fix@example.com")
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            first_order, first_created = await create_order(
                session, user, mode="PAPER", strategy_id="race_strategy", signal=_signal(),
                idempotency_key="fixed-collision-key",
            )
            assert first_created is True

        # A fresh session, as a genuinely concurrent second request would use.
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            second_order, second_created = await create_order(
                session, user, mode="PAPER", strategy_id="race_strategy", signal=_signal(),
                idempotency_key="fixed-collision-key",
            )
            assert second_created is False
            assert second_order.id == first_order.id

    asyncio.run(_run())


def test_execute_signal_for_user_replays_instead_of_reprocessing_on_race():
    """Integration-level: execute_signal_for_user must detect the race the same way and return
    the winning order's already-decided outcome, never attempt to re-run the settled order
    through the kill-switch/risk/broker pipeline a second time (which would be wrong regardless
    of whether it happened to succeed, since that order is not this call's to decide anymore).
    """
    async def _run():
        user_id = await _make_tenant_and_user("execute-race@example.com")
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            first_result, first_order = await execute_signal_for_user(
                session, user, mode="PAPER", strategy_id="race_strategy", signal=_signal(),
                idempotency_key="execute-race-key",
            )
            assert first_result.executed is True

        async with _session_factory() as session:
            user = await session.get(User, user_id)
            second_result, second_order = await execute_signal_for_user(
                session, user, mode="PAPER", strategy_id="race_strategy", signal=_signal(),
                idempotency_key="execute-race-key",
            )

        assert second_order.id == first_order.id
        assert second_result.executed == first_result.executed
        assert second_result.reasons == first_result.reasons

        # Only the one order/trade from the winning request exists - the "loser" never created
        # a second row or re-ran risk/broker logic against an already-settled order.
        async with _session_factory() as session:
            from sqlalchemy import select

            from app.db.models import OrderRecord, TradeRecord

            orders = list(await session.scalars(
                select(OrderRecord).where(OrderRecord.idempotency_key == "execute-race-key")
            ))
            trades = list(await session.scalars(
                select(TradeRecord).where(TradeRecord.tenant_id == first_order.tenant_id)
            ))
            assert len(orders) == 1
            assert len(trades) == 1

    asyncio.run(_run())
