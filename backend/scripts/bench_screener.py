"""Z10-d (G4): the screener's performance targets (ATP_SCREENER_SPEC §1.5) on a synthetic universe.

    python scripts/bench_screener.py                        # the spec's sizes, vectorised path, 5 timed runs each
    python scripts/bench_screener.py --symbols 500 --per-symbol --repeat 3 --json

Targets (seconds, on the reference machine; a slower machine scales them by the printed CI factor):
* EOD screen, 2,500 symbols x 50 factors on daily bars: under 3 s; the same screen again from the result cache: under
  0.2 s.
* Bar-close scan, about 200 symbols (the F&O universe; four alert rules of 50 symbols, the engine's per-rule cap) on
  5-minute bars: under 1 s. The CI test (tests/test_z10d_perf.py) times the bar-close engine itself (rules, cycle
  cache, events in the database); this script times the screen evaluation part.

Bars are a seeded random walk (no prices are written down anywhere), one shared bar index per universe, like an
exchange's EOD or one bar-close cycle. Each figure is the median of `--repeat` timed runs after one warm-up run, so one
slow run on a shared machine does not decide it. The CI factor comes from a fixed numpy / pandas workload that does
not use the screener: its time here over its time on the reference machine (a GitHub-hosted ubuntu-latest runner),
never below 1 - a faster machine never tightens a budget. docs/PERFORMANCE.md keeps the measured table.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.screener import compile_screen  # noqa: E402
from app.screener.result_cache import ResultCache, namespace  # noqa: E402
from app.screener.runtime import SymbolData, run_screen  # noqa: E402

EOD_SYMBOLS, EOD_BARS = 2_500, 300                # about 14 months of sessions: room for the 200-bar factors
BARCLOSE_SYMBOLS, BARCLOSE_RULE_SIZE, BARCLOSE_SESSIONS = 200, 50, 5
TARGETS = {"eod_screen": 3.0, "eod_cached": 0.2, "barclose_scan": 1.0}
REFERENCE_CALIBRATION_SECONDS = 0.08              # calibrate() on a GitHub-hosted ubuntu-latest runner (0.079 s, docs/PERFORMANCE.md)

# 50 factors: trend, momentum, volatility, range, volume and pattern-count filters at several windows - the mix a
# daily screen library is built from. Each is a filter that most symbols pass, so no factor ends the evaluation early
# (the runtime evaluates every factor anyway).
EOD_FACTORS: List[str] = [
    *(f"close > SMA(close, {w}) * 0.5" for w in (5, 10, 20, 50, 100, 200)),
    *(f"close > EMA(close, {w}) * 0.5" for w in (5, 10, 20, 50, 100, 200)),
    *(f"PctChange(close, {w}) > -60" for w in (1, 5, 20, 60, 120)),
    *(f"ZScore(close, {w}) > -6" for w in (20, 50, 100)),
    *(f"close <= Max(high, {w})" for w in (5, 20, 52, 100)),
    *(f"close >= Min(low, {w})" for w in (5, 20, 52, 100)),
    "RSI(14) > 1", "RSI(7) < 99.9", "RSI(21) BETWEEN 1 AND 99",
    "ATR(14) > 0", "ATR(5) / close < 0.5", "ADX(14) >= 0", "PlusDI(14) >= 0", "MinusDI(14) >= 0",
    "volume > 0", "volume < SMA(volume, 20) * 50", "SMA(volume, 50) > 0", "PctChange(volume, 5) > -100",
    "Count(10, close > open) >= 0", "Count(20, close > close[1]) <= 20", "CountStreak(close > 0) > 0",
    "Lag(close, 5) > 0", "close[1] > 0", "high >= low", "Greatest(open, close) >= Least(open, close)",
    "CrossAbove(EMA(close, 9), EMA(close, 21)) OR close > 0", "NOT CrossBelow(close, SMA(close, 50)) OR close > 0",
    "EMA(close, 12) - EMA(close, 26) > -close",
]
assert len(EOD_FACTORS) == 50, len(EOD_FACTORS)

# A typical intraday alert: trend, momentum, a volume surge, range and a fresh cross - six factors.
BARCLOSE_SCREEN = ("close > EMA(close, 20) AND RSI(14) > 40 AND volume > 0.5 * SMA(volume, 20) AND ATR(14) > 0 "
                   "AND PctChange(close, 12) > -5 AND (CrossAbove(EMA(close, 9), EMA(close, 21)) OR close > SMA(close, 50))")


def eod_text() -> str:
    return " AND ".join(f"({f})" for f in EOD_FACTORS)


def _walk(rng: np.random.Generator, index: pd.DatetimeIndex, vol: float) -> pd.DataFrame:
    n = len(index)
    start = float(np.exp(rng.uniform(np.log(20), np.log(5_000))))
    close = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    open_ = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0, vol / 4, n))
    spread = np.abs(rng.normal(0, vol, n))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + spread), "low": np.minimum(open_, close) * (1 - spread),
                         "close": close, "volume": rng.integers(1_000, 2_000_000, n).astype(float)}, index=index)


def daily_index(bars: int = EOD_BARS, end: str = "2026-10-09") -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=bars, tz="UTC") + pd.Timedelta(hours=3, minutes=45)    # labelled 09:15 IST


def intraday_index(sessions: int = BARCLOSE_SESSIONS, last_day: str = "2026-03-10", minutes: int = 5) -> pd.DatetimeIndex:
    days = pd.bdate_range(end=last_day, periods=sessions)
    per_session = 375 // minutes
    return pd.DatetimeIndex([d + pd.Timedelta(hours=3, minutes=45) + pd.Timedelta(minutes=minutes * i) for d in days for i in range(per_session)], tz="UTC")


def universe(n: int, index: pd.DatetimeIndex, *, seed: int = 7, vol: float = 0.018, prefix: str = "SYM") -> List[SymbolData]:
    rng = np.random.default_rng(seed)
    return [SymbolData(f"{prefix}{i:04d}", {"tf": _walk(rng, index, vol)}) for i in range(n)]


def with_tf(data: List[SymbolData], tf: str) -> List[SymbolData]:
    return [SymbolData(d.symbol, {tf: d.frames["tf"]}) for d in data]


def median_time(fn: Callable[[], Any], repeat: int, warmup: int = 1) -> Dict[str, Any]:
    for _ in range(warmup):
        fn()
    runs = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        runs.append(time.perf_counter() - started)
    return {"median": statistics.median(runs), "runs": [round(r, 4) for r in runs]}


def calibrate(repeat: int = 5) -> float:
    """Median seconds of a fixed workload shaped like the engine's (row-by-row vector updates, whole-array arithmetic,
    pandas rolling windows) that does not touch the code under test."""
    rng = np.random.default_rng(0)
    x = rng.random((300, 2_500))
    frame = pd.DataFrame(rng.random((300, 50)))

    def work() -> None:
        acc, comp = np.zeros(x.shape[1]), np.zeros(x.shape[1])
        for row in x:                                                   # 300 x 6 small vector operations
            y = row - comp
            t = acc + y
            comp = t - acc - y
            acc = np.where(row > 0.5, t, acc)
        for _ in range(10):                                             # 30 whole-array operations
            z = np.maximum(x, x[::-1]) * 1.0001
            np.where(z > 0.5, z, np.nan)
            np.cumsum(z, axis=0)
        frame.rolling(20).mean()
        frame.ewm(span=20, adjust=False).mean()
    return float(median_time(work, repeat)["median"])


def ci_factor(calibration: Optional[float] = None) -> float:
    calibration = calibrate() if calibration is None else calibration
    return max(1.0, calibration / REFERENCE_CALIBRATION_SECONDS)


def bench_eod(symbols: int = EOD_SYMBOLS, repeat: int = 5, vectorized: bool = True) -> Dict[str, Any]:
    data = with_tf(universe(symbols, daily_index()), "1d")
    ast, validated = compile_screen(eod_text(), base_tf="1d", cost_cap=1e9)
    assert validated.ok, validated.problems
    matches: List[Any] = []

    def once() -> None:
        matches[:] = run_screen(ast, validated, data, base_tf="1d", vectorized=vectorized)
    timed = median_time(once, repeat)
    lake = namespace(1, source="lake:eod")                     # as_of keys are for the shared EOD lake only
    cache = ResultCache()
    as_of = data[0].frames["1d"].index[-1]
    cache.run(ast, validated, data, namespace=lake, base_tf="1d", as_of=as_of, ttl_seconds=3600, vectorized=vectorized)
    cached = median_time(lambda: cache.run(ast, validated, data, namespace=lake, base_tf="1d", as_of=as_of, ttl_seconds=3600, vectorized=vectorized), max(repeat, 5) * 4)
    assert cache.hits >= 1
    fingerprinted = ResultCache()
    fingerprinted.run(ast, validated, data, namespace=namespace(1, source="broker:synthetic"), base_tf="1d", ttl_seconds=3600, vectorized=vectorized)
    by_bars = median_time(lambda: fingerprinted.run(ast, validated, data, namespace=namespace(1, source="broker:synthetic"), base_tf="1d", ttl_seconds=3600, vectorized=vectorized), max(repeat, 5))
    return {"symbols": symbols, "bars": EOD_BARS, "factors": len(EOD_FACTORS), "vectorized": vectorized,
            "matched": sum(m.matched for m in matches), "with_reason": sum(1 for m in matches if m.reason),
            "eod_screen": timed, "eod_cached": cached, "eod_cached_by_bars": by_bars}


def barclose_universe(symbols: int = BARCLOSE_SYMBOLS) -> List[SymbolData]:
    return with_tf(universe(symbols, intraday_index(), seed=11, vol=0.0025, prefix="FNO"), "5m")


def bench_barclose(symbols: int = BARCLOSE_SYMBOLS, repeat: int = 5, vectorized: bool = True) -> Dict[str, Any]:
    """The screen part of one bar-close cycle: each 50-symbol rule's universe evaluated on its closed bars."""
    data = barclose_universe(symbols)
    ast, validated = compile_screen(BARCLOSE_SCREEN, base_tf="5m")
    assert validated.ok, validated.problems
    rules = [data[i:i + BARCLOSE_RULE_SIZE] for i in range(0, len(data), BARCLOSE_RULE_SIZE)]
    timed = median_time(lambda: [run_screen(ast, validated, part, base_tf="5m", vectorized=vectorized) for part in rules], repeat)
    return {"symbols": symbols, "rules": len(rules), "bars": len(intraday_index()), "vectorized": vectorized, "barclose_scan": timed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=int, default=EOD_SYMBOLS)
    parser.add_argument("--barclose-symbols", type=int, default=BARCLOSE_SYMBOLS)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--per-symbol", action="store_true", help="time the per-symbol runtime instead of the vectorised one")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    calibration = calibrate()
    factor = ci_factor(calibration)
    vectorized = not args.per_symbol
    report: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "calibration_s": round(calibration, 4), "ci_factor": round(factor, 2),
                              "eod": bench_eod(args.symbols, args.repeat, vectorized),
                              "barclose": bench_barclose(args.barclose_symbols, args.repeat, vectorized)}
    rows = [("eod_screen", report["eod"]["eod_screen"]["median"], args.symbols / EOD_SYMBOLS),
            ("eod_cached", report["eod"]["eod_cached"]["median"], 1.0),
            ("barclose_scan", report["barclose"]["barclose_scan"]["median"], args.barclose_symbols / BARCLOSE_SYMBOLS)]
    report["budgets"] = {name: {"median_s": round(t, 4), "budget_s": round(TARGETS[name] * factor * max(scale, 1e-9), 4),
                                "pass": t <= TARGETS[name] * factor * scale} for name, t, scale in rows}
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"CI factor {factor:.2f} (calibration {calibration:.3f} s, reference {REFERENCE_CALIBRATION_SECONDS} s); "
              f"{'vectorised' if vectorized else 'per-symbol'} runtime")
        for name, budget in report["budgets"].items():
            print(f"{name:14} median {budget['median_s']:8.3f} s  budget {budget['budget_s']:7.3f} s  {'PASS' if budget['pass'] else 'FAIL'}")
    return 0 if all(b["pass"] for b in report["budgets"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
