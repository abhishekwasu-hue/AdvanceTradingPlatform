from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from pydantic import BaseModel, Field

from app.core.enums import SignalDirection, SignalGrade, StrategyCategory


class OHLCVBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def bars_to_dataframe(bars: List[OHLCVBar]) -> pd.DataFrame:
    df = pd.DataFrame([b.model_dump() for b in bars])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


class Signal(BaseModel):
    symbol: str
    strategy_id: str
    strategy_name: str
    direction: SignalDirection
    timestamp: datetime
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    target1: Optional[float] = None
    target2: Optional[float] = None
    risk_reward: Optional[float] = None
    score: int = 0
    grade: SignalGrade = SignalGrade.NO_TRADE
    reasons: List[str] = Field(default_factory=list)
    timeframe_combo: str = ""

    @property
    def is_tradeable(self) -> bool:
        return self.direction != SignalDirection.NO_TRADE


class StrategyInfo(BaseModel):
    id: str
    name: str
    description: str
    category: StrategyCategory
    timeframes: List[str]
    default_params: Dict[str, Any]


class RiskConfig(BaseModel):
    capital: float = 100_000.0
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 3.0
    max_trades_per_day: int = 20
    max_open_positions: int = 3
    max_consecutive_losses: int = 4
    min_risk_reward: float = 1.2
    lot_size: int = 1


class RiskDecision(BaseModel):
    approved: bool
    # float, not int: a crypto position sizes in fractional units (e.g. 0.0043 BTC) - see
    # app/instruments/registry.py and RiskManager.validate_and_size's `contract_spec` parameter.
    # Every non-fractional instrument (equity, index options, MCX) still always lands on a whole
    # multiple of its lot size; this only widens the type, it doesn't change equity behavior.
    quantity: float = 0.0
    reasons: List[str] = Field(default_factory=list)


class Trade(BaseModel):
    symbol: str
    strategy_id: str
    direction: SignalDirection
    entry_time: datetime
    entry_price: float
    quantity: float
    stop_loss: float
    target1: float
    target2: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    charges: float = 0.0


class BacktestResult(BaseModel):
    strategy_id: str
    symbol: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: Optional[float] = None
    max_drawdown: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    trades: List[Trade] = Field(default_factory=list)
    equity_curve: List[float] = Field(default_factory=list)
