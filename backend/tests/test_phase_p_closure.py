"""Phase P: protective-stop re-arm, financial-year tax report, base currency + FX, drift gate,
staging deploy assets."""
import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest
from sqlalchemy import select

from app.ai import monitor
from app.brokers.models import BrokerOrderResponse, BrokerOrderStatus
from app.core.enums import OrderSide
from app.db.models import BacktestRunRecord, FxRateRecord, StrategyDeploymentRecord, Tenant, TradeRecord
from app.fx import service as fx
from app.market_data.calendar import IST
from app.tax import report as tax
from app.trading.stop_guard import verify_protective_stops
from tests.test_admin_api import _admin
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import _FakeBroker, _deploy, _get, _tenant


def _run(coro):
    return asyncio.run(coro)


def _owner(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def _seed(tenant_id, user_id, *, symbol="RELIANCE", exchange="NSE", kind="UNDERLYING", direction="LONG", entry=100.0, exit_=None,
          qty=10, pnl=None, charges=0.0, mode="LIVE", exit_at=None, sl_order_id=None, deployment_id=None, leg_group_id=None) -> int:
    async def go():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            t = TradeRecord(tenant_id=tenant_id, user_id=user_id, mode=mode, strategy_id="ema_rsi_scalper_1m", symbol=symbol, exchange=exchange,
                            instrument_kind=kind, direction=direction, entry_time=(exit_at or now) - timedelta(minutes=30), entry_price=entry,
                            quantity=qty, stop_loss=entry - 2 if direction == "LONG" else entry + 2, target1=entry + 4, target2=entry + 8,
                            sl_order_id=sl_order_id, deployment_id=deployment_id, leg_group_id=leg_group_id, charges=charges)
            if exit_ is not None:
                t.exit_time, t.exit_price, t.pnl, t.exit_reason = exit_at or now, exit_, pnl, "TEST"
            session.add(t)
            await session.commit()
            return t.id
    return _run(go())


# --- P1: protective-stop re-arm ----------------------------------------------------------------------

class _BookBroker(_FakeBroker):
    def __init__(self, book: List[BrokerOrderStatus], fail_stop=False):
        super().__init__()
        self.book = book
        self.stops = []
        self.fail_stop = fail_stop

    async def get_order_book(self):
        return self.book

    async def place_stop_loss_order(self, symbol, exchange, transaction_type, quantity, trigger_price, product="MIS", tag=None):
        if self.fail_stop:
            raise ConnectionError("margin check failed")
        self.stops.append((symbol, transaction_type, quantity, trigger_price, tag))
        return BrokerOrderResponse(order_id=f"SL-{len(self.stops)}", status="TRIGGER PENDING")


def _order(order_id, status, symbol="RELIANCE"):
    return BrokerOrderStatus(order_id=order_id, symbol=symbol, transaction_type=OrderSide.SELL, quantity=10, order_type="SL-M", status=status)


def test_stop_guard_rearms_missing_and_cancelled_stops_and_leaves_standing_ones():
    t = _tenant("p1-guard@example.com")
    standing = _seed(t["tenant_id"], t["user_id"], symbol="TCS", sl_order_id="SL-OK")
    cancelled = _seed(t["tenant_id"], t["user_id"], symbol="INFY", sl_order_id="SL-CANCELLED")
    missing = _seed(t["tenant_id"], t["user_id"], symbol="RELIANCE", sl_order_id=None)
    filled = _seed(t["tenant_id"], t["user_id"], symbol="SBIN", sl_order_id="SL-FILLED")
    paper = _seed(t["tenant_id"], t["user_id"], symbol="HDFC", mode="PAPER", sl_order_id=None)
    leg = _seed(t["tenant_id"], t["user_id"], symbol="NIFTY24OCT25000CE", exchange="NFO", kind="OPTION", sl_order_id=None, leg_group_id="grp-1")
    broker = _BookBroker([_order("SL-OK", "TRIGGER PENDING", "TCS"), _order("SL-CANCELLED", "CANCELLED", "INFY"), _order("SL-FILLED", "COMPLETE", "SBIN")])

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            return await verify_protective_stops(session, tenant, broker, user_id=t["user_id"])
    counts = _run(go())
    assert counts == {"checked": 4, "standing": 1, "rearmed": 2, "filled_pending": 1, "failed": 0}
    assert sorted(s[0] for s in broker.stops) == ["INFY", "RELIANCE"]
    assert all(s[1] == OrderSide.SELL and s[3] == 98.0 for s in broker.stops)
    assert _get(TradeRecord, cancelled).sl_order_id.startswith("SL-") and _get(TradeRecord, cancelled).sl_order_id != "SL-CANCELLED"
    assert _get(TradeRecord, missing).sl_order_id is not None
    assert _get(TradeRecord, standing).sl_order_id == "SL-OK" and _get(TradeRecord, filled).sl_order_id == "SL-FILLED"
    assert _get(TradeRecord, paper).sl_order_id is None and _get(TradeRecord, leg).sl_order_id is None
    notes = client.get("/api/notifications", headers=t["headers"]).json()
    items = notes if isinstance(notes, list) else notes.get("items", [])
    assert sum(1 for n in items if "re-armed" in n["title"]) == 2


def test_stop_guard_failure_raises_critical_once_and_keeps_position_open():
    t = _tenant("p1-guard-fail@example.com")
    trade = _seed(t["tenant_id"], t["user_id"], symbol="RELIANCE", sl_order_id=None)
    broker = _BookBroker([], fail_stop=True)

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            first = await verify_protective_stops(session, tenant, broker, user_id=t["user_id"])
            second = await verify_protective_stops(session, tenant, broker, user_id=t["user_id"])
            return first, second
    first, second = _run(go())
    assert first["failed"] == 1 and second["failed"] == 1
    assert _get(TradeRecord, trade).exit_time is None and _get(TradeRecord, trade).sl_order_id is None
    notes = client.get("/api/notifications", headers=t["headers"]).json()
    items = notes if isinstance(notes, list) else notes.get("items", [])
    assert sum(1 for n in items if "No broker-side stop" in n["title"]) == 1   # cooldown: one alert, not one per cycle


def test_stop_guard_runs_in_worker_cycle(monkeypatch):
    from tests.test_trading_worker import OPEN_NOW, _worker
    t = _tenant("p1-guard-worker@example.com")
    _deploy(t, mode="LIVE")
    _seed(t["tenant_id"], t["user_id"], symbol="RELIANCE", sl_order_id="SL-GONE")
    broker = _BookBroker([_order("SL-GONE", "REJECTED")])
    broker.ltp = 100.5
    worker = _worker(monkeypatch, broker)
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.stops_rearmed == 1 and len(broker.stops) == 1


# --- P2: tax report ----------------------------------------------------------------------------------

def test_financial_year_helpers():
    assert tax.financial_year(datetime(2026, 3, 31).date()) == "2025-26"
    assert tax.financial_year(datetime(2026, 4, 1).date()) == "2026-27"
    start, end = tax.fy_bounds("2026-27")
    assert start.astimezone(IST).isoformat().startswith("2026-04-01T00:00") and end.astimezone(IST).isoformat().startswith("2027-04-01T00:00")
    with pytest.raises(ValueError):
        tax.fy_bounds("nope")


def test_tax_report_classifies_and_estimates_by_income_head():
    headers, me = _owner("p2-tax@example.com")
    when = datetime(2026, 6, 15, 10, 0, tzinfo=IST).astimezone(timezone.utc)
    # Equity intraday: buy 100 -> sell 104 x 10 => gross 40, charges 5 => net 35; sell value 1040
    _seed(me["tenant_id"], me["id"], symbol="RELIANCE", entry=100.0, exit_=104.0, qty=10, pnl=35.0, charges=5.0, exit_at=when)
    # Option bought: premium 50 -> 40 x 50 => gross -500, charges 20 => net -520; premium sold (exit) 2000
    _seed(me["tenant_id"], me["id"], symbol="NIFTY26JUN25000CE", exchange="NFO", kind="OPTION", entry=50.0, exit_=40.0, qty=50, pnl=-520.0, charges=20.0, exit_at=when)
    # Crypto: BTCINR long 100 -> 110 x 1 => +10 ; and a loss 100 -> 95 => -5 (not set off)
    _seed(me["tenant_id"], me["id"], symbol="BTCINR", exchange="CRYPTO", entry=100.0, exit_=110.0, qty=1, pnl=10.0, exit_at=when)
    _seed(me["tenant_id"], me["id"], symbol="BTCINR", exchange="CRYPTO", entry=100.0, exit_=95.0, qty=1, pnl=-5.0, exit_at=when)
    # Previous FY and PAPER are excluded by default
    _seed(me["tenant_id"], me["id"], symbol="TCS", entry=100.0, exit_=101.0, qty=10, pnl=10.0, exit_at=datetime(2026, 3, 30, 10, 0, tzinfo=IST).astimezone(timezone.utc))
    _seed(me["tenant_id"], me["id"], symbol="INFY", entry=100.0, exit_=101.0, qty=10, pnl=10.0, mode="PAPER", exit_at=when)

    r = client.get("/api/tax/report?fy=2026-27", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    eq, fno, crypto = body["classes"]["EQUITY_INTRADAY"], body["classes"]["FNO"], body["classes"]["CRYPTO"]
    assert body["trades"] == 4 and body["net_pnl"] == pytest.approx(35 - 520 + 10 - 5)
    assert eq["trades"] == 1 and eq["gross_pnl"] == 40.0 and eq["net_pnl"] == 35.0 and eq["turnover"] == 40.0
    assert eq["stt_estimate"] == pytest.approx(1040 * 0.025 / 100, abs=0.01)
    assert fno["trades"] == 1 and fno["gross_pnl"] == -500.0 and fno["turnover"] == pytest.approx(500 + 2000)
    assert fno["stt_estimate"] == pytest.approx(2000 * 0.1 / 100, abs=0.01) and fno["income_head"].startswith("Non-speculative")
    assert crypto["trades"] == 2 and crypto["taxable_gains"] == 10.0 and crypto["disallowed_losses"] == 5.0 and crypto["tax_estimate"] == 3.0
    assert crypto["tds_estimate"] == pytest.approx((110 + 95) * 1.0 / 100, abs=0.01)
    assert any("115BBH" in n for n in body["notes"]) and body["rates"]["CRYPTO"]["tax_pct"] == 30.0

    years = client.get("/api/tax/years", headers=headers).json()
    assert "2026-27" in years["years"] and "2025-26" in years["years"]
    all_modes = client.get("/api/tax/report?fy=2026-27&mode=ALL", headers=headers).json()
    assert all_modes["trades"] == 5
    assert client.get("/api/tax/report?fy=2026-27&mode=BOTH", headers=headers).status_code == 400
    assert client.get("/api/tax/report?fy=garbage", headers=headers).status_code == 400
    csv_resp = client.get("/api/tax/report.csv?fy=2026-27", headers=headers)
    assert csv_resp.status_code == 200 and "text/csv" in csv_resp.headers["content-type"]
    assert "EQUITY_INTRADAY,Speculative business income,1" in csv_resp.text and "attachment" in csv_resp.headers["content-disposition"]
    # Another tenant sees nothing of it
    other, _ = _owner("p2-tax-other@example.com")
    assert client.get("/api/tax/report?fy=2026-27", headers=other).json()["trades"] == 0


# --- P3: base currency + FX ----------------------------------------------------------------------------

def test_fx_convert_direct_inverse_pivot_and_missing():
    rates = {("USD", "INR"): 84.0, ("USDT", "INR"): 83.5}
    assert fx.convert(2, "USD", "INR", rates) == 168.0
    assert fx.convert(168, "INR", "USD", rates) == pytest.approx(2.0)
    assert fx.convert(1, "USD", "USDT", rates) == pytest.approx(84.0 / 83.5)
    assert fx.convert(5, "INR", "INR", {}) == 5
    with pytest.raises(fx.FxError):
        fx.convert(1, "EUR", "INR", rates)


def test_admin_sets_rates_owner_sets_base_currency_and_exposure_converts(monkeypatch):
    admin_headers, _ = _admin("p3-admin@example.com")
    headers, me = _owner("p3-owner@example.com")
    assert client.put("/api/admin/fx-rates", headers=headers, json={"base": "USD", "quote": "INR", "rate": 84}).status_code == 403
    assert client.put("/api/admin/fx-rates", headers=admin_headers, json={"base": "XYZ", "quote": "INR", "rate": 84}).status_code == 400
    r = client.put("/api/admin/fx-rates", headers=admin_headers, json={"base": "USD", "quote": "INR", "rate": 84.0, "source": "rbi-ref"})
    assert r.status_code == 200 and r.json()["rate"] == 84.0
    r = client.put("/api/admin/fx-rates", headers=admin_headers, json={"base": "USD", "quote": "INR", "rate": 85.0})
    assert r.json()["rate"] == 85.0 and len(client.get("/api/admin/fx-rates", headers=admin_headers).json()) >= 1
    assert any(x["base"] == "USD" for x in client.get("/api/fx/rates", headers=headers).json()["rates"])

    assert client.get("/api/team/tenant", headers=headers).json()["base_currency"] == "INR"
    assert client.patch("/api/team/tenant", headers=headers, json={"base_currency": "ZZZ"}).status_code == 400
    assert client.patch("/api/team/tenant", headers=headers, json={"base_currency": "usd"}).json()["base_currency"] == "USD"

    # A USD-quoted instrument spec: 10 units at 100 USD = 1,000 USD notional; INR position converts at 1/85.
    from app.instruments import registry
    from app.instruments.models import ContractSpec
    from app.core.enums import AssetClass
    monkeypatch.setitem(registry.CONTRACT_SPECS, "BTCUSDT", ContractSpec(symbol="BTCUSDT", exchange="CRYPTO", asset_class=AssetClass.CRYPTO,
                                                                          description="BTC/USDT", lot_size=0.0001, tick_size=0.01, fractional=True,
                                                                          quote_currency="USDT"))
    _seed(me["tenant_id"], me["id"], symbol="RELIANCE", entry=850.0, qty=10, mode="PAPER")      # 8,500 INR = 100 USD
    _seed(me["tenant_id"], me["id"], symbol="BTCUSDT", exchange="CRYPTO", entry=100.0, qty=10, mode="PAPER")   # 1,000 USDT -> no USDT rate yet
    body = client.get("/api/portfolio/exposure?live_prices=false", headers=headers).json()
    assert body["base_currency"] == "USD" and body["fx_missing"] == ["USDT->USD"]
    assert body["fx_rates_used"]["INR->USD"] == pytest.approx(1 / 85.0)
    client.put("/api/admin/fx-rates", headers=admin_headers, json={"base": "USDT", "quote": "INR", "rate": 85.0})
    body = client.get("/api/portfolio/exposure?live_prices=false", headers=headers).json()
    assert body["fx_missing"] == [] and body["gross_notional"] == pytest.approx(100.0 + 1000.0, rel=1e-6)


# --- P4: drift gate -----------------------------------------------------------------------------------

def test_degradation_proposes_pause_when_live_diverges_from_backtest():
    t = _tenant("p4-drift@example.com")
    dep_id = _deploy(t)
    now = datetime.now(timezone.utc)

    async def seed():
        async with _session_factory() as session:
            session.add(BacktestRunRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE",
                                          base_timeframe="1min", metrics_json=json.dumps({"win_rate": 0.65, "expectancy": 120.0, "profit_factor": 1.8})))
            for i in range(12):
                pnl = -200.0 if i % 4 else 50.0     # 25% win rate, negative expectancy
                session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE",
                                        direction="LONG", entry_time=now - timedelta(days=2, minutes=60 - i), entry_price=100.0, quantity=10, stop_loss=99.0,
                                        target1=102.0, target2=104.0, exit_time=now - timedelta(days=2, minutes=50 - i), exit_price=100.0 + pnl / 10,
                                        exit_reason="TEST", pnl=pnl, deployment_id=dep_id))
            await session.commit()
            deps = list(await session.scalars(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.id == dep_id)))
            return await monitor.observe(session, t["tenant_id"], deps, now)
    proposals = _run(seed())
    rules = {(p.rule, p.action) for p in proposals}
    assert ("DEGRADATION", "PAUSE_DEPLOYMENT") in rules and ("WIN_RATE_DRIFT", "REVIEW_STRATEGY") in rules
    drift = next(p for p in proposals if p.rule == "DEGRADATION")
    assert drift.evidence["closed_trades"] == 12 and any("win rate" in r for r in drift.evidence["reasons"])


# --- P5: staging deploy assets -------------------------------------------------------------------------

def test_deploy_assets_parse():
    root = Path(__file__).resolve().parents[2]
    import yaml
    staging = yaml.safe_load((root / "docker-compose.staging.yml").read_text())
    assert {"postgres", "backend", "worker", "frontend", "backup"} <= set(staging["services"])
    assert staging["services"]["backend"]["environment"]["ENVIRONMENT"] == "staging"
    assert "18000:8000" in staging["services"]["backend"]["ports"]
    assert subprocess.run(["sh", "-n", str(root / "scripts" / "deploy.sh")], capture_output=True).returncode == 0
    workflow = yaml.safe_load((root / ".github" / "workflows" / "deploy-staging.yml").read_text())
    assert "deploy" in workflow["jobs"] and "STAGING_ENABLED" in workflow["jobs"]["deploy"]["if"]
