"""N3 S9 (spec §3 natural-language box): plain language -> a ScreenQL screen the trader confirms and can edit. Flag
`screener_copilot` (off by default) on top of `screener_v2` and the organisation's `ai_copilot`.

* The model is given the registry as it is for this organisation (flagged functions whose flag is off are left out)
  and answers with one JSON object `{text, explanation, assumptions}`. Our validator and flag gates check the text;
  with problems the model gets one repair turn with them; still invalid -> the problems are returned, nothing guessed.
* Nothing runs and nothing is stored: the answer is a draft the trader reads, edits and runs from the Screener.
* The explanation passes the H-C1 c output filter (no advice or guarantee words; a specific call is framed).
* No provider configured (the rule-based one): a plain refusal saying so - the builder and the query editor still work.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Set

from app.ai.prompt_versions import version_of

FLAG = "screener_copilot"
MAX_REGISTRY_ENTRIES = 260

SYSTEM = """You turn a trader's plain-language screen request into ONE ScreenQL screen.

ScreenQL: comparisons (> < >= <= == !=), AND / OR / NOT, parentheses, numbers, "strings", fields and functions from the
list below only. x[1] is the value one bar earlier; x@1w reads another timeframe. A screen is a condition (true/false).
Examples: close > SMA(close, 50) AND RSI(14) > 55   |   PctChange(close, 20) > 10 AND volume > SMA(volume, 20) * 1.5

Fields and functions (name(args): what it returns):
{registry}

Rules:
1. Use ONLY the names above, with their arguments in order. If the request needs something not in the list, leave it
   out and say so in "assumptions".
2. Choose sensible defaults for anything the trader did not say (windows, thresholds) and list them in "assumptions".
3. A screen finds symbols that pass filters. Never recommend buying or selling, never promise returns.
4. Answer with one JSON object and nothing else:
   {"text": "<the ScreenQL screen>", "explanation": "<one or two plain sentences: what it looks for>",
    "assumptions": ["<each default you chose or thing you left out>"]}
Answer in {lang}."""

PROMPT_VERSION = version_of("screener_nl", SYSTEM)
DISCLAIMER = "A draft screen from your words: check it, edit it, then run it. Matches are filters passed, not recommendations."


def registry_text(hidden_flags: Sequence[str] = ()) -> str:
    """The registry the model may use: one line per entry (name(args): returns, doc), flagged-off entries left out."""
    from app.screener.registry import FIELDS, FUNCTIONS
    lines: List[str] = []
    for spec in (*FIELDS.values(), *FUNCTIONS.values()):
        if spec.flag and spec.flag in hidden_flags:
            continue
        args = ", ".join(a.name if a.required else f"{a.name}={a.default!r}" for a in spec.args)
        sig = spec.name if spec.name in FIELDS else f"{spec.name}({args}{', ...' if spec.varargs else ''})"
        lines.append(f"- {sig}: {spec.returns}{f' ({spec.unit})' if spec.unit else ''} - {spec.doc[:140]}")
    return "\n".join(lines[:MAX_REGISTRY_ENTRIES])


def system_prompt(lang: str, hidden_flags: Sequence[str] = ()) -> str:
    return SYSTEM.replace("{registry}", registry_text(hidden_flags)).replace("{lang}", "Marathi" if lang == "mr" else "English")


def parse(raw: str) -> Dict[str, Any]:
    """The model's JSON answer -> {text, explanation, assumptions}. Raises ValueError when there is none."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.S)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(body[start:end + 1])
    if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not data["text"].strip():
        raise ValueError("the answer has no screen text")
    assumptions = data.get("assumptions") or []
    return {"text": data["text"].strip()[:2000], "explanation": str(data.get("explanation") or "").strip()[:600],
            "assumptions": [str(a).strip()[:200] for a in assumptions if str(a).strip()][:10] if isinstance(assumptions, list) else []}


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def _vocabulary() -> Set[str]:
    """The words a problem may show as they are: the registry's own names, argument names and fixed choices, and the
    ScreenQL keywords - never the model's."""
    from app.screener.parser import KEYWORDS
    from app.screener.registry import FIELDS, FUNCTIONS
    words: Set[str] = {k.lower() for k in KEYWORDS} | {"all", "any"}
    for spec in (*FIELDS.values(), *FUNCTIONS.values()):
        words.add(spec.name.lower())
        words.update(str(v).lower() for v in getattr(spec, "values", ()) or ())
        for arg in spec.args:
            words.add(arg.name.lower())
            words.update(str(c).lower() for c in arg.choices or ())
    return words


def withheld(message: str, draft: str) -> str:
    """Review of S9 (MED-1, ADR-0006): a problem with a drafted screen, with every word of the model's withheld. The
    validator quotes what it rejects ("unknown field 'X'", "holder 'X' is not one of ..."), names $parameters, and a
    parse error can show a token - each comes from the model's draft. Quoted text is replaced unless it is punctuation
    or the registry's own word; $names are replaced; and, as a net, any other word that is in the draft and not the
    registry's or ScreenQL's own. The output filter is not relied on: it passes neutral-looking words."""
    vocab = _vocabulary()

    def quoted(m: "re.Match[str]") -> str:
        inner = m.group(0)[1:-1]
        if not re.search(r"[A-Za-z0-9]", inner) or inner.lower() in vocab:
            return m.group(0)
        before = message[:m.start()].rstrip().lower()
        return "(name withheld)" if before.endswith(("unknown field", "unknown function", "unknown parameter")) else "(value withheld)"
    out = _QUOTED.sub(quoted, message)
    out = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "$(name withheld)", out)
    model_words = {w.lower() for w in _IDENT.findall(draft or "")} - vocab
    return _IDENT.sub(lambda m: "(withheld)" if m.group(0).lower() in model_words else m.group(0), out)


def screened(text: str, lang: str) -> Optional[str]:
    """The H-C1 c output filter on the model's words: None when they may not be shown."""
    from app.ai import output_filter
    ok, shown, _ = output_filter.check(text, lang, where="screener_nl")
    return shown if ok else None


__all__ = ["FLAG", "SYSTEM", "PROMPT_VERSION", "DISCLAIMER", "registry_text", "system_prompt", "parse", "withheld", "screened"]
