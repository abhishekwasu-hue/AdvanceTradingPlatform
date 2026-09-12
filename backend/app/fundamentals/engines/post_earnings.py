"""Post-earnings analysis - the follow-up to PreEarningsEngine once results are actually in.
Compares the just-reported actual revenue/PAT against this company's own historical growth
trend (the average YoY growth of the periods before it), never against an external analyst
consensus - no such feed exists here, so "expected" only ever means this company's own trend.
"""
from datetime import date
from typing import List

from app.core.enums import Bias
from app.fundamentals.engines.statement_analysis import EarningsQualityEngine
from app.fundamentals.models import FinancialPeriod, PostEarningsAnalysis

_BEAT_THRESHOLD_PCT = 3.0
_MISS_THRESHOLD_PCT = -3.0


def _avg_yoy_growth_pct(values: List[float]) -> float:
    growths = [
        (values[i] - values[i - 1]) / values[i - 1] * 100.0
        for i in range(1, len(values))
        if values[i - 1]
    ]
    return sum(growths) / len(growths) if growths else 0.0


class PostEarningsEngine:
    def analyze(self, periods: List[FinancialPeriod], results_event_date: date) -> PostEarningsAnalysis:
        if len(periods) < 3:
            raise ValueError(
                "Post-earnings analysis needs at least 3 financial periods - two prior periods "
                "to establish a trend, plus the just-reported actual result."
            )
        ordered = sorted(periods, key=lambda p: p.period_end_date)
        latest = ordered[-1]
        prior = ordered[:-1]

        revenue_growth_pct = _avg_yoy_growth_pct([p.revenue for p in prior])
        pat_growth_pct = _avg_yoy_growth_pct([p.pat for p in prior])

        revenue_expected = prior[-1].revenue * (1 + revenue_growth_pct / 100.0)
        pat_expected = prior[-1].pat * (1 + pat_growth_pct / 100.0)

        revenue_surprise = (latest.revenue - revenue_expected) / revenue_expected * 100.0 if revenue_expected else 0.0
        pat_surprise = (latest.pat - pat_expected) / pat_expected * 100.0 if pat_expected else 0.0

        earnings_quality = EarningsQualityEngine().analyze(latest).label

        if revenue_surprise >= _BEAT_THRESHOLD_PCT and pat_surprise >= _BEAT_THRESHOLD_PCT:
            verdict = Bias.BULLISH
        elif revenue_surprise <= _MISS_THRESHOLD_PCT or pat_surprise <= _MISS_THRESHOLD_PCT:
            verdict = Bias.BEARISH
        else:
            verdict = Bias.NEUTRAL

        note = (
            f"Revenue {'beat' if revenue_surprise >= 0 else 'missed'} its own trend-implied expectation by "
            f"{revenue_surprise:.1f}%; PAT {'beat' if pat_surprise >= 0 else 'missed'} by {pat_surprise:.1f}%. "
            "\"Expected\" is this company's own historical growth trend, not an external analyst "
            "consensus - no such feed exists here."
        )

        return PostEarningsAnalysis(
            results_event_date=results_event_date, revenue_actual=latest.revenue,
            revenue_expected_trend=round(revenue_expected, 2), revenue_surprise_pct=round(revenue_surprise, 1),
            pat_actual=latest.pat, pat_expected_trend=round(pat_expected, 2), pat_surprise_pct=round(pat_surprise, 1),
            earnings_quality=earnings_quality, verdict=verdict, note=note,
        )
