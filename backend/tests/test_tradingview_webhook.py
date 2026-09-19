from tests.test_auth_api import _register, client


def _get_token(headers):
    response = client.get("/api/webhooks/tradingview/token", headers=headers)
    assert response.status_code == 200
    return response.json()["webhook_token"]


def _alert(strategy_id="pine_breakout", symbol="NIFTY", direction="LONG", alert_id=None, **overrides):
    payload = {
        "strategy_id": strategy_id, "symbol": symbol, "direction": direction,
        "entry": 100.0, "stop_loss": 98.0, "target1": 106.0, "target2": 110.0,
    }
    if alert_id is not None:
        payload["alert_id"] = alert_id
    payload.update(overrides)
    return payload


def test_get_webhook_token_requires_authentication():
    assert client.get("/api/webhooks/tradingview/token").status_code in (401, 403)


def test_webhook_rejects_unknown_token():
    response = client.post("/api/webhooks/tradingview/not-a-real-token", json=_alert())
    assert response.status_code == 401


def test_webhook_rejects_no_trade_direction():
    token = _register("tv_notrade@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    webhook_token = _get_token(headers)

    response = client.post(f"/api/webhooks/tradingview/{webhook_token}", json=_alert(direction="NO_TRADE"))
    assert response.status_code == 422


def test_webhook_executes_a_valid_alert_end_to_end():
    token = _register("tv_valid@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    webhook_token = _get_token(headers)

    response = client.post(f"/api/webhooks/tradingview/{webhook_token}", json=_alert())
    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is True
    assert body["order_id"] is not None

    trades = client.get("/api/trades", headers=headers).json()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "NIFTY"
    assert trades[0]["strategy_id"] == "pine_breakout"

    orders = client.get("/api/orders", headers=headers).json()
    assert len(orders) == 1
    assert orders[0]["status"] == "POSITION_OPEN"

    notifications = client.get("/api/notifications", headers=headers).json()
    assert any(n["event_type"] == "ENTRY" for n in notifications)


def test_webhook_respects_risk_engine_rejection():
    token = _register("tv_riskrejected@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    webhook_token = _get_token(headers)

    from app.core.models import RiskConfig
    restrictive = RiskConfig(max_trades_per_day=0)
    client.put("/api/risk-settings", headers=headers, json=restrictive.model_dump())

    response = client.post(f"/api/webhooks/tradingview/{webhook_token}", json=_alert())
    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert any("Max trades per day" in r for r in body["reasons"])

    notifications = client.get("/api/notifications", headers=headers).json()
    assert any(n["event_type"] == "RISK_REJECTION" for n in notifications)


def test_webhook_respects_tenant_kill_switch():
    token = _register("tv_killswitch@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    webhook_token = _get_token(headers)
    client.post("/api/kill-switch/tenant/engage", headers=headers, json={"reason": "manual stop"})

    response = client.post(f"/api/webhooks/tradingview/{webhook_token}", json=_alert())
    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert any("kill switch" in r.lower() for r in body["reasons"])


def test_webhook_alert_id_prevents_duplicate_execution():
    token = _register("tv_dedupe@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    webhook_token = _get_token(headers)

    alert = _alert(alert_id="alert-123")
    first = client.post(f"/api/webhooks/tradingview/{webhook_token}", json=alert)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["idempotent_replay"] is False

    second = client.post(f"/api/webhooks/tradingview/{webhook_token}", json=alert)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["idempotent_replay"] is True
    assert second_body["order_id"] == first_body["order_id"]

    assert len(client.get("/api/trades", headers=headers).json()) == 1


def test_webhook_without_alert_id_does_not_dedupe():
    token = _register("tv_noalertid@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    webhook_token = _get_token(headers)

    alert = _alert()
    r1 = client.post(f"/api/webhooks/tradingview/{webhook_token}", json=alert)
    r2 = client.post(f"/api/webhooks/tradingview/{webhook_token}", json=alert)
    assert r1.json()["order_id"] != r2.json()["order_id"]
    assert len(client.get("/api/trades", headers=headers).json()) == 2


def test_webhook_is_tenant_scoped_by_its_own_token():
    token1 = _register("tv_tenant1@example.com")
    token2 = _register("tv_tenant2@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}
    webhook_token1 = _get_token(headers1)

    client.post(f"/api/webhooks/tradingview/{webhook_token1}", json=_alert())

    assert len(client.get("/api/trades", headers=headers1).json()) == 1
    assert client.get("/api/trades", headers=headers2).json() == []


def test_rotate_webhook_token_invalidates_the_old_one():
    token = _register("tv_rotate@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    old_token = _get_token(headers)

    rotated = client.post("/api/webhooks/tradingview/token/rotate", headers=headers)
    assert rotated.status_code == 200
    new_token = rotated.json()["webhook_token"]
    assert new_token != old_token

    assert client.post(f"/api/webhooks/tradingview/{old_token}", json=_alert()).status_code == 401
    assert client.post(f"/api/webhooks/tradingview/{new_token}", json=_alert()).status_code == 200
