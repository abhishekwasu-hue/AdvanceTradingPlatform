"""Part B5: GET /api/market-data/history - authenticated, point-in-time (as_of hides later rows and later corrections),
adjusted only for equity, says how many quality events fall in the window, and caps the bars per request.
Instants and prices are crafted.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.core import config
from app.db.models import DataQualityEventRecord, MdCandleRecord, MdCorporateActionRecord
from app.market_lake import ingest
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth

KEY, SYM = "NSE:B5TEST", "B5TEST"
T0 = datetime(2000, 1, 3, 4, 0, tzinfo=timezone.utc)


def _t(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _setup():
    async def go():
        async with _session_factory() as session:
            for model, col in ((MdCandleRecord, MdCandleRecord.instrument_key), (DataQualityEventRecord, DataQualityEventRecord.instrument_key)):
                await session.execute(delete(model).where(col == KEY))
            await session.execute(delete(MdCorporateActionRecord).where(MdCorporateActionRecord.symbol == SYM))
            await session.commit()
            for m in (1, 2, 3):
                await ingest.write_candles(session, [ingest.LakeBar(KEY, "1m", _t(m), 100, 100, 100, 100, 10)], "test", ingested_at=_t(m))
            await ingest.write_candles(session, [ingest.LakeBar(KEY, "1m", _t(2), 100, 100, 100, 104, 10)], "test", ingested_at=_t(30))
            session.add(DataQualityEventRecord(kind="spike", instrument_key=KEY, timeframe="1m", ts=_t(2), source="test", detail="crafted"))
            session.add(MdCorporateActionRecord(symbol=SYM, exchange="NSE", ex_date=(T0 + timedelta(days=1)).date(), action="SPLIT",
                                                ratio_new=2, ratio_old=1, version=1, source="test", ingested_at=_t(0)))
            await session.commit()
    asyncio.run(go())


def _params(**kw):
    return {"instrument_key": KEY, "timeframe": "1m", "start": _t(0).isoformat(), "end": _t(3).isoformat(), **kw}


def test_history_is_point_in_time_adjusted_and_honest_about_quality(monkeypatch):
    _setup()
    assert client.get("/api/market-data/history", params=_params()).status_code == 401
    headers = _auth("b5-history@example.com")
    now = client.get("/api/market-data/history", params=_params(), headers=headers).json()
    assert now["bar_label"] == "end" and now["adjusted"] is True and now["quality_events"] == 1
    assert [c["close"] for c in now["candles"]] == [50.0, 52.0, 50.0]                 # split ahead: halved; bar 2 corrected
    before_fix = client.get("/api/market-data/history", params=_params(as_of=_t(10).isoformat()), headers=headers).json()
    assert [c["close"] for c in before_fix["candles"]] == [50.0, 50.0, 50.0]
    raw = client.get("/api/market-data/history", params=_params(adjusted="false"), headers=headers).json()
    assert [c["close"] for c in raw["candles"]] == [100.0, 104.0, 100.0] and raw["adjusted"] is False
    fut = client.get("/api/market-data/history", params=_params(kind="FUT"), headers=headers).json()
    assert [c["close"] for c in fut["candles"]] == [100.0, 104.0, 100.0] and fut["adjusted"] is False
    assert client.get("/api/market-data/history", params=_params(end=_t(0).isoformat()), headers=headers).status_code == 422
    monkeypatch.setattr(config, "LAKE_HISTORY_MAX_BARS", 2)
    assert client.get("/api/market-data/history", params=_params(), headers=headers).status_code == 422
