"""Phase V1: the Risk Guardian's engine rules - cool-down after a stop-out (R10), the drawdown
ladder (P2/P3), event blackouts and size cuts (M8), portfolio risk with correlated buckets (R4)
and platform ceilings - on single-leg and multi-leg entries, plus the status/events API."""
import asyncio
from datetime import datetime, timedelta, timezone

from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.db.models import TradeRecord, User
from app.execution.signal_execution import execute_signal_for_user
from app.platform import controls
from app.risk_engine.guardian import DRAWDOWN_CUT_MULTIPLIER, bucket_for, is_stop_exit
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_contract_rules import _load_master
from tests.test_live_execution import _signal
from tests.test_multileg import _OptionBroker
from tests.test_phase_r_structures import _execute as _execute_structure, _resolve
from tests.test_trading_worker import _tenant, _trades

IST_TODAY = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()


def _run(coro):
    return asyncio.run(coro)


def _seed(t, *, symbol="RELIANCE", pnl=-100.0, exit_reason="Stop Loss", minutes_ago=5, mode="PAPER", open_=False,
          entry=100.0, stop=98.0, quantity=10, underlying=None):
    async def go():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            session.add(TradeRecord(
                tenant_id=t["tenant_id"], user_id=t["user_id"], mode=mode, strategy_id="ema_rsi_scalper_1m", symbol=symbol,
                underlying_symbol=underlying, direction="LONG", entry_time=now - timedelta(minutes=minutes_ago + 30),
                entry_price=entry, quantity=quantity, stop_loss=stop, target1=entry + 4,
                exit_time=None if open_ else now - timedelta(minutes=minutes_ago), exit_price=None if open_ else entry + pnl / quantity,
                exit_reason=None if open_ else exit_reason, pnl=None if open_ else pnl,
            ))
            await session.commit()
    _run(go())


def _execute(t, *, mode="PAPER", risk_config=None, key=None):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            result, order = await execute_signal_for_user(
                session, user, mode=mode, strategy_id="ema_rsi_scalper_1m", signal=_signal(), idempotency_key=key,
                risk_config=risk_config or RiskConfig(capital=100_000.0, risk_per_trade_pct=1.0, max_daily_loss_pct=50.0, max_open_positions=10),
            )
            trade = await session.get(TradeRecord, order.trade_id) if order.trade_id else None
            return result, order, trade
    return _run(go())


def test_buckets_and_stop_exit_detection():
    assert bucket_for("NIFTY 50") == "INDEX" == bucket_for("BANKNIFTY") == bucket_for("SENSEX")
    assert bucket_for("RELIANCE") == "RELIANCE" and bucket_for("NIFTY 24500 CE 01 OCT 26") != "RELIANCE"
    assert is_stop_exit("Stop Loss") and is_stop_exit("Structure stop (P&L -140.00/unit <= -130)") and is_stop_exit("Premium floor")
    assert is_stop_exit("Short 24500 PE breached (underlying 24490.00)")
    assert not is_stop_exit("Target 1") and not is_stop_exit("Intraday square-off") and not is_stop_exit(None)


def test_cooldown_after_a_stop_out_refuses_reentry_in_the_same_underlying():
    t = _tenant("v1-cooldown@example.com")
    _seed(t, exit_reason="Stop Loss", minutes_ago=5)
    result, order, _ = _execute(t, key="v1-cd-1")
    assert not result.executed and order.status == "REJECTED"
    assert any("Cool-down: RELIANCE was stopped out 5 min ago (Stop Loss)" in r and "rule R10" in r for r in result.reasons)
    # A target exit starts no cool-down, another underlying is unaffected, and 40 minutes later it lifts.
    t2 = _tenant("v1-cooldown-2@example.com")
    _seed(t2, exit_reason="Target 1", minutes_ago=5)
    _seed(t2, symbol="TCS", exit_reason="Stop Loss", minutes_ago=5)
    _seed(t2, exit_reason="Stop Loss", minutes_ago=40)
    result, _, trade = _execute(t2, key="v1-cd-2")
    assert result.executed, result.reasons
    assert trade.quantity == 500     # 1% of 1,00,000 = 1,000 / (100 - 98)


def test_drawdown_ladder_halves_then_pauses_per_mode():
    t = _tenant("v1-dd@example.com")
    # Losses on earlier days: the daily-loss limit (clamped to the 5% ceiling) is about today,
    # the drawdown ladder about the whole equity curve.
    _seed(t, exit_reason="Target 1", minutes_ago=3 * 24 * 60, pnl=-6_000.0)     # 6% below the 1,00,000 peak
    result, _, trade = _execute(t, key="v1-dd-1")
    assert result.executed, result.reasons
    assert trade.quantity == 250 and any("risk per trade halved (rule P2)" in r for r in result.reasons)
    # LIVE has its own ladder: a PAPER drawdown does not shrink LIVE... checked through status below.
    _seed(t, exit_reason="Target 1", minutes_ago=2 * 24 * 60, pnl=-6_000.0)     # 12% below
    result, order, _ = _execute(t, key="v1-dd-2")
    assert not result.executed and any("new entries paused" in r and "rule P3" in r for r in result.reasons)
    status = client.get("/api/risk-guardian/status", headers=t["headers"]).json()
    paper, live = status["modes"]["PAPER"], status["modes"]["LIVE"]
    assert paper["state"] == "paused" and paper["size_multiplier"] == 0.0 and paper["closed_trades"] == 2
    assert live["state"] == "normal" and live["size_multiplier"] == 1.0 and live["drawdown_pct"] == 0.0
    # A peak that is never lowered: a big win raises equity, the multiplier never exceeds 1.
    _seed(t, exit_reason="Target 2", minutes_ago=10, pnl=+40_000.0)
    status = client.get("/api/risk-guardian/status", headers=t["headers"]).json()
    assert status["modes"]["PAPER"]["state"] == "normal" and status["modes"]["PAPER"]["size_multiplier"] == 1.0
    assert status["modes"]["PAPER"]["peak"] == 128_000.0


def test_event_blackout_blocks_and_size_cut_scales():
    t = _tenant("v1-events@example.com")
    created = client.post("/api/risk-guardian/events", headers=t["headers"], json={
        "event_date": IST_TODAY, "underlying": "RELIANCE", "kind": "RESULTS", "action": "BLOCK", "description": "Q2 results"})
    assert created.status_code == 201, created.text
    result, order, _ = _execute(t, key="v1-ev-1")
    assert not result.executed and any("Event blackout: RESULTS - Q2 results today" in r and "rule M8" in r for r in result.reasons)
    assert client.delete(f"/api/risk-guardian/events/{created.json()['id']}", headers=t["headers"]).status_code == 204

    cut = client.post("/api/risk-guardian/events", headers=t["headers"], json={
        "event_date": IST_TODAY, "underlying": "", "kind": "RBI_POLICY", "action": "SIZE_CUT", "size_cut_pct": 50, "description": "policy day"})
    assert cut.status_code == 201
    result, _, trade = _execute(t, key="v1-ev-2")
    assert result.executed, result.reasons
    assert trade.quantity == 250 and any("risk per trade cut 50%" in r for r in result.reasons)
    # A window that has passed does not apply; an INDEX-bucket event leaves an equity alone.
    client.delete(f"/api/risk-guardian/events/{cut.json()['id']}", headers=t["headers"])
    client.post("/api/risk-guardian/events", headers=t["headers"], json={
        "event_date": IST_TODAY, "start_time": "00:00", "end_time": "00:01", "kind": "BUDGET", "action": "BLOCK"})
    client.post("/api/risk-guardian/events", headers=t["headers"], json={
        "event_date": IST_TODAY, "underlying": "INDEX", "kind": "EXPIRY", "action": "BLOCK"})
    result, _, trade = _execute(t, key="v1-ev-3")
    assert result.executed and trade.quantity == 500, result.reasons
    listed = client.get("/api/risk-guardian/events", headers=t["headers"]).json()
    assert len(listed) == 2 and all(not e["global"] for e in listed)
    # Only the platform operator may add a global event.
    forbidden = client.post("/api/risk-guardian/events", headers=t["headers"], json={"event_date": IST_TODAY, "kind": "BUDGET", "action": "BLOCK", "global_event": True})
    assert forbidden.status_code == 403
    assert client.post("/api/risk-guardian/events", headers=t["headers"], json={"event_date": IST_TODAY, "kind": "NONSENSE"}).status_code == 400


def test_portfolio_risk_cap_counts_open_stops_and_buckets():
    t = _tenant("v1-portfolio@example.com")
    # Open TCS position risking 5,200 (5.2%) and an open NIFTY structure leg risking 400: 5,600 open.
    _seed(t, symbol="TCS", open_=True, entry=100.0, stop=48.0, quantity=100)
    _seed(t, symbol="NIFTY 24500 PE 01 OCT 26", underlying="NIFTY 50", open_=True, entry=120.0, stop=124.0, quantity=100)
    result, order, _ = _execute(t, key="v1-pf-1")        # + this trade's 1,000 = 6,600 > 6% of 1,00,000
    assert not result.executed and order.status == "REJECTED"
    assert any("Portfolio risk: open risk at the stops 5,600 + this trade 1,000 = 6,600 exceeds 6%" in r and "rule R4" in r for r in result.reasons)
    status = client.get("/api/risk-guardian/status", headers=t["headers"]).json()["modes"]["PAPER"]
    assert status["open_risk_by_bucket"] == {"TCS": 5200.0, "INDEX": 400.0} and status["open_risk_total"] == 5600.0
    # A wider cap lets it through.
    result, _, trade = _execute(t, key="v1-pf-2", risk_config=RiskConfig(capital=100_000.0, risk_per_trade_pct=1.0, max_daily_loss_pct=50.0,
                                                                          max_open_positions=10, max_portfolio_risk_pct=8.0))
    assert result.executed and trade.quantity == 500, result.reasons


def test_platform_ceilings_refuse_settings_and_clamp_at_runtime():
    t = _tenant("v1-ceilings@example.com")
    too_much = client.put("/api/risk-settings", headers=t["headers"], json={**RiskConfig().model_dump(), "risk_per_trade_pct": 2.5})
    assert too_much.status_code == 400 and "exceeds the platform ceiling 2" in too_much.json()["detail"]
    bad_ladder = client.put("/api/risk-settings", headers=t["headers"], json={**RiskConfig().model_dump(), "dd_level_1_pct": 12, "dd_level_2_pct": 10})
    assert bad_ladder.status_code == 400
    ok = client.put("/api/risk-settings", headers=t["headers"], json={**RiskConfig().model_dump(), "risk_per_trade_pct": 1.0, "stop_cooldown_minutes": 45})
    assert ok.status_code == 200 and ok.json()["stop_cooldown_minutes"] == 45 and ok.json()["max_portfolio_risk_pct"] == 6.0
    assert client.get("/api/risk-settings/ceilings", headers=t["headers"]).json()["risk_per_trade_pct"] == 2.0

    # The operator lowers the ceiling below a saved setting: the engine clamps at runtime and says so.
    async def lower():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            await controls.set_risk_ceilings(session, user, {"risk_per_trade_pct": 0.5})
    _run(lower())
    try:
        result, _, trade = _execute(t, key="v1-ceil-1")
        assert result.executed, result.reasons
        assert trade.quantity == 250 and any("capped at the platform ceiling 0.5" in r for r in result.reasons)
        cfg, notes = controls.clamp_config(RiskConfig(risk_per_trade_pct=1.5, stop_cooldown_minutes=0), {**controls.RISK_CEILINGS_DEFAULT, "min_stop_cooldown_minutes": 15})
        assert cfg.risk_per_trade_pct == 1.5 and cfg.stop_cooldown_minutes == 15 and len(notes) == 1
    finally:
        async def restore():
            async with _session_factory() as session:
                user = await session.get(User, t["user_id"])
                await controls.set_risk_ceilings(session, user, {"risk_per_trade_pct": 2.0})
        _run(restore())


def test_structures_pass_through_the_guardian_too():
    _load_master()
    t = _tenant("v1-structure@example.com")
    _seed(t, symbol="NIFTY 24500 PE 01 OCT 26", underlying="NIFTY 50", exit_reason="Spread stop (value 90.00 >= 80)", minutes_ago=3)
    refused = _execute_structure(t, _resolve(OptionStrategy.BULL_PUT_SPREAD), key="v1-st-1", capital=5_000_000)
    assert not refused.executed and any("Cool-down: NIFTY was stopped out 3 min ago" in r for r in refused.reasons)
    assert _trades(t["tenant_id"]) and all(tr.exit_time is not None for tr in _trades(t["tenant_id"]))
    t2 = _tenant("v1-structure-2@example.com")
    _seed(t2, symbol="NIFTY 24500 PE 01 OCT 26", underlying="NIFTY 50", exit_reason="Spread stop (value 90.00 >= 80)", minutes_ago=3, mode="LIVE")
    ok = _execute_structure(t2, _resolve(OptionStrategy.BULL_PUT_SPREAD), key="v1-st-2", capital=5_000_000)   # PAPER: LIVE's stop-out is not its cool-down
    assert ok.executed, ok.reasons
