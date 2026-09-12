"""SWOT (spec section 21): strengths/weaknesses are partly derived from computed metrics
(e.g. a balance-sheet debt-risk reading becomes a weakness bullet automatically) and partly from
cited qualitative bullets an analyst entered (category SWOT_STRENGTH/WEAKNESS/OPPORTUNITY/
THREAT) - opportunities and threats are always qualitative/forward-looking, so they only ever
come from the latter; the engine never invents a forward-looking claim.
"""
from typing import List, Optional

from app.core.enums import QualityLabel, RiskLevel, TrendLabel
from app.fundamentals.models import (
    BalanceSheetAnalysis,
    EarningsQualityResult,
    ProfitabilityAnalysis,
    QualitativeFactor,
    SWOTResult,
)

_QUAL_MAP = {
    "SWOT_STRENGTH": "strengths",
    "SWOT_WEAKNESS": "weaknesses",
    "SWOT_OPPORTUNITY": "opportunities",
    "SWOT_THREAT": "threats",
}


class SWOTEngine:
    def analyze(
        self,
        qualitative_factors: List[QualitativeFactor],
        profitability: Optional[ProfitabilityAnalysis] = None,
        balance_sheet: Optional[BalanceSheetAnalysis] = None,
        earnings_quality: Optional[EarningsQualityResult] = None,
    ) -> SWOTResult:
        buckets = {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}

        for factor in qualitative_factors:
            key = _QUAL_MAP.get(factor.category)
            if key:
                buckets[key].append(factor.label if not factor.note else f"{factor.label}: {factor.note}")

        if profitability:
            if profitability.margin_trend == TrendLabel.IMPROVING:
                buckets["strengths"].append("Margins are expanding while revenue grows.")
            elif profitability.margin_trend == TrendLabel.DETERIORATING:
                buckets["weaknesses"].append("Margins are compressing.")
            elif profitability.margin_trend == TrendLabel.HIGHLY_VOLATILE:
                buckets["weaknesses"].append("Margins are highly volatile quarter to quarter.")

        if balance_sheet:
            if balance_sheet.debt_risk in (RiskLevel.HIGH, RiskLevel.EXTREME):
                buckets["weaknesses"].append(f"Elevated balance-sheet debt risk ({balance_sheet.debt_risk.value}).")
            elif balance_sheet.debt_risk == RiskLevel.LOW and balance_sheet.net_debt_to_ebitda is not None:
                buckets["strengths"].append("Low leverage relative to EBITDA.")

        if earnings_quality:
            if earnings_quality.label == QualityLabel.STRONG:
                buckets["strengths"].append("Earnings are backed by strong operating cash flow conversion.")
            elif earnings_quality.warnings:
                buckets["weaknesses"].append("Earnings quality concerns - see Earnings Quality section.")

        return SWOTResult(**buckets)
