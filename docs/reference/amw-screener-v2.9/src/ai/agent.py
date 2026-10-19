"""AI Strategy Intelligence workflow: draft a Strategy Lab strategy from a plain-English description.

Bounded agent loop (all limits from the user's AI policy):
  1 generation call + up to `max_repair_attempts` repair calls, each repair triggered only by a FAILED host validation;
  never more than `max_calls_per_task`; stops immediately on any local budget threshold or provider error;
  recursion depth is checked (`max_agent_depth`). The result is returned for the user to save as a DRAFT.
  Generated code is validated (parse/static checks/sandboxed definition check) but never run on market data and never
  published or scanned automatically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..lab import engine
from ..lab.contract import StrategySpec
from .gateway import AIGateway, AIResult

GUIDE = {
    "pine": ("Write a Pine Script v5 screener using ONLY this subset: //@version=5, indicator(...), single assignments, "
             "input.int/input.float, series open high low close volume hl2 hlc3 ohlc4, history x[n] with integer n, "
             "ta.ema ta.sma ta.rsi ta.atr(n) ta.highest ta.lowest ta.change ta.stdev ta.crossover ta.crossunder, + - * /, "
             "comparisons, and/or/not. It MUST define long_signal = <condition> and/or short_signal = <condition>. "
             "Optional: stop_atr = input.float(x), target_r = input.float(y). No if/for/:=/ternary/strategy.*/request.*."),
    "python": ("Write a Python function `def evaluate(ctx):` (no imports except math/statistics). ctx['series'] has lists "
               "open, high, low, close, volume, ema20, ema50, ema200, sma20, sma50, rsi14, macd, macd_signal, macd_hist, "
               "atr14, adx, plus_di, minus_di, rvol, vwap, bb_up, bb_low; [-1] is the last completed bar; None = missing. "
               "Helpers: amw.last(s, n=0), amw.highest(s, n), amw.lowest(s, n), amw.crossover(a, b), amw.crossunder(a, b), "
               "amw.ema(s, n), amw.sma(s, n), amw.rsi(s, n). Return {'status': 'qualified'|'rejected', 'bias': "
               "'long'|'short'|'neutral', 'rules': [{'name': str, 'passed': True|False|None}], optional 'stop', 'target'}. "
               "Handle None values. No eval/exec/open/getattr/dunder attributes."),
}


@dataclass
class DraftResult:
    ok: bool
    language: str
    source: str | None
    attempts: int
    calls: int
    errors: list[str] = field(default_factory=list)
    message: str = ""
    task_id: str | None = None
    est_cost_usd: float | None = None


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:[a-zA-Z]+)?\n(.*?)```", text or "", re.S)
    return (m.group(1) if m else text or "").strip() + "\n"


def draft_strategy(gw: AIGateway, user_id: int, description: str, language: str = "pine", provider: str | None = None,
                   authorized: bool = False, depth: int = 0, cancel=None) -> DraftResult:
    if language not in GUIDE:
        return DraftResult(False, language, None, 0, 0, [f"AI drafting supports {sorted(GUIDE)}"])
    L = gw.policy(user_id)["limits"]
    expected = 1 + L["max_repair_attempts"]
    b = gw.new_task(user_id, "strategy_draft", expected_calls=min(expected, L["max_calls_per_task"]), authorized=authorized,
                    depth=depth)
    system = ("You generate trading-screener code for the AMW Strategy Lab. Output ONLY one fenced code block. "
              + GUIDE[language])
    prompt = f"Screener description:\n{description.strip()[:2000]}"
    spec = StrategySpec(language=language)
    src, errors, attempts = None, [], 0
    while attempts <= L["max_repair_attempts"]:
        attempts += 1
        res: AIResult = gw.complete(user_id, b, system, prompt, provider, cancel=cancel)
        if not res.ok:
            return DraftResult(False, language, src, attempts, b.calls, errors, res.message or "AI call failed", b.task_id,
                               b.est_cost or None)
        src = _extract_code(res.text)
        v = engine.validate(language, src, None, spec, f"user{user_id}")
        if v["ok"]:
            return DraftResult(True, language, src, attempts, b.calls, [], f"Valid after {attempts} attempt(s) ({v['stage']}). "
                               "Saved only as a draft - review it, then Run and Publish yourself.", b.task_id, b.est_cost or None)
        errors = v["errors"]
        prompt = (f"Screener description:\n{description.strip()[:2000]}\n\nYour previous code:\n```\n{src[:6000]}```\n"
                  f"It failed validation with these errors:\n- " + "\n- ".join(e[:300] for e in errors[:8]) +
                  "\nReturn a corrected version.")
    return DraftResult(False, language, src, attempts, b.calls, errors,
                       f"Stopped after {attempts} attempt(s) (repair limit {L['max_repair_attempts']}); code still invalid.",
                       b.task_id, b.est_cost or None)
