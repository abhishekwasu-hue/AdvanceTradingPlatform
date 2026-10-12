"""U5 D3a: the result board's context (POST /api/screener/board/context) - company reference data (sector, market cap)
and the last daily closes from the shared lake only (never a broker stream, never a correction ingested later), behind
`screener_board_views` (off by default -> 404). Synthetic rows only; nothing here places an order."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db.models import CompanyRecord, MdCandleRecord
from app.platform import controls
from app.screener import board as B
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z8_tx1_taxonomy import _set_flags

URL = "/api/screener/board/context"
NOW = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _run(coro):
    return asyncio.run(coro)


def _bar(key, days_ago, close, source="nse_bhav:cm", version=1, ingested=None):
    ts = NOW - timedelta(days=days_ago)
    return MdCandleRecord(instrument_key=key, timeframe="day", ts=ts, source=source, version=version, open=close, high=close, low=close, close=close,
                          volume=1, ingested_at=ingested or ts + timedelta(hours=1))


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:6].upper()
    a, b = f"BDA{tag}", f"BDB{tag}"
    rows = [CompanyRecord(symbol=a, name="A", sector="Energy", industry="Oil", market_cap=50000.0, cap_category="LARGE")]
    rows += [_bar(f"NSE:{a}", d, 100.0 + (5 - d)) for d in range(5, 0, -1)]                       # 100 .. 104
    rows.append(_bar(f"NSE:{a}", 1, 999.0, source="stream:upstox", version=9))                     # never read
    rows.append(_bar(f"NSE:{a}", 2, 50.0, version=2, ingested=NOW + timedelta(days=5)))            # a correction not yet known
    _flag_rows = rows

    async def put():
        async with _session_factory() as session:
            session.add_all(_flag_rows)
            await session.commit()
    _run(put())
    _set_flags(screener_v2=True, screener_board_views=True)
    yield a, b
    _set_flags(screener_v2=False, screener_board_views=False)

    async def clean():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_([f"NSE:{a}", f"NSE:{b}"])))
            await session.execute(delete(CompanyRecord).where(CompanyRecord.symbol == a))
            await session.commit()
    _run(clean())


def test_reference_data_and_shared_closes_only(world):
    a, b = world
    headers, _ = _owner(f"u5d3-{uuid.uuid4().hex[:8]}@example.com")
    r = client.post(URL, json={"symbols": [a.lower(), b, a]}, headers=headers)
    assert r.status_code == 200, r.text
    rows = {s["symbol"]: s for s in r.json()["symbols"]}
    assert list(rows) == [a, b]
    assert rows[a]["sector"] == "Energy" and rows[a]["market_cap"] == 50000.0
    assert [p["c"] for p in rows[a]["closes"]] == [100.0, 101.0, 102.0, 103.0, 104.0]          # no stream row, no unknown correction
    assert rows[a]["change_pct"] == pytest.approx(round((104 / 103 - 1) * 100, 2))
    assert rows[b] == {**rows[b], "sector": B.UNCLASSIFIED, "market_cap": None, "closes": [], "change_pct": None}


def test_flag_and_limits():
    headers, _ = _owner(f"u5d3-off-{uuid.uuid4().hex[:8]}@example.com")
    assert client.post(URL, json={"symbols": ["X"]}, headers=headers).status_code == 404
    _set_flags(screener_v2=True, screener_board_views=True)
    try:
        assert client.post(URL, json={"symbols": [f"S{i}" for i in range(B.MAX_SYMBOLS + 1)]}, headers=headers).status_code == 422
        assert client.post(URL, json={"symbols": ["X"]}).status_code == 401
    finally:
        _set_flags(screener_v2=False, screener_board_views=False)
    assert B.FLAG in controls.DEFAULT_OFF_FLAGS and "places no orders" in controls.FEATURE_FLAGS[B.FLAG]
