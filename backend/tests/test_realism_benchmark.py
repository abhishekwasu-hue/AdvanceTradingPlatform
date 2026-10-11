"""Backtest realism C3: the CI guard on the engine's cost.

Wall-clock timings are noisy on CI runners, so the guard checks what makes a run linear: per run, the indicator
cache computes each causal indicator once on the whole frame and serves every bar's prefix from it. If a change
makes a strategy compute on each bar's window again (a derived series, an indicator outside the cache, a frame
not registered), the count of whole computations or uncached calls grows with the bars - O(n^2) per run - and this
test fails before anyone notices a two-year backtest taking hours. scripts/bench_backtest.py measures the time.
"""
import sys
from pathlib import Path

import pytest

from app.backtest import engine
from app.core.models import RiskConfig
from app.indicators.prefix_cache import run_stats
from app.strategy_engine.registry import registry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bench_backtest import bench, crafted_bars  # noqa: E402

RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5)


def _stats(strategy, sessions):
    daily = strategy.timeframes[0] == "day"
    df = crafted_bars(52 * sessions if daily else sessions, daily=daily)
    engine.run_backtest(strategy, df, "NIFTY 50", "day" if daily else "1min", RISK)
    return run_stats()


@pytest.mark.parametrize("strategy", registry.list_all(), ids=registry.ids())
def test_indicator_work_per_run_does_not_grow_with_the_data(strategy):
    short, long_ = _stats(strategy, 4), _stats(strategy, 10)
    assert set(short) == {"computed", "served", "uncached"}
    assert long_["computed"] == short["computed"], (short, long_)      # one whole-frame computation per indicator
    assert long_["uncached"] == short["uncached"], (short, long_)      # nothing recomputed per bar outside the cache
    assert long_["served"] >= short["served"]                          # the per-bar windows are answered from it


def test_the_stats_belong_to_one_run_and_nothing_is_cached_outside_a_run():
    strategy = registry.get("ema_rsi_scalper_1m")
    first = _stats(strategy, 4)
    assert first["computed"] > 0 and first["served"] > first["computed"]
    engine.run_backtest.__wrapped__(strategy, crafted_bars(4), "NIFTY 50", "1min", RISK)   # no cache: stats untouched
    assert run_stats() == first


def test_the_benchmark_reports_time_growth_and_cache_counters():
    rows = bench({"ema_rsi_scalper_1m"}, [2, 4])
    (row,) = rows
    assert row["strategy"] == "ema_rsi_scalper_1m" and [r["sessions"] for r in row["runs"]] == [2, 4]
    assert row["runs"][1]["bars"] == 2 * row["runs"][0]["bars"] and row["growth"] > 0 and row["bars_per_second"] > 0
    assert row["runs"][0]["indicator_cache"]["computed"] == row["runs"][1]["indicator_cache"]["computed"]
