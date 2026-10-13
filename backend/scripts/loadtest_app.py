"""Z10-d: the test app that `scripts/loadtest.py --in-process` drives (CI's 20-user run).

The real FastAPI app with:
* a throwaway SQLite database file (WAL, so readers do not wait for a writer) - never a deployed database;
* the per-IP auth rate limits and the AI rate limit off, as in the test suite (every in-process user shares one fake
  IP; the limiter itself is tested in tests/test_rate_limiting.py) - the run measures the app, not the limiter;
* a synthetic broker session for charts and screener runs: seeded random-walk bars, no network, no broker;
* `screener_v2` and `paper_account` on, so the screener and PAPER account scenarios run; every other flag at its default.

It writes nothing outside its temporary directory and holds no secret.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import zlib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))                        # the backend package (app.*)


async def build() -> Any:
    folder = tempfile.mkdtemp(prefix="atp-loadtest-")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{folder}/loadtest.db"       # before the app (and its engine) is imported
    os.environ.setdefault("GLOBAL_CUES_ENABLED", "false")
    from sqlalchemy import text

    from app.ai.rate_limit import ai_rate_limit
    from app.auth.routes import forgot_rate_limit, login_rate_limit, mfa_rate_limit, refresh_rate_limit, register_rate_limit
    from app.db import session as db_session
    from app.main import app

    async with db_session.get_engine().begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
    await db_session.init_models()
    for limiter in (register_rate_limit, login_rate_limit, refresh_rate_limit, forgot_rate_limit, mfa_rate_limit, ai_rate_limit):
        app.dependency_overrides[limiter] = lambda: None
    _synthetic_broker()
    return app


@lru_cache(maxsize=512)
def _bars(symbol: str, interval: str) -> tuple:
    from app.core.models import OHLCVBar
    rng = np.random.default_rng(zlib.crc32(f"{symbol}|{interval}".encode()))
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if interval == "day":
        stamps = [now - timedelta(days=d) for d in range(260, 0, -1)]
        vol = 0.015
    else:
        step = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}.get(interval, 1)
        stamps = [now - timedelta(minutes=step * k) for k in range(5 * 375 // step, 0, -1)]
        vol = 0.0015
    close = 500 * np.exp(np.cumsum(rng.normal(0, vol, len(stamps))))
    open_ = np.r_[close[0], close[:-1]]
    return tuple(OHLCVBar(timestamp=t, open=float(o), high=float(max(o, c) * 1.001), low=float(min(o, c) * 0.999), close=float(c), volume=1000.0)
                 for t, o, c in zip(stamps, open_, close))


def _synthetic_broker() -> None:
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session: Any, tenant_id: int, broker: Optional[str], label: str) -> Any:
        return types.SimpleNamespace(id=tenant_id, broker_name="synthetic", account_label="primary")  # one credential per organisation

    async def candles(self: Any, symbol: str, exchange: str = "NSE", interval: str = "1min", now: Any = None, fresh: bool = False) -> List[Any]:
        return list(_bars(symbol, interval))

    candles_routes._pick_record = pick                       # type: ignore[assignment]
    candles_routes.build_adapter = lambda record: object()   # type: ignore[assignment]
    token_lifecycle.build_adapter = lambda record: object()  # type: ignore[assignment]
    service.MarketDataService.get_candles = candles          # type: ignore[method-assign]


async def prepare(emails: List[str]) -> None:
    """The screener and the PAPER account on for everyone; the registered organisations on the plan the screener needs."""
    from sqlalchemy import select

    from app.db.models import Tenant, User
    from app.db.session import _session_factory
    from app.platform import controls

    async with _session_factory() as session:
        current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
        flags = dict(current.get("flags") or {})
        flags["screener_v2"] = {"on": True, "tenants": []}
        flags["paper_account"] = {"on": True, "tenants": []}
        await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
        tenant_ids = [u.tenant_id for u in await session.scalars(select(User).where(User.email.in_(emails)))]
        for tenant in await session.scalars(select(Tenant).where(Tenant.id.in_(tenant_ids))):
            tenant.plan = "pro"
        await session.commit()
