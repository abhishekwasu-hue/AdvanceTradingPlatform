from datetime import datetime, timezone

from app.brokers.models import BrokerProfile
from app.core.models import RiskConfig, Signal
from app.core.enums import SignalDirection, SignalGrade
from app.db.models import TradeRecord, User
from app.strategy_engine.registry import registry
from tests.test_auth_api import _register, _session_factory, client
from tests.utils import make_series


def _sample_candles_payload():
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


def test_notifications_require_authentication():
    assert client.get("/api/notifications").status_code in (401, 403)
    assert client.post("/api/notifications/1/read").status_code in (401, 403)
    assert client.post("/api/notifications/read-all").status_code in (401, 403)


def test_filled_paper_execute_creates_entry_notification(monkeypatch):
    token = _register("notif_entry@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response.status_code == 200

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    assert notifications[0]["event_type"] == "ENTRY"
    assert notifications[0]["severity"] == "INFO"
    assert notifications[0]["read"] is False


def test_risk_rejected_paper_execute_creates_risk_rejection_notification(monkeypatch):
    token = _register("notif_risk@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    restrictive = RiskConfig(max_trades_per_day=0)
    client.put("/api/risk-settings", headers=headers, json=restrictive.model_dump())

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    assert notifications[0]["event_type"] == "RISK_REJECTION"
    assert notifications[0]["severity"] == "WARNING"


def test_daily_loss_limit_breach_creates_critical_notification(monkeypatch):
    token = _register("notif_dailyloss@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    restrictive = RiskConfig(max_daily_loss_pct=0.0001, capital=100.0)
    client.put("/api/risk-settings", headers=headers, json=restrictive.model_dump())

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    async def _seed_daily_loss():
        from app.auth.security import decode_access_token
        user_id = int(decode_access_token(token)["sub"])
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            now = datetime.now(timezone.utc)
            session.add(TradeRecord(
                tenant_id=user.tenant_id, user_id=user.id, mode="PAPER", symbol="X", strategy_id="s",
                direction="LONG", entry_time=now, entry_price=100.0, quantity=10, stop_loss=98.0,
                target1=104.0, exit_time=now, exit_price=90.0, pnl=-50.0, exit_reason="Stop Loss",
            ))
            await session.commit()

    import asyncio
    asyncio.run(_seed_daily_loss())

    client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    assert notifications[0]["event_type"] == "DAILY_LOSS_LIMIT"
    assert notifications[0]["severity"] == "CRITICAL"


def test_kill_switch_rejection_creates_rejection_notification(monkeypatch):
    token = _register("notif_killswitch@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/kill-switch/tenant/engage", headers=headers, json={"reason": "manual stop"})

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())
    client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    assert notifications[0]["event_type"] == "REJECTION"


def test_mark_price_exit_creates_notification():
    token = _register("notif_exit@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed():
        from app.auth.security import decode_access_token
        user_id = int(decode_access_token(token)["sub"])
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            record = TradeRecord(
                tenant_id=user.tenant_id, user_id=user.id, mode="PAPER", symbol="MARKTEST", strategy_id="s",
                direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=250,
                stop_loss=98.0, target1=104.0,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record.id

    import asyncio
    trade_id = asyncio.run(_seed())

    client.post(f"/api/positions/{trade_id}/mark-price", headers=headers, json={"current_price": 105.0})

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    assert notifications[0]["event_type"] == "EXIT"
    assert notifications[0]["severity"] == "INFO"  # profitable exit


def test_broker_auth_failure_creates_broker_disconnect_or_token_expired_notification(monkeypatch):
    token = _register("notif_broker@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k", "access_token": "t"})

    class _FailingAdapter:
        async def authenticate(self):
            raise RuntimeError("token expired")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _FailingAdapter())
    client.post("/api/broker/zerodha/authenticate", headers=headers)

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    assert notifications[0]["event_type"] == "TOKEN_EXPIRED"
    assert notifications[0]["severity"] == "CRITICAL"


def test_broker_generic_auth_failure_creates_broker_disconnect_notification(monkeypatch):
    token = _register("notif_broker2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k", "access_token": "t"})

    class _FailingAdapter:
        async def authenticate(self):
            raise RuntimeError("bad credentials")

    monkeypatch.setattr("app.brokers.routes.get_broker_adapter", lambda name, creds: _FailingAdapter())
    client.post("/api/broker/zerodha/authenticate", headers=headers)

    notifications = client.get("/api/notifications", headers=headers).json()
    assert notifications[0]["event_type"] == "BROKER_DISCONNECT"


def test_emergency_exit_creates_critical_notification():
    token = _register("notif_emergency@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/api/kill-switch/emergency-exit", headers=headers, json={"reason": "risk event"})
    assert response.status_code == 200

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    assert notifications[0]["event_type"] == "EMERGENCY_EXIT"
    assert notifications[0]["severity"] == "CRITICAL"


def test_reconciliation_failure_creates_system_failure_notification(monkeypatch):
    token = _register("notif_reconfail@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": "k", "access_token": "t"})

    class _FailingAdapter:
        async def get_positions(self):
            raise RuntimeError("network error")

    monkeypatch.setattr("app.reconciliation.routes.get_broker_adapter", lambda name, creds: _FailingAdapter())
    client.post("/api/reconciliation/zerodha", headers=headers)

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    assert notifications[0]["event_type"] == "SYSTEM_FAILURE"


def test_mark_read_and_read_all(monkeypatch):
    token = _register("notif_readstate@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())
    client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )

    notifications = client.get("/api/notifications", headers=headers).json()
    assert len(notifications) == 1
    notification_id = notifications[0]["id"]
    assert notifications[0]["read"] is False

    marked = client.post(f"/api/notifications/{notification_id}/read", headers=headers)
    assert marked.status_code == 200
    assert marked.json()["read"] is True

    unread = client.get("/api/notifications", headers=headers, params={"unread_only": True}).json()
    assert unread == []

    client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    read_all = client.post("/api/notifications/read-all", headers=headers)
    assert read_all.status_code == 200
    assert read_all.json()["marked_read"] == 1

    unread_after = client.get("/api/notifications", headers=headers, params={"unread_only": True}).json()
    assert unread_after == []


def test_notifications_are_tenant_scoped(monkeypatch):
    token1 = _register("notif_tenant1@example.com")
    token2 = _register("notif_tenant2@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())
    client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers1, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )

    assert client.get("/api/notifications", headers=headers2).json() == []

    notification_id = client.get("/api/notifications", headers=headers1).json()[0]["id"]
    assert client.post(f"/api/notifications/{notification_id}/read", headers=headers2).status_code == 404
