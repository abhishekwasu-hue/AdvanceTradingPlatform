"""AI Chart Analyst: deterministic evidence builder + optional LLM explanation layer.

* `build_evidence` turns computed analyses/signals into a typed, timestamped evidence record (single source of truth).
* `deterministic_report` produces the analysis WITHOUT any LLM (always available).
* `LLMClient` is a provider-independent interface. In AMW it is always `src.ai.gateway.GatewayLLM`, which uses the
  requesting user's OWN provider key (BYOK); there is no shared/company key.
* `validate_llm_text` rejects LLM output that cites numbers absent from the evidence (anti-hallucination guard).
The LLM never changes signal status, scores, risk limits or data-quality verdicts.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

from ..analysis.context import TFAnalysis


def build_evidence(symbol: str, analyses: dict[str, TFAnalysis | None], quality: dict, signals: list[dict],
                   regime: dict | None = None) -> dict:
    tfs = {}
    for tf, a in analyses.items():
        q = quality.get(tf)
        if a is None:
            tfs[tf] = {"data_status": getattr(q, "status", "NA"), "issues": getattr(q, "issues", [])}
            continue
        ev = a.events[-3:]
        tfs[tf] = {
            "data_status": getattr(q, "status", "NA"), "last_bar": a.time(), "close": round(a.close, 2),
            "trend": a.trend_label, "trend_score": None if a.trend_score is None else round(a.trend_score, 2),
            "structure_trend": a.structure_trend,
            "recent_swings": [f"{s.label} {s.price:.2f} @ {a.time(s.i)}" for s in a.swings[-4:]],
            "structure_events": [f"{e.type} {e.direction} close beyond {e.level:.2f} @ {a.time(e.i)}" for e in ev],
            "rsi14": None if a.last.get("rsi14") is None else round(a.last["rsi14"], 1),
            "adx": None if a.last.get("adx") is None else round(a.last["adx"], 1),
            "atr14": None if a.last.get("atr14") is None else round(a.last["atr14"], 2),
            "rvol": None if a.last.get("rvol") is None else round(a.last["rvol"], 2),
            "volatility": a.compression,
            "patterns": [f"{p['pattern']} [{p['status']}]" for p in a.patterns],
            "zones": [f"{z['kind']} {z['bottom']:.2f}-{z['top']:.2f} ({z['status']})" for z in a.zones[:4]],
        }
    sigs = [{k: s.get(k) for k in ("strategy", "setup", "timeframe", "direction", "status", "label", "score", "score_coverage",
                                   "signal_time", "entry_zone", "stop", "target", "rr", "conditions_passed", "conditions_failed",
                                   "warnings", "expiry")} for s in signals]
    return {"symbol": symbol, "generated_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "regime": regime or {},
            "timeframes": tfs, "signals": sigs}


def deterministic_report(ev: dict) -> str:
    L = [f"## {ev['symbol']} — evidence-based analysis", f"_generated {ev['generated_utc']} UTC · analysis only, not advice_", ""]
    if ev.get("regime"):
        rg = ev["regime"]
        L.append(f"**Market regime (primary TF):** {rg.get('regime')} · volatility {rg.get('volatility')} — "
                 + "; ".join(rg.get("evidence", [])) + (f" (as of {rg['as_of']})" if rg.get("as_of") else ""))
    L.append("\n### Multi-timeframe structure")
    for tf, d in ev["timeframes"].items():
        if "close" not in d:
            L.append(f"- **{tf}**: data {d['data_status']} — no analysis ({'; '.join(d.get('issues', []))})")
            continue
        L.append(f"- **{tf}** (last completed bar {d['last_bar']}, data {d['data_status']}): close {d['close']}, trend {d['trend']} "
                 f"({d['trend_score']}), structure {d['structure_trend']}, RSI {d['rsi14']}, ADX {d['adx']}, RVOL {d['rvol']}, volatility {d['volatility']}")
        if d["structure_events"]:
            L.append(f"  - events: {'; '.join(d['structure_events'])}")
        if d["patterns"]:
            L.append(f"  - patterns: {', '.join(d['patterns'])}")
    labels = {d.get("trend") for d in ev["timeframes"].values() if d.get("trend") not in (None, "NA")}
    L.append(f"\n**Alignment:** {'aligned (' + labels.pop() + ')' if len(labels) == 1 else 'CONFLICT/mixed: ' + ', '.join(sorted(labels)) if labels else 'NA'}")
    q = [s for s in ev["signals"] if s["status"] in ("qualified", "candidate")]
    r = [s for s in ev["signals"] if s["status"] == "rejected"]
    L.append("\n### Setups")
    if not q:
        L.append("- **No qualifying setup.** No-trade is a valid outcome.")
    for s in sorted(q, key=lambda x: -x["score"]):
        L.append(f"- **{s['setup']}** {s['direction']} {s['timeframe']} — {s['status']}, {s['label']} (score {s['score']}, coverage {s['score_coverage']}%)")
        L.append(f"  - entry zone {s['entry_zone']}, stop {s['stop']}, target {s['target']}, R:R {s['rr'] if s['rr'] is None else round(s['rr'], 2)}; expiry: {s['expiry']}")
        L.append(f"  - confirmed: {s['conditions_passed'] or '-'}")
        if s["conditions_failed"]:
            L.append(f"  - missing/failed (non-mandatory): {s['conditions_failed']}")
        if s["warnings"]:
            L.append(f"  - warnings: {s['warnings']}")
    if r:
        L.append("\n### Rejected setups (why wait)")
        for s in r[:8]:
            L.append(f"- {s['setup']} {s['direction']} {s['timeframe']}: {s['conditions_failed'] or 'data/gate'}")
    L.append("\n_Score = rule-based confluence, not a probability of success._")
    return "\n".join(L)


_NUM = re.compile(r"\d+(?:\.\d+)?")


def validate_llm_text(text: str, evidence: dict, tol: float = 0.011) -> tuple[bool, list[str]]:
    """Every number with >=3 significant digits in the LLM text must exist in the evidence (within tol)."""
    known = [float(x) for x in _NUM.findall(json.dumps(evidence))]
    bad = []
    for m in _NUM.findall(text):
        v = float(m)
        if len(m.replace(".", "").lstrip("0")) < 3:
            continue
        if not any(abs(v - k) <= max(tol, abs(k) * 1e-4) for k in known):
            bad.append(m)
    return (not bad), bad


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str, timeout: float) -> str: ...


SYSTEM = ("You are a chart analyst. Use ONLY the JSON evidence provided. Never invent prices, patterns or statistics. "
          "Do not change any status, score or risk verdict. If evidence is missing, say so. Answer in {lang}.")


@dataclass
class AnalystResult:
    deterministic: str
    llm_text: str | None = None
    llm_status: str = "not requested"
    rejected_numbers: list[str] = field(default_factory=list)
    latency_s: float | None = None


def analyse(evidence: dict, question: str | None = None, client: LLMClient | None = None, lang: str = "English",
            timeout: float = 30, retries: int = 1) -> AnalystResult:
    res = AnalystResult(deterministic_report(evidence))
    if client is None:
        return res
    user = f"EVIDENCE JSON:\n{json.dumps(evidence, ensure_ascii=False)}\n\nQUESTION: {question or 'Explain the setups, conflicts and invalidation.'}"
    t0 = time.time()
    for attempt in range(retries + 1):
        try:
            txt = client.complete(SYSTEM.format(lang=lang), user, timeout)
            ok, bad = validate_llm_text(txt, evidence)
            res.latency_s = round(time.time() - t0, 2)
            if ok:
                res.llm_text, res.llm_status = txt, "ok (numbers verified against evidence)"
            else:
                res.llm_status, res.rejected_numbers = "rejected: cited numbers not in evidence", bad
            return res
        except Exception as e:  # noqa: BLE001
            res.llm_status = f"unavailable: {type(e).__name__}: {str(e)[:120]}"
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    res.latency_s = round(time.time() - t0, 2)
    return res
