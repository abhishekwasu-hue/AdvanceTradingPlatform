from enum import Enum


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class SignalGrade(str, Enum):
    A1 = "A1"
    HIGH_QUALITY = "High Quality"
    VALID = "Valid"
    WEAK = "Weak"
    NO_TRADE = "No Trade"


class StrategyCategory(str, Enum):
    MULTI_TIMEFRAME = "multi_timeframe"
    INDICATOR_BASED = "indicator_based"


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED_TARGET = "CLOSED_TARGET"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_MANUAL = "CLOSED_MANUAL"


class CapCategory(str, Enum):
    LARGE_CAP = "LARGE_CAP"
    MID_CAP = "MID_CAP"
    SMALL_CAP = "SMALL_CAP"
    MICRO_CAP = "MICRO_CAP"


class PeriodType(str, Enum):
    QUARTER = "QUARTER"
    ANNUAL = "ANNUAL"
    TTM = "TTM"


class QualityLabel(str, Enum):
    """Generic 5-band quality label reused across business quality, earnings quality, etc."""

    STRONG = "Strong"
    GOOD = "Good"
    AVERAGE = "Average"
    WEAK = "Weak"
    DETERIORATING = "Deteriorating"


class TrendLabel(str, Enum):
    IMPROVING = "Improving"
    STABLE = "Stable"
    DETERIORATING = "Deteriorating"
    HIGHLY_VOLATILE = "Highly Volatile"


class ValuationLabel(str, Enum):
    DEEPLY_UNDERVALUED = "Deeply Undervalued"
    UNDERVALUED = "Undervalued"
    FAIRLY_VALUED = "Fairly Valued"
    EXPENSIVE = "Expensive"
    EXTREMELY_EXPENSIVE = "Extremely Expensive"


class EarningsGrowthVisibility(str, Enum):
    VERY_HIGH = "Very High"
    HIGH = "High"
    MODERATE = "Moderate"
    LOW = "Low"
    DETERIORATING = "Deteriorating"


class FundamentalGrade(str, Enum):
    EXCEPTIONAL = "Exceptional"
    STRONG = "Strong"
    GOOD = "Good"
    AVERAGE = "Average"
    WEAK = "Weak"
    POOR = "Poor"


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    EXTREME = "Extreme"


class Bias(str, Enum):
    BULLISH = "Bullish"
    NEUTRAL = "Neutral"
    BEARISH = "Bearish"


class FusionBias(str, Enum):
    A1_LONG_BIAS = "A1 LONG BIAS"
    A1_SHORT_BIAS = "A1 SHORT BIAS"
    WATCHLIST = "WATCHLIST"
    NO_TRADE = "NO TRADE"
    CAUTION = "CAUTION"


class InvestmentHorizon(str, Enum):
    INTRADAY = "Intraday"
    SWING = "Swing"
    POSITIONAL = "Positional"
    LONG_TERM = "Long Term"


class DataFreshness(str, Enum):
    LIVE = "LIVE"
    TODAY = "TODAY"
    THIS_WEEK = "THIS WEEK"
    RECENT = "RECENT"
    LAST_QUARTER = "LAST QUARTER"
    ANNUAL = "ANNUAL"
    HISTORICAL = "HISTORICAL"
    STALE = "STALE"


class ClaimType(str, Enum):
    """Fact vs Opinion (spec section 43): every claim the engines emit is tagged with one of
    these so the frontend never presents a computed estimate as if it were a filed fact.
    """

    FACT = "FACT"
    INTERPRETATION = "INTERPRETATION"
    ESTIMATE = "ESTIMATE"
    RISK = "RISK"


def grade_from_score(score: int) -> SignalGrade:
    if score >= 90:
        return SignalGrade.A1
    if score >= 80:
        return SignalGrade.HIGH_QUALITY
    if score >= 70:
        return SignalGrade.VALID
    if score >= 60:
        return SignalGrade.WEAK
    return SignalGrade.NO_TRADE
