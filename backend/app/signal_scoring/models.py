from typing import Dict, List

from pydantic import BaseModel

from app.core.enums import SignalGrade
from app.core.models import Signal


class ScoreComponent(BaseModel):
    pct: float  # 0-100, how well this factor supports the signal on its own scale
    weight: int  # this factor's weight out of 100 in the composite
    contribution: float  # pct/100 * weight
    note: str


class EnrichedSignal(BaseModel):
    signal: Signal
    composite_score: int
    grade: SignalGrade
    breakdown: Dict[str, ScoreComponent]
    confirmations: List[str]
