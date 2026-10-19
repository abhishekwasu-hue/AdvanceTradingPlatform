"""U6 BK5 (spec §6, SCREENER_SPEC §2.6): breakout screen fitness - what happened after each historical signal of a
template, as forward-return statistics and the failed-break rate, by grade, by timeframe and by volatility regime.

Informational only: it describes past matches of a screen on shared market data; it never ranks, recommends or scores a
trade, and nothing here places, changes or blocks an order. A bucket with fewer than `fitness_min_samples` signals is
marked `low_sample` (the UI greys it).

Per signal (from quality.run_breakout over the whole history, within=None):
  forward return  the close FITNESS_HORIZONS bars later against the signal bar's close, in % and signed for the side
                  (a breakdown that falls is positive); a horizon past the last bar is not counted (no partial horizons)
  r_multiple      that move over the stop distance (close - stop reference of the overlay), the same sign rule
  failed          the overlay's failed-break status (a close back inside the fail level within failed_break_bars)
  grade, regime   the overlay's grade (A / B / C) and volatility regime (low / normal / high / unknown) AT the signal bar
Aggregates per bucket: signals, failed rate (of the signals whose failed-break window had closed), and per horizon:
samples, mean, median, the share above zero (hit rate) and the mean R. Deterministic: same bars + parameters, same stats.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.screener.breakouts import params as bp
from app.screener.breakouts.quality import BreakoutRun, run_breakout

FITNESS_HORIZONS: Tuple[int, ...] = (1, 5, 20)          # SCREENER_SPEC §2.6: rolling forward returns of passers (1 / 5 / 20 bars)
DISCLAIMER = ("Screen fitness describes what followed this screen's past matches on shared market data. It is not a "
              "recommendation and says nothing about any future match.")


def outcomes(run: BreakoutRun, frame: pd.DataFrame, horizons: Iterable[int] = FITNESS_HORIZONS) -> List[Dict[str, Any]]:
    """One row per signal of `run` (its signal-timeframe bars `frame`): the forward returns that are complete."""
    close = frame["close"].to_numpy(float)
    last = len(close) - 1
    rows: List[Dict[str, Any]] = []
    for s in run.signals:
        j, sign = int(s["bar"]), (1.0 if s["side"] == "up" else -1.0)
        c0 = close[j]
        risk = (c0 - float(s["stop_ref"])) * sign if s.get("stop_ref") is not None else float("nan")
        fwd: Dict[int, Optional[float]] = {}
        r: Dict[int, Optional[float]] = {}
        for h in horizons:
            if j + h > last or not np.isfinite(close[j + h]) or not c0:
                fwd[h] = r[h] = None
                continue
            move = (close[j + h] - c0) * sign
            fwd[h] = move / abs(c0) * 100.0
            r[h] = move / risk if np.isfinite(risk) and risk > 0 else None
        decided = s["status"] in ("failed", "accepted", "unconfirmed")      # pending: its failed-break window is still open
        rows.append({"side": s["side"], "bar": j, "grade": s["grade"], "regime": (s.get("checks") or {}).get("regime") or "unknown",
                     "timeframe": run.signal_tf, "failed": s["status"] == "failed" if decided else None, "forward_pct": fwd,
                     "forward_r": r})
    return rows


def _bucket(rows: List[Dict[str, Any]], horizons: Tuple[int, ...], min_samples: int) -> Dict[str, Any]:
    decided = [x["failed"] for x in rows if x["failed"] is not None]
    out: Dict[str, Any] = {"signals": len(rows), "failed_rate": round(sum(decided) / len(decided), 4) if decided else None,
                           "failed_samples": len(decided), "low_sample": len(rows) < min_samples, "horizons": {}}
    for h in horizons:
        vals = np.array([x["forward_pct"][h] for x in rows if x["forward_pct"].get(h) is not None], dtype=float)
        rs = np.array([x["forward_r"][h] for x in rows if x["forward_r"].get(h) is not None], dtype=float)
        out["horizons"][str(h)] = {
            "samples": int(len(vals)), "low_sample": int(len(vals)) < min_samples,
            "mean_pct": round(float(vals.mean()), 4) if len(vals) else None,
            "median_pct": round(float(np.median(vals)), 4) if len(vals) else None,
            "hit_rate": round(float((vals > 0).mean()), 4) if len(vals) else None,
            "mean_r": round(float(rs.mean()), 4) if len(rs) else None,
        }
    return out


def aggregate(rows: List[Dict[str, Any]], horizons: Tuple[int, ...] = FITNESS_HORIZONS, min_samples: Optional[int] = None) -> Dict[str, Any]:
    """{overall, by_grade, by_regime, by_timeframe, by_side}: each a bucket (see the module notes)."""
    m = int(bp.DEFAULTS["fitness_min_samples"] if min_samples is None else min_samples)

    def by(key: str, order: Tuple[str, ...]) -> Dict[str, Any]:
        keys = [k for k in order if any(x[key] == k for x in rows)] + sorted({str(x[key]) for x in rows} - set(order))
        return {k: _bucket([x for x in rows if str(x[key]) == k], horizons, m) for k in keys}
    return {"overall": _bucket(rows, horizons, m), "by_grade": by("grade", ("A", "B", "C")),
            "by_regime": by("regime", ("low", "normal", "high", "unknown")), "by_timeframe": by("timeframe", ()),
            "by_side": by("side", ("up", "down")), "min_samples": m, "horizons": list(horizons), "disclaimer": DISCLAIMER}


def template_fitness(screen: str, signal_tf: str, symbol_frames: Dict[str, Dict[str, pd.DataFrame]], params: Optional[Dict[str, Any]] = None,
                     horizons: Tuple[int, ...] = FITNESS_HORIZONS) -> Dict[str, Any]:
    """Fitness of one screen (a template's screen, timeframe and parameters) over many symbols' closed bars
    ({symbol: {timeframe: frame}}, the signal timeframe at least). A symbol whose run raises (too little history) is
    counted in `skipped`, never silently dropped."""
    p = bp.settings(**(params or {}))
    rows: List[Dict[str, Any]] = []
    skipped: Dict[str, str] = {}
    for symbol, frames in symbol_frames.items():
        try:
            run = run_breakout(screen, frames, signal_tf, params=params, within=None)
        except ValueError as exc:
            skipped[symbol] = str(exc)
            continue
        rows += [{**x, "symbol": symbol} for x in outcomes(run, frames[signal_tf], horizons)]
    out = aggregate(rows, horizons, int(p["fitness_min_samples"]))
    out.update(screen=screen, signal_tf=signal_tf, symbols=len(symbol_frames) - len(skipped), skipped=skipped)
    return out


__all__ = ["FITNESS_HORIZONS", "DISCLAIMER", "outcomes", "aggregate", "template_fitness"]
