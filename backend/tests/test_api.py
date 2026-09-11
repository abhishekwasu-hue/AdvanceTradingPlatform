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
