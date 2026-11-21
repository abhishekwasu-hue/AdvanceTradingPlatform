"""P2-8 J4 + J5: "My proof" - facts of good process from his own trades - and the progress of his own process goals.

Pure functions over `reviews.Unit`s in time order (same input -> same output). Each fact names what happened, when and
on which trade; none is praise, a forecast or a performance claim (no AI text: fixed sentences filled with his own
numbers, and each passes `review.advice_in`). Facts:

* stop_kept        a closed trade with a stop, never moved against him nor missing;
* plan_on_loss     a losing trade where he answered "plan followed: Yes" (a process win even though it lost);
* waited_setup     an entry on his own signal / setup, with no FOMO tag;
* no_revenge       the first entry after a loss, without a revenge tag;
* exit_as_planned  a close by his own rule (stop, exit level, time rule), not by hand, with no early-exit tag;
* streak           STREAK_MIN or more closed trades in a row with "plan followed: Yes";
* recovered        his running result came back to its earlier high after a dip, with no behaviour tag on the way.

Goals (J5) are his own: a process measure (plan followed / stop kept / checked in), a target share and a period.
Progress is a count of his trades; the share is shown only on `needed` or more trades (the first-20 rule).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.journal.detectors import is_trader_exit
from app.journal.review import advice_in
from app.journal.reviews import Unit, share

STREAK_MIN = 5
METRICS = {"PLAN_FOLLOWED": "Plan followed", "STOP_KEPT": "Stop kept", "CHECKED_IN": "Checked in"}
PERIODS = ("WEEK", "MONTH")
TRIGGERS = ("NONE", "STOP_HIT", "LOSS")


@dataclass(frozen=True)
class Fact:
    key: str                  # stable: kind + the unit (pins and own entries refer to it)
    kind: str
    text: str
    at: str | None            # ISO, when it happened
    unit_kind: str | None
    unit_id: int | None
    chart: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "text": self.text, "at": self.at, "entry_kind": self.unit_kind,
                "entry_id": self.unit_id, "chart": self.chart, "own": False}


def _money(value: float) -> str:
    return f"₹{abs(value):,.0f}"


def _stop_kept(u: Unit) -> bool:
    return u.closed and u.stop_set and "moved_stop_against" not in u.tags and "no_stop" not in u.tags


def facts(units: Sequence[Unit]) -> list[Fact]:
    """Every fact of good process in `units` (time order in, newest first out)."""
    out: list[Fact] = []

    def add(kind: str, u: Unit | None, text: str, at: str | None) -> None:
        if advice_in(text):
            return
        key = f"{kind}:{u.kind}:{u.id}" if u is not None else f"{kind}:{at}"
        out.append(Fact(key, kind, text, at, u.kind if u else None, u.id if u else None, u.chart if u else None))

    ordered = sorted(units, key=lambda u: u.entry_order)
    for u in ordered:
        if _stop_kept(u):
            add("stop_kept", u, f"Stop was kept on {u.symbol}.", u.exit_time or u.entry_time)
        if u.closed and (u.pnl or 0) < 0 and u.plan_followed == "YES":
            add("plan_on_loss", u, f"Plan followed on a loss ({u.symbol}): a process win.", u.exit_time or u.entry_time)
        if u.from_signal and "fomo_entry" not in u.tags:
            add("waited_setup", u, f"Waited for your own setup on {u.symbol}.", u.entry_time)
        if u.closed and u.exit_by_rule and "early_exit" not in u.tags:
            add("exit_as_planned", u, f"Exit as planned on {u.symbol}.", u.exit_time)
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.closed and (prev.pnl or 0) < 0 and "revenge_trade" not in nxt.tags:
            add("no_revenge", nxt, f"No hurry after the loss on {prev.symbol}: next entry {nxt.symbol} without a revenge tag.", nxt.entry_time)
    closed = [u for u in ordered if u.closed]
    run: list[Unit] = []

    def close_run() -> None:
        if len(run) >= STREAK_MIN:
            last = run[-1]
            add("streak", last, f"{len(run)} trades in a row with your plan followed (to {last.symbol}).", last.exit_time or last.entry_time)
    for u in closed:
        if u.plan_followed == "YES":
            run.append(u)
            continue
        close_run()
        run = []
    close_run()
    peak = level = 0.0
    dip_start: int | None = None
    for i, u in enumerate(closed):
        level += u.pnl or 0.0
        if level < peak and dip_start is None:
            dip_start = i
        if dip_start is not None and level >= peak:
            span = closed[dip_start:i + 1]
            low = min(sum(x.pnl or 0.0 for x in closed[:j + 1]) for j in range(dip_start, i + 1))
            if not any(x.tags for x in span):
                add("recovered", u, f"Recovered a {_money(peak - low)} dip over {len(span)} trades without a rule-break tag.",
                    u.exit_time or u.entry_time)
            dip_start = None
        peak = max(peak, level)
    out.sort(key=lambda f: (f.at or "", f.key), reverse=True)
    return out


def last_good(found: Sequence[Fact]) -> Fact | None:
    """His most recent good decision (for Today and the top of the Journal)."""
    return found[0] if found else None


def after_loss_line(units: Sequence[Unit], n: int = 20) -> str | None:
    """After a loss, a calm fact: "In your last 20 trades you followed your plan in N." (None when his last closed trade
    did not lose, or he has answered no "plan followed" yet)."""
    closed = [u for u in sorted(units, key=lambda u: u.entry_order) if u.closed]
    if not closed or (closed[-1].pnl or 0) >= 0:
        return None
    last = closed[-n:]
    answered = [u for u in last if u.plan_followed]
    if not answered:
        return None
    yes = sum(1 for u in answered if u.plan_followed == "YES")
    line = f"In your last {len(last)} trades you followed your plan in {yes}."
    return None if advice_in(line) else line


def _hit(metric: str, u: Unit) -> bool | None:
    """Whether one unit meets the measure (None = it does not count for it)."""
    if metric == "PLAN_FOLLOWED":
        return None if not u.plan_followed else u.plan_followed == "YES"
    if metric == "STOP_KEPT":
        return _stop_kept(u)
    if metric == "CHECKED_IN":
        return bool(u.plan_followed or u.feeling_before or u.feeling_after or u.confidence)
    return None


def goal_progress(metric: str, target_pct: int, units: Sequence[Unit], needed: int) -> dict[str, Any]:
    counted = [h for h in (_hit(metric, u) for u in units if u.closed) if h is not None]
    done, base = sum(1 for h in counted if h), len(counted)
    pct = share(done, base, needed)
    return {"metric": metric, "label": METRICS.get(metric, metric), "target_pct": target_pct, "done": done, "base": base,
            "pct": pct, "reached": pct is not None and pct >= target_pct, "small": base < needed}


def triggered(trigger: str, units: Sequence[Unit]) -> Unit | None:
    """The latest closed unit that matches an if-then plan's trigger (a stop hit, a loss), else None."""
    for u in sorted((x for x in units if x.closed), key=lambda x: x.entry_order, reverse=True):
        if trigger == "LOSS" and (u.pnl or 0) < 0:
            return u
        if trigger == "STOP_HIT" and "stop" in (u.exit_reason or "").lower() and not is_trader_exit(u.exit_reason):
            return u
    return None


__all__ = ["Fact", "facts", "last_good", "after_loss_line", "goal_progress", "triggered", "METRICS", "PERIODS", "TRIGGERS",
           "STREAK_MIN"]
