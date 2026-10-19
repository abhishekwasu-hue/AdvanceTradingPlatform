"""Strategy Lab service: integrates lab strategies with the existing scanner, signal store, alerts and backtester.

Lab score = % of the strategy's own rules that passed (0-100). It is a rule-coverage measure, NOT a probability.
Job statuses: QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED, PARTIALLY_COMPLETED.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import pandas as pd

from ..analysis.context import analyze_tf
from ..backtest.engine import run_backtest
from ..data.validation import STALE, usable
from ..screener.scanner import run_scan
from ..strategies.base import Condition, Signal, Strategy, StrategyContext, label_for
from . import engine
from .contract import StrategySpec, build_context, resolve_params

MAX_JOBS_PER_USER = int(os.environ.get("AMW_LAB_MAX_JOBS_PER_USER", "2"))


class LabStrategy(Strategy):
    """Adapter: a published (immutable) lab version behaves like any built-in strategy in scans."""

    def __init__(self, cfg: dict, version_row: dict, strategy_name: str, params: dict | None = None,
                 user_key: str = "local", cancel=None):
        self.cfg = cfg
        self.row = version_row
        self.spec = StrategySpec.model_validate(version_row["spec"] or {"language": version_row["language"]})
        self.name = f"lab:{version_row['strategy_id']}@v{version_row['version']}"
        self.title = strategy_name
        self.version = int(version_row["version"])
        self.params = resolve_params(self.spec, params)
        self.weights = {"structure": 1.0, "trend_mtf": 0, "volume": 0, "level_quality": 0, "momentum": 0,
                        "relative_strength": 0, "risk_reward": 0}
        self.user_key, self.cancel = user_key, cancel
        self.p = {}
        self.runtime = ""
        self.errors: list[str] = []

    @property
    def timeframes(self) -> list[str]:
        return list(dict.fromkeys(list(self.spec.timeframes) + list(self.spec.htf)))

    def contexts(self, ctx: StrategyContext) -> list[tuple[str, dict | None, str]]:
        out = []
        for tf in self.spec.timeframes:
            t = ctx.get(tf)
            rep = ctx.quality.get(tf)
            reason = ""
            if t is None or rep is None or not usable(rep):
                reason = f"data {rep.status if rep else 'NA'}"
            elif self.spec.data_quality == "OK_only" and rep.status == STALE:
                reason = "data STALE (strategy requires OK)"
            elif t.n < self.spec.min_bars:
                reason = f"INSUFFICIENT_DATA: {t.n} bars < {self.spec.min_bars}"
            if reason:
                out.append((tf, None, reason))
                continue
            meta = {"symbol": ctx.symbol, "ticker": ctx.ticker, "exchange": "NSE", "timeframe": tf,
                    "timezone": self.cfg["market"]["timezone"], "provider": "validated host data", "data_kind": rep.data_kind,
                    "data_status": rep.status}
            htf = {h: ctx.get(h) for h in self.spec.htf}
            out.append((tf, build_context(t, self.params, meta, htf, self.spec.lookback), ""))
        return out

    def evaluate(self, ctx: StrategyContext) -> list[Signal]:
        items = self.contexts(ctx)
        good = [(tf, c) for tf, c, _ in items if c is not None]
        results = engine.execute(self.row["language"], self.row["source"], self.row["ir"], self.spec, [c for _, c in good],
                                 self.user_key, self.cancel) if good else []
        res_by_tf = {tf: r for (tf, _), r in zip(good, results)}
        sigs = []
        for tf, c, reason in items:
            t = ctx.get(tf)
            sig = Signal(symbol=ctx.symbol, ticker=ctx.ticker, strategy=self.name, strategy_version=self.version,
                         setup=f"Lab: {self.title}", timeframe=tf, direction="bullish",
                         signal_time=t.time() if t is not None else "", data_time=t.time() if t is not None else "",
                         data_status=ctx.quality[tf].status if tf in ctx.quality else "NA")
            if c is None:
                sig.conditions.append(Condition("data quality / history", False, True, reason))
                sig.status, sig.label = "rejected", "Rejected (data)"
                sigs.append(sig)
                continue
            r = res_by_tf[tf]
            self.runtime = r.runtime
            if not r.ok:
                sig.conditions.append(Condition("valid strategy output", False, True, "; ".join(r.errors)[:400]))
                sig.status, sig.label = "rejected", "Rejected (error)"
                self.errors += r.errors
                sigs.append(sig)
                continue
            out = r.result
            sig.direction = "bearish" if out["bias"] == "short" else "bullish"
            for rule in out["rules"]:
                sig.conditions.append(Condition(rule["name"], rule["passed"], False, rule.get("detail", "")))
            sig.conditions.append(Condition("strategy qualified", out["status"] == "qualified" and out["bias"] != "neutral", True,
                                            out.get("explanation", "")))
            sig.stop, sig.target, sig.invalidation = out.get("stop"), out.get("target"), out.get("invalidation")
            sig.stop_method, sig.target_method = "strategy-defined", "strategy-defined"
            sig.entry_condition = out.get("entry") or ""
            close = t.close
            if sig.stop is not None and sig.target is not None:
                risk = abs(close - sig.stop)
                sig.rr = abs(sig.target - close) / risk if risk > 0 else None
            rules = [x for x in out["rules"]]
            ratio = (sum(1 for x in rules if x["passed"] is True) / len(rules)) if rules else (1.0 if out["status"] == "qualified" else 0.0)
            sig.components = {"structure": (ratio, f"{sum(1 for x in rules if x['passed'] is True)}/{len(rules)} rules passed")}
            sig.warnings += r.warnings + [f"runtime {r.runtime}"]
            sig.expiry = "re-evaluated on the next completed bar"
            sigs.append(self.finalize(sig, ctx))
        return sigs

    def finalize(self, sig: Signal, ctx: StrategyContext) -> Signal:
        sig = super().finalize(sig, ctx)
        if sig.status == "qualified":
            sig.label = label_for(sig.score, self.cfg)
        return sig


def published(store, user_id: int, sid: str, version: int | None = None) -> tuple[dict, dict]:
    st = store.lab_get(user_id, sid)
    if st is None:
        raise KeyError("strategy not found")
    vs = store.lab_versions(user_id, sid)
    if not vs:
        raise ValueError("publish a version first (drafts are never executed by scans/backtests)")
    row = vs[0] if version is None else next((v for v in vs if v["version"] == version), None)
    if row is None:
        raise KeyError(f"version {version} not found")
    return st, row


def start_lab_scan(svc, user_id: int, sid: str, version: int | None, universe_df: pd.DataFrame, universe_name: str,
                   params: dict | None = None):
    store = svc.store
    st, row = published(store, user_id, sid, version)
    from ..saas.entitlements import check
    check(store, user_id, "symbols_per_scan", len(universe_df))
    check(store, user_id, "concurrent_jobs", len(svc.jobs.active(user_id)) + 1)
    if store.lab_active_runs(user_id) >= MAX_JOBS_PER_USER:
        raise PermissionError(f"usage limit: {MAX_JOBS_PER_USER} concurrent lab jobs per user")
    dup = [r for r in store.lab_runs(user_id, sid, 20) if r["status"] in ("QUEUED", "RUNNING") and r["kind"] == "scan"
           and r["version"] == row["version"] and (r["config"] or {}).get("universe") == universe_name]
    if dup:
        raise PermissionError(f"duplicate job suppressed: run #{dup[0]['id']} is already running for this strategy/universe")
    cfg = svc.cfg(user_id)
    run_id = store.lab_run_start(user_id, strategy_id=sid, version=row["version"], kind="scan", language=row["language"],
                                 config={"universe": universe_name, "symbols": len(universe_df), "params": params or {},
                                         "spec": row["spec"], "completed_bars_only": True, "provider": svc.provider(cfg).name})

    def job_fn(progress, should_stop, job):
        strat = LabStrategy(cfg, row, st["name"], params, f"user{user_id}", job.cancel_event)
        t0 = time.perf_counter()
        try:
            alerts_on = bool(((row["spec"] or {}).get("scanner") or {}).get("alerts", {}).get("enabled"))

            def on_batch(rows, latest):
                live = [r for r in rows if r["status"] in ("qualified", "candidate")]
                store.record_signals(live, user_id)
                from ..services.forward import enroll
                enroll(store, live, user_id)                 # paper forward test, same as built-in scans
                if alerts_on:                                 # scanner envelope: alerts enabled -> user's alert rules
                    from ..services.alerts import process_alerts
                    process_alerts(store, live, user_id)
            res = run_scan(universe_df, cfg, svc.provider_for(user_id), [strat], progress=progress, should_stop=should_stop,
                           universe_name=universe_name, on_batch=on_batch)
        except Exception as e:  # noqa: BLE001
            store.lab_run_finish(user_id, run_id, "FAILED", error=f"{type(e).__name__}: {e}", runtime=strat.runtime)
            raise
        sig = pd.DataFrame(res.signals)
        q = pd.DataFrame(res.quality)
        stats = {"symbols_done": res.symbols_done, "total": res.total,
                 "matches": int((sig.status == "qualified").sum()) if len(sig) else 0,
                 "rejections": int((sig.status == "rejected").sum()) if len(sig) else 0,
                 "errors": len(strat.errors), "data_issues": int((q.status != "OK").sum()) if len(q) else 0,
                 "elapsed_s": round(time.perf_counter() - t0, 2)}
        if job.cancel_event.is_set() or res.stopped_early:
            status = "CANCELLED" if res.symbols_done == 0 else "PARTIALLY_COMPLETED"
        elif strat.errors and stats["matches"] + stats["rejections"] > len(strat.errors):
            status = "PARTIALLY_COMPLETED"
        elif strat.errors:
            status = "FAILED"
        else:
            status = "COMPLETED"
        store.lab_run_finish(user_id, run_id, status, stats, "; ".join(strat.errors[:5]) or None, runtime=strat.runtime)
        job.lab_status = status
        return res

    job = svc.jobs.submit("lab_scan", job_fn, owner=user_id)
    job.lab_run_id = run_id
    return job, run_id


@dataclass
class _Sig:
    direction: str
    stop: float | None
    target: float | None
    setup: str
    score: float


def lab_backtest(svc, user_id: int, sid: str, version: int | None, clean: pd.DataFrame, tf: str,
                 bench: pd.DataFrame | None = None, params: dict | None = None, warmup: int = 210, cancel=None, **kw) -> dict:
    """Shared backtest: contexts for every bar j are built from bars <= j only, evaluated in ONE sandboxed batch,
    then simulated by the same engine/cost model as built-in strategies."""
    store = svc.store
    st, row = published(store, user_id, sid, version)
    spec = StrategySpec.model_validate(row["spec"] or {"language": row["language"]})
    if row["language"] == "sql":
        compat = "per-bar screening only (no stateful trade logic in SQL)"
    elif row["language"] == "pine":
        compat = "Pine subset translated to IR"
    else:
        compat = "full"
    cfg = svc.cfg(user_id)
    prm = resolve_params(spec, params)
    run_id = store.lab_run_start(user_id, strategy_id=sid, version=row["version"], kind="backtest", language=row["language"],
                                 config={"tf": tf, "bars": len(clean), "params": prm, "costs": cfg["costs"]})
    n = len(clean)
    idx, ctxs = [], []
    for j in range(warmup, n - 1):
        sub = clean.iloc[max(0, j - max(spec.lookback, 250) + 1): j + 1]
        t = analyze_tf(sub, tf, cfg)
        meta = {"symbol": "BT", "ticker": "BT", "exchange": "NSE", "timeframe": tf, "timezone": cfg["market"]["timezone"],
                "provider": "validated host data", "data_kind": "historical", "data_status": "OK"}
        ctxs.append(build_context(t, prm, meta, {}, spec.lookback))
        idx.append(j)
    t0 = time.perf_counter()
    results = engine.execute(row["language"], row["source"], row["ir"], spec, ctxs, f"user{user_id}", cancel) if ctxs else []
    errs = [e for r in results if not r.ok for e in r.errors]
    by_j: dict[int, list] = {}
    for j, r, c in zip(idx, results, ctxs):
        if r.ok and r.result["status"] == "qualified" and r.result["bias"] in ("long", "short") and r.result.get("stop") is not None:
            # signal timestamp is the host bar time of j (runtime cannot shift it)
            assert r.signal_time == c["meta"]["last_completed_bar"]
            by_j[j] = [_Sig("bullish" if r.result["bias"] == "long" else "bearish", r.result["stop"], r.result.get("target"),
                            f"Lab: {st['name']}", 100.0)]

    def source(j):
        return by_j.get(j, [])
    source.version = row["version"]
    res = run_backtest(clean, tf, cfg, "lab", bench=bench, warmup=warmup, signal_source=source, **kw)
    res.update({"strategy": f"lab:{sid}", "language": row["language"], "compatibility": compat,
                "runtime": results[0].runtime if results else "", "evaluation_errors": len(errs),
                "evaluation_seconds": round(time.perf_counter() - t0, 2), "params": prm})
    status = "COMPLETED" if not errs else ("PARTIALLY_COMPLETED" if len(errs) < len(ctxs) else "FAILED")
    store.lab_run_finish(user_id, run_id, status, {"trades": res["all"].get("trades", 0), "contexts": len(ctxs), "errors": len(errs),
                                                   "evaluation_seconds": res["evaluation_seconds"]},
                         "; ".join(dict.fromkeys(errs))[:1000] or None, runtime=res["runtime"])
    res["run_status"] = status
    return res


def run_on_symbols(svc, user_id: int, language: str, source: str | None, ir: dict | None, spec: StrategySpec,
                   tickers: list[str], params: dict | None = None, cancel=None) -> list[dict]:
    """Preview / 'Run' for a DRAFT on a few symbols with validated data (not stored as signals)."""
    from ..screener.scanner import analyse_symbol
    cfg = svc.cfg(user_id)
    prov = svc.provider(cfg)
    prm = resolve_params(spec, params)
    tfs = list(dict.fromkeys(list(spec.timeframes) + list(spec.htf)))
    rows, ctxs, keys = [], [], []
    for tk in tickers:
        an, reps = analyse_symbol({tf: prov.fetch_many([tk], tf).get(tk) for tf in tfs}, tk, tfs, cfg, data_kind=prov.data_kind)
        for tf in spec.timeframes:
            t, rep = an.get(tf), reps[tf]
            if t is None or (spec.data_quality == "OK_only" and rep.status != "OK") or t.n < spec.min_bars:
                rows.append({"ticker": tk, "timeframe": tf, "status": "INSUFFICIENT_DATA" if t is None or t.n < spec.min_bars else f"DATA_{rep.status}",
                             "bias": None, "signal_time": rep.last, "errors": "; ".join(rep.issues), "rules": None})
                continue
            meta = {"symbol": tk.replace(".NS", ""), "ticker": tk, "exchange": "NSE", "timeframe": tf, "timezone": cfg["market"]["timezone"],
                    "provider": prov.name, "data_kind": prov.data_kind, "data_status": rep.status}
            ctxs.append(build_context(t, prm, meta, {h: an.get(h) for h in spec.htf}, spec.lookback))
            keys.append((tk, tf))
    res = engine.execute(language, source, ir, spec, ctxs, f"user{user_id}", cancel) if ctxs else []
    for (tk, tf), r in zip(keys, res):
        rows.append({"ticker": tk, "timeframe": tf, "status": (r.result["status"] if r.ok else "ERROR"),
                     "bias": r.result["bias"] if r.ok else None, "signal_time": r.signal_time,
                     "stop": r.result.get("stop") if r.ok else None, "target": r.result.get("target") if r.ok else None,
                     "rules": " | ".join(f"{'✓' if x['passed'] else '✗' if x['passed'] is False else '?'} {x['name']}" for x in r.result["rules"]) if r.ok else None,
                     "explanation": r.result.get("explanation") if r.ok else None, "errors": "; ".join(r.errors + r.warnings),
                     "logs": r.logs, "runtime": r.runtime, "elapsed_s": r.elapsed_s})
    return rows
