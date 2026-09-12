"""Business quality is inherently qualitative (moat, pricing power, brand, switching costs -
spec section 2) so this engine never invents a rating: it aggregates `QualitativeFactor` entries
an analyst/user has entered and cited (category="BUSINESS_QUALITY_MOAT"), each scored 0-100, into
one composite score and label.
"""
from typing import List

from app.core.enums import QualityLabel
from app.fundamentals.models import BusinessQualityResult, QualitativeFactor

MOAT_CATEGORY = "BUSINESS_QUALITY_MOAT"


def _label_from_score(score: float) -> QualityLabel:
    if score >= 80:
        return QualityLabel.STRONG
    if score >= 65:
        return QualityLabel.GOOD
    if score >= 50:
        return QualityLabel.AVERAGE
    if score >= 35:
        return QualityLabel.WEAK
    return QualityLabel.DETERIORATING


class BusinessQualityEngine:
    def analyze(self, factors: List[QualitativeFactor]) -> BusinessQualityResult:
        moat_factors = [f for f in factors if f.category == MOAT_CATEGORY and f.score is not None]
        if not moat_factors:
            raise ValueError(
                "No business-quality moat factors supplied - the platform never invents a moat "
                "rating; enter at least one QualitativeFactor(category='BUSINESS_QUALITY_MOAT', score=...)."
            )

        breakdown = {f.label: f.score for f in moat_factors}  # type: ignore[misc]
        score = sum(breakdown.values()) / len(breakdown)
        notes = [f.note for f in moat_factors if f.note]

        return BusinessQualityResult(score=round(score, 1), label=_label_from_score(score), factor_breakdown=breakdown, notes=notes)
