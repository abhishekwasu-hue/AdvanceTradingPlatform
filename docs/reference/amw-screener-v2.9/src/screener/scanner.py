"""Batch scanner: benchmark + symbols -> validate -> analyse -> strategies -> signals, with per-symbol isolation,
cooperative cancellation and resume. Keeps compact summaries only (full analysis is recomputed on demand from cache).
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from ..analysis.context import TFAnalysis, analyze_tf
from ..data.provider import DataProvider, batched
from ..data.validation import QualityReport, clean_and_validate, usable
from ..strategies.base import StrategyContext
from ..strategies.registry import evaluate_all, required_timeframes

log = logging.getLogger(__name__)
FATAL_PROVIDER_ERRORS = {"token_expired", "rate_limit_guard"}   # provider error codes that abort a scan


@dataclass
class ScanResult:
    universe: str = ""
    symbols: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    matrix: list[dict] = field(default_factory=list)
    quality: list[dict] = field(default_factory=list)
    benchmark: dict = field(default_factory=dict)
    timeframes: list[str] = field(default_factory=list)
    strategies: list[str] = field(default_factory=list)
    started: str = ""
    finished: str = ""
    stopped_early: bool = False
    total: int = 0
    done_tickers: list[str] = field(default_factory=list)

    @property
    def symbols_done(self) -> int:
        return len(self.done_tickers)


SIGNAL_SCHEMA = "amw.signal.v1"


def data_snapshot_hash(df: pd.DataFrame | None, bars: int = 300) -> str | None:
    """Deterministic fingerprint of the OHLCV window the signal was computed from (detects later data revisions)."""
    if df is None or len(df) == 0:
        return None
    tail = df[["Open", "High", "Low", "Close", "Volume"]].iloc[-bars:]
    payload = "|".join(f"{ts.isoformat()},{o:.6f},{h:.6f},{lo:.6f},{c:.6f},{'' if pd.isna(v) else int(v)}"
                       for ts, (o, h, lo, c, v) in zip(tail.index, tail.to_numpy()))
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _snapshot_cached(analysis) -> str | None:
    """One fingerprint per analysed frame (several signals share the same symbol/timeframe frame)."""
    if analysis is None:
        return None
    cached = getattr(analysis, "_amw_snapshot", None)
    if cached is None:
        cached = data_snapshot_hash(analysis.df)
        try:
            analysis._amw_snapshot = cached
        except AttributeError:
            pass
    return cached


def stamp_canonical(row: dict, analysis, provider, scanner: str, evaluated_at: str) -> dict:
    """Add provenance so dashboard, reports, charts and Telegram all reference the same immutable record."""
    row["schema"] = SIGNAL_SCHEMA
    row["scanner"] = scanner
    src = provider.source_for(row.get("ticker"), row.get("timeframe")) if hasattr(provider, "source_for") else None
    row["provider"] = src or getattr(provider, "name", "unknown")       # per-symbol attribution under failover
    row["data_kind"] = getattr(provider, "data_kind", "unknown")
    row["data_snapshot"] = _snapshot_cached(analysis)
    row["evaluated_at"] = evaluated_at
    return row


def analyse_symbol(raw_by_tf: dict, ticker: str, tfs: list[str], cfg: dict, now=None, data_kind: str = "delayed/historical"):
    analyses: dict[str, TFAnalysis | None] = {}
    reps: dict[str, QualityReport] = {}
    for tf in tfs:
        try:
            clean, rep = clean_and_validate(raw_by_tf.get(tf), ticker, tf, cfg, now=now, data_kind=data_kind)
            reps[tf] = rep
            analyses[tf] = analyze_tf(clean, tf, cfg) if usable(rep) else None
        except Exception as e:  # noqa: BLE001
            log.exception("analysis failed %s %s", ticker, tf)
            reps[tf] = QualityReport(ticker, tf, "ERROR", issues=[f"{type(e).__name__}: {e}"])
            analyses[tf] = None
    return analyses, reps


def load_benchmark(provider: DataProvider, cfg: dict, tfs: list[str], now=None) -> tuple[dict, dict]:
    bt = cfg.get("benchmark")
    frames, info = {}, {}
    if not bt:
        return frames, info
    for tf in tfs:
        try:
            raw = provider.fetch_many([bt], tf).get(bt)
            clean, rep = clean_and_validate(raw, bt, tf, cfg, now=now, data_kind=provider.data_kind)
            frames[tf] = clean if usable(rep) else None
            info[tf] = rep.status
        except Exception as e:  # noqa: BLE001
            frames[tf], info[tf] = None, f"ERROR {type(e).__name__}"
    return frames, info


def summarise_symbol(ticker: str, meta: dict, analyses: dict, reps: dict, tfs: list[str], sigs: list[dict]) -> tuple[dict, dict]:
    d = analyses.get("1D") or next((a for a in analyses.values() if a is not None), None)
    q = [s for s in sigs if s["status"] in ("qualified", "candidate")]
    best = max(q, key=lambda s: s["score"]) if q else None
    row = {"ticker": ticker, "symbol": meta.get("symbol", ticker), "company": meta.get("company", ""), "industry": meta.get("industry", ""),
           "data_time": d.time() if d else None, "close": d.last.get("Close") if d else None,
           "rsi14": d.last.get("rsi14") if d else None, "rvol": d.last.get("rvol") if d else None,
           "atr_pct": d.last.get("atr_pct") if d else None, "adx": d.last.get("adx") if d else None,
           "above_ema50": (d.last["Close"] > d.last["ema50"]) if d and d.last.get("ema50") else None,
           "above_ema200": (d.last["Close"] > d.last["ema200"]) if d and d.last.get("ema200") else None,
           "chg_1bar_pct": float((d.df.Close.iloc[-1] / d.df.Close.iloc[-2] - 1) * 100) if d and d.n > 1 else None,
           "ret_20_pct": float((d.df.Close.iloc[-1] / d.df.Close.iloc[-21] - 1) * 100) if d and d.n > 21 else None,
           "avg_traded_value": float((d.df.Close * d.df.Volume).iloc[-20:].mean()) if d and d.df.Volume.iloc[-20:].notna().all() else None,
           "qualified": sum(s["status"] == "qualified" for s in sigs), "candidates": sum(s["status"] == "candidate" for s in sigs),
           "best_strategy": best["strategy"] if best else None, "best_setup": best["setup"] if best else None,
           "best_direction": best["direction"] if best else None, "best_score": best["score"] if best else None,
           "best_label": best["label"] if best else None,
           "data_status": ",".join(f"{tf}:{reps[tf].status}" for tf in tfs if tf in reps),
           "data_ok": all(reps[tf].status == "OK" for tf in tfs if tf in reps)}
    m = {"ticker": ticker}
    labels = []
    for tf in tfs:
        a = analyses.get(tf)
        m[f"{tf}_trend"] = a.trend_label if a else "NA"
        m[f"{tf}_structure"] = (a.structure_trend or "none") if a else "NA"
        m[f"{tf}_signals"] = ", ".join(f"{s['strategy']}:{s['direction'][:4]}({s['status'][:4]})" for s in q if s["timeframe"] == tf) or "-"
        m[f"{tf}_data"] = reps[tf].status if tf in reps else "NA"
        if a:
            labels.append(a.trend_label)
    m["alignment"] = ("Bullish aligned" if labels and all(x == "Bullish" for x in labels) else
                      "Bearish aligned" if labels and all(x == "Bearish" for x in labels) else "Mixed/partial")
    return row, m


def run_scan(universe_df: pd.DataFrame, cfg: dict, provider: DataProvider, strategies: list,
             progress: Callable[[float, str], None] | None = None, should_stop: Callable[[], bool] | None = None,
             sleeper: Callable[[float], None] = time.sleep, resume: ScanResult | None = None, now=None,
             universe_name: str = "", on_batch: Callable[[list[dict], dict], None] | None = None) -> ScanResult:
    tfs = required_timeframes(strategies)
    res = resume or ScanResult(universe=universe_name, started=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S UTC"))
    res.stopped_early = False
    res.timeframes, res.strategies = tfs, [s.name for s in strategies]
    metas = {r.ticker: r._asdict() for r in universe_df.itertuples(index=False)}
    res.total = len(metas)
    todo = [t for t in metas if t not in set(res.done_tickers)]
    bench_frames, res.benchmark = load_benchmark(provider, cfg, tfs, now)
    bs = int(cfg["data"]["batch_size"])
    for batch in batched(todo, bs):
        if should_stop and should_stop():
            res.stopped_early = True
            break
        raw: dict[str, dict] = {t: {} for t in batch}
        for tf in tfs:
            try:
                got = provider.fetch_many(batch, tf)
            except Exception as e:  # noqa: BLE001
                if getattr(e, "code", None) in FATAL_PROVIDER_ERRORS:
                    # e.g. the user's broker session expired mid-scan: stop with an explicit error instead of
                    # silently producing "no data" for every remaining symbol
                    raise RuntimeError(f"data source stopped the scan: {e}") from e
                log.error("fetch failed %s: %s", tf, e)
                got = {}
            for t in batch:
                raw[t][tf] = got.get(t)
        new_rows: list[dict] = []
        latest: dict = {}
        for t in batch:
            analyses, reps = analyse_symbol(raw[t], t, tfs, cfg, now=now, data_kind=provider.data_kind)
            for tf, a in analyses.items():
                if a is not None:
                    latest[(t, tf)] = {"close": a.close, "high": float(a.df.High.iloc[-1]), "low": float(a.df.Low.iloc[-1]),
                                       "times": [str(x)[:16] for x in a.df.index[-300:]]}
            res.quality += [r.as_row() for r in reps.values()]
            if not any(analyses.values()):
                continue
            try:
                ctx = StrategyContext(metas[t].get("symbol", t), t, analyses, reps, bench_frames, cfg)
                rows = [s.to_row() for s in evaluate_all(ctx, strategies)]
                evaluated = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                for r in rows:
                    r["company"], r["industry"] = metas[t].get("company", ""), metas[t].get("industry", "")
                    stamp_canonical(r, analyses.get(r["timeframe"]), provider, universe_name, evaluated)
                res.signals += rows
                new_rows += rows
                srow, mrow = summarise_symbol(t, metas[t], analyses, reps, tfs, rows)
                res.symbols.append(srow)
                res.matrix.append(mrow)
            except Exception as e:  # noqa: BLE001
                log.exception("strategy evaluation failed %s", t)
                res.quality.append({"ticker": t, "timeframe": "ALL", "status": "ERROR", "issues": f"{type(e).__name__}: {e}"})
        res.done_tickers += batch
        if on_batch:
            on_batch(new_rows, latest)
        if progress:
            progress(res.symbols_done / max(1, res.total), f"{res.symbols_done}/{res.total} symbols")
        if res.symbols_done < res.total:
            sleeper(float(cfg["data"]["pause_between_batches"]))
    res.finished = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S UTC")
    return res


def signals_frame(res: ScanResult | None, include_rejected: bool = False) -> pd.DataFrame:
    if res is None or not res.signals:
        return pd.DataFrame()
    df = pd.DataFrame(res.signals)
    if not include_rejected:
        df = df[df.status.isin(["qualified", "candidate"])]
    return df.sort_values(["score", "score_coverage"], ascending=False).reset_index(drop=True)


def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    """Pure, testable filtering of the signals table. NaN fails an ACTIVE numeric filter."""
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)
    if f.get("strategies"):
        m &= df.strategy.isin(f["strategies"])
    if f.get("timeframes"):
        m &= df.timeframe.isin(f["timeframes"])
    if f.get("direction") in ("bullish", "bearish"):
        m &= df.direction == f["direction"]
    if f.get("status"):
        m &= df.status.isin(f["status"])
    if f.get("min_score") is not None:
        m &= df.score >= f["min_score"]
    if f.get("min_rr"):
        m &= df.rr >= f["min_rr"]
    if f.get("labels"):
        m &= df.label.isin(f["labels"])
    if f.get("search"):
        q = f["search"].strip().upper()
        m &= df.symbol.str.upper().str.contains(q, regex=False) | df.get("company", pd.Series("", index=df.index)).fillna("").str.upper().str.contains(q, regex=False)
    if f.get("data_ok_only"):
        m &= df.data_status == "OK"
    return df[m.fillna(False)].reset_index(drop=True)


def market_overview(res: ScanResult | None, bench: TFAnalysis | None) -> dict:
    out: dict = {"regime": "uncertain", "regime_reason": "benchmark data NA"}
    if bench is not None and bench.trend_score is not None:
        adx = bench.last.get("adx")
        ts = bench.trend_score
        if adx is not None and adx < 20:
            out["regime"], why = "sideways", f"ADX {adx:.1f} < 20"
        elif ts >= 0.5:
            out["regime"], why = "bullish", f"trend score {ts:+.2f}"
        elif ts <= -0.5:
            out["regime"], why = "bearish", f"trend score {ts:+.2f}"
        else:
            out["regime"], why = "uncertain", f"mixed trend score {ts:+.2f}"
        out["regime_reason"] = why + (f", ADX {adx:.1f}" if adx is not None else "")
        out["benchmark_close"], out["benchmark_time"] = bench.close, bench.time()
    if res and res.symbols:
        s = pd.DataFrame(res.symbols)
        out["breadth_n"] = int(s.above_ema50.notna().sum())
        out["pct_above_ema50"] = float(s.above_ema50.dropna().astype(bool).mean() * 100) if s.above_ema50.notna().any() else None
        out["pct_above_ema200"] = float(s.above_ema200.dropna().astype(bool).mean() * 100) if s.above_ema200.notna().any() else None
        ch = s.chg_1bar_pct.dropna()
        out["advancers"], out["decliners"] = int((ch > 0).sum()), int((ch < 0).sum())
        sec = s[s.industry.fillna("") != ""]
        out["sectors"] = (sec.groupby("industry").agg(stocks=("ticker", "count"), avg_ret_20_pct=("ret_20_pct", "mean"))
                          .sort_values("avg_ret_20_pct", ascending=False).reset_index() if len(sec) else pd.DataFrame())
    return out
