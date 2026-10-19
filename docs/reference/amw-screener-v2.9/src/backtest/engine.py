"""Correctly-sequenced backtest for ONE strategy on ONE symbol/timeframe.

Sequencing (no look-ahead):
  * At bar j the strategy sees only bars [j-window+1 .. j] (re-analysed from scratch; swings need k right bars,
    so they appear only after confirmation). Only signals whose signal bar == j (fresh) can trigger a trade.
  * Entry at the NEXT bar's open, adjusted by per-side costs (brokerage + exchange/taxes + slippage).
  * If that open is already beyond the stop (gap through stop) the trade is SKIPPED and counted.
  * Exits: open beyond stop -> filled at the open (gap stop); open beyond target -> filled at the open;
    if stop and target are both inside one bar's range the STOP is assumed first (conservative);
    otherwise time exit at the close of bar entry+max_hold_bars-1.
  * One position at a time. Position sizing for the equity curve = fixed fraction risk_per_trade_pct of
    normalised equity (start = 1.0); no account balance is assumed.
Limitations: survivorship bias (current constituents), unadjusted Yahoo prices (corporate actions), delayed data,
weekly context for swing is resampled from the daily window (current week partial). Results are historical only.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..analysis.context import analyze_tf
from ..data.validation import QualityReport
from ..risk import cost_per_side
from ..strategies.base import StrategyContext
from ..strategies.registry import REGISTRY


@dataclass
class Trade:
    signal_time: str
    entry_time: str
    exit_time: str
    setup: str
    direction: str
    entry: float
    stop: float
    target: float | None
    exit: float
    r_multiple: float
    ret_pct: float
    bars: int
    reason: str


def _weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Weekly bars from the daily bars available at the evaluation bar. The last week is DROPPED while it is still
    forming (its Friday label is after the last daily bar), exactly as the live scanner excludes a forming weekly
    candle; otherwise the backtest would treat a partial week as a completed bar (AUDIT A-001)."""
    w = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
    if len(w) and len(df) and w.index[-1].date() > df.index[-1].date():
        w = w.iloc[:-1]
    return w


def _daily_completed(sub: pd.DataFrame, bar_tf: str, market: dict) -> pd.DataFrame:
    """Daily bars built from intraday bars available at the evaluation bar; the current session is dropped until its
    last bar has closed (same rule as the live scanner's forming-candle exclusion)."""
    from ..data.validation import TF_DELTA, is_forming
    if not len(sub):
        return sub.iloc[0:0]
    d = sub.groupby(sub.index.normalize()).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last",
                                                "Volume": "sum"}).dropna(subset=["Close"])
    end_of_bar = sub.index[-1] + TF_DELTA[bar_tf]
    if len(d) and is_forming(d.index[-1], "1D", end_of_bar, market):
        d = d.iloc[:-1]
    return d


def _intraday_completed(sub: pd.DataFrame, bar_tf: str, htf: str, market: dict) -> pd.DataFrame:
    """Higher intraday bars (15m/30m/1H) aggregated from the evaluation timeframe, aligned to the 09:15 session open;
    the last bucket is dropped while it is still forming at the end of the evaluation bar."""
    from ..data.validation import TF_DELTA, is_forming
    from ..feed.bars import resample
    if not len(sub):
        return sub.iloc[0:0]
    rule = {"15m": "15min", "30m": "30min", "1H": "60min"}[htf]
    h = resample(sub, rule)
    end_of_bar = sub.index[-1] + TF_DELTA[bar_tf]
    if len(h) and is_forming(h.index[-1], htf, end_of_bar, market):
        h = h.iloc[:-1]
    return h


def _htf_frames(sub: pd.DataFrame, tf: str, market: dict) -> dict[str, pd.DataFrame]:
    """Point-in-time higher-timeframe frames for the evaluation bar (AUDIT A-007): only COMPLETED higher bars, for
    every higher timeframe the strategies look at (base.HIGHER): 1D -> 1W; 1H -> 1D; 30m/15m -> 1H, 1D; 5m -> 15m, 1H, 1D."""
    from ..strategies.base import HIGHER
    out: dict[str, pd.DataFrame] = {}
    for htf in HIGHER.get(tf, []):
        if htf == "1W":
            out[htf] = _weekly(sub)
        elif htf == "1D":
            out[htf] = _daily_completed(sub, tf, market)
        else:
            out[htf] = _intraday_completed(sub, tf, htf, market)
    return out


def run_backtest(clean: pd.DataFrame, tf: str, cfg: dict, strategy: str, bench: pd.DataFrame | None = None,
                 start: str | None = None, end: str | None = None, window: int = 250, warmup: int = 210,
                 step: int = 1, signal_source=None) -> dict:
    """`signal_source(j) -> list[obj]` (objects with direction/stop/target/setup/score) replaces the built-in strategy;
    it MUST only use bars <= j (the Strategy Lab passes truncated windows). Trade simulation is identical for all sources."""
    cfg = copy.deepcopy(cfg)
    if signal_source is None:
        cls = REGISTRY[strategy]
        cfg["strategies"][strategy]["timeframes"] = [tf]
        strat = cls(cfg)
        version = strat.version
    else:
        strat, version = None, getattr(signal_source, "version", None)
    cost = cost_per_side(cfg["costs"])
    O, H, L, C = (clean[c].to_numpy() for c in ("Open", "High", "Low", "Close"))
    idx = clean.index
    n = len(clean)
    t0 = pd.Timestamp(start, tz=idx.tz) if start else None
    t1 = pd.Timestamp(end, tz=idx.tz) if end else None
    max_hold = int(cfg["backtest"]["max_hold_bars"])
    trades: list[Trade] = []
    skipped_gap = 0
    htf_cache: dict = {}
    hist_window = max(window, 1500)          # higher-TF context needs a longer history than the signal window

    def htf_src(jj):
        return clean.iloc[max(0, jj - hist_window + 1): jj + 1]
    j = warmup
    while j < n - 1:
        if (t0 is not None and idx[j] < t0) or (t1 is not None and idx[j] > t1):
            j += step
            continue
        sub = clean.iloc[max(0, j - window + 1): j + 1]
        if signal_source is not None:
            try:
                sigs = [x for x in signal_source(j) if x.stop is not None]
            except Exception:  # noqa: BLE001
                sigs = []
        else:
          try:
            a = analyze_tf(sub, tf, cfg)
            analyses = {tf: a}
            for htf, hdf in _htf_frames(htf_src(j), tf, cfg["market"]).items():
                key = (htf, str(hdf.index[-1]) if len(hdf) else None, len(hdf))
                if key not in htf_cache:                     # re-analyse only when a new higher bar completed
                    htf_cache.clear() if len(htf_cache) > 8 else None
                    htf_cache[key] = analyze_tf(hdf, htf, cfg) if len(hdf) >= 30 else None
                analyses[htf] = htf_cache[key]
            reps = {k: QualityReport("BT", k, "OK") for k in analyses}
            b = {tf: bench[bench.index <= idx[j]]} if bench is not None else {}
            ctx = StrategyContext("BT", "BT", analyses, reps, b, cfg)
            sigs = [s for s in strat.evaluate(ctx) if s.status == "qualified" and s.stop is not None
                    and s.signal_time == a.time()]
          except Exception:  # noqa: BLE001
            sigs = []
        if not sigs:
            j += step
            continue
        s = max(sigs, key=lambda x: x.score)
        long = s.direction == "bullish"
        e = j + 1
        stop, target = float(s.stop), (float(s.target) if s.target is not None else None)
        raw_entry = O[e]
        if (long and raw_entry <= stop) or (not long and raw_entry >= stop):
            skipped_gap += 1
            j += 1
            continue
        entry = raw_entry * (1 + cost) if long else raw_entry * (1 - cost)
        risk = abs(entry - stop)
        exit_px, reason, k = None, "time", e
        for k in range(e, min(n, e + max_hold)):
            if k > e:   # gap handling at the open of later bars
                if (long and O[k] <= stop) or (not long and O[k] >= stop):
                    exit_px, reason = O[k], "gap stop"
                    break
                if target is not None and ((long and O[k] >= target) or (not long and O[k] <= target)):
                    exit_px, reason = O[k], "gap target"
                    break
            hit_stop = L[k] <= stop if long else H[k] >= stop
            hit_tgt = target is not None and (H[k] >= target if long else L[k] <= target)
            if hit_stop:
                exit_px, reason = stop, "stop" + (" (both touched: stop assumed first)" if hit_tgt else "")
                break
            if hit_tgt:
                exit_px, reason = target, "target"
                break
        if exit_px is None:
            exit_px = C[k]
        exit_net = exit_px * (1 - cost) if long else exit_px * (1 + cost)
        pnl = (exit_net - entry) if long else (entry - exit_net)
        trades.append(Trade(str(idx[j])[:16], str(idx[e])[:16], str(idx[k])[:16], s.setup, s.direction, round(entry, 2),
                            round(stop, 2), None if target is None else round(target, 2), round(exit_net, 2),
                            round(pnl / risk, 3) if risk > 0 else 0.0, round(pnl / entry * 100, 3), k - e + 1, reason))
        j = k + 1
    tdf = pd.DataFrame([asdict(t) for t in trades])
    risk_frac = cfg["risk"]["risk_per_trade_pct"] / 100
    res: dict = {"trades": tdf, "skipped_gap_entries": skipped_gap, "assumptions": __doc__, "strategy": strategy,
                 "version": version, "timeframe": tf, "bars": n}
    res["equity"] = equity_curve(tdf, risk_frac)
    split = idx[min(n - 1, int(n * cfg["backtest"]["train_fraction"]))]
    res["split_time"] = str(split)[:16]
    st = pd.to_datetime(tdf.signal_time) if len(tdf) else pd.Series(dtype="datetime64[ns]")
    sp = split.tz_localize(None)
    res["all"] = metrics(tdf, n, risk_frac)
    res["in_sample"] = metrics(tdf[st < sp] if len(tdf) else tdf, n, risk_frac)
    res["out_of_sample"] = metrics(tdf[st >= sp] if len(tdf) else tdf, n, risk_frac)
    folds = int(cfg["backtest"]["walk_forward_folds"])
    edges = [idx[min(n - 1, warmup + (n - warmup) * f // folds)] for f in range(folds + 1)]
    wf = []
    for f in range(folds):
        a_, b_ = edges[f].tz_localize(None), edges[f + 1].tz_localize(None)
        part = tdf[(st >= a_) & (st < b_)] if len(tdf) else tdf
        wf.append({"fold": f + 1, "from": str(a_)[:10], "to": str(b_)[:10], **metrics(part, n, risk_frac)})
    res["walk_forward"] = pd.DataFrame(wf)
    s0 = C[warmup] if n > warmup else np.nan
    res["benchmark"] = {"symbol_buy_hold_pct": round(float((C[-1] / s0 - 1) * 100), 2) if np.isfinite(s0) else None}
    if bench is not None and len(bench):
        bb = bench[(bench.index >= idx[min(warmup, n - 1)]) & (bench.index <= idx[-1])]
        res["benchmark"]["index_buy_hold_pct"] = round(float((bb.Close.iloc[-1] / bb.Close.iloc[0] - 1) * 100), 2) if len(bb) > 1 else None
    return res


def equity_curve(tdf: pd.DataFrame, risk_frac: float) -> pd.DataFrame:
    if tdf is None or tdf.empty:
        return pd.DataFrame(columns=["time", "equity", "drawdown_pct"])
    eq = np.cumprod(1 + risk_frac * tdf.r_multiple.to_numpy())
    dd = eq / np.maximum.accumulate(eq) - 1
    return pd.DataFrame({"time": tdf.exit_time, "equity": eq, "drawdown_pct": dd * 100})


MIN_TRADES_FOR_EVIDENCE = 30


def metrics(tdf: pd.DataFrame, n_bars: int, risk_frac: float) -> dict:
    """Metrics computed from actual trades. With fewer than MIN_TRADES_FOR_EVIDENCE trades the result is labelled
    INSUFFICIENT EVIDENCE: the numbers are still shown (they are what happened) but must not be read as a validated edge."""
    if tdf is None or tdf.empty:
        return {"trades": 0, "evidence": "INSUFFICIENT EVIDENCE (no trades)", "note": "no trades"}
    r = tdf.r_multiple.to_numpy()
    wins, losses = r[r > 0], r[r <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    eq = equity_curve(tdf, risk_frac)
    ev = "OK" if len(r) >= MIN_TRADES_FOR_EVIDENCE else f"INSUFFICIENT EVIDENCE ({len(r)} trades < {MIN_TRADES_FOR_EVIDENCE})"
    return {"trades": int(len(r)), "evidence": ev, "win_rate_pct": round(100 * len(wins) / len(r), 1),
            "avg_r": round(float(r.mean()), 3),
            "expectancy_r": round(float((wins.mean() if len(wins) else 0) * len(wins) / len(r) + (losses.mean() if len(losses) else 0) * len(losses) / len(r)), 3),
            "profit_factor": round(float(pf), 2) if np.isfinite(pf) else "inf (no losses)",
            "avg_trade_ret_pct": round(float(tdf.ret_pct.mean()), 3),
            "equity_return_pct": round(float((eq.equity.iloc[-1] - 1) * 100), 2),
            "max_drawdown_pct": round(float(eq.drawdown_pct.min()), 2),
            "exposure_pct": round(float(100 * tdf.bars.sum() / n_bars), 1)}


def sensitivity(clean: pd.DataFrame, tf: str, cfg: dict, strategy: str, param: str, values: list, **kw) -> pd.DataFrame:
    rows = []
    for v in values:
        c = copy.deepcopy(cfg)
        c["strategies"][strategy]["params"][param] = v
        r = run_backtest(clean, tf, c, strategy, **kw)
        rows.append({param: v, **r["all"]})
    return pd.DataFrame(rows)
