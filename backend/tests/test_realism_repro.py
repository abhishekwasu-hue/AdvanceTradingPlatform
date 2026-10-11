"""Backtest realism C4: reproducibility.

- the same engine, code, data and configuration give the same result, byte for byte (canonical JSON), in this process
  and in fresh processes with different hash seeds (no dict/set-order or float-formatting dependence);
- the fingerprint moves when, and only when, what it names moves: one price changed -> data_version; one parameter ->
  config_hash; the result follows;
- every API run carries the fingerprint and the run record keeps it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from app.backtest import engine
from app.backtest.repro import canonical_json, config_hash, data_version, fingerprint, result_hash
from app.core.models import RiskConfig
from app.strategy_engine.registry import registry
from tests.test_auth_api import client
from tests.test_deployments_api import _auth
from tests.test_exit_rules_and_backtest_depth import _candles

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))
from bench_backtest import crafted_bars  # noqa: E402

RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5)

_CHILD = """
import sys
sys.path.insert(0, "scripts")
from bench_backtest import crafted_bars
from app.backtest import engine
from app.backtest.repro import fingerprint
from app.core.models import RiskConfig
from app.strategy_engine.registry import registry
s = registry.get("mtf_1m_5m_trend_pullback")
df = crafted_bars(4)
risk = RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5)
r = engine.run_backtest(s, df, "NIFTY 50", "1min", risk)
print(fingerprint(r, df, engine_version=engine.ENGINE_VERSION, strategy_id=s.id, params=s.params, risk=risk)["result_hash"])
"""


def _run(strategy_id="mtf_1m_5m_trend_pullback", df=None, params=None):
    strategy = registry.get(strategy_id)
    if params:
        import copy
        strategy = copy.copy(strategy)
        strategy.params = {**strategy.params, **params}
    df = crafted_bars(4) if df is None else df
    result = engine.run_backtest(strategy, df, "NIFTY 50", "1min", RISK)
    return result, fingerprint(result, df, engine_version=engine.ENGINE_VERSION, strategy_id=strategy.id, params=strategy.params, risk=RISK)


def test_same_inputs_give_the_same_bytes_in_one_process():
    (a, fa), (b, fb) = _run(), _run()
    assert a.total_trades > 0                                            # the fixture exercises entries and exits
    assert canonical_json(a.model_dump(mode="json")) == canonical_json(b.model_dump(mode="json"))
    assert fa == fb and fa["result_hash"].startswith("sha256:") and fa["engine_version"] == engine.ENGINE_VERSION


def test_same_inputs_give_the_same_result_hash_across_processes_and_hash_seeds():
    hashes = set()
    for seed in ("1", "2", "777"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", _CHILD], cwd=BACKEND, env=env, capture_output=True, text=True, check=True)
        hashes.add(out.stdout.strip().splitlines()[-1])
    assert len(hashes) == 1 and hashes == {_run()[1]["result_hash"]}


def test_the_fingerprint_names_exactly_what_changed():
    df = crafted_bars(4)
    _, base = _run(df=df)
    moved = df.copy()
    moved.iloc[500, moved.columns.get_loc("close")] += 0.05              # one tick on one bar
    _, other_data = _run(df=moved)
    assert other_data["data_version"] != base["data_version"] and other_data["config_hash"] == base["config_hash"]
    _, other_params = _run(df=df, params={"_repro_probe": 1})            # a parameter the strategy ignores
    assert other_params["config_hash"] != base["config_hash"] and other_params["data_version"] == base["data_version"]
    assert other_params["result_hash"] == base["result_hash"]            # same trades: the result hash follows the result


def test_data_version_reads_the_bars_not_their_labels():
    df = crafted_bars(1)
    same = df.copy()
    same.index = same.index.tz_localize("Asia/Kolkata").tz_convert("UTC").tz_convert("Asia/Kolkata")   # same instants
    assert data_version(df.tz_localize("Asia/Kolkata")) == data_version(same)
    assert data_version(df) != data_version(df.iloc[:-1])
    assert config_hash(a=1, b=[1.0, 2]) == config_hash(b=[1.0, 2], a=1)  # key order does not matter
    assert canonical_json({"x": float("nan")}) == '{"x":"nan"}'


def test_api_runs_carry_the_fingerprint_and_the_run_record_keeps_it():
    headers = _auth("repro-api@example.com")
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "nifty", "base_timeframe": "1min", "candles": _candles(), "data_source": "sample"}
    first = client.post("/api/backtest", headers=headers, json=body).json()
    second = client.post("/api/backtest", headers=headers, json=body).json()
    fp = first["reproducibility"]
    assert set(fp) >= {"engine_version", "code_version", "data_version", "config_hash", "result_hash", "seed", "bars"}
    assert fp == second["reproducibility"] and fp["bars"] == 300
    stored = client.get(f"/api/backtests/{first['run_id']}", headers=headers).json()
    assert stored["reproducibility"] == fp and stored["metrics"]["reproducibility"] == fp
    changed = client.post("/api/backtest", headers=headers, json={**body, "exit_rules": {"trailing_stop_pct": 0.5}}).json()
    assert changed["reproducibility"]["config_hash"] != fp["config_hash"] and changed["reproducibility"]["data_version"] == fp["data_version"]
    assert json.loads(json.dumps(fp)) == fp


def test_result_hash_ignores_the_run_id():
    result, fp = _run()
    result.run_id = 99
    assert result_hash(result) == fp["result_hash"]
