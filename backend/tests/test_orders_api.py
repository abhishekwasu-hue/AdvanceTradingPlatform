from datetime import datetime, timezone

from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal
from app.strategy_engine.registry import registry
from tests.test_auth_api import _register, client


def _sample_candles_payload():
    from tests.utils import make_series
    df = make_series([100.0] * 30)
    return [
        {
            "timestamp": ts.isoformat(),
            "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume,
        }
        for ts, row in df.iterrows()
    ]


def _fake_long_signal() -> Signal:
    return Signal(
        symbol="TESTSYM", strategy_id="ema_rsi_scalper_1m", strategy_name="EMA + RSI Scalper",
        direction=SignalDirection.LONG, timestamp=datetime.now(timezone.utc),
        entry=100.0, stop_loss=98.0, target1=104.0, target2=108.0,
        risk_reward=2.0, score=80, grade=SignalGrade.HIGH_QUALITY, reasons=["forced for test"],
        timeframe_combo="1min",
    )


def test_orders_require_authentication():
    assert client.get("/api/orders").status_code in (401, 403)
    assert client.get("/api/orders/1/events").status_code in (401, 403)


def test_filled_paper_execute_produces_full_order_lifecycle(monkeypatch):
    token = _register("olive@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is True
    order_id = body["order_id"]
    assert order_id is not None

    orders = client.get("/api/orders", headers=headers).json()
    assert len(orders) == 1
    assert orders[0]["id"] == order_id
    assert orders[0]["status"] == "POSITION_OPEN"
    assert orders[0]["trade_id"] is not None
    assert orders[0]["quantity"] > 0

    events = client.get(f"/api/orders/{order_id}/events", headers=headers).json()
    to_statuses = [e["to_status"] for e in events]
    assert to_statuses == [
        "CREATED", "VALIDATING", "RISK_CHECK", "SUBMITTED", "PENDING", "FILLED", "POSITION_OPEN",
    ]
    assert events[0]["from_status"] is None
    assert events[-1]["from_status"] == "FILLED"


def test_risk_rejected_paper_execute_produces_rejected_order(monkeypatch):
    token = _register("pearl@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    restrictive = RiskConfig(max_trades_per_day=0)
    client.put("/api/risk-settings", headers=headers, json=restrictive.model_dump())

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    order_id = body["order_id"]

    order = client.get("/api/orders", headers=headers).json()[0]
    assert order["id"] == order_id
    assert order["status"] == "REJECTED"
    assert order["trade_id"] is None
    assert any("Max trades per day" in r for r in order["reasons"])

    events = client.get(f"/api/orders/{order_id}/events", headers=headers).json()
    assert [e["to_status"] for e in events] == ["CREATED", "VALIDATING", "RISK_CHECK", "REJECTED"]


def test_idempotency_key_prevents_duplicate_execution(monkeypatch):
    token = _register("quincy@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    payload = {
        "symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}, "idempotency_key": "retry-key-1",
    }
    first = client.post("/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=headers, json=payload)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["executed"] is True
    assert first_body["idempotent_replay"] is False

    second = client.post("/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=headers, json=payload)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["idempotent_replay"] is True
    assert second_body["order_id"] == first_body["order_id"]
    assert second_body["executed"] == first_body["executed"]

    # Only one order and one trade must exist despite two HTTP calls with the same key.
    assert len(client.get("/api/orders", headers=headers).json()) == 1
    assert len(client.get("/api/trades", headers=headers).json()) == 1


def test_different_idempotency_keys_each_execute():
    token = _register("ruth@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    def _call(key):
        return client.post(
            "/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=headers,
            json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}, "idempotency_key": key},
        )

    r1 = _call("key-a")
    r2 = _call("key-b")
    assert r1.json()["order_id"] != r2.json()["order_id"]
    assert len(client.get("/api/orders", headers=headers).json()) == 2


def test_orders_and_events_are_tenant_scoped(monkeypatch):
    token1 = _register("sonia@example.com")
    token2 = _register("tara@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers1, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    order_id = response.json()["order_id"]

    assert client.get("/api/orders", headers=headers2).json() == []
    assert client.get(f"/api/orders/{order_id}/events", headers=headers2).status_code == 404
