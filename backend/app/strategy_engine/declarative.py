"""A declarative, condition-based strategy engine - the backend for the no-code Strategy
Builder. Instead of writing Python, a user composes AND-combined conditions comparing an
indicator against a fixed value or another indicator (optionally as a crossover), separately
for long and short entries. A DeclarativeStrategy instance produces a standard Signal through
the exact same BaseStrategy.build_signal() path every inbuilt strategy uses, so it works
transparently through /signal, /signal/enrich, /paper-execute and /backtest.
"""
from enum import Enum
from typing import Any, Dict, List, Literal, Tuple

import pandas as pd
from pydantic import BaseModel, Field

from app.core.enums import SignalDirection, StrategyCategory
from app.core.models import Signal
from app.indicators.directional import adx as adx_indicator
from app.indicators.momentum import rsi as rsi_indicator
from app.indicators.trend import ema, sma
from app.indicators.volatility import atr as atr_indicator
from app.indicators.volatility import supertrend as supertrend_indicator
from app.strategy_engine.base import BaseStrategy

IndicatorName = Literal["EMA", "SMA", "RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR", "SUPERTREND", "CLOSE", "OPEN", "HIGH", "LOW"]
Operator = Literal["GT", "LT", "GTE", "LTE", "CROSSES_ABOVE", "CROSSES_BELOW"]

_PRICE_COLUMNS = {"CLOSE": "close", "OPEN": "open", "HIGH": "high", "LOW": "low"}
_PERIODLESS = {"CLOSE", "OPEN", "HIGH", "LOW"}


class Operand(BaseModel):
    """Either a fixed numeric value, or an indicator computed from the candle series."""

    type: Literal["value", "indicator"]
    value: float = 0.0
    indicator: IndicatorName = "CLOSE"
    # A period of 0 (or negative) reaches pandas .ewm(alpha=1/period, ...)/.rolling(period) and
    # raises ZeroDivisionError/ValueError deep inside analyze() rather than at strategy-creation
    # time - bounding it here makes that a normal 422 on POST /api/custom-strategies instead.
    period: int = Field(default=14, gt=0, le=500)
    multiplier: float = Field(default=3.0, gt=0)  # only used by SUPERTREND

    def label(self) -> str:
        if self.type == "value":
            return f"{self.value:g}"
        if self.indicator in _PERIODLESS:
            return self.indicator
        if self.indicator == "SUPERTREND":
            return f"SUPERTREND({self.period},{self.multiplier:g})"
        return f"{self.indicator}({self.period})"

    def series(self, df: pd.DataFrame) -> pd.Series:
        if self.type == "value":
            return pd.Series(self.value, index=df.index)
        name = self.indicator
        if name in _PRICE_COLUMNS:
            return df[_PRICE_COLUMNS[name]]
        if name == "EMA":
            return ema(df["close"], self.period)
        if name == "SMA":
            return sma(df["close"], self.period)
        if name == "RSI":
            return rsi_indicator(df["close"], self.period)
        if name == "ATR":
            return atr_indicator(df, self.period)
        if name == "ADX":
            return adx_indicator(df, self.period)["adx"]
        if name == "PLUS_DI":
            return adx_indicator(df, self.period)["plus_di"]
        if name == "MINUS_DI":
            return adx_indicator(df, self.period)["minus_di"]
        if name == "SUPERTREND":
            return supertrend_indicator(df, self.period, self.multiplier)["supertrend"]
        raise ValueError(f"Unknown indicator: {name}")

    def warmup_bars(self) -> int:
        if self.type == "value" or self.indicator in _PERIODLESS:
            return 0
        return self.period + 5


class Condition(BaseModel):
    left: Operand
    operator: Operator
    right: Operand

    def label(self) -> str:
        symbols = {"GT": ">", "LT": "<", "GTE": ">=", "LTE": "<=", "CROSSES_ABOVE": "crosses above", "CROSSES_BELOW": "crosses below"}
        return f"{self.left.label()} {symbols[self.operator]} {self.right.label()}"

    def warmup_bars(self) -> int:
        return max(self.left.warmup_bars(), self.right.warmup_bars())

    def evaluate(self, df: pd.DataFrame) -> Tuple[bool, bool]:
        """Returns (holds, has_enough_data). `holds` is meaningless when has_enough_data is False."""
        left_s = self.left.series(df)
        right_s = self.right.series(df)
        needs_prior = self.operator in ("CROSSES_ABOVE", "CROSSES_BELOW")
        min_len = 2 if needs_prior else 1
        if len(df) < min_len:
            return False, False

        l_last, r_last = left_s.iloc[-1], right_s.iloc[-1]
        if needs_prior:
            l_prev, r_prev = left_s.iloc[-2], right_s.iloc[-2]
            if any(pd.isna(v) for v in (l_last, r_last, l_prev, r_prev)):
                return False, False
            if self.operator == "CROSSES_ABOVE":
                return (l_prev <= r_prev and l_last > r_last), True
            return (l_prev >= r_prev and l_last < r_last), True

        if any(pd.isna(v) for v in (l_last, r_last)):
            return False, False
        if self.operator == "GT":
            return l_last > r_last, True
        if self.operator == "LT":
            return l_last < r_last, True
        if self.operator == "GTE":
            return l_last >= r_last, True
        return l_last <= r_last, True


class CustomStrategyConfig(BaseModel):
    name: str
    timeframe: str = "5min"
    long_conditions: List[Condition] = Field(default_factory=list)
    short_conditions: List[Condition] = Field(default_factory=list)
    stop_loss_atr_mult: float = Field(default=1.0, gt=0)
    atr_period: int = Field(default=14, gt=0, le=500)
    target_rr: Tuple[float, float] = (1.5, 2.0)
    min_rr: float = Field(default=1.2, gt=0)

    def model_post_init(self, __context: Any) -> None:
        if not self.long_conditions and not self.short_conditions:
            raise ValueError("A custom strategy needs at least one long or short condition")


class DeclarativeStrategy(BaseStrategy):
    """Builds a Signal from a CustomStrategyConfig's AND-combined long/short condition sets."""

    category = StrategyCategory.INDICATOR_BASED

    def __init__(self, strategy_id: str, config: CustomStrategyConfig) -> None:
        self.id = strategy_id
        self.name = config.name
        self.description = "User-defined strategy built with the no-code Strategy Builder."
        self.timeframes = [config.timeframe]
        self.config = config
        super().__init__(min_rr=config.min_rr)

    def min_history(self) -> Dict[str, int]:
        tf = self.config.timeframe
        all_conditions = self.config.long_conditions + self.config.short_conditions
        needed = max((c.warmup_bars() for c in all_conditions), default=0)
        return {tf: max(needed, self.config.atr_period) + 5}

    def _side_holds(self, conditions: List[Condition], df: pd.DataFrame) -> Tuple[bool, List[str]]:
        if not conditions:
            return False, []
        labels: List[str] = []
        for condition in conditions:
            holds, enough = condition.evaluate(df)
            if not enough:
                return False, []
            if not holds:
                return False, []
            labels.append(condition.label())
        return True, labels

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])

        df = data[self.config.timeframe]
        timestamp = df.index[-1]
        close = df["close"].iloc[-1]
        atr_last = atr_indicator(df, self.config.atr_period).iloc[-1]

        if pd.isna(atr_last):
            return self.no_trade(symbol, timestamp, ["ATR not yet warmed up"])

        long_holds, long_labels = self._side_holds(self.config.long_conditions, df)
        if long_holds:
            stop_loss = close - atr_last * self.config.stop_loss_atr_mult
            return self.build_signal(
                symbol, timestamp, SignalDirection.LONG, close, stop_loss, score=70,
                reasons=[f"Long rule matched: {label}" for label in long_labels],
                target_rr=self.config.target_rr,
            )

        short_holds, short_labels = self._side_holds(self.config.short_conditions, df)
        if short_holds:
            stop_loss = close + atr_last * self.config.stop_loss_atr_mult
            return self.build_signal(
                symbol, timestamp, SignalDirection.SHORT, close, stop_loss, score=70,
                reasons=[f"Short rule matched: {label}" for label in short_labels],
                target_rr=self.config.target_rr,
            )

        return self.no_trade(symbol, timestamp, ["No custom rule set fully matched on the latest bar"])
