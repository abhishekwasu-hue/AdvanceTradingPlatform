"""Phase M / V4.6: parameter optimisation with an out-of-sample guard.

A small grid over strategy parameters is run on the in-sample part of the data and every
combination is then re-run untouched on the held-out part. Candidates are ranked by the in-sample
metric and each row carries its out-of-sample figure as `validation` (P0.6 / B3: ranking on the held-out
part would have made it in-sample too), so an over-fitted winner (great in-sample, flat or negative
out-of-sample) is visible instead of hidden. Bounded to
`MAX_COMBOS` combinations per call; this is a guided search, not a promise.

Trade port (validation): bars from the sealed holdout (`data_policy`, BACKTEST_HOLDOUT_START or a per-run
`holdout_start`) are dropped before the split and never run; the search also reports PBO by CSCV over the in-sample
per-day P&L of every combination and the Deflated Sharpe Ratio of the in-sample winner (`app/backtest/validation`).
"""
import copy
import itertools
from typing import Dict, List, Optional

import pandas as pd

from app.backtest import data_policy, validation
from app.backtest.engine import run_backtest
from app.core.models import RiskConfig
from app.strategy_engine.base import BaseStrategy

MAX_COMBOS = 60
PBO_BLOCKS = 8                     # CSCV blocks over the in-sample days (C(8,4) = 70 splits)
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
             metric: str = "net_pnl", split: float = 0.7, min_trades: int = 5, holdout_start=None) -> Dict:
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {METRICS}")
    if not 0.5 <= split <= 0.9:
        raise ValueError("split must be between 0.5 and 0.9")
    combos = expand_grid(grid)
    if not combos:
        raise ValueError("param_grid is empty")
    if len(combos) > MAX_COMBOS:
        raise ValueError(f"{len(combos)} combinations exceed the limit of {MAX_COMBOS}; narrow the grid")
    boundary = data_policy.holdout_start(holdout_start)
    sealed = int((~data_policy.research_mask(base_df.index, boundary)).sum())
    base_df = data_policy.filter_allowed(base_df, boundary)          # the optimizer never sees the holdout
    if base_df.empty:
        raise ValueError("All bars fall in the sealed holdout - supply data from before it")
    n = len(base_df)
    cut = int(n * split)
    in_sample, out_sample = base_df.iloc[:cut], base_df.iloc[cut:]
    min_hist = strategy.min_history()[strategy.timeframes[0]]
    if len(out_sample) < min_hist + 20:
        raise ValueError(f"Out-of-sample part too short ({len(out_sample)} bars); need about {min_hist + 20} - supply more data or lower split")
    rows = []
    daily = []                                                       # in-sample P&L per day, one Series per combination
    for params in combos:
        candidate = copy.copy(strategy)
        candidate.params = {**strategy.params, **params}
        ins = run_backtest(candidate, in_sample, symbol, base_tf, risk_config)
        oos = run_backtest(candidate, out_sample, symbol, base_tf, risk_config)
        in_value, out_value = _metric(ins, metric), _metric(oos, metric)
        daily.append(_daily_pnl(ins))
        enough_in, enough_out = ins.total_trades >= min_trades, oos.total_trades >= min_trades
        rows.append({
            "params": params,
            "in_sample": {"trades": ins.total_trades, "net_pnl": ins.net_pnl, "win_rate": ins.win_rate, "profit_factor": ins.profit_factor,
                          "expectancy": ins.expectancy, "max_drawdown": ins.max_drawdown},
            "out_of_sample": {"trades": oos.total_trades, "net_pnl": oos.net_pnl, "win_rate": oos.win_rate, "profit_factor": oos.profit_factor,
                              "expectancy": oos.expectancy, "max_drawdown": oos.max_drawdown},
            # P0.6 / B3: the *in-sample* metric ranks the candidates; the out-of-sample figure is the honest estimate of
            # the winner, never the thing we pick on (picking on it would make it in-sample too).
            "score": in_value if (in_value is not None and enough_in) else None,
            "validation": out_value if (out_value is not None and enough_out) else None,
            "overfit_gap": (in_value - out_value) if (in_value is not None and out_value is not None) else None,
            "flags": ([] if enough_in else [f"only {ins.total_trades} in-sample trades"])
                     + ([] if enough_out else [f"only {oos.total_trades} out-of-sample trades"])
                     + (["in-sample profit did not carry out-of-sample"] if (in_value or 0) > 0 and (out_value or 0) <= 0 else []),
        })
    ranked = sorted(rows, key=lambda r: (r["score"] is None, -(r["score"] or 0.0)))
    best = next((r for r in ranked if r["score"] is not None), None)
    robust = [r for r in ranked if r["score"] is not None and (r["validation"] or 0) > 0 and not r["flags"]]
    confirmed = best is not None and (best["validation"] or 0) > 0 and not best["flags"]
    overfit = _overfitting(rows, daily, best, in_sample.index)
    return {
        "metric": metric, "split": split, "in_sample_bars": cut, "out_of_sample_bars": n - cut, "combinations": len(rows),
        "holdout_start": boundary.isoformat() if boundary is not None else None, "holdout_bars_excluded": sealed,
        "overfitting": overfit,
        "best": best, "best_confirmed_out_of_sample": confirmed, "robust_count": len(robust), "results": ranked,
        "note": ("Ranked by the in-sample metric; `validation` is each candidate's out-of-sample figure and was not used to rank. "
                 "Trust the winner only if best_confirmed_out_of_sample is true and neighbouring parameters also validate; "
                 "a large overfit_gap means the in-sample result did not generalise. Re-run walk-forward on the chosen parameters. "
                 "overfitting.pbo above 0.05 means the in-sample winner tends to land below the median of the other "
                 "combinations on unseen days; holdout bars were not used."),
    }


def _daily_pnl(result) -> pd.Series:
    rows = [(pd.Timestamp(t.exit_time).date(), float(t.pnl or 0.0)) for t in result.trades if t.exit_time is not None]
    if not rows:
        return pd.Series(dtype=float)
    return pd.DataFrame(rows, columns=["day", "pnl"]).groupby("day")["pnl"].sum()


def _overfitting(rows: List[Dict], daily: List[pd.Series], best: Optional[Dict], in_index) -> Dict:
    """PBO (CSCV) over the in-sample trading days and the DSR of the in-sample winner - in-sample data only."""
    days = sorted({pd.Timestamp(t).date() for t in in_index})
    out: Dict = {"pbo": None, "pbo_splits": 0, "dsr": None, "blocks": PBO_BLOCKS,
                 "note": "PBO needs at least 2 combinations and as many in-sample days as blocks."}
    if len(rows) < 2 or len(days) < PBO_BLOCKS:
        return out
    matrix = validation.daily_matrix(daily, pd.Index(days))
    pbo = validation.pbo_cscv(matrix, S=PBO_BLOCKS)
    out.update(pbo=pbo["pbo"], pbo_splits=pbo["n_splits"], note="PBO by CSCV on in-sample daily P&L; DSR of the in-sample winner.")
    if best is not None:
        i = rows.index(best)
        trial_sr = [validation.sharpe(matrix[:, j]) for j in range(matrix.shape[1])]
        out["dsr"] = validation.deflated_sharpe(matrix[:, i], trial_sr)["dsr"]
    return out
