"""Phase J2: robustness checks on a backtest (master prompt sections 31-32, V2.10, V4.8).

* **Monte Carlo** - resample the closed trade P&Ls with replacement `runs` times (same count),
  rebuild each equity path from the starting capital, and report the distribution of final
  P&L and max drawdown (percentiles), the share of runs that lost money, and the probability
  of a drawdown deeper than the original. Answers "how much of this result is sequence luck".
* **Walk-forward** - split the bar history into `folds` consecutive windows, run the *same*
  strategy and parameters on each, and report per-window metrics plus how many windows were
  profitable. A strategy whose edge lives in one window is not an edge. (Parameter
  optimisation per window is deliberately not done here: it needs a parameter grid, and a
  re-optimised strategy is a different strategy version - section 41.)

Both are pure functions; the API layer feeds them the same inputs as `/api/backtest`.
"""
import random
from typing import Callable, Dict, List, Optional

import pandas as pd

from app.backtest.analytics import drawdown_curve
from app.backtest.engine import run_backtest
from app.core.models import RiskConfig, Trade
from app.strategy_engine.base import BaseStrategy


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 2)


def monte_carlo(trades: List[Trade], capital: float, runs: int = 1000, seed: Optional[int] = 42) -> Dict:
    pnls = [t.pnl or 0.0 for t in trades if t.exit_time is not None]
    if not pnls:
        return {"runs": 0, "trades": 0, "note": "No closed trades to resample"}
    rng = random.Random(seed)
    finals: List[float] = []
    drawdowns: List[float] = []
    original_dd = max(drawdown_curve([capital] + _cumulative(capital, pnls)))
    for _ in range(runs):
        sample = [rng.choice(pnls) for _ in pnls]
        curve = [capital] + _cumulative(capital, sample)
        finals.append(round(curve[-1] - capital, 2))
        drawdowns.append(max(drawdown_curve(curve)))
    return {
        "runs": runs, "trades": len(pnls), "seed": seed,
        "final_pnl": {"p5": _percentile(finals, 5), "p25": _percentile(finals, 25), "p50": _percentile(finals, 50),
                      "p75": _percentile(finals, 75), "p95": _percentile(finals, 95), "mean": round(sum(finals) / runs, 2)},
        "max_drawdown": {"p50": _percentile(drawdowns, 50), "p95": _percentile(drawdowns, 95), "worst": round(max(drawdowns), 2),
                         "original": round(original_dd, 2)},
        "probability_of_loss_pct": round(sum(1 for f in finals if f < 0) / runs * 100, 1),
        "probability_dd_exceeds_original_pct": round(sum(1 for d in drawdowns if d > original_dd) / runs * 100, 1),
        "risk_of_ruin_pct": round(sum(1 for d in drawdowns if d >= capital * 0.5) / runs * 100, 1),
        "note": "Trade P&Ls resampled with replacement; the order of trades is what varies. Not a forecast.",
    }


def _cumulative(capital: float, pnls: List[float]) -> List[float]:
    out, equity = [], capital
    for p in pnls:
        equity += p
        out.append(equity)
    return out


def walk_forward(strategy: BaseStrategy, base_df: pd.DataFrame, symbol: str, base_tf: str, risk_config: RiskConfig,
                 folds: int = 4, runner: Optional[Callable[[BaseStrategy, pd.DataFrame], object]] = None) -> Dict:
    """`runner(strategy, window_df)` runs one window; the default is the underlying engine, the
    option backtest (Phase W) passes its own so every window prices the same structures."""
    n = len(base_df)
    folds = max(2, min(folds, 12))
    min_hist = strategy.min_history()[strategy.timeframes[0]]
    if n < folds * (min_hist + 20):
        return {"folds": 0, "note": f"Not enough bars for {folds} windows (need about {folds * (min_hist + 20)}, have {n})", "windows": []}
    size = n // folds
    windows = []
    for i in range(folds):
        start, end = i * size, (i + 1) * size if i < folds - 1 else n
        part = base_df.iloc[start:end]
        result = runner(strategy, part) if runner is not None else run_backtest(strategy, part, symbol, base_tf, risk_config)
        windows.append({
            "window": i + 1, "from": part.index[0].isoformat(), "to": part.index[-1].isoformat(), "bars": len(part),
            "trades": result.total_trades, "net_pnl": result.net_pnl, "win_rate": result.win_rate,
            "profit_factor": result.profit_factor, "max_drawdown": result.max_drawdown, "expectancy": result.expectancy,
        })
    profitable = sum(1 for w in windows if w["net_pnl"] > 0)
    pnls = [w["net_pnl"] for w in windows]
    mean = sum(pnls) / len(pnls)
    spread = (max(pnls) - min(pnls)) if pnls else 0.0
    return {
        "folds": folds, "windows": windows, "profitable_windows": profitable,
        "consistency_pct": round(profitable / folds * 100, 1), "mean_window_pnl": round(mean, 2), "window_pnl_range": round(spread, 2),
        "note": "Same parameters on every window (no per-window optimisation). Consistency below ~60% means the edge is not stable across time.",
    }
