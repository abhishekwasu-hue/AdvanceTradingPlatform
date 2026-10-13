"""N7 C2d (C2 PR 4): `sampled` latency and `physical` settlement (flag backtest_models at the API, off by default).

- sampled: each entry order's delay is drawn from a seeded log-normal (clipped), in entry order - the same seed gives
  the same run, the seed is in the fingerprint, and with no spread it is exactly `fixed_ms`;
- physical: an in-the-money leg of an expiring STOCK option settles by delivery (delivery charges on the settlement
  value, no exercise STT); index options stay cash-settled; out-of-the-money legs are unchanged.
The defaults are untouched (the golden tests of C2 PR 1 still hold)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtest import engine
from app.backtest.models import FLAG, ModelError, ModelSet, SampledLatency
from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
from app.backtest.portfolio import PortfolioError, run_portfolio_backtest
from app.backtest.repro import result_hash
from app.core.enums import OptionPosition, OptionStrategy
from app.core.models import RiskConfig
from app.execution.paper_broker import PaperBroker
from app.strategy_engine.registry import registry
from tests.test_auth_api import client
from tests.test_deployments_api import _auth
from tests.test_phase_p0_6_backtest_fixes import _OnceLong
from tests.utils import make_series, noisy_uptrend


def _frame(days=3):
    return pd.concat([make_series(noisy_uptrend(n=300, seed=7 + k, start=100.0 + k), start=f"2024-01-{2 + k:02d} 09:15") for k in range(days)])


def _key(t):
    return (str(t.entry_time), str(t.exit_time), float(t.entry_price), float(t.quantity), round(float(t.pnl), 6))


# --- the models ---------------------------------------------------------------------------------------------------------------

def test_the_new_models_are_flagged_and_checked():
    assert ModelSet(latency="sampled", settlement="physical").flagged() == {"latency": "sampled", "settlement": "physical"}
    assert ModelSet().settlement_model().name == "default" and ModelSet().seed is None
    with pytest.raises(ModelError):
        ModelSet(settlement="barter")
    for bad in ({"seed": -1}, {"seed": 1.5}, {"median_ms": 0}, {"sigma": -0.1}, {"min_ms": 900, "max_ms": 100}):
        with pytest.raises((ModelError, TypeError)):
            ModelSet(latency="sampled", params={"latency": bad}).built()
    sampled = ModelSet(latency="sampled")
    assert sampled.seed == 7 and sampled.describe()["latency_seed"] == 7
    assert ModelSet(latency="sampled", params={"latency": {"seed": 3}}).describe()["latency_seed"] == 3


def test_sampled_draws_are_seeded_and_clipped():
    a, b, c = SampledLatency(seed=1), SampledLatency(seed=1), SampledLatency(seed=2)
    da, db, dc = [a.draw() for _ in range(50)], [b.draw() for _ in range(50)], [c.draw() for _ in range(50)]
    assert da == db and da != dc and len(set(da)) > 40
    tight = SampledLatency(seed=1, sigma=3.0, min_ms=100, max_ms=400)
    ms = [tight.draw().total_seconds() * 1000 for _ in range(200)]
    assert min(ms) == pytest.approx(100) and max(ms) == pytest.approx(400)


# --- the single engine --------------------------------------------------------------------------------------------------------

def test_a_sampled_run_is_reproducible_and_is_fixed_ms_without_a_spread():
    s, df = registry.get("ema_rsi_scalper_1m"), _frame()
    run = lambda m: engine.run_backtest(s, df, "AAA", "1min", RiskConfig(), models=m)   # noqa: E731
    one = run(ModelSet(latency="sampled", params={"latency": {"seed": 11, "sigma": 1.5, "median_ms": 30_000, "max_ms": 300_000}}))
    two = run(ModelSet(latency="sampled", params={"latency": {"seed": 11, "sigma": 1.5, "median_ms": 30_000, "max_ms": 300_000}}))
    other = run(ModelSet(latency="sampled", params={"latency": {"seed": 12, "sigma": 1.5, "median_ms": 30_000, "max_ms": 300_000}}))
    assert one.total_trades > 5 and [_key(t) for t in one.trades] == [_key(t) for t in two.trades]
    assert [_key(t) for t in other.trades] != [_key(t) for t in one.trades]          # 30 s around a 60 s bar: the seed matters
    flat = run(ModelSet(latency="sampled", params={"latency": {"sigma": 0.0, "median_ms": 250}}))
    fixed = run(ModelSet(latency="fixed_ms", params={"latency": {"ms": 250}}))
    assert [_key(t) for t in flat.trades] == [_key(t) for t in fixed.trades]
    plain = run(None)
    assert result_hash(plain) == result_hash(run(ModelSet()))                         # the default is untouched


def test_sampled_latency_reads_no_later_bar():
    """No look-ahead: the trades closed before a cut are the same when every bar from the cut on is rewritten (the draws
    are consumed in entry order, so later bars cannot change an earlier delay)."""
    s, df = registry.get("ema_rsi_scalper_1m"), _frame()
    models = ModelSet(latency="sampled", params={"latency": {"seed": 5, "sigma": 1.0, "median_ms": 20_000, "max_ms": 300_000}})
    cut = pd.Timestamp("2024-01-03 12:00")
    noisy = df.copy()
    later = noisy.index >= cut
    rng = np.random.default_rng(9)
    for col in ("open", "high", "low", "close"):
        noisy.loc[later, col] = noisy.loc[later, col].to_numpy() * rng.uniform(0.8, 1.2, later.sum())
    noisy.loc[later, "high"] = noisy.loc[later, ["open", "high", "low", "close"]].max(axis=1)
    noisy.loc[later, "low"] = noisy.loc[later, ["open", "high", "low", "close"]].min(axis=1)
    a = engine.run_backtest(s, df, "AAA", "1min", RiskConfig(), models=models)
    b = engine.run_backtest(s, noisy, "AAA", "1min", RiskConfig(), models=models)
    closed = [_key(t) for t in a.trades if t.exit_time < cut]
    assert closed and closed == [_key(t) for t in b.trades if t.exit_time < cut]


def test_the_portfolio_and_single_leg_runs_refuse_what_they_cannot_model():
    s = registry.get("ema_rsi_scalper_1m")
    for m in (ModelSet(latency="sampled"), ModelSet(settlement="physical")):
        with pytest.raises(PortfolioError):
            run_portfolio_backtest(s, {"AAA": _frame(1)}, "1min", RiskConfig(), models=m)


# --- physical settlement ------------------------------------------------------------------------------------------------------

def _expiry_run(symbol, start, end, models, position=OptionPosition.BUY):
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=position, implied_volatility=0.14, intraday=False,
                               max_lots=1, lot_size=250, strike_step=10.0, expiry_weekday=1, weekly_expiry=True)
    rally = make_series(list(np.linspace(start, end, 370)), start="2026-10-06 09:15")     # a Tuesday expiry, bars past 15:15
    return run_option_backtest(_OnceLong(stop=start * 0.99, target1=end * 10), rally, symbol, "1min",
                               RiskConfig(capital=50_000_000, risk_per_trade_pct=1.0), cfg, models=models)


def test_an_in_the_money_stock_option_settles_by_delivery():
    cash = _expiry_run("RELIANCE", 2900.0, 3000.0, None)
    physical = _expiry_run("RELIANCE", 2900.0, 3000.0, ModelSet(settlement="physical"))
    t_cash, t_phys = cash.trades[0], physical.trades[0]
    assert t_cash.exit_reason.startswith("Expiry settlement") and t_phys.exit_reason == t_cash.exit_reason
    leg = physical.options["structures"][0]["legs"][0]
    intrinsic, qty = float(leg["exit_price"]), float(leg["quantity"])
    assert intrinsic > 0
    spot = float(leg["strike"]) + intrinsic
    pb, day = PaperBroker(), date(2026, 10, 6)
    delivery = pb.estimate_round_trip_costs(spot, spot, qty, "UNDERLYING", delivery=True, entry_date=day, exit_date=day)
    exercise = pb.exercise_charges(intrinsic, qty, trade_date=day)
    assert t_phys.charges - t_cash.charges == pytest.approx(delivery - exercise, abs=0.05)
    assert t_cash.pnl - t_phys.pnl == pytest.approx(delivery - exercise, abs=0.05)                  # the option's P&L is still intrinsic
    assert physical.execution["settlement"] == "physical" and physical.execution["physically_settled_legs"] == 1


def test_index_options_and_out_of_the_money_legs_settle_as_before():
    cash = _expiry_run("NIFTY 50", 24500.0, 24800.0, None)
    phys = _expiry_run("NIFTY 50", 24500.0, 24800.0, ModelSet(settlement="physical"))
    assert [_key(t) for t in phys.trades] == [_key(t) for t in cash.trades] and phys.execution["settlement"] == "cash"
    sell = OptionPosition.WRITE                                                   # a written put that expires worthless
    otm_cash = _expiry_run("RELIANCE", 2900.0, 3000.0, None, position=sell)
    otm_phys = _expiry_run("RELIANCE", 2900.0, 3000.0, ModelSet(settlement="physical"), position=sell)
    assert otm_cash.trades[0].exit_reason.startswith("Expiry") and float(otm_cash.trades[0].exit_price) == 0.0
    assert [_key(t) for t in otm_phys.trades] == [_key(t) for t in otm_cash.trades]
    assert otm_phys.execution["physically_settled_legs"] == 0


# --- the API ------------------------------------------------------------------------------------------------------------------

def test_the_api_gates_the_new_models_and_fingerprints_the_seed():
    from tests.test_n1_e1_execution_core import _flag
    headers = _auth("n7c2d-api@example.com")
    tenant = client.get("/api/auth/me", headers=headers).json()["tenant_id"]
    df = _frame(2)
    candles = [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close),
                "volume": float(r.volume)} for ts, r in df.iterrows()]
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "aaa", "base_timeframe": "1min", "candles": candles, "data_source": "sample"}
    sampled = {**body, "execution_models": {"latency": "sampled", "params": {"latency": {"seed": 4}}}}
    assert client.post("/api/backtest", headers=headers, json=sampled).status_code == 403
    _flag(FLAG, tenant)
    one = client.post("/api/backtest", headers=headers, json=sampled)
    assert one.status_code == 200, one.text
    rep = one.json()["reproducibility"]
    assert rep["seed"] == 4
    two = client.post("/api/backtest", headers=headers, json=sampled).json()["reproducibility"]
    other = client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"latency": "sampled", "params": {"latency": {"seed": 5}}}})
    assert two["result_hash"] == rep["result_hash"] and other.json()["reproducibility"]["config_hash"] != rep["config_hash"]
    single = client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"settlement": "physical"}})
    assert single.status_code == 422 and "option backtests only" in single.text
    assert client.post("/api/backtest", headers=headers, json=body).json()["reproducibility"]["seed"] is None
