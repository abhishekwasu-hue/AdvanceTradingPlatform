from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.core.enums import SignalDirection, StrategyCategory, grade_from_score
from app.core.models import Signal, StrategyInfo


class BaseStrategy(ABC):
    id: str
    name: str
    description: str
    category: StrategyCategory
    timeframes: List[str]
    default_params: Dict[str, Any] = {}

    def __init__(self, **params: Any) -> None:
        self.params: Dict[str, Any] = {**self.default_params, **params}

    @abstractmethod
    def min_history(self) -> Dict[str, int]:
        """Minimum number of bars required for each timeframe key this strategy consumes."""

    @abstractmethod
    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        """data maps timeframe label (e.g. '1min', '5min') to an OHLCV DataFrame sorted ascending."""

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            id=self.id,
            name=self.name,
            description=self.description,
            category=self.category,
            timeframes=self.timeframes,
            default_params=self.params,
        )

    def has_enough_history(self, data: Dict[str, pd.DataFrame]) -> bool:
        required = self.min_history()
        for tf, min_len in required.items():
            frame = data.get(tf)
            if frame is None or len(frame) < min_len:
                return False
        return True

    def no_trade(self, symbol: str, timestamp: datetime, reasons: List[str]) -> Signal:
        return Signal(
            symbol=symbol,
            strategy_id=self.id,
            strategy_name=self.name,
            direction=SignalDirection.NO_TRADE,
            timestamp=timestamp,
            score=0,
            grade=grade_from_score(0),
            reasons=reasons,
            timeframe_combo="/".join(self.timeframes),
        )

    def build_signal(
        self,
        symbol: str,
        timestamp: datetime,
        direction: SignalDirection,
        entry: float,
        stop_loss: float,
        score: int,
        reasons: List[str],
        target_rr: Tuple[float, float] = (1.5, 2.5),
    ) -> Signal:
        if direction == SignalDirection.LONG:
            risk = entry - stop_loss
        else:
            risk = stop_loss - entry

        if risk <= 0:
            return self.no_trade(symbol, timestamp, reasons + ["Invalid stop loss produced non-positive risk"])

        rr1, rr2 = target_rr
        if direction == SignalDirection.LONG:
            target1 = entry + rr1 * risk
            target2 = entry + rr2 * risk
        else:
            target1 = entry - rr1 * risk
            target2 = entry - rr2 * risk

        min_rr = self.params.get("min_rr", 1.2)
        if rr1 < min_rr:
            reasons = reasons + [f"Risk/Reward {rr1:.2f} below minimum {min_rr:.2f}"]
            return self.no_trade(symbol, timestamp, reasons)

        return Signal(
            symbol=symbol,
            strategy_id=self.id,
            strategy_name=self.name,
            direction=direction,
            timestamp=timestamp,
            entry=round(entry, 2),
            stop_loss=round(stop_loss, 2),
            target1=round(target1, 2),
            target2=round(target2, 2),
            risk_reward=round(rr1, 2),
            score=score,
            grade=grade_from_score(score),
            reasons=reasons,
            timeframe_combo="/".join(self.timeframes),
        )
