"""S1d (ADR-0021): the screener API - flag, validation (text and builder tree), saved screens (tenant-scoped, validated
before save, archived not deleted), runs on server bars only (no session -> 409) with a stored, reproducible run."""
import asyncio
import types

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.core.models import OHLCVBar
from app.db.models import ScreenRunRecord
from app.screener import nodes as n, parse
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def _flag(on):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["screener_v2"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def flag_on():
    _flag(True)
    yield
    _flag(False)


def test_the_api_is_flagged_off_by_default():
    headers, _ = _owner("s1d-off@example.com")
    r = client.post("/api/screener/validate", json={"source": "close > 1"}, headers=headers)
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_v2"


def test_validate_text_and_builder_tree(flag_on):
    headers, _ = _owner("s1d-validate@example.com")
    ok = client.post("/api/screener/validate", json={"source": "close   >  sma(close,20)", "base_tf": "1d"}, headers=headers).json()
    assert not ok["ok"] and ok["problems"][0]["message"].startswith("unknown function 'sma'")
    good = client.post("/api/screener/validate", json={"source": "close > SMA(close, 20) and RSI(14)@1w > 50"}, headers=headers).json()
    assert good["ok"] and good["text"] == "close > SMA(close, 20) AND RSI(14)@1w > 50" and good["version"] == "screenql/1"
    assert good["plan"]["lookback"] == {"1d": 20, "1w": 14}
    tree = client.post("/api/screener/validate", json={"source": good["ast"]}, headers=headers).json()
    assert tree["ok"] and tree["text"] == good["text"]
    bad = client.post("/api/screener/validate", json={"source": "close > > 1"}, headers=headers).json()
    assert not bad["ok"] and bad["problems"][0]["pos"] == 8 and bad["ast"] is None
    reg = client.get("/api/screener/registry", headers=headers).json()
    assert reg["version"] == "screenql/1" and reg["entries"]["CrossAbove"]["kind"] == "filter"


def test_saved_screens_are_validated_tenant_scoped_and_archived(flag_on):
    headers, _ = _owner("s1d-screens@example.com")
    other, _ = _owner("s1d-screens-other@example.com")
    refused = client.post("/api/screener/screens", json={"name": "x", "source": "close > RSI(14)"}, headers=headers)
    assert refused.status_code == 422 and "compares price with index" in str(refused.json())
    made = client.post("/api/screener/screens", json={"name": "Above SMA", "source": "close > SMA(close, $n)", "params": {"n": 20}}, headers=headers)
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    assert made.json()["text"] == "close > SMA(close, $n)" and made.json()["params"] == {"n": 20}
    assert client.get("/api/screener/screens", headers=other).json() == []
    assert client.put(f"/api/screener/screens/{sid}", json={"name": "y", "source": "close > 1"}, headers=other).status_code == 404
    upd = client.put(f"/api/screener/screens/{sid}", json={"name": "Above SMA 50", "source": "close > SMA(close, 50)"}, headers=headers).json()
    assert upd["text"] == "close > SMA(close, 50)"
    client.post(f"/api/screener/screens/{sid}/archive", headers=headers)
    assert client.get("/api/screener/screens", headers=headers).json() == []
    assert client.get("/api/screener/screens?include_archived=true", headers=headers).json()[0]["archived"] is True


def test_a_run_needs_a_broker_session(flag_on):
    headers, _ = _owner("s1d-nobroker@example.com")
    r = client.post("/api/screener/run", json={"source": "close > 1", "symbols": ["AAA"]}, headers=headers)
    assert r.status_code == 409 and "broker" in r.json()["detail"].lower()
    assert client.post("/api/screener/run", json={"source": "close > 1", "symbols": ["A"] * 51}, headers=headers).status_code == 422


def _daily_bars(start_price, step, days=60):
    idx = pd.date_range("2026-01-01", periods=days, freq="1D", tz="UTC")
    closes = start_price + np.arange(days) * step
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=c - 0.5, high=c + 1, low=c - 1, close=float(c), volume=1000.0) for t, c in zip(idx, closes)]


def test_a_run_on_server_bars_is_stored_and_reproducible(flag_on, monkeypatch):
    headers, me = _owner("s1d-run@example.com")
    other, _ = _owner("s1d-run-other@example.com")
    from app.market_data import candles_routes, service
    from app.brokers import token_lifecycle

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")
    asked = []

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        asked.append((symbol, exchange, interval))
        if symbol == "BROKEN":
            raise RuntimeError("broker said no")
        return _daily_bars(100, 1.0 if symbol == "UP" else -1.0)
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)

    made = client.post("/api/screener/screens", json={"name": "trend", "source": "close > SMA(close, 20) AND PctChange(close, 5) > 0"}, headers=headers).json()
    out = client.post("/api/screener/run", json={"screen_id": made["id"], "symbols": ["up", "DOWN", "BROKEN", "UP"]}, headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["matched"] == ["UP"] and body["data_source"] == "broker:fake" and body["scanned"] == 3
    assert {r["symbol"]: r["matched"] for r in body["results"]} == {"UP": True, "DOWN": False, "BROKEN": False}
    assert "broker said no" in next(r["reason"] for r in body["results"] if r["symbol"] == "BROKEN")
    assert all(a[2] == "day" for a in asked) and "recommendation" in body["disclaimer"]
    stored = client.get(f"/api/screener/runs/{body['run_id']}", headers=headers).json()
    assert stored["matched"] == 1 and stored["universe"] == ["UP", "DOWN", "BROKEN"] and len(stored["ast_sha256"]) == 64
    assert client.get(f"/api/screener/runs/{body['run_id']}", headers=other).status_code == 404

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(ScreenRunRecord).where(ScreenRunRecord.tenant_id == me["tenant_id"])))
    assert len(_run(rows())) == 1
    weekly = client.post("/api/screener/run", json={"source": "close > close[1]", "base_tf": "1w", "symbols": ["UP"]}, headers=headers).json()
    assert weekly["matched"] == ["UP"]                                                            # weekly bars resampled from server daily bars
    assert parse(body["text"]) == parse(made["text"]) and n.VERSION == stored["version"]


def test_a_run_never_decides_on_the_bar_still_forming(flag_on, monkeypatch):
    headers, _ = _owner("s1d-forming@example.com")
    from datetime import datetime, timedelta, timezone

    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service
    from app.screener.runtime import closed_only

    t_now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = t_now - timedelta(minutes=5 * 30)                                   # 30 closed 5-minute bars, then one forming

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        bars = [OHLCVBar(timestamp=start + timedelta(minutes=5 * i), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0) for i in range(30)]
        return bars + [OHLCVBar(timestamp=t_now, open=100.0, high=5000.0, low=99.0, close=5000.0, volume=10.0)]   # the forming bar
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)
    out = client.post("/api/screener/run", json={"source": "close > 1000", "base_tf": "5m", "symbols": ["TCS"]}, headers=headers).json()
    assert out["matched"] == [] and out["results"] == [{"symbol": "TCS", "matched": False, "reason": None}]

    idx = pd.DatetimeIndex([pd.Timestamp(start), pd.Timestamp(t_now)])
    frame = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    assert list(closed_only(frame, "5m", t_now).index) == [pd.Timestamp(start)]
    assert len(closed_only(frame, "5m", t_now + timedelta(minutes=5))) == 2
