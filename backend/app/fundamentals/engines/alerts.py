"""Fundamental Alert Engine - turns signals every other engine already computed into a flat,
prioritized alert list. It never computes a new judgement of its own; it only re-packages red
flags, valuation/DCF extremes, an imminent earnings date, and negative event momentum into one
feed a dashboard can poll. An empty list is a legitimate, real result (nothing is wrong right
now), not a sign of missing data.
"""
from typing import List, Optional

from app.core.enums import RiskLevel, ValuationLabel
from app.fundamentals.models import Alert, DCFResult, EventImpactResult, RedFlag, ValuationResult

_EARNINGS_IMMINENT_DAYS = 7
_DCF_OVERVALUED_THRESHOLD_PCT = -20.0
_NEGATIVE_MOMENTUM_THRESHOLD = -30.0


class AlertEngine:
    def analyze(
        self,
        red_flags: Optional[List[RedFlag]] = None,
        valuation: Optional[ValuationResult] = None,
        dcf: Optional[DCFResult] = None,
        days_to_next_results: Optional[int] = None,
        event_impact: Optional[EventImpactResult] = None,
    ) -> List[Alert]:
        alerts: List[Alert] = []

        for flag in red_flags or []:
            if flag.severity in (RiskLevel.HIGH, RiskLevel.EXTREME):
                alerts.append(Alert(code=flag.code, severity=flag.severity, message=flag.description))

        if valuation is not None and valuation.classification == ValuationLabel.EXTREMELY_EXPENSIVE:
            alerts.append(Alert(
                code="VALUATION_EXTREME", severity=RiskLevel.MEDIUM,
                message="Trading at an extremely expensive valuation relative to entered peer/historical multiples.",
            ))

        if dcf is not None and dcf.margin_of_safety_pct is not None and dcf.margin_of_safety_pct < _DCF_OVERVALUED_THRESHOLD_PCT:
            alerts.append(Alert(
                code="DCF_OVERVALUED", severity=RiskLevel.MEDIUM,
                message=f"Trading {abs(dcf.margin_of_safety_pct):.1f}% above the base-case DCF intrinsic value.",
            ))

        if days_to_next_results is not None and 0 <= days_to_next_results <= _EARNINGS_IMMINENT_DAYS:
            alerts.append(Alert(
                code="EARNINGS_IMMINENT", severity=RiskLevel.LOW,
                message=f"Results due in {days_to_next_results} day(s) - review pre-earnings analysis.",
            ))

        if event_impact is not None and event_impact.score < _NEGATIVE_MOMENTUM_THRESHOLD:
            alerts.append(Alert(
                code="NEGATIVE_EVENT_MOMENTUM", severity=RiskLevel.MEDIUM,
                message=f"Recent corporate actions skew bearish (event impact score {event_impact.score:.0f}).",
            ))

        severity_rank = {RiskLevel.EXTREME: 0, RiskLevel.HIGH: 1, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 3}
        alerts.sort(key=lambda a: severity_rank[a.severity])
        return alerts
