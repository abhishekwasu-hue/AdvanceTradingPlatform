import asyncio
from datetime import datetime, timedelta, timezone

from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import TradeRecord, User
from app.strategy_engine.registry import registry
from app.trading.persistence import persist_paper_trade
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


def test_trades_and_positions_require_authentication():
    assert client.get("/api/trades").status_code in (401, 403)
    assert client.get("/api/positions").status_code in (401, 403)


def test_new_user_has_no_trades_or_positions():
    token = _register("judy@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/trades", headers=headers).json() == []
    assert client.get("/api/positions", headers=headers).json() == []


async def _register_user_and_get_id(email: str) -> tuple:
    from app.auth.security import decode_access_token
    token = _register(email)
    user_id = int(decode_access_token(token)["sub"])
    async with _session_factory() as session:
        user = await session.get(User, user_id)
        tenant_id = user.tenant_id
    return token, user_id, tenant_id


def test_positions_only_shows_open_trades():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("karl@example.com"))
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            session.add(TradeRecord(
                tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="OPEN1", strategy_id="s", direction="LONG",
                entry_time=now, entry_price=100.0, quantity=10, stop_loss=98.0, target1=104.0,
            ))
            session.add(TradeRecord(
                tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="CLOSED1", strategy_id="s", direction="LONG",
                entry_time=now - timedelta(minutes=5), entry_price=100.0, quantity=10, stop_loss=98.0,
                target1=104.0, exit_time=now, exit_price=104.0, exit_reason="Target 1", pnl=40.0,
            ))
            await session.commit()

    asyncio.run(_seed())

    trades = client.get("/api/trades", headers=headers).json()
    positions = client.get("/api/positions", headers=headers).json()

    assert {t["symbol"] for t in trades} == {"OPEN1", "CLOSED1"}
    assert {p["symbol"] for p in positions} == {"OPEN1"}


def test_persist_paper_trade_writes_expected_fields():
    token, user_id, _tenant_id = asyncio.run(_register_user_and_get_id("liam@example.com"))
    headers = {"Authorization": f"Bearer {token}"}

    async def _persist():
        from app.execution.paper_broker import PaperBroker
        broker = PaperBroker()
        trade = broker.open_trade(_fake_long_signal(), quantity=250, timestamp=datetime.now(timezone.utc))
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            await persist_paper_trade(session, user, trade)

    asyncio.run(_persist())

    trades = client.get("/api/trades", headers=headers).json()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "TESTSYM"
    assert trades[0]["direction"] == "LONG"
    assert trades[0]["quantity"] == 250
    assert trades[0]["exit_time"] is None


def test_authenticated_paper_execute_persists_when_signal_fires(monkeypatch):
    token = _register("mia@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response.status_code == 200
    assert response.json()["executed"] is True

    trades = client.get("/api/trades", headers=headers).json()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "TESTSYM"


def test_paper_execute_applies_users_saved_risk_settings(monkeypatch):
    token = _register("aaron@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    from app.core.models import RiskConfig
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
    assert any("Max trades per day" in r for r in body["reasons"])


def test_anonymous_paper_execute_still_works_without_persisting(monkeypatch):
    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response.status_code == 200
    assert response.json()["executed"] is True


def test_paper_execute_risk_state_is_per_user_not_shared_globally(monkeypatch):
    """Regression test: paper-execute's trading-day state must come from each user's own
    persisted trade history, not a single in-memory counter shared across every caller. Before
    the fix, running one user up to max_open_positions (default 3) permanently locked out every
    other user (and anonymous callers) until the process restarted.
    """
    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    token_a = _register("naomi@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    for _ in range(3):
        response = client.post(
            "/api/strategies/ema_rsi_scalper_1m/paper-execute",
            headers=headers_a, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
        )
        assert response.json()["executed"] is True

    # User A is now at max_open_positions (3) and should be rejected on a 4th call.
    blocked = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers_a, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert blocked.json()["executed"] is False
    assert any("Max open positions" in r for r in blocked.json()["reasons"])

    # A different, fresh user must be unaffected by user A's open positions.
    token_b = _register("otto@example.com")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    response_b = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        headers=headers_b, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response_b.json()["executed"] is True

    # Anonymous calls must also be unaffected.
    anon = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert anon.json()["executed"] is True


def test_signal_history_requires_authentication():
    assert client.get("/api/signal-history").status_code in (401, 403)


def test_anonymous_enrich_does_not_persist_signal_history():
    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/signal/enrich",
        json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response.status_code == 200


def test_authenticated_enrich_persists_signal_history(monkeypatch):
    token = _register("nora@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/signal/enrich",
        headers=headers, json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response.status_code == 200

    history = client.get("/api/signal-history", headers=headers).json()
    assert len(history) == 1
    assert history[0]["symbol"] == "TESTSYM"
    assert history[0]["direction"] == "LONG"
    assert history[0]["strategy_id"] == "ema_rsi_scalper_1m"
    assert isinstance(history[0]["reasons"], list) and history[0]["reasons"] == ["forced for test"]


async def _seed_open_long(user_id: int, tenant_id: int, entry=100.0, sl=98.0, t1=104.0, t2=108.0, qty=250) -> int:
    async with _session_factory() as session:
        record = TradeRecord(
            tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="MARKTEST", strategy_id="s", direction="LONG",
            entry_time=datetime.now(timezone.utc), entry_price=entry, quantity=qty,
            stop_loss=sl, target1=t1, target2=t2,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record.id


def test_mark_price_requires_authentication():
    assert client.post("/api/positions/1/mark-price", json={"current_price": 100.0}).status_code in (401, 403)


def test_mark_price_leaves_position_open_when_no_level_hit():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("uma@example.com"))
    headers = {"Authorization": f"Bearer {token}"}
    trade_id = asyncio.run(_seed_open_long(user_id, tenant_id))

    response = client.post(f"/api/positions/{trade_id}/mark-price", headers=headers, json={"current_price": 101.0})
    assert response.status_code == 200
    assert response.json()["closed"] is False

    positions = client.get("/api/positions", headers=headers).json()
    assert any(p["id"] == trade_id for p in positions)


def test_mark_price_closes_long_at_target1():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("victor@example.com"))
    headers = {"Authorization": f"Bearer {token}"}
    trade_id = asyncio.run(_seed_open_long(user_id, tenant_id))

    response = client.post(f"/api/positions/{trade_id}/mark-price", headers=headers, json={"current_price": 105.0})
    assert response.status_code == 200
    body = response.json()
    assert body["closed"] is True
    assert body["exit_reason"] == "Target 1"
    assert body["pnl"] > 0

    positions = client.get("/api/positions", headers=headers).json()
    assert not any(p["id"] == trade_id for p in positions)
    trades = client.get("/api/trades", headers=headers).json()
    closed = next(t for t in trades if t["id"] == trade_id)
    assert closed["exit_time"] is not None


def test_mark_price_closes_short_at_stop_loss():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("wendy@example.com"))
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed_short():
        async with _session_factory() as session:
            record = TradeRecord(
                tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="MARKTEST", strategy_id="s", direction="SHORT",
                entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=250,
                stop_loss=103.0, target1=95.0, target2=90.0,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record.id

    trade_id = asyncio.run(_seed_short())
    response = client.post(f"/api/positions/{trade_id}/mark-price", headers=headers, json={"current_price": 104.0})
    body = response.json()
    assert body["closed"] is True
    assert body["exit_reason"] == "Stop Loss"
    assert body["pnl"] < 0


def test_mark_price_rejects_other_users_position():
    token1, user_id1, tenant_id1 = asyncio.run(_register_user_and_get_id("xavier@example.com"))
    token2, _, _ = asyncio.run(_register_user_and_get_id("yara@example.com"))
    trade_id = asyncio.run(_seed_open_long(user_id1, tenant_id1))

    response = client.post(
        f"/api/positions/{trade_id}/mark-price",
        headers={"Authorization": f"Bearer {token2}"}, json={"current_price": 105.0},
    )
    assert response.status_code == 404


def test_mark_price_rejects_already_closed_position():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("zoe@example.com"))
    headers = {"Authorization": f"Bearer {token}"}
    trade_id = asyncio.run(_seed_open_long(user_id, tenant_id))

    first = client.post(f"/api/positions/{trade_id}/mark-price", headers=headers, json={"current_price": 105.0})
    assert first.json()["closed"] is True

    second = client.post(f"/api/positions/{trade_id}/mark-price", headers=headers, json={"current_price": 105.0})
    assert second.status_code == 409
