"""Pre-earnings analysis (spec section 31): a read on what to expect ahead of an upcoming
Results event, synthesized entirely from signals the other real engines already computed
(revenue/margin trend, red flags) - never a fabricated "Street expectation," since no analyst
consensus feed exists here.
"""
from datetime import date
from typing import List

from app.core.enums import Bias, RiskLevel, TrendLabel
from app.fundamentals.engines.red_flags import RedFlagEngine
from app.fundamentals.engines.statement_analysis import ProfitabilityEngine, RevenueAnalysisEngine
from app.fundamentals.models import FinancialPeriod, PreEarningsAnalysis, RedFlag


class PreEarningsEngine:
    def analyze(self, periods: List[FinancialPeriod], upcoming_event_date: date, red_flags: List[RedFlag]) -> PreEarningsAnalysis:
        if not periods:
            raise ValueError("No financial periods supplied - cannot assess pre-earnings risk without history")

        growth = RevenueAnalysisEngine().analyze(periods)
        profitability = ProfitabilityEngine().analyze(periods)

        revenue_positive = (growth.yoy_growth_pct or 0) > 0
        margin_improving = profitability.margin_trend == TrendLabel.IMPROVING
        margin_deteriorating = profitability.margin_trend == TrendLabel.DETERIORATING

        high_severity_flags = [f for f in red_flags if f.severity in (RiskLevel.HIGH, RiskLevel.EXTREME)]

        if revenue_positive and margin_improving and not high_severity_flags:
            bias = Bias.BULLISH
        elif not revenue_positive and margin_deteriorating:
            bias = Bias.BEARISH
        elif high_severity_flags:
            bias = Bias.BEARISH
        else:
            bias = Bias.NEUTRAL

        if len(high_severity_flags) >= 2:
            risk = RiskLevel.EXTREME
        elif high_severity_flags:
            risk = RiskLevel.HIGH
        elif margin_deteriorating or not revenue_positive:
            risk = RiskLevel.MEDIUM
        else:
            risk = RiskLevel.LOW

        note = (
            f"Revenue trend is {'positive' if revenue_positive else 'negative'} "
            f"(YoY {growth.yoy_growth_pct:.1f}%)" if growth.yoy_growth_pct is not None else "Revenue trend unavailable"
        )
        note += f"; margin trend is {profitability.margin_trend.value.lower()}"
        note += f"; {len(red_flags)} red flag(s) on record ({len(high_severity_flags)} high/extreme severity)."

        return PreEarningsAnalysis(
            upcoming_event_date=upcoming_event_date, revenue_trend_note=growth.note,
            margin_trend_note=profitability.revenue_vs_margin_note, red_flag_count=len(red_flags),
            earnings_bias=bias, risk_level=risk, note=note,
        )
