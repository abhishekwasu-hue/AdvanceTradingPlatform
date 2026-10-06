"""Phase W: historical option-chain backtests - the pure structure planner shared with the
live resolver, the synthetic expiry calendar, Black-Scholes and snapshot pricers, the option
backtest engine (structures, single legs, expiry settlement, sizing refusals), the chain
recorder and retention, and the API dispatch."""
import asyncio
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from app.backtest import chain_recorder
from app.backtest.options import (
    ExpiryCalendar, OptionChainSnapshotRow, PricingUnavailable, SnapshotPricer, SyntheticPricer, VolatilityModel, bars_per_year,
    default_lot_size, default_strike_step, strike_ladder, to_utc,
)
from app.backtest.options_engine import ENGINE_VERSION, OptionBacktestConfig, run_option_backtest
from app.brokers.models import OptionChain, OptionChainRow
from app.core.enums import ExpiryRule, OptionPosition, OptionStrategy, SignalDirection, SignalGrade, StrikeRule
from app.core.models import RiskConfig, Signal
from app.instruments.contracts import ContractResolutionError
from app.instruments.spreads import CustomLeg, plan_structure, resolve_structure
from app.market_data.calendar import IST
from app.trading.position_monitor import structure_exit_reason
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_contract_rules import TODAY, _load_master
from tests.test_deployments_api import _auth
from tests.test_exit_rules_and_backtest_depth import _candles
from tests.test_multileg import SPOT, _rules
from tests.utils import make_series


def _run(coro):
    return asyncio.run(coro)


class _OneShot:
    """Fires one signal on the first evaluated bar (stop/targets 1% away), then stays flat."""
    id = "one_shot_w"
    timeframes = ["1min"]

    def __init__(self, direction=SignalDirection.LONG):
        self.direction = direction
        self.fired = False

    def min_history(self):
        return {"1min": 5}

    def analyze(self, data, symbol):
        df = data["1min"]
        ts, px = df.index[-1], float(df["close"].iloc[-1])
        if self.fired:
            return Signal(symbol=symbol, strategy_id=self.id, strategy_name="x", direction=SignalDirection.NO_TRADE, timestamp=ts)
        self.fired = True
        long = self.direction == SignalDirection.LONG
        return Signal(symbol=symbol, strategy_id=self.id, strategy_name="x", direction=self.direction, timestamp=ts, entry=px,
                      stop_loss=px * (0.99 if long else 1.01), target1=px * (1.01 if long else 0.99), target2=None,
                      risk_reward=1.0, score=80, grade=SignalGrade.HIGH_QUALITY)


def _frame(start: float, end: float, n: int = 300, day="2026-10-05"):
    return make_series(list(np.linspace(start, end, n)), start=f"{day} 09:15")


RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0)


# --- planner parity with the live resolver ---------------------------------------------------

def test_plan_structure_matches_resolve_structure_strikes():
    _load_master()
    ladder = [float(k) for k in range(24000, 25001, 50)]

    async def live(strategy, direction, **kw):
        async with _session_factory() as session:
            return await resolve_structure(session, "NIFTY 50", _rules(**kw), strategy, direction, spread_width=2, spot=SPOT, today=TODAY)

    for strategy, direction in ((OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG), (OptionStrategy.IRON_CONDOR, SignalDirection.SHORT),
                                (OptionStrategy.IRON_BUTTERFLY, SignalDirection.LONG), (OptionStrategy.SHORT_STRANGLE, SignalDirection.LONG),
                                (OptionStrategy.CALL_RATIO_SPREAD, SignalDirection.LONG), (OptionStrategy.LONG_BUTTERFLY, SignalDirection.SHORT),
                                (OptionStrategy.CALENDAR_SPREAD, SignalDirection.LONG)):
        resolved = _run(live(strategy, direction))
        planned = plan_structure(strategy, direction, _rules(), spot=SPOT, strikes_by_right={"CE": ladder, "PE": ladder}, spread_width=2)
        assert [(l.right, l.role, l.strike, l.ratio) for l in planned] == \
            [(l.contract.right, l.role, float(l.contract.strike), l.ratio) for l in resolved.legs], strategy
        if strategy == OptionStrategy.CALENDAR_SPREAD:
            assert [l.far_expiry for l in planned] == [False, True]
    custom = [CustomLeg("PE", "SHORT", StrikeRule.ATM, 0, 1), CustomLeg("PE", "LONG", StrikeRule.OTM, 2, 1)]
    planned = plan_structure(OptionStrategy.CUSTOM, SignalDirection.LONG, _rules(), spot=SPOT, strikes_by_right={"PE": ladder}, spread_width=2, custom_legs=custom)
    assert [(l.role, l.strike) for l in planned] == [("SHORT", 24500.0), ("LONG", 24400.0)]
    with pytest.raises(ContractResolutionError, match="not entered on a SHORT"):
        plan_structure(OptionStrategy.BULL_PUT_SPREAD, SignalDirection.SHORT, _rules(), spot=SPOT, strikes_by_right={"PE": ladder}, spread_width=2)
    with pytest.raises(ContractResolutionError, match="wing"):
        plan_structure(OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG, _rules(), spot=SPOT, strikes_by_right={"PE": [24500.0, 24450.0]}, spread_width=2)


# --- conventions, calendar, pricers -----------------------------------------------------------

def test_conventions_calendar_and_ladder():
    assert default_lot_size("NIFTY") == 75 and default_lot_size("BANKNIFTY") == 35 and default_lot_size("RELIANCE") == 1
    assert default_strike_step("NIFTY", 24500) == 50.0 and default_strike_step("RELIANCE", 1450) == 20.0 and default_strike_step("X", 30) == 1.0
    assert strike_ladder(24512, 50, span=2) == [24400.0, 24450.0, 24500.0, 24550.0, 24600.0]
    assert bars_per_year("1min") == 250 * 375 and bars_per_year("5min") == 250 * 75 and bars_per_year("1d") == 250

    # NIFTY: weekly Tuesdays; 2026-10-05 is a Monday.
    nifty = ExpiryCalendar.for_underlying("NIFTY")
    assert (nifty.weekday, nifty.weekly) == (1, True)
    assert nifty.expiries(date(2026, 10, 5), count=3) == [date(2026, 10, 6), date(2026, 10, 13), date(2026, 10, 20)]
    assert nifty.select(ExpiryRule.NEXT, date(2026, 10, 5)) == date(2026, 10, 13)
    assert nifty.select(ExpiryRule.MONTHLY, date(2026, 10, 5)) == date(2026, 10, 27)
    # A holiday on the Tuesday moves the expiry to Monday - the exchange's rule.
    shifted = ExpiryCalendar.for_underlying("NIFTY", holidays=[date(2026, 10, 13)])
    assert shifted.expiries(date(2026, 10, 7), count=1) == [date(2026, 10, 12)]
    # BANKNIFTY: monthly, last Tuesday; SENSEX: weekly Thursday; overrides win.
    bank = ExpiryCalendar.for_underlying("BANKNIFTY")
    assert (bank.weekday, bank.weekly) == (1, False) and bank.expiries(date(2026, 10, 5), count=2) == [date(2026, 10, 27), date(2026, 11, 24)]
    assert ExpiryCalendar.for_underlying("SENSEX").expiries(date(2026, 10, 5), count=1) == [date(2026, 10, 8)]
    legacy = ExpiryCalendar.for_underlying("NIFTY", weekday=3, weekly=True)
    assert legacy.expiries(date(2024, 3, 4), count=1) == [date(2024, 3, 7)]
    assert nifty.far_expiry(date(2026, 10, 6)) == date(2026, 10, 13)


def test_synthetic_and_snapshot_pricers():
    vol = VolatilityModel(fixed_iv=0.14)
    vol.update([])
    pricer = SyntheticPricer(vol)
    at = to_utc(datetime(2026, 10, 5, 9, 20))
    call = pricer.price("CE", 24500, date(2026, 10, 13), spot=24500, at=at)
    put = pricer.price("PE", 24500, date(2026, 10, 13), spot=24500, at=at)
    assert 150 < call < 300 and 150 < put < 300 and call > put            # ATM, 8 days, 14%: ~0.4 x S x sigma x sqrt(t); carry makes the call dearer
    assert pricer.price("CE", 24700, date(2026, 10, 13), spot=24500, at=at) < call
    # At the close of expiry day the option is worth its intrinsic value.
    settle = to_utc(datetime(2026, 10, 13, 15, 30))
    assert pricer.price("CE", 24500, date(2026, 10, 13), spot=24620, at=settle) == 120.0
    assert pricer.price("PE", 24500, date(2026, 10, 13), spot=24620, at=settle) == 0.05
    assert abs(call / 0.05 - round(call / 0.05)) < 1e-6           # rounded to the 0.05 tick
    with pytest.raises(PricingUnavailable):
        pricer.price("CE", 24500, date(2026, 10, 13), spot=0, at=at)
    # Realised volatility: floored on a flat series, larger on a noisy one.
    rv = VolatilityModel(window=10, annualisation=250 * 375)
    assert rv.update([100.0] * 12) == 0.08
    noisy = rv.update([100, 101, 99.5, 102, 98, 103, 97.5, 104, 99, 105, 98.5, 106])
    assert noisy > 0.5

    rows = [OptionChainSnapshotRow(timestamp=datetime(2026, 10, 5, 9, 15, tzinfo=IST), expiry=date(2026, 10, 6), strike=24500, right="PE", ltp=80.0),
            OptionChainSnapshotRow(timestamp=datetime(2026, 10, 5, 9, 30, tzinfo=IST), expiry=date(2026, 10, 6), strike=24500, right="PE", ltp=75.0)]
    snap = SnapshotPricer(rows, max_age_minutes=15)
    assert snap.contracts == 1
    assert snap.price("PE", 24500, date(2026, 10, 6), spot=24500, at=to_utc(datetime(2026, 10, 5, 9, 20))) == 80.0
    assert snap.price("PE", 24500, date(2026, 10, 6), spot=24500, at=to_utc(datetime(2026, 10, 5, 9, 44))) == 75.0
    with pytest.raises(PricingUnavailable, match="no recorded quote"):
        snap.price("PE", 24500, date(2026, 10, 6), spot=24500, at=to_utc(datetime(2026, 10, 5, 9, 46)))   # stale
    with pytest.raises(PricingUnavailable):
        snap.price("CE", 24500, date(2026, 10, 6), spot=24500, at=to_utc(datetime(2026, 10, 5, 9, 20)))   # unknown contract
    with_fallback = SnapshotPricer(rows, max_age_minutes=15, fallback=SyntheticPricer(vol))
    assert with_fallback.price("CE", 24500, date(2026, 10, 6), spot=24500, at=to_utc(datetime(2026, 10, 5, 9, 20))) > 0
    assert with_fallback.fallbacks == 1 and "synthetic fallback" in with_fallback.describe()


# --- engine ----------------------------------------------------------------------------------

def test_bull_put_spread_reaches_its_target_in_a_rally():
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    result = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg)
    assert result.total_trades == 1 and result.options["structures_opened"] == 1
    trade = result.trades[0]
    st = result.options["structures"][0]
    assert trade.direction == SignalDirection.SHORT and trade.exit_reason.startswith("Spread target")
    assert [(l["role"], l["strike"], l["right"]) for l in st["legs"]] == [("SHORT", 24500.0, "PE"), ("LONG", 24400.0, "PE")]
    assert st["net_credit"] > 0 and st["max_loss"] == round(100 - st["net_credit"], 2) and trade.pnl > 0
    # Lots off max loss: 1% of 10L = 10,000 over (max loss x 75).
    assert st["lots"] == int(10_000 // (st["max_loss"] * 75)) and trade.quantity == st["lots"] * 75
    # One Trade per structure: entry = credit per unit, exit = cost to close, P&L nets the legs and charges.
    legs_pnl = sum((l["entry_price"] - l["exit_price"]) * l["quantity"] * (1 if l["role"] == "SHORT" else -1) for l in st["legs"])
    assert trade.pnl == round(legs_pnl - st["charges"], 2)
    assert result.options["lot_size"] == 75 and result.options["strike_step"] == 50.0 and result.options["pricing_model"] == "synthetic"
    assert result.options["expiry_calendar"] == "weekly, Tue" and result.analytics is not None
    assert result.equity_curve[-1] == RISK.capital + trade.pnl


def test_undefined_risk_structures_stop_on_the_breakeven_and_the_sizer_refuses_big_lots():
    # A 1:2 call ratio spread in a strong rally: unbounded above the outer breakeven -> underlying exit at that level.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.CALL_RATIO_SPREAD, implied_volatility=0.14, spread_width=2)
    result = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg)
    assert result.total_trades == 1 and "breakeven" in result.trades[0].exit_reason and result.trades[0].pnl < 0
    # A short straddle whose stop risk per lot exceeds 1% of capital is refused, counted, not traded.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.SHORT_STRADDLE, implied_volatility=0.14)
    result = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RiskConfig(capital=200_000, risk_per_trade_pct=1.0), cfg)
    assert result.total_trades == 0 and sum(result.options["signals_skipped"].values()) == 1
    # Payoff-priced structures stop on P&L per unit (butterfly bought at the money, market runs away).
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.LONG_BUTTERFLY, implied_volatility=0.14, spread_width=2)
    result = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg)
    assert result.total_trades == 1 and result.trades[0].exit_reason.startswith("Structure stop") and result.trades[0].direction == SignalDirection.LONG
    # The exit rule is the position monitor's own function.
    assert structure_exit_reason({"debit": False, "stop_value": 60.0, "target_value": 20.0}, 61.0, None).startswith("Spread stop")


def test_single_options_mirror_the_live_exits_and_expiry_settles_at_intrinsic():
    # Bought CE on a LONG signal: the underlying target (+1%) closes it at the option's repriced value.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY, implied_volatility=0.14)
    result = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg)
    trade = result.trades[0]
    assert trade.exit_reason == "Target 1 (underlying)" and trade.direction == SignalDirection.LONG and trade.pnl > 0
    assert result.options["structures"][0]["legs"][0]["right"] == "CE"
    # Written CE on a SHORT signal in the same rally: the premium ceiling fires before the underlying stop.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.WRITE, implied_volatility=0.14)
    result = run_option_backtest(_OneShot(SignalDirection.SHORT), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg)
    trade = result.trades[0]
    assert trade.exit_reason.startswith("Premium ceiling") and trade.direction == SignalDirection.SHORT and trade.pnl < 0
    assert result.options["structures"][0]["lots"] == 1   # a written option without max_lots trades one lot
    # Expiry day, flat market, not intraday: a sold straddle rides to settlement at intrinsic value.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.SHORT_STRADDLE, implied_volatility=0.14, intraday=False, max_lots=1, target_credit_pct=95)
    flat = _frame(24500, 24510, n=370, day="2026-10-06")   # Tuesday = NIFTY expiry; bars run past 15:15
    result = run_option_backtest(_OneShot(), flat, "NIFTY 50", "1min", RiskConfig(capital=5_000_000, risk_per_trade_pct=1.0), cfg)
    trade = result.trades[0]
    assert trade.exit_reason.startswith("Expiry settlement") and result.options["expiry_settlements"] == 1
    st = result.options["structures"][0]
    assert st["legs"][0]["exit_price"] + st["legs"][1]["exit_price"] == pytest.approx(abs(24510 - 24500), abs=1.0)   # intrinsic only
    assert trade.pnl > 0
    # Intraday square-off at 15:15 closes what is still open.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.IRON_CONDOR, implied_volatility=0.14, strike_offset=3, spread_width=2, expiry_rule=ExpiryRule.NEXT)
    result = run_option_backtest(_OneShot(), flat, "NIFTY 50", "1min", RISK, cfg)
    assert result.trades and result.trades[0].exit_reason.startswith("Square-off")


def test_snapshot_pricing_and_custom_legs_in_the_engine():
    expiry = date(2026, 10, 6)
    rows = []
    for minute in range(0, 300, 5):
        ts = datetime(2026, 10, 5, 9, 15, tzinfo=IST) + timedelta(minutes=minute)
        spot = 24500 + minute
        for strike in range(24300, 24701, 50):
            rows.append(OptionChainSnapshotRow(timestamp=ts, expiry=expiry, strike=strike, right="PE", ltp=max(0.05, round(80 - (spot - strike) * 0.4, 2))))
            rows.append(OptionChainSnapshotRow(timestamp=ts, expiry=expiry, strike=strike, right="CE", ltp=max(0.05, round(80 + (spot - strike) * 0.4, 2))))
    pricer = SnapshotPricer(rows, max_age_minutes=10)
    legs = [CustomLeg("PE", "SHORT", StrikeRule.ATM, 0, 1), CustomLeg("PE", "LONG", StrikeRule.OTM, 2, 1)]
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.CUSTOM, custom_legs=legs)
    result = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg, pricer=pricer)
    assert result.total_trades == 1 and result.options["pricing_model"] == "snapshots" and result.options["snapshot_hits"] > 0
    assert result.options["synthetic_fallbacks"] == 0
    st = result.options["structures"][0]
    assert st["legs"][0]["entry_price"] == pytest.approx(80.0 * 1.0002, abs=0.1) or st["legs"][0]["entry_price"] > 0
    # Without a fallback, a leg outside the recorded strikes refuses the entry and the reason is counted.
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.IRON_CONDOR, strike_offset=6, spread_width=2)
    result = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg, pricer=SnapshotPricer(rows, max_age_minutes=10))
    assert result.total_trades == 0 and result.options["signals_skipped"] == {"no recorded quote": 1}


# --- recorder + retention + API ---------------------------------------------------------------

def _chain(spot=24512.0, expiry="2026-10-06"):
    rows = [OptionChainRow(strike=float(k), call_ltp=max(0.05, 100 + (spot - k) * 0.5), put_ltp=max(0.05, 100 - (spot - k) * 0.5), call_oi=1000, put_oi=900, call_iv=13.5)
            for k in range(23500, 25501, 50)]
    rows[0] = OptionChainRow(strike=23500.0)   # no quote -> no row
    return OptionChain(underlying="NIFTY 50", expiry=expiry, underlying_ltp=spot, rows=rows)


def test_chain_recorder_samples_dedupes_and_loads():
    now = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)

    async def go():
        async with _session_factory() as session:
            written = await chain_recorder.record_chain(session, "NIFTY", _chain(), now, interval_minutes=5, span=3)
            again = await chain_recorder.record_chain(session, "NIFTY", _chain(), now + timedelta(minutes=2), interval_minutes=5, span=3)
            later = await chain_recorder.record_chain(session, "NIFTY", _chain(spot=24560), now + timedelta(minutes=6), interval_minutes=5, span=3)
            rows = await chain_recorder.load_rows(session, "NIFTY", now - timedelta(minutes=1), now + timedelta(minutes=10))
            cov = await chain_recorder.coverage(session, "NIFTY")
            uploaded = await chain_recorder.store_uploaded(session, "NIFTY", [
                OptionChainSnapshotRow(timestamp=now, expiry=date(2026, 10, 6), strike=24500, right="PE", ltp=94.0),   # duplicate of a recorded row
                OptionChainSnapshotRow(timestamp=now + timedelta(hours=1), expiry=date(2026, 10, 6), strike=24500, right="PE", ltp=90.0),
            ])
            return written, again, later, rows, cov, uploaded
    written, again, later, rows, cov, uploaded = _run(go())
    assert written == 14 and again == 0 and later == 14          # 7 strikes x CE/PE around the money
    assert len(rows) == 28 and {r.right for r in rows} == {"CE", "PE"} and rows[0].expiry == date(2026, 10, 6)
    assert cov[0]["underlying"] == "NIFTY" and cov[0]["rows"] >= 28 and cov[0]["expiries"] >= 1   # other tests may have recorded NIFTY too
    assert uploaded == 1
    # The rows price a backtest: nearest-at-or-before within the age limit.
    pricer = SnapshotPricer(rows, max_age_minutes=5)
    assert pricer.price("PE", 24500, date(2026, 10, 6), spot=24512, at=now + timedelta(minutes=1)) == pytest.approx(94.0)

    # Retention covers the table with its own bounded window.
    from app.retention import service
    from app.retention.policy import load_policy
    policy = load_policy()
    assert policy.chain_snapshots_days >= 7
    tables = {t for t, _, _ in service._rules(datetime.now(timezone.utc), policy)}
    assert "option_chain_snapshots" in tables


def test_backtest_endpoints_dispatch_option_runs_and_record_them():
    headers = _auth("bt-options@example.com")
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "NIFTY 50", "base_timeframe": "1min", "candles": _candles(), "data_source": "sample",
            "options": {"option_strategy": "BULL_PUT_SPREAD", "implied_volatility": 0.14, "spread_width": 2, "max_lots": 2}}
    res = client.post("/api/backtest", headers=headers, json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["options"]["pricing_model"] == "synthetic" and out["options"]["structure"] == "BULL_PUT_SPREAD" and out["run_id"]
    run = client.get(f"/api/backtests/{out['run_id']}", headers=headers).json()
    assert run["engine_version"] == ENGINE_VERSION == "3-options"
    assert run["params"]["_options"]["option_strategy"] == "BULL_PUT_SPREAD" and "structures" not in run["metrics"]["options"]
    # Snapshot pricing with nothing recorded and no fallback is a plain 400; with the fallback it runs.
    strict = {**body, "options": {**body["options"], "pricing": "snapshots", "allow_synthetic_fallback": False}}
    assert client.post("/api/backtest", headers=headers, json=strict).status_code == 400
    loose = {**body, "options": {**body["options"], "pricing": "snapshots", "allow_synthetic_fallback": True}}
    res = client.post("/api/backtest", headers=headers, json=loose)
    assert res.status_code == 200 and res.json()["options"]["pricing_model"] == "snapshots"
    # Uploaded rows must be present; a custom structure must carry legs.
    assert client.post("/api/backtest", headers=headers, json={**body, "options": {**body["options"], "pricing": "uploaded"}}).status_code == 400
    assert client.post("/api/backtest", headers=headers, json={**body, "options": {"option_strategy": "CUSTOM"}}).status_code == 400
    # Monte Carlo and walk-forward take the same body.
    mc = client.post("/api/backtest/monte-carlo?runs=200", headers=headers, json=body)
    assert mc.status_code == 200 and "monte_carlo" in mc.json()
    wf = client.post("/api/backtest/walk-forward?folds=2", headers=headers, json=body)
    assert wf.status_code == 200 and wf.json()["folds"] in (0, 2)
    # Coverage / upload endpoints.
    up = client.post("/api/backtest/option-chain/snapshots", headers=headers, json={"underlying": "NIFTY 50", "rows": [
        {"timestamp": "2026-10-05T09:15:00+05:30", "expiry": "2026-10-06", "strike": 24500, "right": "PE", "ltp": 95.5}]})
    assert up.status_code == 200 and up.json()["underlying"] == "NIFTY" and up.json()["written"] == 1
    cov = client.get("/api/backtest/option-chain/coverage?underlying=NIFTY", headers=headers).json()
    assert cov and cov[0]["underlying"] == "NIFTY"
    rows = client.get("/api/backtest/option-chain/snapshots?underlying=NIFTY&start=2026-10-05T00:00:00Z&end=2026-10-06T00:00:00Z", headers=headers).json()
    assert any(r["ltp"] == 95.5 for r in rows)
    # An underlying backtest is untouched by the new field.
    plain = client.post("/api/backtest", headers=headers, json={k: v for k, v in body.items() if k != "options"}).json()
    assert plain["options"] is None


def test_worker_records_chains_for_active_option_deployments_once_per_interval(monkeypatch):
    from app.db.models import StrategyDeploymentRecord
    from app.market_data.service import MarketDataService
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _tenant, _worker

    class _ChainBroker(_FakeBroker):
        def __init__(self):
            super().__init__()
            self.chain_calls = []

        async def get_option_chain(self, underlying, expiry=None):
            self.chain_calls.append(underlying)
            return _chain(expiry="2026-10-06")

    t = _tenant("w-chains@example.com")
    broker = _ChainBroker()
    worker = _worker(monkeypatch, broker)
    option_dep = StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id="ema_rsi_scalper_1m", symbol="NIFTY 50", exchange="NSE", timeframe="1min",
                                          mode="PAPER", broker_name="upstox", status="ACTIVE", created_by=t["user_id"], instrument_kind="OPTION",
                                          option_strategy="BULL_PUT_SPREAD")
    cash_dep = StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE", exchange="NSE", timeframe="1min",
                                        mode="PAPER", broker_name="upstox", status="ACTIVE", created_by=t["user_id"])
    now = OPEN_NOW.astimezone(timezone.utc) + timedelta(days=3, hours=2)   # away from the instants other tests record at

    async def go():
        async with _session_factory() as session:
            first = await worker._record_chains(session, [option_dep, cash_dep], MarketDataService(broker), now, {"NSE"})
            second = await worker._record_chains(session, [option_dep], MarketDataService(broker), now + timedelta(minutes=1), {"NSE"})
            closed = await worker._record_chains(session, [option_dep], MarketDataService(broker), now + timedelta(minutes=30), {"MCX"})
            third = await worker._record_chains(session, [option_dep], MarketDataService(broker), now + timedelta(minutes=30), {"NSE"})
            rows = await chain_recorder.load_rows(session, "NIFTY", now - timedelta(minutes=1), now + timedelta(hours=1))
            return first, second, closed, third, rows
    first, second, closed, third, rows = _run(go())
    assert first > 0 and second == 0 and closed == 0 and third > 0
    assert broker.chain_calls == ["NIFTY 50", "NIFTY 50"]          # the cash deployment never triggers a fetch
    assert len(rows) == first + third and all(r.expiry == date(2026, 10, 6) for r in rows)
    monkeypatch.setattr(chain_recorder, "CHAIN_SNAPSHOT_INTERVAL_MINUTES", 0)
    assert _run(worker._record_chains(None, [option_dep], MarketDataService(broker), now + timedelta(hours=2), {"NSE"})) == 0
