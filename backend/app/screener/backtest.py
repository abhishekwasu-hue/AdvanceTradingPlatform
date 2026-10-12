"""N3 S6b (spec §2.7): the rolling screen backtest (flag `screener_backtest`, on top of `screener_history`; off).

At every rebalance date d:
- the screen's matches at d (S6a `history.signals`: bars closed by d), ranked by an optional rank expression read at
  d (descending by default);
- the top N (or all matches);
- equal or rank weights (N, N-1, ..., 1);
- the portfolio is rebalanced at the **next session's open** (decided at d's close, filled after it - never at a
  price known before the decision), and held to the next rebalance.

Fills:
- The fills pay the part C slippage model (`backtest.models.ModelSet`, fixed % against the trade, the paper broker's
  default) and India's dated charges for equity delivery (`execution.india_costs.leg_cost`: STT, exchange, SEBI,
  stamp, GST, brokerage per order).
- Whole shares only.
- A holding that stops trading (delisting, suspension) is marked at its last close and sold at it at the next
  rebalance.

The equity is marked at every session's close. Benchmarks:
- the equal-weight eligible universe (rebalanced at the same dates, no costs);
- an index's closes when given (the route passes NIFTY 50 from the lake when it is there).

Reported:
- total return, CAGR, annualised volatility and Sharpe, maximum drawdown;
- costs paid, turnover per rebalance, average holdings;
- the same figures for the benchmarks, and the excess over each.

The route records the run in the research ledger (`kind: screen_backtest`, daily returns) and deflates its Sharpe
against the organisation's other screen backtests of the last 30 days. Pure: frames and tables in, a result out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set

import numpy as np
import pandas as pd

from app.screener import history as H

FLAG = "screener_backtest"
WEIGHTINGS = ("equal", "rank")
MAX_TOP_N = 50
DEFAULT_CAPITAL = 1_000_000.0
DEFAULT_SLIPPAGE_PCT = 0.02             # the paper broker's and C2's default fixed slippage, % against the trade
DEFAULT_BROKERAGE = 20.0                # per order, the paper broker's default
SEGMENT = "EQUITY_DELIVERY"             # held across sessions
CASH_BUFFER = 0.005                     # of the equity kept back at a rebalance for the orders' charges
TRADING_DAYS = 252
DISCLAIMER = ("A simulation on past data with the costs and slippage shown; chosen screens are deflated for the number "
              "tried. It is not a recommendation and does not predict future results.")


@dataclass
class Settings:
    top_n: Optional[int] = None
    weighting: str = "equal"
    descending: bool = True
    capital: float = DEFAULT_CAPITAL
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    brokerage: float = DEFAULT_BROKERAGE


@dataclass
class Result:
    days: List[date] = field(default_factory=list)
    equity: List[float] = field(default_factory=list)
    returns: List[float] = field(default_factory=list)
    benchmark_returns: Dict[str, List[float]] = field(default_factory=dict)
    costs: float = 0.0
    turnover: List[float] = field(default_factory=list)
    holdings: List[int] = field(default_factory=list)
    rebalances: List[Dict[str, Any]] = field(default_factory=list)


def rank_table(rank_ast: Any, frames: Dict[str, pd.DataFrame], dates: Sequence[date], params: Optional[Dict[str, Any]] = None,
               validated: Any = None) -> pd.DataFrame:
    """[dates x symbols] the rank expression's value at each date, on bars closed by then (S6a's causal series read at
    the date, vectorised where the panel covers the expression; NaN where there is none)."""
    if validated is None:
        from app.screener.validator import validate
        validated = validate(rank_ast, base_tf=H.BASE_TF, params=params)       # a number, not a condition: only its plan is used
    values = H.series_at_dates(rank_ast, validated, frames, dates, params)
    return values.apply(pd.to_numeric, errors="coerce").astype(float)


def choose(passers: List[str], ranks: Optional[pd.Series], settings: Settings) -> Dict[str, float]:
    """The target weights at one rebalance: the ranked top N of the matches (no rank: all matches, in symbol order,
    capped at N), equal or rank weighted."""
    if not passers:
        return {}
    if ranks is not None:
        values = {s: float(ranks.get(s, np.nan)) for s in passers}
        # NaN ranks last; ties by symbol (deterministic)
        ordered = sorted(passers, key=lambda s: (math.isnan(values[s]), -values[s] if settings.descending else values[s], s))
    else:
        ordered = sorted(passers)
    chosen = ordered[: settings.top_n] if settings.top_n else ordered
    if settings.weighting == "rank":
        raw = list(range(len(chosen), 0, -1))
    else:
        raw = [1] * len(chosen)
    total = float(sum(raw))
    return {s: w / total for s, w in zip(chosen, raw)}


def _grids(frames: Dict[str, pd.DataFrame], calendar: Sequence[date]):
    """(symbols, opens, closes, marks) on the calendar x symbols grid; marks = the last close on or before each day."""
    cols = sorted(s for s, f in frames.items() if f is not None and not f.empty)
    row = {d: i for i, d in enumerate(calendar)}
    opens = np.full((len(calendar), len(cols)), np.nan)
    closes = np.full((len(calendar), len(cols)), np.nan)
    for j, s in enumerate(cols):
        f = frames[s]
        rows = np.array([row.get(d, -1) for d in H.session_dates(f.index)], dtype=int)
        keep = rows >= 0
        opens[rows[keep], j] = f["open"].to_numpy(dtype=float)[keep]
        closes[rows[keep], j] = f["close"].to_numpy(dtype=float)[keep]
    marks = pd.DataFrame(closes).ffill().to_numpy()
    return cols, opens, closes, marks


def simulate(sig: pd.DataFrame, ranks: Optional[pd.DataFrame], frames: Dict[str, pd.DataFrame], calendar: Sequence[date],
             end: date, settings: Settings, eligible: Optional[Dict[date, Set[str]]] = None,
             benchmark_closes: Optional[Dict[str, pd.Series]] = None) -> Result:
    """The rolling backtest (pure). `sig`: the matches at the rebalance dates; `ranks`: the rank values there (or None)."""
    from app.execution.india_costs import leg_cost
    calendar = sorted(calendar)
    pos = {d: i for i, d in enumerate(calendar)}
    cols, opens, closes, marks = _grids(frames, calendar)
    col = {s: j for j, s in enumerate(cols)}
    slip = settings.slippage_pct / 100.0
    result = Result()
    rebalance_at: Dict[int, date] = {}                      # fill row -> decision date
    for d in sig.index:
        i = pos.get(d)
        if i is not None and i + 1 < len(calendar) and calendar[i + 1] <= end:
            rebalance_at[i + 1] = d
    if not rebalance_at:
        return result
    first = min(rebalance_at)
    last = max(k for k, d in enumerate(calendar) if d <= end)
    cash = float(settings.capital)
    qty = np.zeros(len(cols))
    prev_equity: Optional[float] = None
    bench_prev: Dict[str, Optional[float]] = {}
    ew_index = np.zeros(0, dtype=int)
    for k in range(first, last + 1):
        day = calendar[k]
        if k in rebalance_at:
            d = rebalance_at[k]
            passers = [s for s in sig.columns if bool(sig.at[d, s]) and s in col]
            target = choose(passers, ranks.loc[d] if ranks is not None else None, settings)
            # value the book at this open (a holding without an open today is valued, and sold, at its last close)
            px = np.where(np.isfinite(opens[k]), opens[k], marks[k - 1] if k > 0 else marks[k])
            equity_before = cash + float(np.nansum(qty * np.nan_to_num(px)))
            traded_value = 0.0
            new_qty = np.zeros(len(cols))
            for s, w in target.items():
                j = col[s]
                if not np.isfinite(opens[k, j]) or opens[k, j] <= 0:
                    continue                                  # does not trade at this open: not bought
                # a cash buffer (CASH_BUFFER) for the charges, so the book is never financed by negative cash
                new_qty[j] = math.floor(w * equity_before * (1 - CASH_BUFFER) / (opens[k, j] * (1 + slip)))
            for jj in np.nonzero((new_qty != qty))[0]:
                j = int(jj)
                delta = new_qty[j] - qty[j]
                base = float(px[j]) if np.isfinite(px[j]) else 0.0
                if base <= 0:
                    continue
                side = "buy" if delta > 0 else "sell"
                price = base * (1 + slip) if side == "buy" else base * (1 - slip)
                cost = leg_cost(day, side, price, abs(float(delta)), SEGMENT, settings.brokerage)["total"]
                cash -= float(delta) * price + cost
                result.costs += cost
                traded_value += abs(float(delta)) * price
            qty = new_qty
            result.turnover.append(traded_value / max(equity_before, 1e-9) / 2.0)
            result.holdings.append(int((qty > 0).sum()))
            result.rebalances.append({"decided": str(d), "filled": str(day), "matches": len(passers), "held": sorted(target)})
            elig = eligible.get(d) if eligible is not None else None
            ew_index = np.array([col[s] for s in cols if elig is None or s in elig], dtype=int)
        equity = cash + float(np.nansum(qty * np.nan_to_num(marks[k])))
        result.days.append(day)
        result.equity.append(equity)
        if prev_equity is not None:
            result.returns.append(equity / prev_equity - 1.0)
            # the equal-weight universe: today's close over yesterday's, over the members that traded both days
            with np.errstate(invalid="ignore", divide="ignore"):
                day_ret = closes[k, ew_index] / closes[k - 1, ew_index] - 1.0
            day_ret = day_ret[np.isfinite(day_ret)]
            ew = float(day_ret.mean()) if len(day_ret) else 0.0
            result.benchmark_returns.setdefault("equal_weight_universe", []).append(ew)
            for name, series in (benchmark_closes or {}).items():
                value = series.get(day)
                before = bench_prev.get(name)
                result.benchmark_returns.setdefault(name, []).append(
                    float(value) / before - 1.0 if value is not None and before and np.isfinite(value) else 0.0)
        for name, series in (benchmark_closes or {}).items():
            value = series.get(day)
            if value is not None and np.isfinite(value):
                bench_prev[name] = float(value)
        prev_equity = equity
    return result


def metrics(returns: Sequence[float]) -> Dict[str, Optional[float]]:
    r = np.array([x for x in returns if np.isfinite(x)], dtype=float)
    if len(r) == 0:
        return {"total_return": None, "cagr": None, "volatility": None, "sharpe": None, "max_drawdown": None, "sessions": 0}
    curve = np.cumprod(1 + r)
    total = float(curve[-1] - 1.0)
    years = len(r) / TRADING_DAYS
    cagr = float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else None
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    peak = np.maximum.accumulate(np.concatenate([[1.0], curve]))
    dd = float(np.min(np.concatenate([[1.0], curve]) / peak - 1.0))
    return {"total_return": round(total, 6), "cagr": None if cagr is None else round(cagr, 6),
            "volatility": round(sd * math.sqrt(TRADING_DAYS), 6),
            "sharpe": round(float(r.mean() / sd * math.sqrt(TRADING_DAYS)), 4) if sd > 0 else None,
            "max_drawdown": round(dd, 6), "sessions": int(len(r))}


def report(result: Result, settings: Settings) -> Dict[str, Any]:
    own = metrics(result.returns)
    benches = {name: metrics(r) for name, r in result.benchmark_returns.items()}
    excess = {name: (round(own["total_return"] - b["total_return"], 6)
                     if own["total_return"] is not None and b["total_return"] is not None else None) for name, b in benches.items()}
    return {"strategy": own, "benchmarks": benches, "excess_total_return": excess,
            "final_equity": round(result.equity[-1], 2) if result.equity else settings.capital, "capital": settings.capital,
            "costs": round(result.costs, 2), "rebalances": len(result.rebalances),
            "turnover_per_rebalance": round(float(np.mean(result.turnover)), 4) if result.turnover else None,
            "avg_holdings": round(float(np.mean(result.holdings)), 2) if result.holdings else 0.0,
            "settings": {"top_n": settings.top_n, "weighting": settings.weighting, "descending": settings.descending,
                         "slippage_pct": settings.slippage_pct, "brokerage_per_order": settings.brokerage, "segment": SEGMENT},
            "fills": "Decided at the rebalance date's close, filled at the next session's open (slippage against the trade), "
                     "whole shares; India's dated charges for equity delivery on every order."}
