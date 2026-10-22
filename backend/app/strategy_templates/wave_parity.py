"""Strategy templates, family G: the parity harness for the wave templates (docs/design/WAVE_TEMPLATES_REVIEW.md build
plan, "Parity harness for G": scan = backtest = options backtest = worker on one trailing window W, with the wave and
higher timeframe frames; owner order section 8, S11, R3, R6).

It runs on the TPL-1 harness (`app.strategy_templates.parity`) and adds what the wave templates need:

* the wave timeframe and the higher timeframe are read from frames built from the SAME trailing window W of closed
  signal bars (`context_frames`: the ScreenQL runtime's own resampling, buckets from the exchange open, a bucket that had
  not closed dropped). A wave count reads its swings from the first bar it is given (the swings are path-dependent,
  review finding WB12), so a context frame with more history than W could count differently: every path derives its
  context frames from W, and `check_frames` refuses a frame that starts before W or ends after it;
* the scan is run twice - with the context frames handed in (a screen that loads them) and with the signal frame only
  (the backtest engines and the worker resample it themselves) - and both must give the same answer on every bar;
* `wave_history_ok`: the window holds every timeframe's history (the wave degree's swings on the wave timeframe), so
  the comparison is made after the warm-up, as a deployment fetching `parity.fetch_bars` bars would see it.

`run` returns every difference (empty = parity). Signals only: nothing here places, sizes or exits an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from app.core.models import RiskConfig
from app.screener import nodes as n
from app.screener.runtime import bar_close, resample
from app.strategy_templates import engine, parity
from app.strategy_templates.adapter import TemplateStrategy
from app.strategy_templates.compile import Recipe


def context_frames(recipe: Recipe, window: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Every timeframe the recipe reads besides its signal timeframe, built from `window` alone (the runtime's resample)."""
    tf = recipe.settings.timeframe
    return {other: resample(window, tf, other) for other in sorted(recipe.lookback) if other != tf}


def check_frames(window: pd.DataFrame, base_tf: str, frames: Dict[str, pd.DataFrame]) -> List[str]:
    """A context frame must lie within the window's span: its first bar closes after the window's first bar starts (no
    earlier history) and its last bar closes by the window's last close (no later or forming bar)."""
    out: List[str] = []
    if not len(window):
        return out
    first, last_close = window.index[0], bar_close(window.index[-1:], base_tf)[0]
    for tf, frame in sorted(frames.items()):
        if not len(frame):
            continue
        if bar_close(frame.index[:1], tf)[0] <= first:
            out.append(f"{tf}: the frame starts before the window (more history can count the waves differently)")
        if bar_close(frame.index[-1:], tf)[0] > last_close:
            out.append(f"{tf}: the frame has a bar that closes after the window's last bar")
    return out


def scan_with_frames(recipe: Recipe, window: pd.DataFrame, frames: Dict[str, pd.DataFrame]) -> Dict[pd.Timestamp, parity.Answer]:
    """The scan path when the context frames are handed in (as a screen that loads them would)."""
    series = engine.compute(recipe, {recipe.settings.timeframe: window, **frames})
    out: Dict[pd.Timestamp, parity.Answer] = {}
    for t, ts in enumerate(series.index):
        d = int(series.direction[t])
        out[pd.Timestamp(ts)] = parity.Answer(d, float(series.invalidation[t]), series.instance_id(t)) if d else parity.Answer(0)
    return out


def wave_history_ok(recipe: Recipe, window: pd.DataFrame) -> bool:
    """The window holds the history every timeframe the recipe reads needs (its warm-up ends inside the window)."""
    tf = recipe.settings.timeframe
    if len(window) < recipe.min_bars:
        return False
    for other, bars in recipe.lookback.items():
        if other != tf and n.TF_MINUTES[other] > n.TF_MINUTES[tf] and len(resample(window, tf, other)) < bars:
            return False
    return True


@dataclass
class Report:
    """What the harness compared: the scan's signal bars, how many bars each path asked about, and every difference."""
    signals: List[pd.Timestamp] = field(default_factory=list)
    asked: Dict[str, int] = field(default_factory=dict)
    trades: int = 0
    problems: List[str] = field(default_factory=list)


def run(strategy: TemplateStrategy, window: pd.DataFrame, symbol: str = "S", risk: Optional[RiskConfig] = None,
        worker_bars: Optional[List[int]] = None, options_config: Optional[object] = None) -> Report:
    """scan (frames handed in) = scan (signal frame only) = backtest = options backtest (when `options_config` is given) =
    worker (on `worker_bars`, default: every signal bar, its neighbours and every tenth bar), on the one window."""
    recipe = strategy.recipe
    report = Report()
    frames = context_frames(recipe, window)
    report.problems += check_frames(window, recipe.settings.timeframe, frames)
    with_frames = scan_with_frames(recipe, window, frames)
    scan = parity.scan_answers(recipe, window)
    report.signals = [ts for ts, a in scan.items() if a.direction]
    report.problems += parity.compare(scan, with_frames, "scan with the context frames")
    answers, trades = parity.backtest_answers(strategy, window, symbol, risk)
    report.asked["backtest"], report.trades = len(answers), len(trades)
    report.problems += parity.compare(scan, answers, "backtest")
    report.problems += parity.compare_trades(scan, trades, lambda ts: ts)
    if options_config is not None:
        options = parity.options_answers(strategy, window, symbol, options_config, risk)
        report.asked["options"] = len(options)
        report.problems += parity.compare(scan, options, "options")
    if worker_bars is None:
        at = [i for i, a in enumerate(scan.values()) if a.direction]
        worker_bars = sorted({j for i in at for j in (i - 1, i, i + 1) if 0 <= j < len(window)}
                             | set(range(0, len(window), 10)) | {len(window) - 1})
    worker = parity.worker_answers(strategy, window, symbol, worker_bars)
    report.asked["worker"] = len(worker)
    report.problems += parity.compare(scan, worker, "worker")
    seen = {ts for ts, a in worker.items() if a.direction}
    missing = sorted(ts for ts in report.signals if pd.Timestamp(ts) in {pd.Timestamp(window.index[j]) for j in worker_bars} and ts not in seen)
    report.problems += [f"worker: no signal on the scan signal bar {ts}" for ts in missing]
    return report


__all__ = ["context_frames", "check_frames", "scan_with_frames", "wave_history_ok", "Report", "run"]
