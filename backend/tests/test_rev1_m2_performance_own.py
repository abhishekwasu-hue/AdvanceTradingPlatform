"""REV-1 M2 (review: Performance): the Performance page reads the signed-in user's OWN trades - a teammate's trades in
the same organisation are never counted - and its strategy-vs-backtest rows say which mode (PAPER / LIVE) they are."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

from app.db.models import BacktestRunRecord, TradeRecord, User
from tests.test_auth_api import _session_factory, client
from tests.test_trading_api import _register_user_and_get_id


def _trade(tenant_id: int, user_id: int, strategy_id: str, pnl: float, *, mode: str = "PAPER", closed: bool = True,
           minutes: int = 10) -> TradeRecord:
    now = datetime.now(UTC)
    return TradeRecord(tenant_id=tenant_id, user_id=user_id, mode=mode, symbol="NIFTY", strategy_id=strategy_id, direction="LONG",
                       entry_time=now - timedelta(minutes=minutes), entry_price=100.0, quantity=10, stop_loss=98.0, target1=104.0,
                       exit_time=now if closed else None, exit_price=(100.0 + pnl / 10) if closed else None,
                       pnl=pnl if closed else None, exit_reason="Target 1" if closed else None)


def _seed(tenant_id: int, user_id: int, mate_email: str) -> None:
    async def run():
        async with _session_factory() as session:
            mate = User(tenant_id=tenant_id, email=mate_email, hashed_password="x", role="USER")
            session.add(mate)
            await session.flush()
            # Mine: two PAPER on "mine_a" (+40, -20), one LIVE on "mine_a" (+10), one open.
            session.add_all([_trade(tenant_id, user_id, "mine_a", 40.0), _trade(tenant_id, user_id, "mine_a", -20.0, minutes=20),
                             _trade(tenant_id, user_id, "mine_a", 10.0, mode="LIVE", minutes=30),
                             _trade(tenant_id, user_id, "mine_b", 0.0, closed=False)])
            # A teammate's, in the same organisation: never on my page.
            session.add_all([_trade(tenant_id, mate.id, "mate_x", 500.0), _trade(tenant_id, mate.id, "mine_a", -300.0)])
            session.add(BacktestRunRecord(tenant_id=tenant_id, strategy_id="mine_a", symbol="NIFTY", base_timeframe="5m",
                                          metrics_json=json.dumps({"win_rate": 60.0, "expectancy": 5.0})))
            session.add(BacktestRunRecord(tenant_id=tenant_id, strategy_id="mine_a", symbol="NIFTY", base_timeframe="5m",
                                          metrics_json=json.dumps({"win_rate": 55.0, "expectancy": 4.0})))
            await session.commit()
    asyncio.run(run())


def test_performance_reads_need_sign_in():
    assert client.get("/api/performance/summary").status_code in (401, 403)
    assert client.get("/api/performance/vs-backtest").status_code in (401, 403)


def test_summary_counts_only_my_own_trades():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("rev1-perf-own@example.com"))
    _seed(tenant_id, user_id, "rev1-perf-mate@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    mine = client.get("/api/performance/summary", headers=headers).json()
    assert mine["total_trades"] == 4 and mine["closed_trades"] == 3 and mine["open_trades"] == 1
    assert mine["net_pnl"] == 30.0
    keys = {g["key"]: g for g in mine["by_strategy"]}
    assert set(keys) == {"mine_a"} and keys["mine_a"]["trades"] == 3 and keys["mine_a"]["net_pnl"] == 30.0

    # The organisation-wide read still counts everyone (unchanged for its other callers).
    org = client.get("/api/analytics/summary", headers=headers).json()
    assert org["total_trades"] == 6


def test_vs_backtest_is_per_strategy_and_mode_from_my_trades_against_the_latest_run():
    token, user_id, tenant_id = asyncio.run(_register_user_and_get_id("rev1-perf-modes@example.com"))
    _seed(tenant_id, user_id, "rev1-perf-mate2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    body = client.get("/api/performance/vs-backtest", headers=headers).json()
    rows = {(r["strategy_id"], r["mode"]): r for r in body["strategies"]}
    assert set(rows) == {("mine_a", "PAPER"), ("mine_a", "LIVE")}             # no teammate strategy, no teammate trade
    paper = rows[("mine_a", "PAPER")]
    assert paper["live"]["trades"] == 2 and paper["live"]["net_pnl"] == 20.0
    assert paper["status"] == "INSUFFICIENT_DATA"
    assert rows[("mine_a", "LIVE")]["live"]["trades"] == 1
    assert paper["backtest"]["win_rate"] == 0.55                                # the latest saved run, normalised
    assert "backtest" in body["note"] and "_" not in body["note"]
