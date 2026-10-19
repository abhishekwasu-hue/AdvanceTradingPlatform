"""Point-in-time scanner REPLAY and same-data VERSION COMPARISON.

Replay(as_of): for each symbol the validated history is cut at `as_of` (exchange time) and the bar that would still
have been forming at `as_of` is dropped, then the pinned scanner version is evaluated exactly as a live scan would.
Higher-timeframe frames are cut the same way. The DECISION uses only bars <= as_of. Subsequent outcomes (did a
qualified candidate reach stop/target?) are computed separately and labelled RETROSPECTIVE.

Limitation (documented): the history comes from the current provider; if the provider later revised bars or applied
corporate-action adjustments, the replay sees the revised values (no point-in-time vendor snapshots are stored).

Version comparison: both versions are evaluated on the SAME symbols, timeframe, data and window with the SAME
backtest engine and cost model; the report lists counts, qualification rate, trade metrics, runtime and data
failures, and never declares a winner (in-sample results on one window are not independent validation).
"""
from __future__ import annotations

import time

import pandas as pd

from ..analysis.context import analyze_tf
from ..data.validation import clean_and_validate, is_forming, usable
from ..lab import engine
from ..lab.contract import StrategySpec, build_context, resolve_params
from ..lab.service import lab_backtest, published
from .spec import outcome

MAX_REPLAY_SYMBOLS = 200
MAX_COMPARE_SYMBOLS = 10


def _as_of(ts, tz: str) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize(tz) if t.tzinfo is None else t.tz_convert(tz)


def point_in_time(clean: pd.DataFrame, tf: str, as_of: pd.Timestamp, market: dict) -> pd.DataFrame:
    """Bars that were COMPLETED at `as_of` (no later bar, no bar still forming at that moment)."""
    if clean is None or not len(clean):
        return clean
    cut = clean[clean.index <= as_of]
    if len(cut) and is_forming(cut.index[-1], tf, as_of, market):
        cut = cut.iloc[:-1]
    return cut


def replay(svc, user_id: int, sid: str, as_of, tickers: list[str], version: int | None = None,
           retrospective: bool = True) -> dict:
    if len(tickers) > MAX_REPLAY_SYMBOLS:
        raise ValueError(f"replay is limited to {MAX_REPLAY_SYMBOLS} symbols")
    st, row = published(svc.store, user_id, sid, version)
    spec = StrategySpec.model_validate(row["spec"] or {"language": row["language"]})
    cfg = svc.cfg(user_id)
    tz = cfg["market"]["timezone"]
    t_as = _as_of(as_of, tz)
    prov = svc.provider_for(user_id)
    prm = resolve_params(spec, None)
    tfs = list(dict.fromkeys(list(spec.timeframes) + list(spec.htf)))
    raw = {tf: prov.fetch_many(tickers, tf) for tf in tfs}
    rows, ctxs, keys, full = [], [], [], {}
    for tk in tickers:
        frames, reps = {}, {}
        for tf in tfs:
            clean, rep = clean_and_validate(raw[tf].get(tk), tk, tf, cfg, data_kind=getattr(prov, "data_kind", "unknown"))
            full[(tk, tf)] = clean
            pit = point_in_time(clean, tf, t_as, cfg["market"]) if usable(rep) else None
            # quality is re-assessed AS OF the replay moment (staleness relative to as_of, not to today)
            if pit is not None and len(pit):
                pit, rep = clean_and_validate(pit, tk, tf, cfg, now=t_as, data_kind=getattr(prov, "data_kind", "unknown"))
            frames[tf], reps[tf] = pit, rep
        for tf in spec.timeframes:
            f, rep = frames.get(tf), reps.get(tf)
            reason = ""
            if f is None or not len(f) or not usable(rep):
                reason = f"data {rep.status if rep else 'NA'} at as_of"
            elif spec.data_quality == "OK_only" and rep.status != "OK":
                reason = f"data {rep.status} at as_of (scanner requires OK)"
            elif len(f) < spec.min_bars:
                reason = f"INSUFFICIENT_DATA: {len(f)} bars < {spec.min_bars} at as_of"
            base = {"ticker": tk, "timeframe": tf, "as_of": str(t_as), "data_status": rep.status if rep else "NA",
                    "provider": getattr(prov, "name", "unknown"), "version": row["version"]}
            if reason:
                rows.append({**base, "status": "not_evaluated", "reason": reason, "conditions": []})
                continue
            t = analyze_tf(f, tf, cfg)
            htf = {h: (analyze_tf(frames[h], h, cfg) if frames.get(h) is not None and len(frames[h]) >= 30 else None)
                   for h in spec.htf}
            meta = {"symbol": tk, "ticker": tk, "exchange": "NSE", "timeframe": tf, "timezone": tz,
                    "provider": base["provider"], "data_kind": rep.data_kind, "data_status": rep.status}
            ctxs.append(build_context(t, prm, meta, htf, spec.lookback))
            keys.append(base)
    t0 = time.perf_counter()
    results = engine.execute(row["language"], row["source"], row["ir"], spec, ctxs, f"user{user_id}") if ctxs else []
    for base, c, r in zip(keys, ctxs, results):
        if not r.ok:
            rows.append({**base, "status": "error", "reason": "; ".join(r.errors)[:300], "conditions": []})
            continue
        res = r.result
        rows.append({**base, "status": res["status"], "bias": res.get("bias"), "signal_bar": c["meta"]["last_completed_bar"],
                     "stop": res.get("stop"), "target": res.get("target"),
                     "conditions": [{"name": x["name"], "outcome": outcome(x.get("passed")), "detail": x.get("detail", "")}
                                    for x in res.get("rules", [])]})
    retro = []
    if retrospective:
        from ..services.forward import DEFAULT_MAX_BARS, evaluate
        for x in rows:
            if x["status"] == "qualified" and x.get("stop") is not None and x.get("target") is not None:
                ev = evaluate({"signal_time": x["signal_bar"], "direction": "bullish" if x["bias"] == "long" else "bearish",
                               "stop": x["stop"], "target": x["target"], "max_bars": DEFAULT_MAX_BARS.get(x["timeframe"], 20)},
                              full[(x["ticker"], x["timeframe"])])
                retro.append({"ticker": x["ticker"], "timeframe": x["timeframe"], **(ev or {"status": "NO_LATER_BARS"})})
    return {"scanner_id": sid, "scanner": st["name"], "version": row["version"], "as_of": str(t_as),
            "evaluated": len(ctxs), "qualified": sum(x["status"] == "qualified" for x in rows),
            "results": rows, "retrospective": retro, "evaluation_seconds": round(time.perf_counter() - t0, 3),
            "method": "history cut at as_of, forming bar dropped, quality re-assessed at as_of; retrospective outcomes "
                      "use later bars and are NOT part of the decision",
            "limitations": ["provider history may include later revisions/adjustments (no point-in-time vendor snapshot)"]}


def compare_versions(svc, user_id: int, sid: str, va: int, vb: int, tickers: list[str], tf: str = "1D") -> dict:
    if len(tickers) > MAX_COMPARE_SYMBOLS:
        raise ValueError(f"version comparison is limited to {MAX_COMPARE_SYMBOLS} symbols")
    if va == vb:
        raise ValueError("choose two different versions")
    cfg = svc.cfg(user_id)
    prov = svc.provider_for(user_id)
    raw = prov.fetch_many(tickers, tf)
    data, dq_fail = {}, []
    for tk in tickers:
        clean, rep = clean_and_validate(raw.get(tk), tk, tf, cfg, data_kind=getattr(prov, "data_kind", "unknown"))
        if usable(rep) and len(clean) > 260:
            data[tk] = clean
        else:
            dq_fail.append({"ticker": tk, "status": rep.status, "bars": len(clean)})
    out = {}
    for v in (va, vb):
        published(svc.store, user_id, sid, v)                     # raises if missing / foreign
        trades, t0, errors = [], time.perf_counter(), 0
        for tk, clean in data.items():
            r = lab_backtest(svc, user_id, sid, v, clean, tf)
            errors += r.get("evaluation_errors", 0)
            if len(r["trades"]):
                trades.append(r["trades"].assign(ticker=tk))
        tdf = pd.concat(trades) if trades else pd.DataFrame(columns=["r_multiple", "ret_pct"])
        n = len(tdf)
        wins = int((tdf.r_multiple > 0).sum()) if n else 0
        gross_win = float(tdf.r_multiple[tdf.r_multiple > 0].sum()) if n else 0.0
        gross_loss = float(-tdf.r_multiple[tdf.r_multiple < 0].sum()) if n else 0.0
        out[f"v{v}"] = {"trades": n, "win_rate": round(wins / n, 3) if n else None,
                        "expectancy_r": round(float(tdf.r_multiple.mean()), 3) if n else None,
                        "profit_factor_r": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
                        "evaluation_errors": errors, "runtime_s": round(time.perf_counter() - t0, 2),
                        "evidence": "INSUFFICIENT EVIDENCE" if n < 30 else "SUFFICIENT SAMPLE (still in-sample)"}
    return {"scanner_id": sid, "timeframe": tf, "symbols": list(data), "data_quality_failures": dq_fail, "versions": out,
            "methodology": "same symbols, timeframe, validated data and window; same backtest engine, fills and cost model; "
                           "signals from bars <= j only; metrics in R multiples after costs",
            "verdict": "No winner is declared: one in-sample window is not independent validation. Fewer signals or a "
                       "higher win rate alone does not make a version better."}
