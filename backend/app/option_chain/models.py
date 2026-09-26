from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


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


class GreeksResult(BaseModel):
    """Black-Scholes Greeks for one option (app/option_chain/greeks.py). `implied_volatility`
    is either the caller-supplied one or solved from a real quoted price - never fabricated."""

    implied_volatility: float
    theoretical_price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 percentage point (0.01) change in implied volatility


class StrikeAnalysis(BaseModel):
    strike: float
    call_oi: Optional[float] = None
    call_change_oi: Optional[float] = None
    call_activity: OIActivity
    call_moneyness: Moneyness
    call_greeks: Optional[GreeksResult] = None
    put_oi: Optional[float] = None
    put_change_oi: Optional[float] = None
    put_activity: OIActivity
    put_moneyness: Moneyness
    put_greeks: Optional[GreeksResult] = None


class OptionLegInput(BaseModel):
    """One leg of a (possibly multi-leg) options position - the input to the standalone Greeks
    calculator (POST /api/option-chain/greeks). Exactly one of `option_ltp` (a real quoted
    price, from which IV is solved) or `implied_volatility` (supplied directly) must be given.
    `quantity` is signed: positive for long, negative for short (short options have inverted
    Greeks), already scaled by lot size - e.g. -75 for one short NIFTY lot.
    """

    strike: float
    option_type: OptionType
    quantity: int
    underlying_ltp: float
    expiry: str  # ISO date (YYYY-MM-DD)
    option_ltp: Optional[float] = None
    implied_volatility: Optional[float] = None
    as_of: Optional[str] = None  # ISO date; defaults to today when omitted


class LegGreeksResult(BaseModel):
    strike: float
    option_type: OptionType
    quantity: int
    greeks: GreeksResult
    # Position-level Greeks: the per-unit Greeks above scaled by `quantity`, so a short leg's
    # position_delta/gamma/theta/vega already carry the sign flip a short position implies.
    position_delta: float
    position_gamma: float
    position_theta: float
    position_vega: float


class StrategyGreeksResult(BaseModel):
    legs: List[LegGreeksResult]
    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float


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
