import asyncio
from datetime import datetime, timezone

from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
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


def _paper_execute(headers, monkeypatch=None, idempotency_key=None):
    strategy = registry.get("ema_rsi_scalper_1m")
    if monkeypatch is not None:
        monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())
    payload = {"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}}
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    return client.post("/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=headers, json=payload)


def test_kill_switch_endpoints_require_authentication():
    assert client.get("/api/kill-switch/status").status_code in (401, 403)
    assert client.post("/api/kill-switch/tenant/engage", json={}).status_code in (401, 403)
    assert client.post("/api/kill-switch/global/engage", json={}).status_code in (401, 403)
    assert client.post("/api/kill-switch/emergency-exit", json={}).status_code in (401, 403)


def test_ordinary_user_cannot_engage_global_kill_switch():
    token = _register("umberto@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/api/kill-switch/global/engage", headers=headers, json={"reason": "test"})
    assert response.status_code == 403


def test_tenant_kill_switch_blocks_new_orders_but_not_other_tenants(monkeypatch):
    token1 = _register("vera@example.com")
    token2 = _register("wade@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}

    engaged = client.post("/api/kill-switch/tenant/engage", headers=headers1, json={"reason": "manual stop"})
    assert engaged.status_code == 200
    assert engaged.json()["engaged"] is True

    blocked = _paper_execute(headers1, monkeypatch)
    assert blocked.status_code == 200
    body = blocked.json()
    assert body["executed"] is False
    assert any("kill switch" in r.lower() for r in body["reasons"])

    order = client.get("/api/orders", headers=headers1).json()[0]
    assert order["status"] == "REJECTED"

    # A different tenant is entirely unaffected.
    unaffected = _paper_execute(headers2, monkeypatch)
    assert unaffected.json()["executed"] is True

    disengaged = client.post("/api/kill-switch/tenant/disengage", headers=headers1)
    assert disengaged.status_code == 200
    assert disengaged.json()["engaged"] is False

    resumed = _paper_execute(headers1, monkeypatch)
    assert resumed.json()["executed"] is True


def test_strategy_kill_switch_only_blocks_that_strategy(monkeypatch):
    token = _register("xiomara@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    engaged = client.post(
        "/api/kill-switch/strategy/ema_rsi_scalper_1m/engage", headers=headers, json={"reason": "bad performance"},
    )
    assert engaged.status_code == 200

    blocked = _paper_execute(headers, monkeypatch)
    assert blocked.json()["executed"] is False

    status = client.get("/api/kill-switch/status", headers=headers).json()
    assert status["tenant_switch"]["engaged"] is False
    assert any(s["strategy_id"] == "ema_rsi_scalper_1m" and s["engaged"] for s in status["strategy_switches"])

    # A different, unaffected strategy should still be tradeable.
    other_strategy = registry.get("supertrend_adx_scalper_1m")
    monkeypatch.setattr(other_strategy, "analyze", lambda data, symbol: _fake_long_signal())
    unaffected = client.post(
        "/api/strategies/supertrend_adx_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert unaffected.json()["executed"] is True


def test_global_kill_switch_blocks_every_tenant_including_anonymous(monkeypatch):
    admin_token = _register("yannick@example.com")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    async def _promote_to_super_admin():
        from app.auth.security import decode_access_token
        user_id = int(decode_access_token(admin_token)["sub"])
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            user.role = "SUPER_ADMIN"
            await session.commit()

    asyncio.run(_promote_to_super_admin())
    from tests.utils import enable_mfa
    enable_mfa(admin_headers)  # the global switch needs an MFA-verified admin session (Phase C3)

    engaged = client.post("/api/kill-switch/global/engage", headers=admin_headers, json={"reason": "platform incident"})
    assert engaged.status_code == 200
    assert engaged.json()["engaged"] is True

    other_token = _register("zelda@example.com")
    other_headers = {"Authorization": f"Bearer {other_token}"}
    blocked = _paper_execute(other_headers, monkeypatch)
    assert blocked.json()["executed"] is False

    anon_blocked = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert anon_blocked.json()["executed"] is False

    disengaged = client.post("/api/kill-switch/global/disengage", headers=admin_headers)
    assert disengaged.json()["engaged"] is False

    resumed = _paper_execute(other_headers, monkeypatch)
    assert resumed.json()["executed"] is True


def test_emergency_exit_engages_switch_and_closes_priced_positions():
    token = _register("aldo@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed_open_position():
        from app.auth.security import decode_access_token
        user_id = int(decode_access_token(token)["sub"])
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            record = TradeRecord(
                tenant_id=user.tenant_id, user_id=user.id, mode="PAPER", symbol="EXITME", strategy_id="s",
                direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=10,
                stop_loss=98.0, target1=104.0,
            )
            session.add(record)
            await session.commit()

    asyncio.run(_seed_open_position())

    response = client.post(
        "/api/kill-switch/emergency-exit", headers=headers,
        json={"reason": "risk event", "prices": {"EXITME": 101.5}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_kill_switch_engaged"] is True
    assert len(body["closed_trade_ids"]) == 1
    assert body["skipped_symbols"] == []

    positions = client.get("/api/positions", headers=headers).json()
    assert positions == []

    trades = client.get("/api/trades", headers=headers).json()
    closed = next(t for t in trades if t["symbol"] == "EXITME")
    assert closed["exit_reason"] == "Emergency Exit"
    assert closed["exit_price"] == 101.5

    status = client.get("/api/kill-switch/status", headers=headers).json()
    assert status["tenant_switch"]["engaged"] is True


def test_emergency_exit_skips_positions_with_no_supplied_price():
    token = _register("bree@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed_open_position():
        from app.auth.security import decode_access_token
        user_id = int(decode_access_token(token)["sub"])
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            record = TradeRecord(
                tenant_id=user.tenant_id, user_id=user.id, mode="PAPER", symbol="NOPRICE", strategy_id="s",
                direction="LONG", entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=10,
                stop_loss=98.0, target1=104.0,
            )
            session.add(record)
            await session.commit()

    asyncio.run(_seed_open_position())

    response = client.post("/api/kill-switch/emergency-exit", headers=headers, json={"reason": "test", "prices": {}})
    body = response.json()
    assert body["closed_trade_ids"] == []
    assert body["skipped_symbols"] == ["NOPRICE"]

    positions = client.get("/api/positions", headers=headers).json()
    assert len(positions) == 1
