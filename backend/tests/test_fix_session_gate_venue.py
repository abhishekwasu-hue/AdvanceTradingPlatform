"""Owner 29 Oct (deploy refused at 16:37 IST): the deploy's session gate counts a position only while its OWN venue is
open. Crypto is always open, so "any session open + any position" refused every deploy while one NSE position was
held after 15:30. An MCX position during MCX hours still refuses."""
import asyncio
import importlib.util
import os
import uuid
from datetime import datetime, timezone

import pytest

from app.db.models import TradeRecord
from app.market_data import calendar
from app.market_data.calendar import SessionStatus
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import _tenant

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "session_check.py")


def _module():
    spec = importlib.util.spec_from_file_location("session_check_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _open(t, exchange):
    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id="manual",
                                    symbol=f"X{uuid.uuid4().hex[:6].upper()}", exchange=exchange, direction="LONG", entry_price=100.0,
                                    quantity=1, stop_loss=90.0, target1=110.0, target2=120.0, entry_time=datetime.now(timezone.utc)))
            await session.commit()
    asyncio.run(go())


@pytest.fixture
def evening(monkeypatch):
    """16:37 IST on a weekday: NSE closed; MCX, CDS and crypto open."""
    async def statuses(session, now=None):
        return {"NSE": SessionStatus(False, "closed", None), "MCX": SessionStatus(True, "open", None),
                "CDS": SessionStatus(True, "open", None), "CRYPTO": SessionStatus(True, "open", None)}
    monkeypatch.setattr(calendar, "all_session_statuses", statuses)
    from app.db import session as db_session
    monkeypatch.setattr(db_session, "_session_factory", _session_factory)    # the script reads the app's own factory


def test_an_nse_position_after_its_close_does_not_block_and_an_mcx_one_does(evening):
    mod = _module()
    before = asyncio.run(mod.collect(None))
    t = _tenant(f"gate-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    _open(t, "NFO")
    out = asyncio.run(mod.collect(None))
    assert out["open_sessions"] == ["CDS", "CRYPTO", "MCX"]
    assert out["open_positions"] == before["open_positions"]            # the NSE option is not watched now
    assert out["open_positions_all"] == before["open_positions_all"] + 1
    _open(t, "MCX")
    out = asyncio.run(mod.collect(None))
    assert out["open_positions"] == before["open_positions"] + 1        # MCX is open: this one still refuses a restart
