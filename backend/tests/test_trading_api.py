import asyncio
from datetime import datetime, timedelta, timezone

from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import TradeRecord
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
    return token, user_id


def test_positions_only_shows_open_trades():
    token, user_id = asyncio.run(_register_user_and_get_id("karl@example.com"))
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            session.add(TradeRecord(
                user_id=user_id, mode="PAPER", symbol="OPEN1", strategy_id="s", direction="LONG",
                entry_time=now, entry_price=100.0, quantity=10, stop_loss=98.0, target1=104.0,
            ))
            session.add(TradeRecord(
                user_id=user_id, mode="PAPER", symbol="CLOSED1", strategy_id="s", direction="LONG",
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
    token, user_id = asyncio.run(_register_user_and_get_id("liam@example.com"))
    headers = {"Authorization": f"Bearer {token}"}

    async def _persist():
        from app.execution.paper_broker import PaperBroker
        broker = PaperBroker()
        trade = broker.open_trade(_fake_long_signal(), quantity=250, timestamp=datetime.now(timezone.utc))
        async with _session_factory() as session:
            await persist_paper_trade(session, user_id, trade)

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


def test_anonymous_paper_execute_still_works_without_persisting(monkeypatch):
    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())

    response = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute",
        json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert response.status_code == 200
    assert response.json()["executed"] is True


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
