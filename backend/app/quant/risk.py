"""Phase Z: a small, honest risk model on the candles the caller supplies.

Log returns aligned on the timestamps every symbol shares; from them a correlation matrix,
betas to a chosen benchmark, annualised volatilities, and for a weight vector the portfolio's
annualised volatility, its historical one-bar 95% VaR and CVaR (as fractions of the book),
the worst drawdown of the weighted return path over the window and a diversification ratio.
Two weight suggestions: inverse volatility and a risk-parity approximation (equal risk
contribution by iterative rescaling). Everything is descriptive of the past window; nothing
here predicts, and none of it sizes or places a trade - the risk engine does that.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

VAR_CONFIDENCE = 0.95
MIN_OVERLAP = 20


def returns_matrix(frames: Dict[str, pd.DataFrame], lookback: int = 250) -> pd.DataFrame:
    """Log returns on the inner join of timestamps, last `lookback` rows."""
    series = {}
    for symbol, df in frames.items():
        if df is None or df.empty or "close" not in df:
            continue
        close = df["close"].astype(float)
        close = close[~close.index.duplicated(keep="last")]
        series[symbol] = np.log(close / close.shift(1)).dropna()
    if not series:
        return pd.DataFrame()
    matrix = pd.concat(series, axis=1, join="inner").dropna(how="any")
    return matrix.iloc[-lookback:]


def correlation(returns: pd.DataFrame) -> Dict[str, Dict[str, Optional[float]]]:
    if returns.empty or len(returns) < 2:
        return {}
    corr = returns.corr()
    return {a: {b: (None if pd.isna(corr.loc[a, b]) else round(float(corr.loc[a, b]), 4)) for b in corr.columns} for a in corr.index}


def annualised_vol(returns: pd.DataFrame, bars_per_year: float) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for col in returns.columns:
        s = returns[col].dropna()
        out[col] = round(float(s.std(ddof=1) * math.sqrt(bars_per_year)), 6) if len(s) >= 5 else None
    return out


def betas(returns: pd.DataFrame, benchmark: str) -> Dict[str, Optional[float]]:
    if benchmark not in returns.columns or len(returns) < MIN_OVERLAP:
        return {col: None for col in returns.columns}
    bench = returns[benchmark]
    var = float(bench.var(ddof=1))
    out: Dict[str, Optional[float]] = {}
    for col in returns.columns:
        out[col] = round(float(returns[col].cov(bench) / var), 4) if var > 0 else None
    return out


def normalise(weights: Dict[str, float]) -> Dict[str, float]:
    total = sum(abs(float(w)) for w in weights.values())
    if total <= 0:
        raise ValueError("weights must not all be zero")
    return {k: round(float(w) / total, 6) for k, w in weights.items()}


def portfolio_risk(returns: pd.DataFrame, weights: Dict[str, float], bars_per_year: float) -> Dict[str, Optional[float]]:
    cols = [c for c in returns.columns if c in weights and weights[c]]
    if not cols or len(returns) < MIN_OVERLAP:
        return {"vol_annual": None, "var_95": None, "cvar_95": None, "max_drawdown_pct": None, "diversification_ratio": None, "bars": len(returns)}
    w = np.array([float(weights[c]) for c in cols])
    gross = float(np.sum(np.abs(w)))
    w_norm = w / gross if gross > 0 else w
    r = returns[cols].values
    port = r @ w_norm
    vol = float(np.std(port, ddof=1) * math.sqrt(bars_per_year))
    losses = -port
    var = float(np.quantile(losses, VAR_CONFIDENCE))
    tail = losses[losses >= var]
    cvar = float(tail.mean()) if len(tail) else var
    equity = np.cumprod(1.0 + port)
    peak = np.maximum.accumulate(equity)
    max_dd = float(((peak - equity) / peak).max()) if len(equity) else 0.0
    single_vols = np.std(r, axis=0, ddof=1) * math.sqrt(bars_per_year)
    weighted_avg_vol = float(np.sum(np.abs(w_norm) * single_vols))
    return {"vol_annual": round(vol, 6), "var_95": round(var, 6), "cvar_95": round(cvar, 6), "max_drawdown_pct": round(max_dd * 100, 4),
            "diversification_ratio": round(weighted_avg_vol / vol, 4) if vol > 0 else None, "bars": len(returns)}


def inverse_vol_weights(vols: Dict[str, Optional[float]]) -> Dict[str, float]:
    inv = {k: 1.0 / v for k, v in vols.items() if v and v > 0}
    return normalise(inv) if inv else {}


def risk_parity_weights(returns: pd.DataFrame, iterations: int = 200) -> Dict[str, float]:
    """Equal risk contribution by iterative rescaling (weights ∝ 1 / marginal risk); long-only."""
    cols = list(returns.columns)
    if len(cols) == 0 or len(returns) < MIN_OVERLAP:
        return {}
    cov = np.cov(returns.values, rowvar=False, ddof=1)
    cov = np.atleast_2d(cov)
    w = np.ones(len(cols)) / len(cols)
    for _ in range(iterations):
        marginal = cov @ w
        contrib = w * marginal
        total = float(contrib.sum())
        if total <= 0:
            break
        target = total / len(cols)
        adjust = np.where(contrib > 0, np.sqrt(target / np.maximum(contrib, 1e-18)), 1.0)
        w = w * adjust
        w = np.maximum(w, 1e-9)
        w = w / w.sum()
    return {c: round(float(x), 6) for c, x in zip(cols, w)}


def risk_contributions(returns: pd.DataFrame, weights: Dict[str, float]) -> Dict[str, Optional[float]]:
    cols = [c for c in returns.columns if c in weights]
    if not cols or len(returns) < MIN_OVERLAP:
        return {c: None for c in cols}
    w = np.array([float(weights[c]) for c in cols])
    cov = np.atleast_2d(np.cov(returns[cols].values, rowvar=False, ddof=1))
    contrib = w * (cov @ w)
    total = float(contrib.sum())
    return {c: round(float(x / total), 4) if total > 0 else None for c, x in zip(cols, contrib)}


def max_drawdown_pct(close: pd.Series) -> Optional[float]:
    if close is None or len(close) < 2:
        return None
    values = close.astype(float).values
    peak = np.maximum.accumulate(values)
    return round(float(((peak - values) / peak).max() * 100), 4)


def summarise(frames: Dict[str, pd.DataFrame], *, weights: Optional[Dict[str, float]], benchmark: Optional[str], lookback: int,
              bars_per_year: float) -> dict:
    returns = returns_matrix(frames, lookback)
    warnings: List[str] = []
    if returns.empty or len(returns) < MIN_OVERLAP:
        warnings.append(f"need at least {MIN_OVERLAP} overlapping bars across the symbols, have {len(returns)}")
    vols = annualised_vol(returns, bars_per_year) if not returns.empty else {}
    bench = benchmark if benchmark in returns.columns else (None if benchmark else None)
    if benchmark and bench is None:
        warnings.append(f"benchmark {benchmark} is not in the universe - betas skipped")
    given = normalise({k: v for k, v in (weights or {}).items() if k in returns.columns}) if weights and any(k in returns.columns for k in weights) else None
    equal = {c: round(1.0 / len(returns.columns), 6) for c in returns.columns} if len(returns.columns) else {}
    inv = inverse_vol_weights(vols)
    parity = risk_parity_weights(returns)
    chosen = given or equal
    return {
        "bars": int(len(returns)), "symbols": list(returns.columns), "bars_per_year": bars_per_year,
        "correlation": correlation(returns), "volatility": vols, "betas": betas(returns, bench) if bench else {},
        "benchmark": bench, "weights_used": chosen, "weights_source": "given" if given else "equal",
        "portfolio": portfolio_risk(returns, chosen, bars_per_year) if chosen else {},
        "risk_contributions": risk_contributions(returns, chosen) if chosen else {},
        "suggested": {"inverse_volatility": inv, "risk_parity": parity,
                      "inverse_volatility_portfolio": portfolio_risk(returns, inv, bars_per_year) if inv else {},
                      "risk_parity_portfolio": portfolio_risk(returns, parity, bars_per_year) if parity else {}},
        "max_drawdown_pct": {s: max_drawdown_pct(df["close"]) for s, df in frames.items() if df is not None and not df.empty},
        "warnings": warnings,
        "disclaimer": "Descriptive statistics of the supplied window (log returns on shared timestamps). They describe the past, "
                      "not the future, and never size or place a trade - the risk engine does that.",
    }
