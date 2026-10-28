"""Z6: the weekly discipline score (0-100) with its components - pure and deterministic.

Each component is the share of the week's decisions (a trade, or a whole multi-leg structure) that carry none of the
component's tags; the score is the weighted mean of those shares (weights from the trader's settings, normalised by
their sum), rounded half up to a whole number. A DISMISSED tag never counts; a SUGGESTED one counts while
`count_unconfirmed` is on (the default); CONFIRMED and trader-added tags always count. No decisions in the week: no
score (None), never a made-up 100.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from app.journal.settings import COMPONENT_TAGS, DisciplineSettings


@dataclass(frozen=True)
class TagState:
    trade_id: int
    tag: str
    status: str                 # SUGGESTED / CONFIRMED / DISMISSED


def counted(tags: Iterable[TagState], settings: DisciplineSettings) -> List[TagState]:
    ok = {"CONFIRMED"} | ({"SUGGESTED"} if settings.count_unconfirmed else set())
    return [t for t in tags if t.status in ok]


NOT_YET = "n/a until a trade closes"


def weekly_score(units: Dict[int, str], tags: Sequence[TagState], settings: DisciplineSettings,
                 closed: Optional[Iterable[int]] = None) -> Dict:
    """`units` maps every trade id in the week to its decision unit (trade or structure). Returns
    {score, decisions, components: [...]} with each component's weight, flagged decisions, clean share and points.

    AUD-V4 (owner, 28 Oct): `closed` = the trade ids that have closed. A decision is closed when all its trades are; with
    open decisions only there is no score yet (`note` = "n/a until a trade closes" - an open, untagged trade is not yet a
    clean one); otherwise `note` says what the score covers: "(N open · M closed)"."""
    decisions = sorted(set(units.values()))
    n = len(decisions)
    closed_ids = set(closed) if closed is not None else set(units)
    open_units = {u for tid, u in units.items() if tid not in closed_ids}
    n_open = len(open_units)
    n_closed = n - n_open
    weights = {name: max(0.0, float(settings.weights.get(name, 0.0))) for name in COMPONENT_TAGS}
    total_weight = sum(weights.values()) or 1.0
    live = [t for t in counted(tags, settings) if t.trade_id in units]
    components: List[Dict] = []
    score_points = 0.0
    for name, tag_names in COMPONENT_TAGS.items():
        flagged_units = sorted({units[t.trade_id] for t in live if t.tag in tag_names})
        per_tag = Counter(t.tag for t in live if t.tag in tag_names)
        share = weights[name] / total_weight
        clean_pct: Optional[float] = None
        points = 0.0
        if n:
            clean_pct = round(100.0 * (n - len(flagged_units)) / n, 1)
            points = share * 100.0 * (n - len(flagged_units)) / n
        score_points += points
        components.append({
            "name": name, "tags": list(tag_names), "weight": weights[name], "weight_share": round(share, 4),
            "decisions": n, "flagged": len(flagged_units), "clean_pct": clean_pct, "points": round(points, 1),
            "max_points": round(share * 100.0, 1), "by_tag": {k: per_tag.get(k, 0) for k in tag_names},
        })
    scored = n > 0 and n_closed > 0
    note = None if n == 0 else (f"({n_open} open · {n_closed} closed)" if scored else NOT_YET)
    return {"score": int(math.floor(score_points + 0.5 + 1e-9)) if scored else None, "decisions": n, "trades": len(units),
            "components": components, "count_unconfirmed": settings.count_unconfirmed, "open": n_open, "closed": n_closed,
            "note": note}
