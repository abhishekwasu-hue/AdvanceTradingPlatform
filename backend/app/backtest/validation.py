"""Overfitting checks for backtests and parameter searches - report-only statistics, numpy + stdlib (no scipy).

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61:
  research_stats.py - `sharpe`, `moments`, `expected_max_sharpe`, `deflated_sharpe` (Bailey & Lopez de Prado 2014),
                      `pbo_cscv` (Bailey, Borwein, Lopez de Prado & Zhu 2015, combinatorially symmetric cross-validation),
                      `shuffle_pvalue` (permutation test);
  research/elliott_candle_merge_report.py - `day_block_ci` (day-block bootstrap), `random_entry_compare` (strategy vs a
                      random entry inside the same episodes), `reality_check` (White 2000 Reality Check on mean R per trade).
Trade-specific data loading and Elliott episode construction are not ported; callers pass plain arrays / DataFrames.

Reading the numbers: DSR >= 0.95 - the chosen variant's Sharpe survives the number of variants tried; PBO > 0.05 - the
in-sample winner tends to rank below the median out-of-sample (reject); Reality Check p < 0.05 - at least one variant
beats the benchmark beyond data-snooping. None of these says a strategy will make money.
"""
import itertools
import math
from statistics import NormalDist
from typing import Callable, Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def _clean(r) -> np.ndarray:
    a = np.asarray(r, dtype=float)
    return a[np.isfinite(a)]


def sharpe(r) -> float:
    """mean / std (ddof=1) per observation (not annualised). Fewer than 2 samples or zero std -> 0.0."""
    a = _clean(r)
    if len(a) < 2:
        return 0.0
    sd = a.std(ddof=1)
    return float(a.mean() / sd) if sd > 0 else 0.0


def moments(r):
    """(skew, kurtosis - normal = 3). Fewer than 3 samples -> (0, 3)."""
    a = _clean(r)
    if len(a) < 3:
        return 0.0, 3.0
    m, sd = a.mean(), a.std(ddof=0)
    if sd == 0:
        return 0.0, 3.0
    z = (a - m) / sd
    return float((z ** 3).mean()), float((z ** 4).mean())


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """The expected maximum Sharpe of `n_trials` independent zero-skill trials - the DSR benchmark SR0."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    sd = math.sqrt(var_sr)
    return float(sd * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1.0 / n_trials) + EULER_GAMMA * _N.inv_cdf(1 - 1.0 / (n_trials * math.e))))


def deflated_sharpe(r, trial_sharpes) -> Dict:
    """`r` = returns of the selected trial; `trial_sharpes` = the Sharpe of every trial tried (same unit). Returns
    {sr, sr0, dsr (P[true SR > sr0], 0-1, skew/kurtosis corrected), n, n_trials}; dsr None below 3 samples."""
    a, srs = _clean(r), _clean(trial_sharpes)
    n, nt = len(a), max(len(srs), 1)
    sr = sharpe(a)
    var_sr = float(srs.var(ddof=1)) if len(srs) > 1 else 0.0
    sr0 = expected_max_sharpe(nt, var_sr)
    skew, kurt = moments(a)
    out = {"sr": round(sr, 4), "sr0": round(sr0, 4), "dsr": None, "n": n, "n_trials": nt}
    if n < 3:
        return out
    denom = 1 - skew * sr + (kurt - 1) / 4.0 * sr * sr
    if denom <= 0:
        return out
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    out["dsr"] = round(float(_N.cdf(z)), 4)
    return out


def _col_sharpe(B: np.ndarray) -> np.ndarray:
    mu = B.mean(axis=0)
    sd = B.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, mu / sd, 0.0)


def pbo_cscv(M, S: int = 16, metric: Optional[Callable[[np.ndarray], np.ndarray]] = None, max_combos: Optional[int] = None,
             seed: int = 5) -> Dict:
    """Probability of Backtest Overfitting by CSCV. `M` = T x N (rows in time order, columns = trials). The rows are cut
    into S blocks; for every half/half split the in-sample best trial's out-of-sample rank gives a logit; PBO = share of
    logits <= 0. `metric(block) -> N scores` (default column Sharpe). `max_combos` samples the splits (seeded).
    Returns {pbo, n_splits, logits, N, T}; pbo None when N < 2, T < S or S odd."""
    M = np.asarray(M, dtype=float)
    if M.ndim != 2:
        raise ValueError("M must be 2-D (T x N)")
    T, N = M.shape
    if N < 2 or T < S or S < 2 or S % 2:
        return {"pbo": None, "n_splits": 0, "logits": [], "N": N, "T": T}
    metric = metric or _col_sharpe
    groups = np.array_split(np.arange(T), S)
    combos = list(itertools.combinations(range(S), S // 2))
    if max_combos is not None and len(combos) > max_combos:
        rng = np.random.default_rng(seed)
        combos = [combos[i] for i in rng.choice(len(combos), max_combos, replace=False)]
    logits = []
    for is_ids in combos:
        chosen = set(is_ids)
        is_rows = np.concatenate([groups[g] for g in is_ids])
        oos_rows = np.concatenate([groups[g] for g in range(S) if g not in chosen])
        is_score, oos_score = metric(M[is_rows]), metric(M[oos_rows])
        best = int(np.argmax(is_score))
        rank = 1 + int((oos_score < oos_score[best]).sum()) + 0.5 * int((oos_score == oos_score[best]).sum() - 1)
        w = rank / (N + 1.0)
        logits.append(math.log(w / (1 - w)))
    lg = np.array(logits)
    return {"pbo": round(float((lg <= 0).mean()), 4), "n_splits": len(lg), "logits": lg.tolist(), "N": N, "T": T}


def shuffle_pvalue(stat_fn: Callable, x, y, n_perm: int = 1000, seed: int = 0):
    """Permutation test: `stat_fn(x, y)` on the real data, then on `n_perm` shuffles of `y`. Returns (stat, p) with
    p = P[shuffled >= real] (one-sided, +1 corrected); seeded, so reproducible."""
    rng = np.random.default_rng(seed)
    y = np.asarray(y)
    real = float(stat_fn(x, y))
    hits = sum(1 for _ in range(n_perm) if float(stat_fn(x, rng.permutation(y))) >= real)
    return real, (hits + 1.0) / (n_perm + 1.0)


def _days(trades: pd.DataFrame, when: str) -> pd.Series:
    return pd.to_datetime(trades[when]).dt.date


def day_block_ci(trades: pd.DataFrame, value: str = "R", when: str = "time", reps: int = 1000, seed: int = 11,
                 level: float = 0.95):
    """Bootstrap CI of the mean `value` per trade, resampling whole DAYS (trades of one day are not independent).
    Returns (low, high); (nan, nan) for no trades."""
    if trades.empty:
        return (float("nan"), float("nan"))
    days = list(trades.assign(_d=_days(trades, when)).groupby("_d")[value].apply(list).values)
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(reps):
        vals = [v for i in rng.integers(len(days), size=len(days)) for v in days[i]]
        means.append(np.mean(vals) if vals else 0.0)
    tail = (1 - level) / 2 * 100
    return float(np.percentile(means, tail)), float(np.percentile(means, 100 - tail))


def random_entry_compare(strategy_r, pool: pd.DataFrame, reps: int = 1000, seed: int = 17, level: float = 0.95) -> Dict:
    """The strategy's mean R minus a random-entry baseline. `pool` = DataFrame(episode, R): the R of entries taken at
    random bars inside the same episodes / setups (built by the caller with the same exits and costs). Both sides are
    bootstrapped -> {diff, lo, hi, p (= P[diff <= 0]), random_mean}; NaNs below 5 strategy trades or an empty pool."""
    ew = _clean(strategy_r)
    nan = float("nan")
    if len(ew) < 5 or pool.empty:
        return {"diff": nan, "lo": nan, "hi": nan, "p": nan, "random_mean": nan}
    eps = [v for v in pool.groupby("episode")["R"].apply(np.array).values if len(v)]
    rnd_mean = float(np.mean([v.mean() for v in eps]))
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(reps):
        e = ew[rng.integers(len(ew), size=len(ew))].mean()
        r = np.mean([eps[i][rng.integers(len(eps[i]))] for i in rng.integers(len(eps), size=len(eps))])
        diffs.append(e - r)
    d = np.array(diffs)
    tail = (1 - level) / 2 * 100
    return {"diff": float(ew.mean() - rnd_mean), "lo": float(np.percentile(d, tail)), "hi": float(np.percentile(d, 100 - tail)),
            "p": float(np.mean(d <= 0)), "random_mean": rnd_mean}


def reality_check(variants: Mapping[str, pd.DataFrame], bench: pd.DataFrame, value: str = "R", when: str = "time",
                  reps: int = 1000, seed: int = 3) -> float:
    """White's Reality Check on mean `value` per trade with a day-block bootstrap. H0: no variant beats the benchmark.
    Days a variant did not trade add nothing (fewer trades never looks 'better' by itself). Returns the p-value (nan with
    no variants)."""
    if not variants:
        return float("nan")
    frames = list(variants.values()) + [bench]
    days = sorted(set().union(*[set(_days(df, when)) for df in frames if not df.empty]))
    pos = {d: i for i, d in enumerate(days)}

    def arrays(df: pd.DataFrame):
        S, N = np.zeros(len(days)), np.zeros(len(days))
        if not df.empty:
            g = df.assign(_d=_days(df, when)).groupby("_d")[value]
            sm, ct = g.sum(), g.count()
            S[[pos[d] for d in sm.index]] = sm.to_numpy()
            N[[pos[d] for d in ct.index]] = ct.to_numpy()
        return S, N

    bS, bN = arrays(bench)
    VS = [arrays(df) for df in variants.values()]
    obs = np.array([S.sum() / max(N.sum(), 1) - bS.sum() / max(bN.sum(), 1) for S, N in VS])
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        idx = rng.integers(len(days), size=len(days))
        b = bS[idx].sum() / max(bN[idx].sum(), 1)
        d = np.array([S[idx].sum() / max(N[idx].sum(), 1) - b for S, N in VS]) - obs      # centred on H0
        hits += d.max() >= obs.max()
    return hits / reps


def daily_matrix(series_by_trial: Sequence[pd.Series], index: Optional[pd.Index] = None) -> np.ndarray:
    """T x N matrix of per-day P&L (days without a trade = 0) from one Series(day -> pnl) per trial - the input of
    `pbo_cscv`."""
    days = index if index is not None else sorted(set().union(*[set(s.index) for s in series_by_trial])) if series_by_trial else []
    return np.column_stack([s.reindex(days, fill_value=0.0).to_numpy(dtype=float) for s in series_by_trial]) if series_by_trial \
        else np.zeros((0, 0))
