"""P2-8 J3: the daily card, the weekly extras and the monthly summary - short, in plain words, from his own trades.

Pure functions over `Unit`s (one decision each: a single trade, a whole structure, or an outside-ATP entry, with his
own check-in answers and the behaviour tags he did not dismiss); same input -> same output.

The first-20 rule (`min_closed_for_review`, default 20, his setting): no percentage is shown on fewer trades than the
setting - below it a count is shown ("Plan followed: 3 of 5") and a group of trades is labelled a small sample. The
first formal review comes once he has that many closed trades in all; before it, each review says
"Not enough trades yet (N of 20)" above the plain counts.

Every generated line passes `review.advice_in` (a line that reads as advice is dropped, never shown). The one fixed
reflection question is the owner's own wording (ATP_SESSION_JOURNAL.md J3) and is his question to himself, not a
generated line. Feelings are only what he tapped; nothing is inferred and nothing is scored.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.journal.review import advice_in

WEEK_QUESTION = "What is the one thing to repeat next week?"
MONTH_QUESTION = "Which one habit from this month do you want to keep?"
LABELS = {"YES": "Yes", "PARTLY": "Partly", "NO": "No"}
TAG_WORDS = {"revenge_trade": "Revenge trade", "fomo_entry": "FOMO entry", "averaging_loser": "Added to a loser",
             "moved_stop_against": "Stop moved against", "no_stop": "No stop", "overtrading": "Overtrading",
             "early_exit": "Early exit", "held_through_event": "Held through an event", "other": "Other"}


@dataclass(frozen=True)
class Unit:
    id: int                                 # the entry the sheet opens (a structure: its first leg)
    kind: str                               # trade / manual
    symbol: str
    setup: str | None
    entry_time: str | None                  # ISO
    pnl: float | None                       # None = still open
    stop_set: bool = True
    feeling_before: str | None = None
    feeling_after: str | None = None
    confidence: int | None = None
    plan_followed: str | None = None        # YES / PARTLY / NO
    mistake: str | None = None
    tags: tuple[str, ...] = ()              # behaviour tags not dismissed
    confirmed: tuple[str, ...] = ()         # the ones he confirmed
    mae_amount: float | None = None
    mfe_amount: float | None = None
    chart: str | None = None
    lesson: str | None = None
    entry_order: int = 0                    # position in time (for "after a loss")

    @property
    def closed(self) -> bool:
        return self.pnl is not None


@dataclass
class Gate:
    closed_total: int
    needed: int
    ready: bool = field(init=False)

    def __post_init__(self) -> None:
        self.ready = self.closed_total >= self.needed

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ready": self.ready, "closed_total": self.closed_total, "needed": self.needed}
        if not self.ready:
            out["note"] = f"Not enough trades yet ({self.closed_total} of {self.needed})."
        return out


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{'-' if value < 0 else '+' if value > 0 else ''}₹{abs(value):,.0f}"


def _guard(lines: Iterable[str | None]) -> list[str]:
    return [line for line in lines if line and not advice_in(line)]


def _one(line: str | None) -> str | None:
    kept = _guard([line])
    return kept[0] if kept else None


def _ordered(counts: Counter, order: Sequence[str]) -> list[str]:
    return [f for f in order if f in counts] + sorted(f for f in counts if f not in order)


def share(count: int, base: int, needed: int) -> float | None:
    """A percentage only on `needed` or more trades (the first-20 rule); else None (show the count instead)."""
    return round(100.0 * count / base, 1) if base >= needed and base > 0 else None


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def plan_counts(units: Sequence[Unit], needed: int) -> dict[str, Any]:
    closed = [u for u in units if u.closed]
    answers = Counter(u.plan_followed for u in closed if u.plan_followed)
    answered = sum(answers.values())
    return {"yes": answers.get("YES", 0), "partly": answers.get("PARTLY", 0), "no": answers.get("NO", 0), "answered": answered,
            "unanswered": len(closed) - answered, "rate": share(answers.get("YES", 0), answered, needed)}


def plan_line(p: dict[str, Any]) -> str | None:
    if not p["answered"]:
        return None
    rate = f" ({p['rate']:g}%)" if p["rate"] is not None else ""
    return f"Plan followed: {p['yes']} of {p['answered']} answered{rate}."


def done_well(units: Sequence[Unit]) -> str | None:
    """ONE fact of good process from the period's own trades, or None when no fact holds (never praise text)."""
    closed = [u for u in units if u.closed]
    if not closed:
        return None
    if all(u.stop_set and "moved_stop_against" not in u.tags and "no_stop" not in u.tags for u in closed):
        return f"Stop kept on all {_n(len(closed), 'trade')}."
    answered = [u for u in closed if u.plan_followed]
    for u in closed:
        if u.pnl is not None and u.pnl < 0 and u.plan_followed == "YES":
            return f"Plan followed on a losing trade ({u.symbol})."
    if answered and all(u.plan_followed == "YES" for u in answered):
        return f"Plan followed on all {_n(len(answered), 'trade')} you answered."
    losses = [u for u in closed if u.pnl is not None and u.pnl < 0]
    later = [u for u in units if losses and u.entry_order > min(x.entry_order for x in losses)]
    if losses and later and not any("revenge_trade" in u.tags for u in units):
        return "No trade in a hurry after a loss."
    if not any("early_exit" in u.tags for u in closed):
        return "Every exit by your own rules."
    return None


def _result_rows(units: Sequence[Unit], key: str, needed: int, order: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """Results grouped by one of his own answers (feeling / confidence): counts and rupees; a win share only on
    `needed` or more trades, else the row is marked a small sample."""
    groups: dict[Any, list[Unit]] = defaultdict(list)
    for u in units:
        value = getattr(u, key)
        if u.closed and value is not None:
            groups[value].append(u)
    keys = [k for k in (order or []) if k in groups] + sorted((k for k in groups if not order or k not in order), key=str)
    rows = []
    for k in keys:
        g = groups[k]
        wins = sum(1 for u in g if (u.pnl or 0) > 0)
        net = round(sum(u.pnl or 0.0 for u in g), 2)
        rows.append({"value": k, "trades": len(g), "wins": wins, "losses": sum(1 for u in g if (u.pnl or 0) < 0), "net_pnl": net,
                     "avg_pnl": round(net / len(g), 2), "win_rate": share(wins, len(g), needed), "small": len(g) < needed})
    return rows


def _confirmed_counts(units: Sequence[Unit]) -> list[dict[str, Any]]:
    c = Counter(t for u in units for t in u.confirmed)
    return [{"tag": t, "label": TAG_WORDS.get(t, t), "count": n} for t, n in sorted(c.items(), key=lambda x: (-x[1], x[0]))]


def daily_card(day: str, units: Sequence[Unit], gate: Gate, feelings_order: Sequence[str] = ()) -> dict[str, Any]:
    closed = [u for u in units if u.closed]
    net = round(sum(u.pnl or 0.0 for u in closed), 2) if closed else None
    p = plan_counts(units, gate.needed)
    feelings = Counter(f for u in units for f in (u.feeling_before, u.feeling_after) if f)
    lines = _guard([
        f"{_n(len(units), 'trade')}, {len(closed)} closed." if units else "No trades today.",
        f"Result: {_money(net)}." if net is not None else None,
        plan_line(p),
    ])
    good = done_well(units)
    return {"day": day, "trades": len(units), "closed": len(closed), "net_pnl": net, "plan": p,
            "feelings": [{"feeling": f, "count": feelings[f]} for f in _ordered(feelings, feelings_order)],
            "confirmed_tags": _confirmed_counts(units), "done_well": _one(good),
            "lines": lines, "gate": gate.to_dict(), "entries": [{"kind": u.kind, "id": u.id, "symbol": u.symbol, "pnl": u.pnl} for u in units]}


def _excursion_lines(units: Sequence[Unit]) -> list[str]:
    against = [u for u in units if u.mae_amount is not None and u.mae_amount > 0]
    favour = [u for u in units if u.mfe_amount is not None and u.mfe_amount > 0]
    out = []
    if against:
        u = max(against, key=lambda x: x.mae_amount or 0.0)
        out.append(f"Largest move against you while open: ₹{u.mae_amount:,.0f} on {u.symbol}.")
    if favour:
        u = max(favour, key=lambda x: x.mfe_amount or 0.0)
        out.append(f"Largest move in your favour while open: ₹{u.mfe_amount:,.0f} on {u.symbol}.")
    return out


def weekly_extras(week: str, units: Sequence[Unit], gate: Gate, feelings_order: Sequence[str] = (), answer: str | None = None) -> dict[str, Any]:
    """The weekly review's additions: plan-followed rate, feeling / confidence vs result, the most frequent mistake,
    the biggest move against / in favour, the notable trades (with their charts), the reflection question."""
    p = plan_counts(units, gate.needed)
    mistakes = Counter(u.mistake for u in units if u.mistake)
    top = mistakes.most_common(1)
    notable = sorted((u for u in units if u.closed), key=lambda u: (-abs(u.pnl or 0.0), u.id))[:3]
    mistake_line = (f"Most frequent mistake you named: {TAG_WORDS.get(top[0][0], top[0][0])} ({_n(top[0][1], 'time')})."
                    if top else None)
    return {"week": week, "gate": gate.to_dict(), "plan": p,
            "feelings": _result_rows(units, "feeling_before", gate.needed, feelings_order),
            "feelings_after": _result_rows(units, "feeling_after", gate.needed, feelings_order),
            "confidence": _result_rows(units, "confidence", gate.needed, [1, 2, 3, 4, 5]),
            "mistake": {"value": top[0][0], "label": TAG_WORDS.get(top[0][0], top[0][0]), "count": top[0][1]} if top else None,
            "lines": _guard([plan_line(p), mistake_line, *_excursion_lines(units)]),
            "notable": [{"kind": u.kind, "id": u.id, "symbol": u.symbol, "pnl": u.pnl, "chart": u.chart} for u in notable],
            "done_well": _one(done_well(units)),
            "question": WEEK_QUESTION, "answer": answer}


def _setup_rows(units: Sequence[Unit], needed: int) -> list[dict[str, Any]]:
    groups: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        if u.closed:
            groups[u.setup or "-"].append(u)
    rows = [{"setup": k, "trades": len(g), "net_pnl": round(sum(x.pnl or 0.0 for x in g), 2),
             "win_rate": share(sum(1 for x in g if (x.pnl or 0) > 0), len(g), needed), "small": len(g) < needed} for k, g in groups.items()]
    return sorted(rows, key=lambda r: (-r["net_pnl"], r["setup"]))


def monthly(month: str, units: Sequence[Unit], gate: Gate, weeks: Sequence[dict[str, Any]], answer: str | None = None) -> dict[str, Any]:
    """The month: the discipline score and plan-followed counts week by week, best / worst setup by his own numbers,
    the month's totals - a page he can print."""
    closed = [u for u in units if u.closed]
    net = round(sum(u.pnl or 0.0 for u in closed), 2) if closed else None
    setups = _setup_rows(units, gate.needed)
    best = setups[0] if setups and setups[0]["net_pnl"] > 0 else None
    worst = setups[-1] if setups and setups[-1]["net_pnl"] < 0 else None
    p = plan_counts(units, gate.needed)
    lines = _guard([
        f"{_n(len(units), 'trade')}, {len(closed)} closed; result {_money(net)}." if units else "No trades this month.",
        plan_line(p),
        (f"Best setup by your numbers: {best['setup']} ({_money(best['net_pnl'])}, {_n(best['trades'], 'trade')}"
         f"{', small sample' if best['small'] else ''}).") if best else None,
        (f"Weakest setup by your numbers: {worst['setup']} ({_money(worst['net_pnl'])}, {_n(worst['trades'], 'trade')}"
         f"{', small sample' if worst['small'] else ''}).") if worst else None,
    ])
    return {"month": month, "gate": gate.to_dict(), "trades": len(units), "closed": len(closed), "net_pnl": net, "plan": p,
            "weeks": list(weeks), "setups": setups, "best_setup": best, "worst_setup": worst, "lines": lines,
            "done_well": _one(done_well(units)), "question": MONTH_QUESTION, "answer": answer}


__all__ = ["Unit", "Gate", "share", "plan_counts", "done_well", "daily_card", "weekly_extras", "monthly", "WEEK_QUESTION",
           "MONTH_QUESTION"]
