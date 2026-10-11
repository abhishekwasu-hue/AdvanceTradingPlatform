"""Realism C2 (PR 1): execution models behind a ModelSet.

- golden: for every strategy the default models give exactly the result_hash the engine gave before C2
  (tests/golden/c2_default_models.json, recorded from the pre-C2 engine on crafted bars);
- a non-default slippage changes entry prices against the trade, and enters the fingerprint's config_hash; the default
  set leaves the hash as it was (a request without execution_models and one with the defaults spelt out agree);
- unknown models, models not available yet, and models on an option backtest are refused (422), never ignored.
"""
import json
import sys
from pathlib import Path

import pytest

from app.backtest import engine
from app.backtest.models import FixedPctSlippage, ModelError, ModelSet
from app.backtest.repro import result_hash
from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.strategy_engine.registry import registry
from tests.test_auth_api import client
from tests.test_deployments_api import _auth
from tests.test_exit_rules_and_backtest_depth import _candles

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))
from bench_backtest import crafted_bars  # noqa: E402

GOLDEN = json.loads((BACKEND / "tests" / "golden" / "c2_default_models.json").read_text())
RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=GOLDEN["risk_per_trade_pct"])


def _run(strategy, models=None):
    daily = strategy.timeframes[0] == "day"
    df = crafted_bars(208 if daily else 4, daily=daily)
    return engine.run_backtest(strategy, df, "NIFTY 50", "day" if daily else "1min", RISK, models=models)


@pytest.mark.parametrize("strategy_id", sorted(GOLDEN["results"]))
def test_default_models_reproduce_the_pre_c2_engine_exactly(strategy_id):
    strategy = registry.get(strategy_id)
    assert result_hash(_run(strategy)) == GOLDEN["results"][strategy_id]["result_hash"]
    assert result_hash(_run(strategy, ModelSet())) == GOLDEN["results"][strategy_id]["result_hash"]


def test_golden_covers_every_registered_strategy():
    assert set(GOLDEN["results"]) == {s.id for s in registry.list_all()}
    assert sum(v["trades"] for v in GOLDEN["results"].values()) > 0          # not a set of empty runs


def test_slippage_model_moves_entries_against_the_trade():
    assert FixedPctSlippage(pct=1.0).apply(100.0, SignalDirection.LONG) == 101.0
    assert FixedPctSlippage(pct=1.0).apply(100.0, SignalDirection.SHORT) == 99.0
    strategy = registry.get("supertrend_adx_scalper_1m")
    base = _run(strategy)
    worse = _run(strategy, ModelSet(params={"slippage": {"pct": 0.5}}))
    assert base.total_trades == worse.total_trades > 0
    for a, b in zip(base.trades, worse.trades):
        if a.direction == SignalDirection.LONG:
            assert b.entry_price > a.entry_price
        else:
            assert b.entry_price < a.entry_price
    assert result_hash(worse) != result_hash(base)


def test_unknown_or_unavailable_models_fail_loudly():
    for bad in ({"fill": "magic"}, {"slippage": "zero_cost"}, {"latency": "fixed_ms"}, {"params": {"weather": {}}}):
        with pytest.raises(ModelError):
            ModelSet(**bad)


def test_api_records_non_default_models_in_the_fingerprint_and_refuses_bad_ones():
    headers = _auth("c2-api@example.com")
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "nifty", "base_timeframe": "1min", "candles": _candles(), "data_source": "sample"}
    plain = client.post("/api/backtest", headers=headers, json=body).json()["reproducibility"]
    spelled = client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"fill": "touch", "slippage": "fixed_pct"}}).json()["reproducibility"]
    other = client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"params": {"slippage": {"pct": 0.3}}}}).json()["reproducibility"]
    assert plain["config_hash"] == spelled["config_hash"] and plain["result_hash"] == spelled["result_hash"]
    assert other["config_hash"] != plain["config_hash"]
    assert client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"fill": "magic"}}).status_code == 422
