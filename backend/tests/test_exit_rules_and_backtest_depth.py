"""Phase J: dynamic exit rules shared by the backtest engine and the position monitor, backtest
analytics, Monte Carlo / walk-forward robustness, and backtest run records."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

from app.backtest.analytics import build_analytics, drawdown_curve, streaks
from app.backtest.engine import run_backtest
from app.backtest.robustness import monte_carlo, walk_forward
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import RiskConfig, Signal, Trade
from app.db.models import BacktestRunRecord, StrategyDeploymentRecord, TradeRecord, User
from app.market_data.service import MarketDataService
from app.strategy_engine.indicator_strategies import EmaRsiScalper
from app.trading.exit_rules import ExitRules, apply_exit_rules
from app.trading.position_monitor import monitor_open_positions
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _create, _store_broker
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker
from tests.utils import make_series, noisy_uptrend

IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2026, 9, 25, 10, 0, tzinfo=IST)


def _run(coro):
    return asyncio.run(coro)


# --- rules --------------------------------------------------------------------------------

def test_rules_json_round_trip_and_description():
    r = ExitRules(trailing_stop_pct=1.0, break_even_at_r=1.0, time_exit_minutes=30, time_exit_at="14:45")
    assert ExitRules.from_json(r.to_json()) == r and r.active and not ExitRules().active
    assert ExitRules().to_json() is None and ExitRules.from_json("nope") == ExitRules()
    assert "trail 1%" in r.describe() and "break-even at 1R" in r.describe() and "flat at 14:45" in r.describe()
    assert r.exit_time_of_day().hour == 14 and ExitRules(time_exit_at="bad").exit_time_of_day() is None


def test_trailing_stop_only_tightens_and_only_in_profit():
    rules = ExitRules(trailing_stop_pct=1.0)
    # LONG from 100, stop 98: price falls to 99 -> no trail (not in profit)
    u = apply_exit_rules(rules, direction="LONG", entry_price=100, initial_stop=98, current_stop=98, best_price=None, high=99.5, low=99, entry_time=T0, now=T0)
    assert u.stop_loss == 98 and not u.stop_changed and u.best_price == 100
    # rallies to 110 -> stop trails to 108.9
    u = apply_exit_rules(rules, direction="LONG", entry_price=100, initial_stop=98, current_stop=98, best_price=100, high=110, low=104, entry_time=T0, now=T0)
    assert u.stop_changed and u.stop_loss == 108.9 and u.stop_reason == "trailing" and u.best_price == 110
    # pulls back to 105: stop stays at 108.9 (never loosens), best stays 110
    u = apply_exit_rules(rules, direction="LONG", entry_price=100, initial_stop=98, current_stop=108.9, best_price=110, high=106, low=105, entry_time=T0, now=T0)
    assert not u.stop_changed and u.stop_loss == 108.9 and u.best_price == 110
    # SHORT mirror: from 100, stop 102, falls to 90 -> stop 90.9
    u = apply_exit_rules(rules, direction="SHORT", entry_price=100, initial_stop=102, current_stop=102, best_price=None, high=95, low=90, entry_time=T0, now=T0)
    assert u.stop_loss == 90.9 and u.best_price == 90


def test_break_even_and_time_exits():
    rules = ExitRules(break_even_at_r=1.0, time_exit_minutes=30)
    u = apply_exit_rules(rules, direction="LONG", entry_price=100, initial_stop=98, current_stop=98, best_price=None, high=101.5, low=100, entry_time=T0, now=T0 + timedelta(minutes=5))
    assert not u.stop_changed  # +1.5 < 1R (2.0)
    u = apply_exit_rules(rules, direction="LONG", entry_price=100, initial_stop=98, current_stop=98, best_price=101.5, high=102.5, low=101, entry_time=T0, now=T0 + timedelta(minutes=6))
    assert u.stop_changed and u.stop_loss == 100 and u.stop_reason == "break-even" and u.time_exit_reason is None
    u = apply_exit_rules(rules, direction="LONG", entry_price=100, initial_stop=98, current_stop=100, best_price=102.5, high=102, low=101, entry_time=T0, now=T0 + timedelta(minutes=30))
    assert u.time_exit_reason == "Time exit (30m in trade)"
    clock = ExitRules(time_exit_at="14:45")
    assert apply_exit_rules(clock, direction="LONG", entry_price=100, initial_stop=98, current_stop=98, best_price=None, high=100, low=100, entry_time=T0,
                            now=datetime(2026, 9, 25, 14, 44, tzinfo=IST)).time_exit_reason is None
    assert "flat at 14:45" in apply_exit_rules(clock, direction="LONG", entry_price=100, initial_stop=98, current_stop=98, best_price=None, high=100, low=100,
                                               entry_time=T0, now=datetime(2026, 9, 25, 14, 45, tzinfo=IST)).time_exit_reason


# --- engine -------------------------------------------------------------------------------

class _OneShot:
    """LONG on the first evaluated bar with a far target, so only the exit rules can close it."""
    id = "one_shot"
    timeframes = ["1min"]
    params = {}

    def min_history(self):
        return {"1min": 5}

    def analyze(self, data, symbol):
        df = data["1min"]
        if len(df) == self.min_history()["1min"] + 1:   # the first bar the engine evaluates
            price = float(df["close"].iloc[-1])
            return Signal(symbol=symbol, strategy_id=self.id, strategy_name="one", direction=SignalDirection.LONG, timestamp=df.index[-1].to_pydatetime(),
                          entry=price, stop_loss=price - 2, target1=price + 1000, target2=None, risk_reward=500, score=90, grade=SignalGrade.HIGH_QUALITY,
                          reasons=[], timeframe_combo="1min")
        return Signal(symbol=symbol, strategy_id=self.id, strategy_name="one", direction=SignalDirection.NO_TRADE, timestamp=df.index[-1].to_pydatetime(),
                      entry=None, stop_loss=None, target1=None, target2=None, risk_reward=None, score=0, grade=SignalGrade.NO_TRADE, reasons=[], timeframe_combo="1min")


def _ramp(n=40, start=100.0):
    idx = pd.date_range("2026-09-25 09:15", periods=n, freq="1min", tz="Asia/Kolkata")
    closes = [start + i * 0.5 for i in range(n)]
    closes[25:] = [closes[24] - (i - 24) * 0.2 for i in range(25, n)]   # rally then a shallow reverse (above the fixed stop)
    return pd.DataFrame({"open": closes, "high": [c + 0.2 for c in closes], "low": [c - 0.2 for c in closes], "close": closes, "volume": 10}, index=idx)


def test_engine_trails_the_stop_and_exits_on_time():
    cfg = RiskConfig(capital=100_000, risk_per_trade_pct=1.0)
    plain = run_backtest(_OneShot(), _ramp(), "X", "1min", cfg)
    assert plain.total_trades == 1 and plain.trades[0].exit_reason == "End of backtest"
    trailed = run_backtest(_OneShot(), _ramp(), "X", "1min", cfg, exit_rules=ExitRules(trailing_stop_pct=1.0))
    t = trailed.trades[0]
    assert t.exit_reason == "Stop Loss" and t.exit_price > t.entry_price and t.pnl > 0   # locked in by the trail
    assert trailed.exit_rules == '{"trailing_stop_pct": 1.0}' and trailed.analytics["exit_reasons"][0]["key"] == "Stop Loss"
    timed = run_backtest(_OneShot(), _ramp(), "X", "1min", cfg, exit_rules=ExitRules(time_exit_minutes=10))
    assert timed.trades[0].exit_reason == "Time exit (10m in trade)"
    assert (timed.trades[0].exit_time - timed.trades[0].entry_time) == timedelta(minutes=10)


def test_analytics_views_from_a_real_run():
    df = make_series(noisy_uptrend(n=400))
    result = run_backtest(EmaRsiScalper(tf="1min"), df, "TESTSYM", "1min", RiskConfig(capital=100_000, risk_per_trade_pct=1.0))
    a = result.analytics
    assert set(a) >= {"monthly", "day_of_week", "hour_of_day", "exit_reasons", "direction", "holding_minutes", "slippage", "costs", "streaks", "ratios", "drawdown_curve"}
    assert len(a["drawdown_curve"]) == len(result.equity_curve) and all(d >= 0 for d in a["drawdown_curve"])
    if result.total_trades:
        assert sum(m["trades"] for m in a["monthly"]) == result.total_trades
        assert abs(sum(m["pnl"] for m in a["exit_reasons"]) - result.net_pnl) < 0.05
        assert a["holding_minutes"]["avg"] is not None and a["costs"]["total_charges"] >= 0
    assert streaks([]) == {"max_consecutive_wins": 0, "max_consecutive_losses": 0}
    assert drawdown_curve([100, 110, 105, 120, 90]) == [0, 0, 5, 0, 30]


def test_monte_carlo_and_walk_forward():
    now = datetime.now(timezone.utc)
    trades = [Trade(symbol="X", strategy_id="s", direction=SignalDirection.LONG, entry_time=now + timedelta(minutes=i), entry_price=100, quantity=1,
                    stop_loss=99, exit_time=now + timedelta(minutes=i + 1), exit_price=100 + p, exit_reason="t", pnl=p)
              for i, p in enumerate([10, -5, 8, -6, 12, -4, 9, -7, 11, -3])]
    mc = monte_carlo(trades, capital=1000, runs=500, seed=1)
    assert mc["runs"] == 500 and mc["trades"] == 10
    assert mc["final_pnl"]["p5"] <= mc["final_pnl"]["p50"] <= mc["final_pnl"]["p95"]
    assert 0 <= mc["probability_of_loss_pct"] <= 100 and mc["max_drawdown"]["original"] >= 0
    assert monte_carlo([], 1000)["runs"] == 0

    df = make_series(noisy_uptrend(n=600))
    wf = walk_forward(EmaRsiScalper(tf="1min"), df, "TESTSYM", "1min", RiskConfig(capital=100_000, risk_per_trade_pct=1.0), folds=3)
    assert wf["folds"] == 3 and len(wf["windows"]) == 3 and sum(w["bars"] for w in wf["windows"]) == 600
    assert 0 <= wf["consistency_pct"] <= 100
    assert walk_forward(EmaRsiScalper(tf="1min"), df.iloc[:60], "TESTSYM", "1min", RiskConfig(), folds=4)["folds"] == 0


# --- API ----------------------------------------------------------------------------------

def _candles(n=300):
    df = make_series(noisy_uptrend(n=n))
    return [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
            for ts, r in df.iterrows()]


def test_backtest_endpoint_records_runs_for_logged_in_users_and_lists_them():
    headers = _auth("bt-runs@example.com")
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "nifty", "base_timeframe": "1min", "candles": _candles(),
            "exit_rules": {"trailing_stop_pct": 0.5}, "data_source": "sample"}
    anon = client.post("/api/backtest", json=body)
    assert anon.status_code == 200 and anon.json()["run_id"] is None and anon.json()["analytics"] is not None
    mine = client.post("/api/backtest", headers=headers, json=body)
    assert mine.status_code == 200, mine.text
    run_id = mine.json()["run_id"]
    assert run_id and mine.json()["exit_rules"] == '{"trailing_stop_pct": 0.5}'
    runs = client.get("/api/backtests", headers=headers).json()
    assert runs[0]["id"] == run_id and runs[0]["symbol"] == "NIFTY" and runs[0]["data_source"] == "sample" and runs[0]["bars"] == 300
    one = client.get(f"/api/backtests/{run_id}", headers=headers).json()
    assert one["engine_version"] == "2" and one["exit_rules"] == {"trailing_stop_pct": 0.5} and "analytics" in one["metrics"]
    assert client.get(f"/api/backtests/{run_id}", headers=_auth("bt-other@example.com")).status_code == 404
    bad = client.post("/api/backtest", headers=headers, json={**body, "exit_rules": {"time_exit_at": "25:00"}})
    assert bad.status_code == 422

    mc = client.post("/api/backtest/monte-carlo?runs=200", headers=headers, json=body).json()
    assert mc["monte_carlo"]["runs"] in (200,) or mc["monte_carlo"]["runs"] == 0
    wf = client.post("/api/backtest/walk-forward?folds=2", headers=headers, json=body).json()
    assert wf["folds"] in (0, 2)


def test_deployment_exit_rules_are_validated_stored_and_applied_by_the_monitor(monkeypatch):
    headers = _auth("bt-dep-rules@example.com")
    _store_broker(headers, token_status="VALID")
    bad = _create(headers, exit_rules={"time_exit_at": "9:99"})
    assert bad.status_code == 422
    ok = _create(headers, exit_rules={"trailing_stop_pct": 1.0, "break_even_at_r": 1.0})
    assert ok.status_code == 201, ok.text
    assert ok.json()["exit_rules"] == {"break_even_at_r": 1.0, "trailing_stop_pct": 1.0} and "trail 1%" in ok.json()["contract_rules"]

    # Worker: the rules land on the trade; the monitor trails the stop as price rises and exits at the trailed stop.
    t = _tenant("bt-worker-rules@example.com")
    dep_id = _deploy(t)
    async def mark():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.exit_rules = ExitRules(trailing_stop_pct=1.0).to_json()
            await session.commit()
    _run(mark())
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)   # entry 100, stop 98, target1 104
    _run(worker.run_cycle(now=OPEN_NOW))
    trade = _trades(t["tenant_id"])[0]
    assert trade.exit_rules == '{"trailing_stop_pct": 1.0}' and trade.initial_stop_loss == 98.0 and trade.exit_time is None

    async def sweep(price):
        broker.ltp = price
        async with _session_factory() as session:
            return await monitor_open_positions(session, t["tenant_id"], MarketDataService(broker).get_ltp)
    _run(sweep(103.0))   # +3: trail to 101.97
    trade = _trades(t["tenant_id"])[0]
    assert trade.exit_time is None and trade.best_price == 103.0 and trade.stop_loss == 101.97
    outcomes = _run(sweep(101.5))   # below the trailed stop -> closed at the stop, in profit
    trade = _trades(t["tenant_id"])[0]
    assert outcomes[0].closed and trade.exit_reason == "Stop Loss" and trade.pnl > 0


def test_live_trailing_moves_the_broker_stop(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("bt-live-trail@example.com")
    dep_id = _deploy(t, mode="LIVE")
    async def mark():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.exit_rules = ExitRules(trailing_stop_pct=1.0).to_json()
            await session.commit()
    _run(mark())

    class Broker(_FakeBroker):
        modified = []
        async def modify_order(self, order_id, quantity=None, price=None, trigger_price=None, order_type=None):
            Broker.modified.append((order_id, trigger_price))
            from app.brokers.models import BrokerOrderResponse
            return BrokerOrderResponse(order_id=order_id, status="MODIFIED")
    broker = Broker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    trade = _trades(t["tenant_id"])[0]
    assert trade.mode == "LIVE" and trade.sl_order_id == "ORD-2"

    broker.ltp = 104.5  # above target1 104: the monitor will exit on target this cycle, after trailing
    async def sweep():
        async with _session_factory() as session:
            return await monitor_open_positions(session, t["tenant_id"], MarketDataService(broker).get_ltp, broker=broker)
    _run(sweep())
    assert Broker.modified and Broker.modified[0][0] == "ORD-2" and abs(Broker.modified[0][1] - 103.455) < 0.01
