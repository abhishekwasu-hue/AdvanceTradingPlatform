"""Suite-wide test isolation.

The broker circuit breakers (app/brokers/circuit_breaker.py) are process-wide, time-windowed state:
fake brokers in many worker tests raise NotImplementedError for calls they do not model, and those
failures count towards "3 of 5 calls failed in 60 s". Without a reset, whether a later LIVE test
finds the circuit open depends on how fast the tests before it ran. Each test starts closed.

The global cues (app/ai/global_cues.py) fetch free public market data over the internet; the suite
never does - tests that exercise them turn the flag on and serve the responses themselves.

Feature flags and platform-wide (tenant_id NULL) market events live in the one in-memory database every
test module shares. A module that switches `news_feed` on, or seeds a global RBI event dated today, must
not decide what the next module sees: both are reset after every module. So are positions a module left open - the
worker's exits sweep covers every open position, with or without a deployment.
"""
import os

import pytest

os.environ["GLOBAL_CUES_ENABLED"] = "false"

from app.brokers.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def _closed_circuit_breakers():
    reset_all()
    from app.platform.routes import forget_status
    forget_status()                     # LD-7: the public status is cached for a few seconds per process; each test reads afresh
    yield


@pytest.fixture(autouse=True, scope="module")
def _platform_state_reset_per_module():
    yield
    import asyncio

    from datetime import datetime, timezone

    from sqlalchemy import delete, update

    from app.db.models import MarketEventRecord, TradeRecord
    from app.journal import capture
    from app.platform import controls
    from tests.test_auth_api import _session_factory

    async def reset():
        async with _session_factory() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {}}, None)
            await session.execute(delete(MarketEventRecord).where(MarketEventRecord.tenant_id.is_(None)))
            # The worker sweeps every open position, deployment or not (ADR-0004): a position a module left open would be
            # priced and closed by the next module's worker cycles. Each module starts with none open.
            await session.execute(update(TradeRecord).where(TradeRecord.exit_time.is_(None))
                                  .values(exit_time=datetime.now(timezone.utc), exit_price=TradeRecord.entry_price, pnl=0.0,
                                          exit_reason="test isolation: left open by an earlier module"))
            await session.commit()
    try:
        asyncio.run(reset())
    except Exception:  # noqa: BLE001 - a module that never touched the database has nothing to reset
        pass
    capture.reset_flag_cache()   # Z6: the journal hooks cache the flags they just saw reset
    from app.platform import phase0_lock
    phase0_lock.reset_cache()    # Z10-c: the broker chokepoint reads the lock state the last flag read left


@pytest.fixture
def no_open_positions_from_earlier_tests():
    """For modules whose worker tests count what one cycle closed: the exits sweep covers every open position, deployment
    or not (ADR-0004), so positions an earlier test of the same module left open are closed before this test starts."""
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.db.models import TradeRecord
    from tests.test_auth_api import _session_factory

    async def close_leftovers():
        async with _session_factory() as session:
            await session.execute(update(TradeRecord).where(TradeRecord.exit_time.is_(None))
                                  .values(exit_time=datetime.now(timezone.utc), exit_price=TradeRecord.entry_price, pnl=0.0,
                                          exit_reason="test isolation: left open by an earlier test"))
            await session.commit()
    asyncio.run(close_leftovers())
    yield

