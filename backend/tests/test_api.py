from fastapi.testclient import TestClient

from app.main import app
from tests.utils import decline_then_rally, make_series

client = TestClient(app)


def test_list_strategies_returns_seven_inbuilt_strategies():
    response = client.get("/api/strategies")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 7
    ids = {s["id"] for s in data}
    assert "ema_rsi_scalper_1m" in ids
    assert "mtf_1m_5m_trend_pullback" in ids


def test_generate_signal_endpoint():
    df = make_series(decline_then_rally(decline_len=40, rally_len=20))
    candles = [
        {
            "timestamp": ts.isoformat(),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
        }
        for ts, row in df.iterrows()
    ]
    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/signal",
        json={"symbol": "TESTSYM", "candles": {"1min": candles}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["direction"] in ("LONG", "SHORT", "NO_TRADE")


def test_unknown_strategy_returns_404():
    response = client.get("/api/strategies/does-not-exist")
    assert response.status_code == 404


def test_health_endpoint():
    response = client.get("/api/system/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_signal_enrich_endpoint():
    df = make_series(decline_then_rally(decline_len=40, rally_len=20))
    candles = [
        {
            "timestamp": ts.isoformat(),
            "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume,
        }
        for ts, row in df.iterrows()
    ]
    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/signal/enrich",
        json={"symbol": "TESTSYM", "candles": {"1min": candles}},
    )
    assert response.status_code == 200
    body = response.json()
    assert "composite_score" in body
    assert "signal" in body


def test_option_chain_analyze_endpoint():
    chain = {
        "underlying": "NIFTY",
        "expiry": "2024-01-25",
        "underlying_ltp": 110.0,
        "rows": [
            {"strike": 100, "call_oi": 20, "call_change_oi": -5, "put_oi": 40, "put_change_oi": 15},
            {"strike": 110, "call_oi": 15, "call_change_oi": -2, "put_oi": 35, "put_change_oi": 10},
            {"strike": 120, "call_oi": 10, "call_change_oi": -1, "put_oi": 30, "put_change_oi": 8},
        ],
    }
    response = client.post("/api/option-chain/analyze", json={"chain": chain})
    assert response.status_code == 200
    body = response.json()
    assert body["bias"] == "BULLISH"
    assert body["atm_strike"] == 110


def test_available_brokers_endpoint():
    response = client.get("/api/broker/available")
    assert response.status_code == 200
    brokers = response.json()["brokers"]
    assert set(brokers) == {"zerodha", "upstox", "shoonya", "angel_one", "fyers", "dhan", "coindcx"}


def _candles_payload(prices):
    df = make_series(prices)
    return [
        {
            "timestamp": ts.isoformat(),
            "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume,
        }
        for ts, row in df.iterrows()
    ]


def test_price_action_structure_endpoint():
    prices = decline_then_rally(decline_len=40, rally_len=40)
    response = client.post(
        "/api/price-action/structure",
        json={"symbol": "TESTSYM", "candles": _candles_payload(prices)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trend"] in ("UPTREND", "DOWNTREND", "RANGE")
    assert "swings" in body and "events" in body


def test_price_action_patterns_endpoint():
    prices = decline_then_rally(decline_len=40, rally_len=20)
    response = client.post(
        "/api/price-action/patterns",
        json={"symbol": "TESTSYM", "candles": _candles_payload(prices)},
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_support_resistance_zones_endpoint():
    prices = decline_then_rally(decline_len=40, rally_len=40)
    response = client.post(
        "/api/support-resistance/zones",
        json={"symbol": "TESTSYM", "candles": _candles_payload(prices)},
    )
    assert response.status_code == 200
    zones = response.json()
    assert isinstance(zones, list)
    for zone in zones:
        assert zone["kind"] in ("SUPPORT", "RESISTANCE")


def test_paper_execute_endpoint():
    df_candles = decline_then_rally(decline_len=40, rally_len=20)
    df = make_series(df_candles)
    candles = [
        {
            "timestamp": ts.isoformat(),
            "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume,
        }
        for ts, row in df.iterrows()
    ]
    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        json={"symbol": "TESTSYM", "candles": {"1min": candles}},
    )
    assert response.status_code == 200
    body = response.json()
    assert "executed" in body and "signal" in body
