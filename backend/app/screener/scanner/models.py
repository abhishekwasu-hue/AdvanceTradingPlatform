from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.brokers.models import OptionChain
from app.core.models import OHLCVBar
from app.strategy_engine.declarative import Condition


class StructureFilterType(str, Enum):
    TREND_UPTREND = "TREND_UPTREND"
    TREND_DOWNTREND = "TREND_DOWNTREND"
    TREND_RANGE = "TREND_RANGE"
    BOS_BULLISH = "BOS_BULLISH"
    BOS_BEARISH = "BOS_BEARISH"
    CHOCH_BULLISH = "CHOCH_BULLISH"
    CHOCH_BEARISH = "CHOCH_BEARISH"
    PATTERN_BULLISH = "PATTERN_BULLISH"
    PATTERN_BEARISH = "PATTERN_BEARISH"
    NEAR_SUPPORT = "NEAR_SUPPORT"
    NEAR_RESISTANCE = "NEAR_RESISTANCE"


class StructureFilter(BaseModel):
    filter_type: StructureFilterType
    # Only used by NEAR_SUPPORT/NEAR_RESISTANCE: how close (as a % of price) the latest close
    # must be to a zone's edge to count as "near" it.
    tolerance_pct: float = Field(default=0.5, gt=0)

    def label(self) -> str:
        return self.filter_type.value.replace("_", " ").title()


class OptionFilterType(str, Enum):
    PCR = "PCR"
    BIAS_BULLISH = "BIAS_BULLISH"
    BIAS_BEARISH = "BIAS_BEARISH"
    NEAR_MAX_PAIN = "NEAR_MAX_PAIN"


class OptionFilter(BaseModel):
    filter_type: OptionFilterType
    # PCR only:
    operator: Optional[Literal["GT", "LT", "GTE", "LTE"]] = None
    value: Optional[float] = None
    # NEAR_MAX_PAIN only: how close (as a % of underlying price) to max pain counts as "near".
    tolerance_pct: float = Field(default=1.0, gt=0)

    def label(self) -> str:
        if self.filter_type == OptionFilterType.PCR:
            return f"PCR {self.operator} {self.value:g}"
        return self.filter_type.value.replace("_", " ").title()


class ScannerSymbolInput(BaseModel):
    symbol: str
    timeframe: str = "5min"
    candles: List[OHLCVBar]
    option_chain: Optional[OptionChain] = None


class ScannerRequest(BaseModel):
    symbols: List[ScannerSymbolInput]
    # AND-combined across every supplied filter, across all three categories - a symbol has to
    # clear every filter the caller configured to appear in the results at all.
    indicator_conditions: List[Condition] = Field(default_factory=list)
    structure_filters: List[StructureFilter] = Field(default_factory=list)
    option_filters: List[OptionFilter] = Field(default_factory=list)
    swing_window: int = Field(default=3, gt=0)


class ScannerMatch(BaseModel):
    symbol: str
    close: float
    matched_indicator_labels: List[str] = Field(default_factory=list)
    matched_structure_labels: List[str] = Field(default_factory=list)
    matched_option_labels: List[str] = Field(default_factory=list)


class ScannerResult(BaseModel):
    scanned_count: int
    matched_count: int
    matches: List[ScannerMatch]
