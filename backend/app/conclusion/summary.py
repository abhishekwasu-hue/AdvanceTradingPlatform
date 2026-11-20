"""TERM-CON C4 (plan 3.4): an optional AI summary of the Conclusion read, on tap only (TC-Q3), on the user's own key.

The model sees ONLY the deterministic read (the glance and every shown line) - no new data. Its words pass the same
checks as the Copilot's: every number and symbol must be in those facts (`copilot.grounded`), the output filter, the
describe-only check (no instruction: "you should", "consider" ...), and no trade-call word (`signals.language.call_words`:
no buy / sell / target / stop). Refused twice: no summary - the panel keeps its rule lines. The AI never places, blocks or
changes an order (ADR-0006).
"""
from __future__ import annotations

from typing import Any

SUMMARY_PROMPT = """You describe a trading terminal's readings for one symbol to the trader who owns them.
Write 3 to 5 short sentences in plain English: what the readings agree on, what conflicts, and what needs the trader's
attention. Describe only. Never recommend, never say what to do, never name an entry, exit, target or stop, never use the
words buy, sell, target, stop or stop-loss. Use only numbers and symbols that appear in the FACTS.
=== FACTS ===
{facts}"""
QUESTION = "Summarise these readings."


def facts_of(body: dict[str, Any]) -> list[str]:
    """The read as fact lines: the lean and meter, the line, agrees / conflicts, what needs him, every section line."""
    g = body.get("glance") or {}
    out = [str((g.get("lean") or {}).get("words") or "")]
    m = g.get("meter") or {}
    if m:
        out.append(f"Readings: {m.get('up', 0)} up, {m.get('down', 0)} down, {m.get('range', 0)} in a range or mixed")
    out.append(str(g.get("line") or ""))
    out += [f"Agrees: {a.get('text')}" for a in g.get("agrees") or []]
    out += [f"Conflicts: {c.get('text')}" for c in g.get("conflicts") or []]
    out += [f"Needs attention: {n.get('text')}" for n in body.get("needs_you") or []]
    for s in body.get("sections") or []:
        out += [f"{s.get('title')}: {x.get('text')}" for x in s.get("lines") or []]
    return [x for x in out if x and x.strip()]


async def summarize(provider: Any, body: dict[str, Any]) -> tuple[str | None, str]:
    """(summary, "ok") or (None, why)."""
    from app.ai import copilot, output_filter
    from app.journal.review import advice_in
    from app.signals.language import call_words
    facts = facts_of(body)
    if not facts:
        return None, "no facts"
    system = SUMMARY_PROMPT.format(facts=copilot.facts_text(facts))
    user = f"QUESTION:\n{QUESTION}"
    why = "provider returned no text"
    for _attempt in range(2):
        try:
            text = (await provider.complete(system, user, max_tokens=400)).strip()
        except Exception as exc:  # noqa: BLE001 - the rule lines are always there
            return None, f"provider error: {type(exc).__name__}"
        if not text:
            return None, why
        ok, why = copilot.grounded(text, facts, QUESTION)
        if ok:
            ok, shown, why = output_filter.check(text, "en", where="copilot_chat")
            if ok and shown:
                problems = [*advice_in(shown), *call_words(shown)]
                if not problems:
                    return shown, "ok"
                why = "it read as advice: " + ", ".join(f'"{p}"' for p in problems[:3])
        user = f"QUESTION:\n{QUESTION}\n\nYour previous answer was refused ({why}). Describe again, using only the FACTS."
    return None, why
