"""Phase M / V4.6: parameter optimisation with an out-of-sample guard.

A small grid over strategy parameters is run on the in-sample part of the data and every
combination is then re-run untouched on the held-out part. The ranking is by the out-of-sample
metric, and each row shows the in-sample figure beside it so an over-fitted winner (great
in-sample, flat or negative out-of-sample) is visible instead of hidden. Bounded to
`MAX_COMBOS` combinations per call; this is a guided search, not a promise.
"""
import copy
import itertools
from typing import Dict, List, Optional

import pandas as pd

from app.backtest.engine import run_backtest
from app.core.models import RiskConfig
from app.strategy_engine.base import BaseStrategy

MAX_COMBOS = 60
METRICS = ("net_pnl", "expectancy", "profit_factor", "win_rate")


def expand_grid(grid: Dict[str, List]) -> List[Dict]:
    keys = sorted(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))
    return [dict(zip(keys, values)) for values in combos]


def _metric(result, name: str) -> Optional[float]:
    value = getattr(result, name, None)
    if value is None:
        return None
    return float(value)


def optimize(strategy: BaseStrategy, base_df: pd.DataFrame, symbol: str, base_tf: str, risk_config: RiskConfig, grid: Dict[str, List], *,
             metric: str = "net_pnl", split: float = 0.7, min_trades: int = 5) -> Dict:
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {METRICS}")
    if not 0.5 <= split <= 0.9:
        raise ValueError("split must be between 0.5 and 0.9")
    combos = expand_grid(grid)
    if not combos:
        raise ValueError("param_grid is empty")
    if len(combos) > MAX_COMBOS:
        raise ValueError(f"{len(combos)} combinations exceed the limit of {MAX_COMBOS}; narrow the grid")
    n = len(base_df)
    cut = int(n * split)
    in_sample, out_sample = base_df.iloc[:cut], base_df.iloc[cut:]
    min_hist = strategy.min_history()[strategy.timeframes[0]]
    if len(out_sample) < min_hist + 20:
        raise ValueError(f"Out-of-sample part too short ({len(out_sample)} bars); need about {min_hist + 20} - supply more data or lower split")
    rows = []
    for params in combos:
        candidate = copy.copy(strategy)
        candidate.params = {**strategy.params, **params}
        ins = run_backtest(candidate, in_sample, symbol, base_tf, risk_config)
        oos = run_backtest(candidate, out_sample, symbol, base_tf, risk_config)
        in_value, out_value = _metric(ins, metric), _metric(oos, metric)
        rows.append({
            "params": params,
            "in_sample": {"trades": ins.total_trades, "net_pnl": ins.net_pnl, "win_rate": ins.win_rate, "profit_factor": ins.profit_factor,
                          "expectancy": ins.expectancy, "max_drawdown": ins.max_drawdown},
            "out_of_sample": {"trades": oos.total_trades, "net_pnl": oos.net_pnl, "win_rate": oos.win_rate, "profit_factor": oos.profit_factor,
                              "expectancy": oos.expectancy, "max_drawdown": oos.max_drawdown},
            "score": out_value if (out_value is not None and oos.total_trades >= min_trades) else None,
            "overfit_gap": (in_value - out_value) if (in_value is not None and out_value is not None) else None,
            "flags": ([] if oos.total_trades >= min_trades else [f"only {oos.total_trades} out-of-sample trades"])
                     + (["in-sample profit did not carry out-of-sample"] if (in_value or 0) > 0 and (out_value or 0) <= 0 else []),
        })
    ranked = sorted(rows, key=lambda r: (r["score"] is None, -(r["score"] or 0.0)))
    best = next((r for r in ranked if r["score"] is not None), None)
    robust = [r for r in ranked if r["score"] is not None and (r["score"] or 0) > 0 and not r["flags"]]
    return {
        "metric": metric, "split": split, "in_sample_bars": cut, "out_of_sample_bars": n - cut, "combinations": len(rows),
        "best": best, "robust_count": len(robust), "results": ranked,
        "note": ("Ranked by the out-of-sample metric. Prefer a parameter region where neighbours also score well over a single spike; "
                 "a large overfit_gap means the in-sample result did not generalise. Re-run walk-forward on the chosen parameters."),
    }
