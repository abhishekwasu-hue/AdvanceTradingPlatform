"""Z6-b / P1-e: the weekly review - every number from the trader's own journal, then plain-language lines that only
describe those numbers.

`build_review()` is pure: it takes the week's trades, their tags (as counted by the score rules), the week's discipline
score and the trader's own previous weeks, and returns:
* totals      - trades, decisions, closed / open, wins / losses, win rate, net and gross P&L, charges, average R,
                expectancy per closed trade (rupees and R), profit factor, largest win / loss, average holding time;
* by_setup    - the same per setup, with `what_worked` (setups with a positive net) and `what_lost` (negative net);
* behaviours  - per behaviour tag: trades, net P&L and average R of the closed tagged trades;
* costliest   - the behaviour whose tagged trades lost the most this week (a fact from the data, not a suggestion);
* discipline  - the week's score with its components;
* history     - the trader's own previous weeks (score, trades, net P&L, average R) - never anyone else's;
* lines       - the facts above as short English sentences. They describe; they never tell the trader what to do.

The Copilot may narrate `lines` (`copilot.narrate`: numbers checked against the facts, advice words filtered); when it
cannot, the lines are the explanation.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.journal.settings import DisciplineSettings

TAG_LABELS: Dict[str, str] = {
    "revenge_trade": "revenge trade", "fomo_entry": "FOMO entry", "averaging_loser": "averaging a loser",
    "moved_stop_against": "stop moved against the position", "no_stop": "no stop", "overtrading": "overtrading",
    "early_exit": "early exit before target", "held_through_event": "held through an event",
}
COMPONENT_LABELS: Dict[str, str] = {
    "stop_discipline": "stop discipline", "plan_adherence": "plan adherence", "emotional_control": "emotional control",
    "frequency_control": "trade frequency", "event_awareness": "event awareness",
}
DISCLAIMER = ("Numbers from your own journal for this week. This review describes what happened; it is education, "
              "not investment advice, and it does not suggest any trade.")


@dataclass(frozen=True)
class ReviewTrade:
    id: int
    setup: str
    unit: str
    pnl: Optional[float]           # net of charges; None while open
    charges: float
    risk: float                    # initial stop distance x quantity (0 = no valid stop)
    holding_minutes: Optional[float]

    @property
    def closed(self) -> bool:
        return self.pnl is not None

    @property
    def r(self) -> Optional[float]:
        return self.pnl / self.risk if self.pnl is not None and self.risk > 0 else None


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _stats(trades: Sequence[ReviewTrade]) -> Dict[str, Any]:
    closed = [t for t in trades if t.closed]
    pnls = [float(t.pnl) for t in closed if t.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rs = [r for r in (t.r for t in closed) if r is not None]
    holds = [t.holding_minutes for t in closed if t.holding_minutes is not None]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "trades": len(trades), "decisions": len({t.unit for t in trades}), "closed": len(closed), "open": len(trades) - len(closed),
        "wins": len(wins), "losses": len(losses), "flat": len(pnls) - len(wins) - len(losses),
        "win_rate": _round(100.0 * len(wins) / len(pnls), 1) if pnls else None,
        "net_pnl": round(sum(pnls), 2), "charges": round(sum(t.charges for t in closed), 2),
        "gross_pnl": round(sum(pnls) + sum(t.charges for t in closed), 2),
        "avg_r": _round(_mean(rs)), "r_trades": len(rs), "expectancy": _round(_mean(pnls)),
        "profit_factor": _round(gross_win / gross_loss) if gross_loss > 0 else None,
        "largest_win": _round(max(wins)) if wins else None, "largest_loss": _round(min(losses)) if losses else None,
        "avg_holding_minutes": _round(_mean(holds), 1),
    }


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _money(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{'-' if value < 0 else ''}₹{abs(value):,.2f}"


def build_review(week: str, trades: Sequence[ReviewTrade], counted_tags: Iterable[tuple], score: Dict[str, Any],
                 history: Sequence[Dict[str, Any]], settings: DisciplineSettings) -> Dict[str, Any]:
    """`counted_tags`: (trade_id, tag) pairs that count under the trader's settings (dismissed ones left out)."""
    by_id = {t.id: t for t in trades}
    totals = _stats(trades)

    setups: Dict[str, List[ReviewTrade]] = defaultdict(list)
    for t in trades:
        setups[t.setup or "-"].append(t)
    by_setup = sorted(({"setup": name, **_stats(group)} for name, group in setups.items()), key=lambda g: (-g["net_pnl"], g["setup"]))
    what_worked = [g for g in by_setup if g["closed"] and g["net_pnl"] > 0][:3]
    what_lost = sorted((g for g in by_setup if g["closed"] and g["net_pnl"] < 0), key=lambda g: (g["net_pnl"], g["setup"]))[:3]

    tagged: Dict[str, set] = defaultdict(set)
    for trade_id, tag in counted_tags:
        if trade_id in by_id:
            tagged[tag].add(trade_id)
    behaviours = []
    for tag in sorted(tagged):
        group = [by_id[i] for i in sorted(tagged[tag])]
        stats = _stats(group)
        behaviours.append({"tag": tag, "label": TAG_LABELS.get(tag, tag), "trades": stats["trades"], "closed": stats["closed"],
                           "net_pnl": stats["net_pnl"], "avg_r": stats["avg_r"]})
    behaviours.sort(key=lambda b: (b["net_pnl"], b["tag"]))
    losing = [b for b in behaviours if b["closed"] and b["net_pnl"] < 0]
    costliest = losing[0] if losing else None

    review = {"week": week, "totals": totals, "by_setup": by_setup, "what_worked": what_worked, "what_lost": what_lost,
              "behaviours": behaviours, "costliest": costliest, "discipline": score, "history": list(history),
              "count_unconfirmed": settings.count_unconfirmed, "disclaimer": DISCLAIMER}
    review["lines"] = lines(review)
    return review


def lines(review: Dict[str, Any]) -> List[str]:
    """The review's facts as sentences. Descriptive only: no instruction, no recommendation, no forecast."""
    t = review["totals"]
    out: List[str] = []
    if not t["trades"]:
        out.append(f"No trades were journaled in {review['week']}.")
        return out
    out.append(f"{review['week']}: {_n(t['trades'], 'trade')} ({_n(t['decisions'], 'decision')}), {t['closed']} closed and {t['open']} still open.")
    if t["closed"]:
        win_rate = f"{t['win_rate']:g}%" if t["win_rate"] is not None else "n/a"
        out.append(f"Closed trades: {t['wins']} won, {t['losses']} lost, win rate {win_rate}; net P&L {_money(t['net_pnl'])} "
                   f"after {_money(t['charges'])} in charges.")
        if t["avg_r"] is not None:
            out.append(f"Average result {t['avg_r']:g}R over {_n(t['r_trades'], 'trade')} with a stop; expectancy {_money(t['expectancy'])} per closed trade.")
    for g in review["what_worked"]:
        out.append(f"Setup {g['setup']} made {_money(g['net_pnl'])} over {_n(g['closed'], 'closed trade')}.")
    for g in review["what_lost"]:
        out.append(f"Setup {g['setup']} lost {_money(g['net_pnl'])} over {_n(g['closed'], 'closed trade')}.")
    for b in review["behaviours"]:
        out.append(f"Tagged {b['label']}: {_n(b['trades'], 'trade')}, net {_money(b['net_pnl'])} on the closed ones.")
    if review["costliest"]:
        c = review["costliest"]
        out.append(f"The behaviour with the largest loss this week was {c['label']} ({_money(c['net_pnl'])} over {_n(c['closed'], 'closed trade')}).")
    score = review["discipline"].get("score")
    if score is not None:
        weakest = sorted((c for c in review["discipline"]["components"] if c["clean_pct"] is not None and c["max_points"] > 0),
                         key=lambda c: (c["clean_pct"], c["name"]))
        detail = ""
        if weakest and weakest[0]["clean_pct"] < 100:
            w = weakest[0]
            detail = f"; the lowest component was {COMPONENT_LABELS.get(w['name'], w['name'])} at {w['clean_pct']:g}% of decisions untagged"
        out.append(f"Discipline score {score} out of 100{detail}.")
    previous = [h for h in review["history"] if h.get("score") is not None]
    if previous and score is not None:
        last = previous[0]
        change = score - last["score"]
        direction = "up" if change > 0 else ("down" if change < 0 else "unchanged")
        out.append(f"Compared with {last['week']} (score {last['score']}), the score is {direction}" + (f" by {abs(change)}." if change else "."))
    return out
