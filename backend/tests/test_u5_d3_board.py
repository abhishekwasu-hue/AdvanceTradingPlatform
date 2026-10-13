"""U5 D3a: the result board's context (POST /api/screener/board/context) - company reference data (sector, market cap)
and the last daily closes from the shared lake only (never a broker stream, never a correction ingested later), behind
`screener_board_views` (off by default -> 404). Synthetic rows only; nothing here places an order."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db.models import CompanyRecord, MdCandleRecord, MdCorporateActionRecord
from app.market_lake.ingest import trade_date
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
    rows.append(_bar(f"NSE:{a}", 1, 555.0, source="vendor:x", version=3))                          # a later-ranked source, same day
    rows[-1].ts = NOW - timedelta(days=1) + timedelta(minutes=30)                                 # ... a later ts, same IST date
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
            await session.execute(delete(CompanyRecord).where(CompanyRecord.symbol.in_([a, b])))
            await session.execute(delete(MdCorporateActionRecord).where(MdCorporateActionRecord.symbol.in_([a, b])))
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
    assert [p["c"] for p in rows[a]["closes"]] == [100.0, 101.0, 102.0, 103.0, 104.0]          # no stream row, no unknown correction,
    # one bar per IST date: the preferred source (nse_bhav) wins over a vendor bar with a later ts on the same day
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


def _put(rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def test_reference_data_is_shared_or_own_org_only(world):
    """#234: a company row contributed by one organisation (tenant_id set) is that organisation's alone - another org's
    heatmap files the symbol under Unclassified with no market cap; the contributor sees its own row."""
    _, b = world
    head_a, me_a = _owner(f"u5d3-a-{uuid.uuid4().hex[:8]}@example.com")
    head_b, _ = _owner(f"u5d3-b-{uuid.uuid4().hex[:8]}@example.com")
    _put([CompanyRecord(symbol=b, name="B", sector="Pharma", industry="Drugs", market_cap=777.0, cap_category="MID",
                        tenant_id=me_a["tenant_id"])])
    own = client.post(URL, json={"symbols": [b]}, headers=head_a).json()["symbols"][0]
    other = client.post(URL, json={"symbols": [b]}, headers=head_b).json()["symbols"][0]
    assert own["sector"] == "Pharma" and own["market_cap"] == 777.0 and own["industry"] == "Drugs"
    assert other["sector"] == B.UNCLASSIFIED and other["market_cap"] is None and other["industry"] is None and other["cap_category"] is None


def test_closes_adjusted_for_splits_known_now(world):
    """A 1:2 split inside the window halves the closes before its ex-date (app/market_lake/adjust.share_factor), so the
    sparkline and the day's change show no fake crash; a split ingested after now does not apply yet."""
    _, b = world
    key = f"NSE:{b}"
    ex = trade_date(NOW - timedelta(days=1))
    _put([_bar(key, 3, 200.0), _bar(key, 2, 200.0), _bar(key, 1, 101.0),
          MdCorporateActionRecord(symbol=b, exchange="NSE", ex_date=ex, action="SPLIT", ratio_new=2, ratio_old=1, version=1,
                                  source="nse:ca", ingested_at=NOW - timedelta(days=5)),
          MdCorporateActionRecord(symbol=b, exchange="NSE", ex_date=trade_date(NOW - timedelta(days=2)), action="BONUS", ratio_new=2,
                                  ratio_old=1, version=1, source="nse:ca", ingested_at=NOW + timedelta(days=5))])
    headers, _ = _owner(f"u5d3-s-{uuid.uuid4().hex[:8]}@example.com")
    row = client.post(URL, json={"symbols": [b]}, headers=headers).json()["symbols"][0]
    assert [p["c"] for p in row["closes"]] == [100.0, 100.0, 101.0]
    assert row["change_pct"] == pytest.approx(1.0)


def test_up_to_500_symbols_in_one_call(world):
    a, _ = world
    headers, _ = _owner(f"u5d3-n-{uuid.uuid4().hex[:8]}@example.com")
    syms = [a] + [f"N{i}X{uuid.uuid4().hex[:4].upper()}" for i in range(B.MAX_SYMBOLS - 1)]
    r = client.post(URL, json={"symbols": syms}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()["symbols"]
    assert len(out) == B.MAX_SYMBOLS == 500 and out[0]["sector"] == "Energy"
