from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class Moneyness(str, Enum):
    ITM = "ITM"
    ATM = "ATM"
    OTM = "OTM"


class OIActivity(str, Enum):
    CALL_WRITING = "CALL_WRITING"
    CALL_UNWINDING = "CALL_UNWINDING"
    PUT_WRITING = "PUT_WRITING"
    PUT_UNWINDING = "PUT_UNWINDING"
    FLAT = "FLAT"


class OptionChainBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    CONFLICTING = "CONFLICTING"


class StrikeAnalysis(BaseModel):
    strike: float
    call_oi: Optional[float] = None
    call_change_oi: Optional[float] = None
    call_activity: OIActivity
    call_moneyness: Moneyness
    put_oi: Optional[float] = None
    put_change_oi: Optional[float] = None
    put_activity: OIActivity
    put_moneyness: Moneyness


class OptionChainAnalysis(BaseModel):
    underlying: str
    expiry: str
    underlying_ltp: Optional[float] = None
    atm_strike: Optional[float] = None
    max_pain: Optional[float] = None
    pcr: Optional[float] = None
    total_call_oi: float = 0.0
    total_put_oi: float = 0.0
    total_call_oi_change: Optional[float] = None
    total_put_oi_change: Optional[float] = None
    bias: OptionChainBias
    bias_reasons: List[str]
    call_resistance_strikes: List[float]
    put_support_strikes: List[float]
    strikes: List[StrikeAnalysis]
