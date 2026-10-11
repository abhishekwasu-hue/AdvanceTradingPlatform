"""Backtest realism C3: how fast the engine runs, and how its time grows with the data.

    python scripts/bench_backtest.py                       # every inbuilt strategy, 5 and 20 sessions of 1-min bars
    python scripts/bench_backtest.py --strategies ema_rsi_scalper_1m --sessions 5 40 --repeat 3 --json

Data is crafted (a seeded random walk, 375 one-minute bars per session; daily strategies get 260 x sessions/5 daily
bars), so numbers compare across machines only as ratios. The figure that matters is `growth`: time for the longest
run over time for the shortest, divided by the bar ratio. An engine that recomputes indicators over the whole history
on every bar (the pre-realism-3 behaviour) grows quadratically, so `growth` climbs with the span; a linear engine
stays near 1. Timings are noisy on shared machines, so the CI guard (tests/test_realism_benchmark.py) checks the
deterministic cause instead: `indicator_cache` - whole-frame indicator computations and calls the cache could not serve
- must not grow with the number of bars. docs/BENCHMARKS.md keeps the measured table.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest import engine  # noqa: E402
from app.core.models import RiskConfig  # noqa: E402
from app.indicators.prefix_cache import run_stats  # noqa: E402
from app.strategy_engine.registry import registry  # noqa: E402

BARS_PER_SESSION = 375


def crafted_bars(sessions: int, *, daily: bool = False, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    if daily:
        idx = pd.bdate_range("2020-01-01", periods=sessions)
    else:
        days = pd.bdate_range("2024-01-01", periods=sessions)
        idx = pd.DatetimeIndex([t for d in days for t in pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=BARS_PER_SESSION, freq="1min")])
    close = 20_000 * np.exp(np.cumsum(rng.normal(0, 0.012 if daily else 0.0015, len(idx))))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.0006, "low": np.minimum(open_, close) * 0.9994,
                         "close": close, "volume": rng.integers(500, 5000, len(idx)).astype(float)}, index=idx)


def time_run(strategy, df: pd.DataFrame, tf: str, repeat: int):
    """(best wall time, the indicator cache's counters for the run)."""
    risk = RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5)
    best = float("inf")
    for _ in range(repeat):
        started = time.perf_counter()
        engine.run_backtest(strategy, df, "NIFTY 50", tf, risk)
        best = min(best, time.perf_counter() - started)
    return best, run_stats()


def bench(strategy_ids, sessions, repeat: int = 1) -> list:
    rows = []
    for strategy in registry.list_all():
        if strategy_ids and strategy.id not in strategy_ids:
            continue
        daily = strategy.timeframes[0] == "day"
        tf = "day" if daily else "1min"
        timings = []
        for n in sessions:
            df = crafted_bars(max(60, 52 * n) if daily else n, daily=daily)
            seconds, stats = time_run(strategy, df, tf, repeat)
            timings.append({"sessions": n, "bars": len(df), "seconds": round(seconds, 4), "indicator_cache": stats})
        first, last = timings[0], timings[-1]
        growth = (last["seconds"] / max(first["seconds"], 1e-9)) / (last["bars"] / first["bars"])
        rows.append({"strategy": strategy.id, "timeframe": tf, "runs": timings, "growth": round(growth, 3),
                     "bars_per_second": round(last["bars"] / max(last["seconds"], 1e-9))})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategies", nargs="*", default=None)
    parser.add_argument("--sessions", nargs="+", type=int, default=[5, 20])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = bench(set(args.strategies or []), sorted(args.sessions), args.repeat)
    if args.json:
        print(json.dumps({"engine_version": engine.ENGINE_VERSION, "results": rows}, indent=2))
    else:
        print(f"engine {engine.ENGINE_VERSION}")
        for r in rows:
            spans = "  ".join(f"{t['bars']:>7} bars {t['seconds']:>8.3f}s" for t in r["runs"])
            print(f"{r['strategy']:<34} {spans}   growth {r['growth']:.2f}   {r['bars_per_second']:>7} bars/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
