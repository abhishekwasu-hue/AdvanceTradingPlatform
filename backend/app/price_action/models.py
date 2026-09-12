from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel


class TrendState(str, Enum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE = "RANGE"


class SwingPoint(BaseModel):
    timestamp: datetime
    price: float
    kind: Literal["HIGH", "LOW"]
    label: Optional[Literal["HH", "HL", "LH", "LL"]] = None


class StructureEvent(BaseModel):
    timestamp: datetime
    event: Literal["BOS", "CHoCH"]
    direction: Literal["BULLISH", "BEARISH"]
    level: float
    note: str


class MarketStructureResult(BaseModel):
    trend: TrendState
    swings: List[SwingPoint]
    events: List[StructureEvent]


class PatternMatch(BaseModel):
    timestamp: datetime
    pattern: str
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    confidence: int
