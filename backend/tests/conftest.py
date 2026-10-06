"""Suite-wide test isolation.

The broker circuit breakers (app/brokers/circuit_breaker.py) are process-wide, time-windowed state:
fake brokers in many worker tests raise NotImplementedError for calls they do not model, and those
failures count towards "3 of 5 calls failed in 60 s". Without a reset, whether a later LIVE test
finds the circuit open depends on how fast the tests before it ran. Each test starts closed.

The global cues (app/ai/global_cues.py) fetch free public market data over the internet; the suite
never does - tests that exercise them turn the flag on and serve the responses themselves.

Feature flags and platform-wide (tenant_id NULL) market events live in the one in-memory database every
test module shares. A module that switches `news_feed` on, or seeds a global RBI event dated today, must
not decide what the next module sees: both are reset after every module.
"""
import os

import pytest

os.environ["GLOBAL_CUES_ENABLED"] = "false"

from app.brokers.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def _closed_circuit_breakers():
    reset_all()
    yield


@pytest.fixture(autouse=True, scope="module")
def _platform_state_reset_per_module():
    yield
    import asyncio

    from sqlalchemy import delete

    from app.db.models import MarketEventRecord
    from app.platform import controls
    from tests.test_auth_api import _session_factory

    async def reset():
        async with _session_factory() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {}}, None)
            await session.execute(delete(MarketEventRecord).where(MarketEventRecord.tenant_id.is_(None)))
            await session.commit()
    try:
        asyncio.run(reset())
    except Exception:  # noqa: BLE001 - a module that never touched the database has nothing to reset
        pass

