"""The composite Fundamental Score (spec section 35) and the Fundamental + Technical Fusion
engine (spec section 36) that combines it with this platform's existing technical Signal
Scoring Engine (app/signal_scoring/) into one final trading bias, per the decision matrix in
spec section 48.
"""
from typing import Dict, Optional

from app.core.enums import (
    FundamentalGrade,
    FusionBias,
    InvestmentHorizon,
    QualityLabel,
    RiskLevel,
    SignalDirection,
    ValuationLabel,
)
from app.fundamentals.models import FundamentalScoreBreakdown, FundamentalScoreResult, FusionResult

# Exact weights from spec section 35 - sums to 100.
WEIGHTS: Dict[str, float] = {
    "business_quality": 15.0,
    "earnings_quality": 15.0,
    "growth": 15.0,
    "profitability": 10.0,
    "balance_sheet": 10.0,
    "cash_flow": 10.0,
    "management": 10.0,
    "sector_outlook": 5.0,
    "valuation": 5.0,
    "macro_event_risk": 5.0,
}

_COMPONENT_LABELS = {
    "business_quality": "Business Quality",
    "earnings_quality": "Earnings Quality",
    "growth": "Growth",
    "profitability": "Profitability",
    "balance_sheet": "Balance Sheet",
    "cash_flow": "Cash Flow",
    "management": "Management",
    "sector_outlook": "Sector Outlook",
    "valuation": "Valuation",
    "macro_event_risk": "Macro/Event Risk",
}


def grade_from_score(score: float) -> FundamentalGrade:
    if score >= 90:
        return FundamentalGrade.EXCEPTIONAL
    if score >= 80:
        return FundamentalGrade.STRONG
    if score >= 70:
        return FundamentalGrade.GOOD
    if score >= 60:
        return FundamentalGrade.AVERAGE
    if score >= 50:
        return FundamentalGrade.WEAK
    return FundamentalGrade.POOR


def score_from_quality_label(label: QualityLabel) -> float:
    return {
        QualityLabel.STRONG: 90.0, QualityLabel.GOOD: 72.0, QualityLabel.AVERAGE: 55.0,
        QualityLabel.WEAK: 35.0, QualityLabel.DETERIORATING: 15.0,
    }[label]


def score_from_risk_level(level: RiskLevel) -> float:
    """Inverted: low risk -> high score, since a risk engine outputs risk, not quality."""
    return {RiskLevel.LOW: 90.0, RiskLevel.MEDIUM: 60.0, RiskLevel.HIGH: 30.0, RiskLevel.EXTREME: 10.0}[level]


def score_from_valuation_label(label: ValuationLabel) -> float:
    return {
        ValuationLabel.DEEPLY_UNDERVALUED: 95.0, ValuationLabel.UNDERVALUED: 80.0,
        ValuationLabel.FAIRLY_VALUED: 65.0, ValuationLabel.EXPENSIVE: 35.0,
        ValuationLabel.EXTREMELY_EXPENSIVE: 15.0,
    }[label]


class FundamentalScoreEngine:
    def compute(self, components: Dict[str, float], notes: Optional[Dict[str, str]] = None) -> FundamentalScoreResult:
        """`components` must supply a 0-100 score for every key in WEIGHTS. A component the
        caller genuinely has no data for should be omitted rather than guessed - it is then
        scored as 50 (neutral) and flagged in the note, never silently invented as something
        more specific.
        """
        notes = notes or {}
        breakdown = []
        total = 0.0
        for key, weight in WEIGHTS.items():
            score = components.get(key)
            note = notes.get(key, "")
            if score is None:
                score = 50.0
                note = note or "No data supplied for this component - scored neutral (50), not estimated."
            contribution = score * weight / 100.0
            total += contribution
            breakdown.append(FundamentalScoreBreakdown(
                component=_COMPONENT_LABELS[key], weight_pct=weight, score_0_100=round(score, 1),
                contribution=round(contribution, 2), note=note,
            ))

        return FundamentalScoreResult(score=round(total, 1), grade=grade_from_score(total), breakdown=breakdown)


class FusionEngine:
    """Combines the Fundamental Score with the existing technical composite score (from
    app/signal_scoring/) into one Final Composite Score and bias label, per the decision matrix
    in spec section 48: Strong+Strong -> A1 (subject to a stricter overall threshold), Strong
    technical alone -> CAUTION, strong fundamental alone -> WATCHLIST, both weak -> NO TRADE.
    """

    STRONG_THRESHOLD = 70.0
    A1_THRESHOLD = 85.0

    def compute(
        self,
        fundamental_score: float,
        technical_score: float,
        technical_direction: SignalDirection,
        sector_score: Optional[float] = None,
        event_risk_score: Optional[float] = None,
        horizon: InvestmentHorizon = InvestmentHorizon.POSITIONAL,
    ) -> FusionResult:
        scores = [fundamental_score, technical_score]
        if sector_score is not None:
            scores.append(sector_score)
        if event_risk_score is not None:
            scores.append(event_risk_score)
        final_score = sum(scores) / len(scores)

        if technical_direction == SignalDirection.NO_TRADE:
            bias = FusionBias.NO_TRADE
            note = "Technical engine has no tradeable signal right now - fundamentals are informational context only."
        else:
            fund_strong = fundamental_score >= self.STRONG_THRESHOLD
            tech_strong = technical_score >= self.STRONG_THRESHOLD
            if fund_strong and tech_strong:
                if final_score >= self.A1_THRESHOLD:
                    bias = FusionBias.A1_LONG_BIAS if technical_direction == SignalDirection.LONG else FusionBias.A1_SHORT_BIAS
                    note = f"Both fundamental ({fundamental_score:.0f}) and technical ({technical_score:.0f}) are strong and the composite clears the A1 bar."
                else:
                    bias = FusionBias.WATCHLIST
                    note = "Both fundamental and technical are strong individually, but the composite score is below the A1 threshold - worth watching."
            elif tech_strong and not fund_strong:
                bias = FusionBias.CAUTION
                note = f"Technical signal is strong but fundamentals ({fundamental_score:.0f}) are weak - trade the setup cautiously, if at all."
            elif fund_strong and not tech_strong:
                bias = FusionBias.WATCHLIST
                note = f"Fundamentals ({fundamental_score:.0f}) are strong but there's no strong technical trigger yet - a name to watch for an entry."
            else:
                bias = FusionBias.NO_TRADE
                note = "Neither fundamentals nor the technical setup are strong enough to act on."

        return FusionResult(
            fundamental_score=round(fundamental_score, 1), technical_score=round(technical_score, 1),
            sector_score=sector_score, event_risk_score=event_risk_score, final_score=round(final_score, 1),
            bias=bias, horizon=horizon, note=note,
        )
