"""Trade port, part C: overfitting statistics (app.backtest.validation), the holdout data policy
(app.backtest.data_policy) and their use in the parameter optimizer.

Statistics tests ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, tests/test_research_stats.py; the
bootstrap / random-entry / Reality Check / data-policy / optimizer tests are ATP integration tests."""
import numpy as np
import pandas as pd
import pytest

from app.backtest import data_policy as DP
from app.backtest import validation as RS
from app.backtest.optimizer import optimize
from app.core.models import RiskConfig
from tests.utils import make_series, noisy_uptrend


# --- ported -------------------------------------------------------------------------------------------------------------
def test_sharpe_and_moments_edge_cases():
    assert RS.sharpe([]) == 0.0 and RS.sharpe([1.0]) == 0.0 and RS.sharpe([2.0, 2.0, 2.0]) == 0.0
    assert RS.sharpe([1.0, 3.0]) == pytest.approx(2.0 / np.std([1.0, 3.0], ddof=1))
    assert RS.moments([1.0, 2.0]) == (0.0, 3.0)
    sk, ku = RS.moments(np.random.default_rng(1).normal(size=20000))
    assert abs(sk) < 0.1 and abs(ku - 3) < 0.15


def test_expected_max_sharpe_grows_with_trials():
    assert RS.expected_max_sharpe(1, 0.01) == 0.0
    a, b = RS.expected_max_sharpe(10, 0.01), RS.expected_max_sharpe(100, 0.01)
    assert 0 < a < b


def test_pbo_low_for_a_real_edge_and_high_for_pure_noise():
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 1, size=(800, 8))
    assert RS.pbo_cscv(noise, S=16)["pbo"] > 0.2                       # pure noise: the IS winner is random out of sample
    edge = noise.copy()
    edge[:, 3] += 0.4                                                  # one trial really is better
    r = RS.pbo_cscv(edge, S=16)
    assert r["pbo"] < 0.05 and r["n_splits"] == 12870 and r["N"] == 8
    sampled = RS.pbo_cscv(edge, S=16, max_combos=500)
    assert sampled["n_splits"] == 500 and sampled["pbo"] < 0.05


def test_pbo_degenerate_inputs():
    assert RS.pbo_cscv(np.zeros((10, 1)), S=4)["pbo"] is None
    assert RS.pbo_cscv(np.zeros((3, 4)), S=4)["pbo"] is None
    with pytest.raises(ValueError):
        RS.pbo_cscv(np.zeros(5))


def test_deflated_sharpe_penalises_many_trials():
    rng = np.random.default_rng(3)
    r = rng.normal(0.1, 1.0, 500)
    few = RS.deflated_sharpe(r, [0.0, 0.05, RS.sharpe(r)])
    many = RS.deflated_sharpe(r, list(rng.normal(0, 0.05, 200)) + [RS.sharpe(r)])
    assert 0 <= many["dsr"] <= few["dsr"] <= 1 and many["sr0"] > few["sr0"]
    assert RS.deflated_sharpe([1.0, 2.0], [0.1])["dsr"] is None


def test_shuffle_pvalue_detects_dependence_and_is_reproducible():
    rng = np.random.default_rng(0)
    x = rng.normal(size=300)
    y = x + rng.normal(scale=0.5, size=300)

    def corr(a, b):
        return float(np.corrcoef(a, b)[0, 1])
    stat, p = RS.shuffle_pvalue(corr, x, y, n_perm=200, seed=1)
    assert stat > 0.5 and p < 0.01
    _, p_null = RS.shuffle_pvalue(corr, x, rng.normal(size=300), n_perm=200, seed=1)
    assert p_null > 0.01
    assert RS.shuffle_pvalue(corr, x, y, n_perm=50, seed=2) == RS.shuffle_pvalue(corr, x, y, n_perm=50, seed=2)


# --- bootstrap, random entry, Reality Check -----------------------------------------------------------------------------
def _trades(rng, mean, days=60, per_day=3):
    rows = [(pd.Timestamp("2025-01-01") + pd.Timedelta(days=d, hours=10 + k), float(rng.normal(mean, 1.0)))
            for d in range(days) for k in range(per_day)]
    return pd.DataFrame(rows, columns=["time", "R"])


def test_day_block_ci_covers_the_mean_and_is_reproducible():
    df = _trades(np.random.default_rng(1), 0.3)
    lo, hi = RS.day_block_ci(df, reps=400)
    assert lo < df["R"].mean() < hi and (lo, hi) == RS.day_block_ci(df, reps=400)
    assert all(np.isnan(RS.day_block_ci(df.iloc[0:0])))


def test_random_entry_compare_separates_an_edge_from_luck():
    rng = np.random.default_rng(2)
    pool = pd.DataFrame({"episode": np.repeat(np.arange(40), 10), "R": rng.normal(0.0, 1.0, 400)})
    good = RS.random_entry_compare(rng.normal(0.8, 1.0, 120), pool, reps=400)
    luck = RS.random_entry_compare(rng.normal(0.0, 1.0, 120), pool, reps=400)
    assert good["lo"] > 0 and good["p"] < 0.05 and luck["p"] > 0.05
    assert np.isnan(RS.random_entry_compare([1.0, 2.0], pool)["diff"])


def test_reality_check_rejects_only_a_real_improvement():
    rng = np.random.default_rng(4)
    bench = _trades(rng, 0.0)
    noise = {f"v{i}": _trades(rng, 0.0) for i in range(5)}
    assert RS.reality_check(noise, bench, reps=300) > 0.05
    assert RS.reality_check({**noise, "real": _trades(rng, 0.6)}, bench, reps=300) < 0.05
    assert np.isnan(RS.reality_check({}, bench))


# --- holdout data policy --------------------------------------------------------------------------------------------------
def test_data_policy_boundary_and_filters(monkeypatch):
    monkeypatch.delenv("BACKTEST_HOLDOUT_START", raising=False)
    assert DP.holdout_start() is None and DP.check_range("2030-01-01", "2031-01-01", None)
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", "2024-01-03")
    b = DP.holdout_start()
    assert b == pd.Timestamp("2024-01-03") and DP.holdout_start("2023-06-01") == pd.Timestamp("2023-06-01")
    df = make_series(list(range(3 * 1440)), start="2024-01-01 00:00")
    assert len(DP.filter_allowed(df, b)) == 2 * 1440 and DP.final_holdout_mask(df.index, b).sum() == 1440
    aware = df.tz_localize("Asia/Kolkata")
    assert len(DP.filter_allowed(aware, b)) == 2 * 1440                     # a naive boundary is read in the data's clock
    assert DP.check_range("2023-06-01", "2024-01-02 23:59", b)
    with pytest.raises(DP.HoldoutError):
        DP.check_range("2023-06-01", "2024-01-03", b)


def test_optimizer_never_runs_holdout_bars_and_reports_pbo(monkeypatch):
    from app.strategy_engine.registry import registry
    monkeypatch.delenv("BACKTEST_HOLDOUT_START", raising=False)
    strategy = registry.get("swing_ema_pullback_d")
    df = make_series(noisy_uptrend(400, seed=11, period=20), start="2023-01-02", freq="1D")
    seen = []
    import app.backtest.optimizer as opt
    real = opt.run_backtest

    def spy(strategy, frame, *a, **k):
        seen.append(frame.index.max())
        return real(strategy, frame, *a, **k)
    monkeypatch.setattr(opt, "run_backtest", spy)
    boundary = df.index[300]
    grid = {"ema_fast": [10, 20], "rsi_long_min": [35, 45]}
    result = optimize(strategy, df, "TEST", "day", RiskConfig(), grid, split=0.6, holdout_start=str(boundary))
    assert max(seen) < boundary                                            # no run ever touched the holdout
    assert result["holdout_bars_excluded"] == 100 and result["in_sample_bars"] + result["out_of_sample_bars"] == 300
    ov = result["overfitting"]
    assert ov["blocks"] == 8 and ov["pbo"] is not None and 0 <= ov["pbo"] <= 1 and ov["pbo_splits"] == 70
    with pytest.raises(ValueError, match="sealed holdout"):
        optimize(strategy, df, "TEST", "day", RiskConfig(), grid, holdout_start="2022-12-01")


def test_a_run_can_seal_more_but_never_open_the_operator_holdout(monkeypatch):
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", "2026-04-01")
    assert DP.holdout_start("2099-01-01") == pd.Timestamp("2026-04-01")             # a later per-run date cannot open it
    assert DP.holdout_start("2025-01-01") == pd.Timestamp("2025-01-01")             # an earlier one seals more
    monkeypatch.delenv("BACKTEST_HOLDOUT_START")
    assert DP.holdout_start(pd.Timestamp("2026-03-31 20:00", tz="UTC")) == pd.Timestamp("2026-04-01 01:30")   # aware -> IST
