"""Liquidity map: the pools resting above and below price (buy-side: swing highs, equal highs, the previous day's / week's
high; sell-side: the mirror) and the sweeps of them - known at a bar's close.

Ported from Trade@1e6c9ac, decision3/liquidity.py: `pools`, the sweep half of `sweeps_and_traps` and `pool_marks`.
Logic unchanged; Trade reads its decision3 level engine (`V.levels`: D2 pivots, sigma_1H, bars); here the same
attributes come from a small `View` the caller fills, so any confirmed pivots and any noise scale can be used. The
trapped-trader zones of `sweeps_and_traps` are not ported (they wait for the N1 level-gate port). Parity with Trade's
own output on the same inputs: tests/test_ch3b_trade_parity.py.

  * Pools known at t: confirmed swings (buy above for highs, sell for lows), equal pairs (two swings of a side within
    eq_sigma x sigma: their midpoint), PDH / PDL (the previous session's high / low) and PWH / PWL (the previous week's,
    weeks ending Friday).
  * Sweep: price trades beyond a pool (a wick, or fewer than `accept_closes` closes in a row) and closes back inside -
    no acceptance. A pool still beyond at the last bar is not a sweep (yet).
  * Marks (chart): per side the pools nearest the price within show_distance_sigma x sigma, one per price (within
    eq_sigma x sigma), PWH / PWL first, then PDH / PDL, equal, swing.
Evidence for the chart only: no gate, no order.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd

DEFAULTS: Dict[str, Any] = {"liquidity_eq_sigma": 0.15, "accept_closes": 3, "show_distance_sigma": 12.0, "liq_marks_per_side": 3}
SRC_EN = {"swing": "swing", "equal": "EQ", "PDH": "PDH", "PDL": "PDL", "PWH": "PWH", "PWL": "PWL"}


@dataclass
class KnownPivot:
    kind: str           # "H" / "L"
    price: float
    known_at: int       # the bar whose close confirmed it


@dataclass
class View:
    """What the functions read (Trade: V.levels.* and V.s). `ts`: bar times, session-local and naive (days and weeks are
    calendar days there); `high` / `low` / `close`: per bar; `sigma`: the noise scale per bar; `pivots`: confirmed pivots."""
    ts: pd.Series
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    sigma: np.ndarray
    pivots: Sequence[KnownPivot]
    s: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))

    def known_pivots(self, t: int) -> List[KnownPivot]:
        """Pivots known at t's close, in confirmation order."""
        return sorted((p for p in self.pivots if p.known_at <= t), key=lambda p: p.known_at)


def pools(V: View, t: int) -> List[Dict[str, Any]]:
    """The pools known at t: [{price, side: buy|sell, src}]."""
    out: List[Dict[str, Any]] = []
    for p in V.known_pivots(t):
        out.append({"price": float(p.price), "side": "buy" if p.kind == "H" else "sell", "src": "swing"})
    eq = float(V.s["liquidity_eq_sigma"]) * (V.sigma[t] if np.isfinite(V.sigma[t]) else 0.0)
    for side in ("buy", "sell"):
        xs = sorted(x["price"] for x in out if x["side"] == side and x["src"] == "swing")
        for a, b in zip(xs[:-1], xs[1:]):
            if b - a <= eq:
                out.append({"price": (a + b) / 2, "side": side, "src": "equal"})
    ts = V.ts
    day = ts[t].normalize()
    prev = ts[ts.dt.normalize() < day]
    if len(prev):
        pdx = prev.dt.normalize() == prev.iloc[-1].normalize()
        idx = prev[pdx].index
        out.append({"price": float(V.high[idx].max()), "side": "buy", "src": "PDH"})
        out.append({"price": float(V.low[idx].min()), "side": "sell", "src": "PDL"})
        wk = ts.dt.to_period("W-FRI")
        pw = ts[(wk < wk[t])]
        if len(pw):
            wi = pw[wk[pw.index] == wk[pw.index[-1]]].index
            out.append({"price": float(V.high[wi].max()), "side": "buy", "src": "PWH"})
            out.append({"price": float(V.low[wi].min()), "side": "sell", "src": "PWL"})
    return out


def sweeps(V: View, t: int, lookback: int) -> List[Dict[str, Any]]:
    """The sweeps in the last `lookback` bars (of the pools known at t - lookback): [{price, side, src, bar, extreme}]."""
    acc = int(V.s["accept_closes"])
    t0 = max(1, t - lookback)
    ps = pools(V, t0)
    sw: List[Dict[str, Any]] = []
    for p in ps:
        pr = p["price"]
        sign = 1 if p["side"] == "buy" else -1
        beyond_c = [(V.close[i] - pr) * sign > 0 for i in range(t0, t + 1)]
        beyond_w = [((V.high[i] if sign > 0 else V.low[i]) - pr) * sign > 0 for i in range(t0, t + 1)]
        if not any(beyond_w):
            continue
        first = beyond_w.index(True)
        seq = beyond_c[first:]
        runs, cur = [], 0
        for x in seq:
            cur = cur + 1 if x else 0
            runs.append(cur)
        if max(runs) >= acc or seq[-1]:
            continue                                                     # accepted beyond / still beyond -> not a sweep
        ext = max(V.high[t0:t + 1]) if sign > 0 else min(V.low[t0:t + 1])
        sw.append({**p, "bar": t0 + first, "extreme": float(ext)})
    return sw


def pool_marks(V: View, t: int, per_side: Any = None, max_sigma: Any = None) -> List[Dict[str, Any]]:
    """The pools known at t nearest the price for the chart: per side `liq_marks_per_side`, within show_distance_sigma x
    sigma, duplicates (within eq_sigma x sigma) once - PWH / PWL, then PDH / PDL, then equal, then swing. [{price, side,
    src, label}]."""
    per_side = int(per_side or V.s["liq_marks_per_side"])
    sg = V.sigma[t] if np.isfinite(V.sigma[t]) else 0.0
    lim = float(max_sigma or V.s["show_distance_sigma"]) * sg if sg > 0 else np.inf
    tol = float(V.s["liquidity_eq_sigma"]) * sg
    c = float(V.close[t])
    prio = {"PWH": 0, "PWL": 0, "PDH": 1, "PDL": 1, "equal": 2, "swing": 3}
    out: List[Dict[str, Any]] = []
    for side in ("buy", "sell"):
        ps = [p for p in pools(V, t) if p["side"] == side and abs(p["price"] - c) <= lim
              and ((p["price"] >= c) if side == "buy" else (p["price"] <= c))]
        ps.sort(key=lambda p: (prio.get(p["src"], 9), abs(p["price"] - c)))
        kept: List[Dict[str, Any]] = []
        for p in ps:
            if all(abs(p["price"] - q["price"]) > tol for q in kept):
                kept.append(p)
        kept.sort(key=lambda p: abs(p["price"] - c))
        for p in kept[:per_side]:
            out.append({**p, "label": f"{SRC_EN.get(p['src'], p['src'])} {'BSL' if side == 'buy' else 'SSL'}"})
    return out
