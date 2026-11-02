"""M72 PR-8 (CP-8): the Copilot coach - a week of the trader's OWN journal as numbers, and his own questions back.

`get_coach_week(part, week)` (flag `discipline_engine`, kind read) reads one part of a journal week (the discipline
engine's week, `journal.service.refresh_week`, the same rows as the Journal page and `get_weekly_review`):

* `rules`    - adherence to HIS OWN checklist rules (Journal settings): per rule, how many decisions he marked followed /
               not followed / left unanswered, and the net P&L of the closed ones each way;
* `mistakes` - his own labels (his mistake / emotion tags) and the behaviour tags the journal found, with trades and P&L;
* `setups`   - his setups by net P&L, best first (trades, closed, wins, losses, win rate) - the best and worst setups.

Every part also carries `questions_for_you`: questions built by code from HIS rules, labels and counts ("Was this entry
in your plan?"), so the model may ask them word for word. A review never instructs: each question passes the journal's
`advice_in` check, and the planner's answer after this tool passes it too (agent.py). Numbers only from his journal rows;
nothing is pre-chosen. The week refresh completes his journal exactly as opening the Journal page does; nothing here
places, blocks or changes an order (ADR-0006).
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tools import Tool, ToolContext, ToolResult, register

FLAG = "discipline_engine"
MAX_ROWS = 12
MAX_QUESTIONS = 4
NO_SETUP = "No setup named"


class _Coach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part: Literal["rules", "mistakes", "setups"] = Field(description="rules = his own checklist adherence; mistakes = his labels and "
                                                         "the journal's behaviour tags; setups = best / worst setups")
    week: str | None = Field(default=None, pattern=r"^\d{4}-W\d{2}$", description="ISO week like 2026-W44; omit for this week")


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _net(decisions: list[Any]) -> float | None:
    closed = [d.pnl for d in decisions if d.pnl is not None]
    return _r(sum(closed)) if closed else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _loads(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def rule_rows(decisions: list[Any], entries: dict[int, Any], settings: Any) -> list[dict[str, Any]]:
    """Per own rule: followed / not followed / unanswered decisions and the closed ones' net P&L each way."""
    from app.journal.entries import checklist_view
    answers: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"yes": [], "no": [], "none": []})
    order: list[str] = list(settings.checklist_rules)
    for d in decisions:
        entry = entries.get(d.id)
        for item in checklist_view(entry.checklist_json if entry is not None else None, settings):
            rule = item["rule"]
            if rule not in order:
                order.append(rule)
            answers[rule]["yes" if item["followed"] is True else "no" if item["followed"] is False else "none"].append(d)
    rows = []
    for rule in order:
        a = answers[rule]
        rows.append({"rule": rule, "followed": len(a["yes"]), "not_followed": len(a["no"]), "unanswered": len(a["none"]),
                     "net_pnl_followed": _net(a["yes"]), "net_pnl_not_followed": _net(a["no"])})
    return rows[:MAX_ROWS]


def mistake_rows(decisions: list[Any], entries: dict[int, Any], counted_tags: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """His own labels and the journal's behaviour tags: decisions and net P&L, costliest first."""
    from app.journal.review import TAG_LABELS
    by_row = {row: d for d in decisions for row in d.rows}
    groups: dict[tuple[str, str], dict[int, Any]] = defaultdict(dict)
    for d in decisions:
        for row in d.rows:
            entry = entries.get(row)
            for label in _loads(entry.user_tags_json if entry is not None else None):
                if isinstance(label, str) and label.strip():
                    groups[(label.strip(), "Your label")][d.id] = d
    for trade_id, tag in counted_tags:
        if trade_id in by_row:
            groups[(TAG_LABELS.get(tag, tag), "Found by the journal")][by_row[trade_id].id] = by_row[trade_id]
    rows = [{"label": label, "source": source, "trades": len(group), "closed": sum(1 for d in group.values() if d.pnl is not None),
             "net_pnl": _net(list(group.values()))} for (label, source), group in groups.items()]
    rows.sort(key=lambda r: (r["net_pnl"] if r["net_pnl"] is not None else 0.0, -r["trades"], r["label"]))
    return rows[:MAX_ROWS]


def setup_rows(decisions: list[Any]) -> list[dict[str, Any]]:
    """His setups by net P&L of the closed decisions, best first."""
    groups: dict[str, list[Any]] = defaultdict(list)
    for d in decisions:
        groups[str(d.setup or "").strip() or NO_SETUP].append(d)
    rows = []
    for name, group in groups.items():
        closed = [d for d in group if d.pnl is not None]
        wins = sum(1 for d in closed if d.pnl > 0)
        rows.append({"setup": name, "trades": len(group), "closed": len(closed), "wins": wins, "losses": sum(1 for d in closed if d.pnl < 0),
                     "net_pnl": _net(group), "win_rate_pct": _r(wins / len(closed) * 100) if closed else None})
    rows.sort(key=lambda r: (-(r["net_pnl"] if r["net_pnl"] is not None else 0.0), r["setup"]))
    return rows[:MAX_ROWS]


def questions(part: str, rows: list[dict[str, Any]], has_rules: bool) -> list[str]:
    """His own questions back, from his own rules, labels and counts (never an instruction)."""
    from app.journal.review import advice_in
    out: list[str] = []
    if part == "rules":
        if not has_rules:
            out.append("You have no checklist rules in your journal yet. Which rules do you check before an entry?")
        for r in rows:
            if r["not_followed"]:
                out.append(f"Your rule \"{r['rule']}\" was marked not followed on {_plural(r['not_followed'], 'trade')}. "
                           "What led to those entries?")
        missing = max((r["unanswered"] for r in rows), default=0)
        if missing:
            out.append(f"{_plural(missing, 'trade')} {'has' if missing == 1 else 'have'} no checklist answer. Was each of those entries in your plan?")
    elif part == "mistakes":
        for r in rows[:2]:
            out.append(f"\"{r['label']}\" came up on {_plural(r['trades'], 'trade')}. What was happening just before those entries?")
    else:
        if rows:
            out.append(f"Was each \"{rows[0]['setup']}\" entry in your plan?")
        if len(rows) > 1 and (rows[-1]["net_pnl"] or 0) < 0:
            out.append(f"What did the \"{rows[-1]['setup']}\" entries have in common?")
    return [q for q in out if not advice_in(q)][:MAX_QUESTIONS]


async def get_coach_week(ctx: ToolContext, args: _Coach) -> ToolResult:
    """One part of a week of your own journal: checklist adherence, labels / behaviour tags, or setups, with questions."""
    from app.journal import service
    from app.journal.score import TagState, counted
    try:
        data = await service.refresh_week(ctx.session, ctx.user, args.week, now=ctx.now)
    except service.WeekError as exc:
        return ToolResult(False, None, None, "journal", error=str(exc))
    decisions = service.review_trades(data["trades"])
    s = data["settings"]
    if args.part == "rules":
        rows = rule_rows(decisions, data["entries"], s)
    elif args.part == "mistakes":
        live = counted([TagState(r.trade_id, r.tag, r.status) for r in data["tags"]], s)
        rows = mistake_rows(decisions, data["entries"], [(t.trade_id, t.tag) for t in live])
    else:
        rows = setup_rows(decisions)
    out = {args.part: rows, "week": data["week"], "decisions": len(decisions),
           "closed": sum(1 for d in decisions if d.pnl is not None),
           "questions_for_you": questions(args.part, rows, bool(s.checklist_rules))}
    as_of = ctx.now.isoformat()
    return ToolResult(True, out, as_of, "journal", {"week": data["week"], "read_at": as_of})


register(Tool("get_coach_week", "A week of your own journal as numbers - part 'rules' (your own checklist: followed / not followed / "
              "unanswered), 'mistakes' (your labels and the behaviour tags found) or 'setups' (best and worst setups) - with "
              "questions_for_you to ask back word for word. Read only.", _Coach, get_coach_week, flag=FLAG))

__all__ = ["get_coach_week", "rule_rows", "mistake_rows", "setup_rows", "questions"]
