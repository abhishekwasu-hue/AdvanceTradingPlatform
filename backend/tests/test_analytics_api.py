import asyncio
from datetime import datetime, timedelta, timezone

from app.db.models import TradeRecord
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_api import _register_user_and_get_id


def test_analytics_requires_authentication():
    assert client.get("/api/analytics/summary").status_code in (401, 403)


def test_analytics_with_no_trades_is_all_zero():
    token = _register("evan@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    summary = client.get("/api/analytics/summary", headers=headers).json()
    assert summary["total_trades"] == 0
    assert summary["win_rate"] == 0.0
    assert summary["by_strategy"] == []


def test_analytics_aggregates_by_strategy_and_symbol():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("fiona@example.com"))
    headers = {"Authorization": f"Bearer {token}"}

    async def _seed():
        now = datetime.now(timezone.utc)
        async with _session_factory() as session:
            session.add(TradeRecord(
                tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="NIFTY", strategy_id="stratA", direction="LONG",
                entry_time=now - timedelta(minutes=10), entry_price=100.0, quantity=10,
                stop_loss=98.0, target1=104.0, exit_time=now, exit_price=104.0, pnl=40.0, exit_reason="Target 1",
            ))
            session.add(TradeRecord(
                tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="NIFTY", strategy_id="stratA", direction="LONG",
                entry_time=now - timedelta(minutes=20), entry_price=100.0, quantity=10,
                stop_loss=98.0, target1=104.0, exit_time=now, exit_price=98.0, pnl=-20.0, exit_reason="Stop Loss",
            ))
            session.add(TradeRecord(
                tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="BANKNIFTY", strategy_id="stratB", direction="SHORT",
                entry_time=now - timedelta(minutes=5), entry_price=100.0, quantity=10,
                stop_loss=102.0, target1=96.0, exit_time=now, exit_price=96.0, pnl=30.0, exit_reason="Target 1",
            ))
            session.add(TradeRecord(
                tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="OPENSYM", strategy_id="stratC", direction="LONG",
                entry_time=now, entry_price=100.0, quantity=10, stop_loss=98.0, target1=104.0,
            ))
            await session.commit()

    asyncio.run(_seed())

    summary = client.get("/api/analytics/summary", headers=headers).json()
    assert summary["total_trades"] == 4
    assert summary["closed_trades"] == 3
    assert summary["open_trades"] == 1
    assert summary["net_pnl"] == 50.0
    assert summary["win_rate"] == round(100 * 2 / 3, 1)

    by_strategy = {g["key"]: g for g in summary["by_strategy"]}
    assert by_strategy["stratA"]["trades"] == 2
    assert by_strategy["stratA"]["net_pnl"] == 20.0
    assert by_strategy["stratB"]["net_pnl"] == 30.0
    assert "stratC" not in by_strategy  # still open, excluded from closed-trade aggregation

    by_symbol = {g["key"]: g for g in summary["by_symbol"]}
    assert by_symbol["NIFTY"]["trades"] == 2
    assert by_symbol["BANKNIFTY"]["net_pnl"] == 30.0
