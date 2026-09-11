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
