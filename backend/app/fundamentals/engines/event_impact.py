"""Event Impact Score - a net directional read on corporate actions already on record, weighted
toward more recent events. Computed purely from CorporateAction.expected_*_impact fields a human
entered (Bullish/Neutral/Bearish); this engine never assigns an impact itself.
"""
from datetime import date
from typing import List, Optional

from app.core.enums import Bias
from app.fundamentals.models import CorporateAction, EventImpactResult

_IMPACT_VALUE = {Bias.BULLISH: 1.0, Bias.BEARISH: -1.0, Bias.NEUTRAL: 0.0}
_RECENCY_HALF_LIFE_DAYS = 180.0


class EventImpactEngine:
    def analyze(self, actions: List[CorporateAction], as_of: Optional[date] = None) -> EventImpactResult:
        scored = []
        for action in actions:
            impacts = [
                i for i in (action.expected_revenue_impact, action.expected_margin_impact, action.expected_eps_impact)
                if i is not None
            ]
            if not impacts:
                continue
            avg = sum(_IMPACT_VALUE[i] for i in impacts) / len(impacts)
            scored.append((action.announced_date, avg))

        if not scored:
            raise ValueError("No corporate actions with a recorded expected impact - nothing to score")

        anchor = as_of or max(d for d, _ in scored)
        weighted_sum = 0.0
        total_weight = 0.0
        for announced_date, avg in scored:
            age_days = max((anchor - announced_date).days, 0)
            weight = 1.0 / (1.0 + age_days / _RECENCY_HALF_LIFE_DAYS)
            weighted_sum += avg * weight
            total_weight += weight

        normalized = weighted_sum / total_weight
        score = round(normalized * 100.0, 1)
        if score > 15:
            bias = Bias.BULLISH
        elif score < -15:
            bias = Bias.BEARISH
        else:
            bias = Bias.NEUTRAL

        note = (
            f"{len(scored)} corporate action(s) with a recorded expected impact considered, "
            f"weighted toward more recent events (half-life ~{int(_RECENCY_HALF_LIFE_DAYS)} days)."
        )
        return EventImpactResult(score=score, bias=bias, events_considered=len(scored), note=note)
