"""M72 PR-6 (CP-5): plain English -> an editable draft the trader checks, runs or approves himself.

The planner turns "NIFTY 50 stocks pulling back to a Daily demand area" into tool calls; the chat shows the result as
ONE editable draft card per thing it made - never a saved screen, a run or an alert that exists without his tap:

* screen - from a successful `build_screen` call: the ScreenQL text, its timeframe, its $parameters (a value only when
  the trader stated it, else EMPTY for him to fill) and the validator's problems. The card checks and runs it in place
  through the Screener's own endpoints (`/screener/validate`, `/screener/run`) only when he taps, on the symbols HE
  enters (none are pre-filled).
* alert - from a `propose_alert` call that created a PROPOSED action: the card says it waits for his approval in the
  Watchtower (ADR-0006: the alert exists only after a person approves it; an alert notifies, never orders).
Unstated values stay empty (M68): every number in a screen's text and parameters must come from the trader's message or
a tool result of this request (`number_problem`, enforced before the tool runs); otherwise the model is told to write a
$parameter without a value or to ask. Strategy and backtest drafts stay on the chat's own rule path (job 6, the review
card and "Test it on past data"); custom indicators have no planner tool yet. Nothing here places or changes an order.
"""
from __future__ import annotations

import re
from typing import Any

from app.ai import grounding

DRAFT_TOOLS = ("build_screen", "run_screen")    # every number in their ScreenQL must be the trader's (or a tool's)
MAX_DRAFTS = 2
_STRING = re.compile(r'"[^"]*"|\'[^\']*\'')
_NUMBER = re.compile(r"(?<![A-Za-z_$\d.])\d+(?:\.\d+)?")
_PARAM = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def numbers_in_screen(text: str) -> list[float]:
    """The number literals of a ScreenQL text (not inside quotes, not part of a name or a $parameter)."""
    body = _STRING.sub(" ", text or "")
    body = re.sub(r"[A-Za-z_$][A-Za-z0-9_]*", lambda m: " " * len(m.group(0)), body)
    return [float(m.group(0)) for m in _NUMBER.finditer(body)]


def params_in_screen(text: str) -> list[str]:
    return list(dict.fromkeys(_PARAM.findall(_STRING.sub(" ", text or ""))))


def number_problem(name: str, arguments: dict[str, Any], allowed: set) -> str | None:
    """Why a draft tool call is refused (None = it may run): a number the trader never stated and no tool returned."""
    if name not in DRAFT_TOOLS:
        return None
    values = numbers_in_screen(str((arguments or {}).get("text") or ""))
    values += [float(v) for v in ((arguments or {}).get("params") or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    for number in values:
        if number == 0:                     # a zero line ("change > 0") is not a chosen value (as the calc tools)
            continue
        if not grounding.numbers_in_values([number]) & allowed:
            return (f"{number:g} is a value the user did not state; write it as a $parameter without a value (the draft "
                    "asks him to fill it) or ask him - never pick one")
    return None


def _screen(call: dict[str, Any], data: dict[str, Any]) -> dict[str, Any] | None:
    args = call.get("arguments") or {}
    text = str(data.get("text") or args.get("text") or "").strip()
    if not text:
        return None
    given = dict(args.get("params") or {})
    names = params_in_screen(text)
    problems = [str(p.get("message") or "") for p in data.get("problems") or [] if isinstance(p, dict) and p.get("kind") != "parameter"]
    return {"kind": "screen", "text": text, "base_tf": str(data.get("base_tf") or args.get("base_tf") or "1d"),
            "params": [{"name": n, "value": given.get(n)} for n in names],
            "stages": [str(s.get("title") or s.get("name") or "") for s in data.get("stages") or [] if isinstance(s, dict)][:6],
            "problems": [p for p in problems if p][:4], "ok": bool(data.get("ok"))}


def build_drafts(calls: list[dict[str, Any]], results: dict[str, Any]) -> list[dict[str, Any]]:
    """The editable drafts of this answer, from the successful calls' own results (newest first, at most MAX_DRAFTS)."""
    out: list[dict[str, Any]] = []
    for call in reversed(calls):
        if not call.get("ok") or not isinstance(results.get(call["id"]), dict):
            continue
        data = results[call["id"]]
        if call["name"] == "build_screen":
            draft = _screen(call, data)
        elif call["name"] == "propose_alert" and data.get("created"):
            draft = {"kind": "alert", "status": "proposed", "proposal_id": data.get("proposal_id")}
        else:
            draft = None
        if draft is not None and not any(d["kind"] == draft["kind"] for d in out):
            out.append(draft)
        if len(out) >= MAX_DRAFTS:
            break
    return out


__all__ = ["DRAFT_TOOLS", "numbers_in_screen", "params_in_screen", "number_problem", "build_drafts"]
