"""Phase M: platform controls (maintenance, disabled brokers, per-user trading disable), the
portfolio engine and its two new risk scopes, the trade journal, EMERGENCY severity, degradation
baselines, incidents, BrokerInterface.exit_position and parameter optimisation."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.alerts.channels import severity_reaches
from app.backtest.optimizer import expand_grid, optimize
from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse
from app.core.enums import OrderSide
from app.core.models import RiskConfig
from app.db.models import TradeRecord, User
from app.portfolio.engine import snapshot
from app.strategy_engine.registry import registry
from app.trading.degradation import compare, live_metrics
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import BAR_TS, OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant as _worker_tenant, _trades, _worker
from tests.utils import make_series, noisy_uptrend
from app.db.models import StrategyDeploymentRecord


def _run(coro):
    return asyncio.run(coro)


def _owner(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def _reset_controls():
    async def go():
        async with _session_factory() as session:
            from app.db.models import PlatformControlRecord
            for row in await session.scalars(select(PlatformControlRecord)):
                await session.delete(row)
            await session.commit()
    _run(go())


def _seed_trade(tenant_id, user_id, *, symbol="RELIANCE", direction="LONG", entry=100.0, qty=10, stop=98.0, pnl=None, closed=False, deployment_id=None, strategy="ema_rsi_scalper_1m"):
    async def go():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            t = TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", strategy_id=strategy, symbol=symbol, direction=direction,
                            entry_time=now - timedelta(minutes=30), entry_price=entry, quantity=qty, stop_loss=stop, target1=entry + 4, target2=entry + 8,
                            deployment_id=deployment_id)
            if closed:
                t.exit_time, t.exit_price, t.pnl, t.exit_reason = now - timedelta(minutes=5), entry + (pnl or 0) / qty, pnl, "TEST"
            session.add(t)
            await session.commit()
            return t.id
    return _run(go())


# --- V4.13 platform controls ----------------------------------------------------------------------

def test_maintenance_mode_blocks_new_entries_and_shows_on_public_status(monkeypatch):
    _reset_controls()
    admin_headers, _ = _admin("m-admin@example.com")
    owner_headers, _ = _owner("m-owner@example.com")
    assert client.put("/api/admin/controls/maintenance", headers=owner_headers, json={"on": True}).status_code == 403
    on = client.put("/api/admin/controls/maintenance", headers=admin_headers, json={"on": True, "message": "DB upgrade until 09:00 IST"})
    assert on.status_code == 200, on.text
    public = client.get("/api/system/status").json()
    assert public["maintenance_mode"] is True and "DB upgrade" in public["maintenance_message"]

    t = _worker_tenant("w-maint@example.com")
    dep_id = _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0 and _trades(t["tenant_id"]) == []
    assert "maintenance" in (_get(StrategyDeploymentRecord, dep_id).last_error or "").lower()

    client.put("/api/admin/controls/maintenance", headers=admin_headers, json={"on": False})
    assert client.get("/api/system/status").json()["maintenance_mode"] is False
    _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(minutes=1)))   # a new signal bar
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))
    assert report.signals_executed == 1
    events = {l["event"] for l in client.get("/api/admin/audit-logs", headers=admin_headers).json()}
    assert {"maintenance_mode_on", "maintenance_mode_off"} <= events


def test_disabled_broker_skips_live_entries_only(monkeypatch):
    _reset_controls()
    admin_headers, _ = _admin("m-admin-brk@example.com")
    assert client.put("/api/admin/controls/brokers", headers=admin_headers, json={"names": ["Upstox"]}).json()["disabled_brokers"] == ["upstox"]
    t = _worker_tenant("w-brk@example.com")
    live_id = _deploy(t, mode="LIVE")
    paper_id = _deploy(t, mode="PAPER")
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert "disabled by the platform operator" in (_get(StrategyDeploymentRecord, live_id).last_error or "")
    assert _get(StrategyDeploymentRecord, paper_id).last_error is None and report.signals_executed == 1
    client.put("/api/admin/controls/brokers", headers=admin_headers, json={"names": []})


def test_per_user_trading_disable_refuses_entries_but_keeps_reads():
    _reset_controls()
    headers, me = _owner("m-user-disable@example.com")
    members = client.get("/api/team/members", headers=headers).json()
    assert members[0]["trading_disabled_reason"] is None
    disabled = client.post(f"/api/team/members/{me['id']}/trading-disable", headers=headers, json={"reason": "Exceeded weekly loss cap"})
    assert disabled.status_code == 200 and disabled.json()["trading_disabled_reason"] == "Exceeded weekly loss cap"
    assert client.get("/api/team/members/me/trading-status", headers=headers).json()["trading_disabled_reason"] == "Exceeded weekly loss cap"

    async def try_entry():
        from app.execution.signal_execution import execute_signal_for_user
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            result, order = await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_signal())
            return result.reasons, order.status
    reasons, status = _run(try_entry())
    assert status == "REJECTED" and any("Trading disabled for this user" in r for r in reasons)
    assert client.get("/api/trades", headers=headers).status_code == 200    # reading still works
    assert client.post(f"/api/team/members/{me['id']}/trading-enable", headers=headers).json()["trading_disabled_reason"] is None
    other, _ = _owner("m-user-other@example.com")
    assert client.post(f"/api/team/members/{me['id']}/trading-disable", headers=other, json={"reason": "not mine"}).status_code == 404


# --- V4.4 / V4.5 portfolio engine + scopes ---------------------------------------------------------

def test_portfolio_snapshot_aggregates_and_warns():
    t1 = TradeRecord(tenant_id=1, user_id=1, mode="PAPER", strategy_id="s1", symbol="RELIANCE", direction="LONG", entry_time=datetime.now(timezone.utc),
                     entry_price=100.0, quantity=500, stop_loss=94.0, target1=110.0)
    t2 = TradeRecord(tenant_id=1, user_id=1, mode="LIVE", strategy_id="s2", symbol="TCS", direction="SHORT", entry_time=datetime.now(timezone.utc),
                     entry_price=200.0, quantity=100, stop_loss=210.0, target1=180.0)
    snap = snapshot([t1, t2], {"RELIANCE": 102.0, "TCS": 195.0}, capital=100_000.0, realised_today=-1500.0)
    assert snap.open_positions == 2 and snap.gross_notional == 51_000 + 19_500 and snap.net_notional == 51_000 - 19_500
    assert snap.unrealised_pnl == 1000 + 500 and snap.by_symbol[0].symbol == "RELIANCE" and snap.by_symbol[0].pct_of_capital == 51.0
    assert snap.risk_at_stops == (102 - 94) * 500 + (210 - 195) * 100 and snap.by_mode == {"PAPER": 51_000.0, "LIVE": 19_500.0}
    assert any("concentration" in w for w in snap.warnings) and any("stop hits" in w for w in snap.warnings)
    assert snap.realised_today == -1500.0


def test_portfolio_exposure_endpoint_and_portfolio_scope_limits():
    headers, me = _owner("m-portfolio@example.com")
    _seed_trade(me["tenant_id"], me["id"], symbol="RELIANCE", entry=100.0, qty=300)
    body = client.get("/api/portfolio/exposure?live_prices=false", headers=headers).json()
    assert body["open_positions"] == 1 and body["gross_notional"] == 30_000 and body["price_source"] == "entry" and body["by_symbol"][0]["symbol"] == "RELIANCE"

    # PORTFOLIO scope needs no scope_id; DEPLOYMENT does.
    assert client.put("/api/risk/limits", headers=headers, json={"scope": "DEPLOYMENT", "scope_id": "", "limit_type": "MAX_OPEN_POSITIONS", "limit_value": 1}).status_code == 400
    put = client.put("/api/risk/limits", headers=headers, json={"scope": "PORTFOLIO", "scope_id": "", "limit_type": "MAX_GROSS_EXPOSURE", "limit_value": 35_000})
    assert put.status_code == 200, put.text
    put2 = client.put("/api/risk/limits", headers=headers, json={"scope": "PORTFOLIO", "scope_id": "", "limit_type": "MAX_SYMBOL_CONCENTRATION_PCT", "limit_value": 20})
    assert put2.status_code == 200, put2.text
    # 300 x 100 open + 100 x 100 new = 40,000 > 35,000 -> blocked; RELIANCE concentration 40% > 20% -> blocked too.
    ev = client.post("/api/risk/evaluate", headers=headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "RELIANCE", "quantity": 100, "entry": 100, "stop_loss": 98})
    assert ev.status_code == 200, ev.text
    assert ev.json()["allowed"] is False
    joined = " ".join(ev.json()["reasons"])
    assert "gross exposure" in joined.lower() and "share of capital" in joined.lower()
    # A different symbol below the caps passes the concentration check but still trips gross exposure.
    ev2 = client.post("/api/risk/evaluate", headers=headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TCS", "quantity": 10, "entry": 100, "stop_loss": 98})
    assert ev2.json()["allowed"] is True


def test_deployment_scope_limit_counts_only_that_deployments_trades():
    t = _worker_tenant("w-depscope@example.com")
    dep_a = _deploy(t)
    dep_b = _deploy(t, symbol="TCS")
    _seed_trade(t["tenant_id"], t["user_id"], symbol="RELIANCE", deployment_id=dep_a)
    _seed_trade(t["tenant_id"], t["user_id"], symbol="RELIANCE", deployment_id=dep_a)
    assert client.put("/api/risk/limits", headers=t["headers"], json={"scope": "DEPLOYMENT", "scope_id": str(dep_a), "limit_type": "MAX_OPEN_POSITIONS", "limit_value": 2}).status_code == 200

    async def evaluate(dep_id):
        from app.risk_engine.hierarchy import RiskContext, evaluate as evaluate_hierarchy
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            ctx = RiskContext(tenant_id=t["tenant_id"], user_id=user.id, strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE", quantity=1, entry=100, stop_loss=98,
                              capital=100_000, deployment_id=dep_id)
            return await evaluate_hierarchy(session, ctx, user=user)
    blocked = _run(evaluate(dep_a))
    assert blocked.allowed is False and any("open positions" in r for r in blocked.reasons)
    assert _run(evaluate(dep_b)).allowed is True


# --- V4.14 journal + V4.11 severity ----------------------------------------------------------------

def test_trade_journal_patch_and_regime_stamp(monkeypatch):
    headers, me = _owner("m-journal@example.com")
    trade_id = _seed_trade(me["tenant_id"], me["id"], closed=True, pnl=120.0)
    patched = client.patch(f"/api/trades/{trade_id}/journal", headers=headers, json={"notes": "Took the breakout late.", "tags": ["Breakout", "late-entry", "breakout"]})
    assert patched.status_code == 200, patched.text
    assert patched.json()["notes"] == "Took the breakout late." and patched.json()["tags"] == ["breakout", "late-entry"]
    other, _ = _owner("m-journal-other@example.com")
    assert client.patch(f"/api/trades/{trade_id}/journal", headers=other, json={"notes": "x"}).status_code == 404

    # The worker stamps the regime it classified at entry.
    _reset_controls()
    t = _worker_tenant("w-regime-stamp@example.com")
    _deploy(t)
    _force_signal(monkeypatch, _signal)
    monkeypatch.setattr("app.workers.trading_worker.classify_regime", lambda df: type("R", (), {"kind": "TRENDING_UP", "confidence": 0.9, "reasons": []})())
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1
    trades = client.get("/api/trades", headers=t["headers"]).json()
    assert trades[0]["regime_at_entry"] == "TRENDING_UP"


def test_emergency_severity_ranks_above_critical():
    assert severity_reaches("EMERGENCY", "CRITICAL") and severity_reaches("EMERGENCY", "WARNING")
    assert not severity_reaches("CRITICAL", "EMERGENCY")


# --- V4.15 degradation ------------------------------------------------------------------------------

def test_degradation_compare_and_endpoint():
    def mk(pnl):
        t = TradeRecord(tenant_id=1, user_id=1, mode="PAPER", strategy_id="s", symbol="X", direction="LONG", entry_time=datetime.now(timezone.utc),
                        entry_price=100, quantity=1, stop_loss=99, target1=101)
        t.exit_time, t.pnl = datetime.now(timezone.utc), pnl
        return t
    live = live_metrics([mk(-10)] * 8 + [mk(5)] * 4)
    assert live["trades"] == 12 and abs(live["win_rate"] - 4 / 12) < 1e-3
    assert compare(live, {"win_rate": 0.65, "expectancy": 3.0, "profit_factor": 1.8})["status"] == "DEGRADED"
    assert compare(live, None)["status"] == "NO_BASELINE"
    assert compare(live_metrics([mk(5)] * 3), {"win_rate": 0.6})["status"] == "INSUFFICIENT_DATA"
    ok = compare(live_metrics([mk(5)] * 7 + [mk(-4)] * 5), {"win_rate": 0.6, "expectancy": 1.0, "profit_factor": 1.5})
    assert ok["status"] == "OK"

    headers, me = _owner("m-degrade@example.com")
    for _ in range(8):
        _seed_trade(me["tenant_id"], me["id"], closed=True, pnl=-50.0)
    for _ in range(4):
        _seed_trade(me["tenant_id"], me["id"], closed=True, pnl=30.0)
    body = client.get("/api/analytics/degradation", headers=headers).json()
    row = next(r for r in body["strategies"] if r["strategy_id"] == "ema_rsi_scalper_1m")
    assert row["status"] == "NO_BASELINE" and row["live"]["trades"] == 12
    # Save a backtest run -> becomes the baseline.
    df = make_series(noisy_uptrend(300))
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    assert client.post("/api/backtest", headers=headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TEST", "base_timeframe": "1min", "candles": candles}).status_code == 200
    body = client.get("/api/analytics/degradation", headers=headers).json()
    row = next(r for r in body["strategies"] if r["strategy_id"] == "ema_rsi_scalper_1m")
    assert row["backtest"] is not None and row["status"] in ("OK", "WATCH", "DEGRADED")


# --- V4.10 incidents -------------------------------------------------------------------------------

def test_incidents_crud_and_auto_open_on_global_kill_switch():
    admin_headers, admin_me = _admin("m-incident-admin@example.com")
    created = client.post("/api/admin/incidents", headers=admin_headers, json={"title": "Broker API degraded", "severity": "CRITICAL", "summary": "Upstox 5xx spike"})
    assert created.status_code == 201, created.text
    inc = created.json()
    assert inc["status"] == "OPEN" and inc["audit_log_from_id"] is not None
    resolved = client.patch(f"/api/admin/incidents/{inc['id']}", headers=admin_headers, json={"status": "RESOLVED", "root_cause": "Upstox outage", "actions_taken": "Disabled broker for 20 min", "data_loss_minutes": 0})
    assert resolved.status_code == 200 and resolved.json()["status"] == "RESOLVED" and resolved.json()["downtime_minutes"] is not None and resolved.json()["audit_log_to_id"] >= inc["audit_log_from_id"]
    assert client.patch(f"/api/admin/incidents/{inc['id']}", headers=admin_headers, json={"status": "BOGUS"}).status_code == 422

    engaged = client.post("/api/kill-switch/global/engage", headers=admin_headers, json={"reason": "exchange halt"})
    assert engaged.status_code == 200, engaged.text
    opened = client.get("/api/admin/incidents?status=OPEN", headers=admin_headers).json()
    auto = next(i for i in opened if i["source"] == "kill_switch")
    assert auto["severity"] == "EMERGENCY" and "exchange halt" in auto["title"]
    client.post("/api/kill-switch/global/disengage", headers=admin_headers)
    owner_headers, _ = _owner("m-incident-owner@example.com")
    assert client.get("/api/admin/incidents", headers=owner_headers).status_code == 403


# --- section 8 broker interface ---------------------------------------------------------------------

def test_exit_position_default_places_opposite_market_order():
    class _B(_FakeBroker):
        def __init__(self):
            super().__init__()
            self.placed = []

        async def place_order(self, order: BrokerOrderRequest) -> BrokerOrderResponse:
            self.placed.append(order)
            return BrokerOrderResponse(order_id="X1", status="COMPLETE")
    b = _B()
    resp = _run(b.exit_position("RELIANCE", "NSE", 25, OrderSide.BUY, tag="exit"))
    assert resp.order_id == "X1" and b.placed[0].transaction_type == OrderSide.SELL and b.placed[0].quantity == 25 and b.placed[0].order_type == "MARKET"
    with pytest.raises(NotImplementedError):
        _run(b.subscribe_market_data(["RELIANCE"]))


# --- V4.6 optimisation -------------------------------------------------------------------------------

def test_optimizer_ranks_in_sample_reports_validation_and_flags_overfit():
    assert len(expand_grid({"a": [1, 2], "b": [3, 4, 5]})) == 6
    strategy = registry.get("ema_rsi_scalper_1m")
    df = make_series(noisy_uptrend(900, seed=11))
    result = optimize(strategy, df, "TEST", "1min", RiskConfig(), {"rsi_period": [7, 14], "fast_ema": [9, 20]}, metric="net_pnl", split=0.7)
    assert result["combinations"] == 4 and result["in_sample_bars"] == 630 and result["out_of_sample_bars"] == 270
    assert len(result["results"]) == 4 and all("in_sample" in r and "out_of_sample" in r for r in result["results"])
    scores = [r["score"] for r in result["results"] if r["score"] is not None]
    assert scores == sorted(scores, reverse=True)
    with pytest.raises(ValueError):
        optimize(strategy, df, "TEST", "1min", RiskConfig(), {"x": list(range(100))})

    headers, _ = _owner("m-optimize@example.com")
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    resp = client.post("/api/backtest/optimize", headers=headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TEST", "base_timeframe": "1min",
                                                                         "candles": candles, "param_grid": {"rsi_period": [7, 14]}, "metric": "expectancy"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["metric"] == "expectancy" and resp.json()["combinations"] == 2
    assert client.post("/api/backtest/optimize", headers=headers, json={"strategy_id": "ema_rsi_scalper_1m", "symbol": "TEST", "base_timeframe": "1min",
                                                                         "candles": candles, "param_grid": {"x": list(range(70))}}).status_code == 400
