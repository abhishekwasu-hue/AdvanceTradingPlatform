import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
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


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICROSECOND = timedelta(microseconds=1)


def _timestamp_index(stamps: List[datetime]) -> pd.DatetimeIndex:
    """`pd.to_datetime(stamps)` as an index, faster for the usual case - every bar in the one timezone object: exact
    integer microseconds since the epoch, then the same timezone. pandas converts aware datetimes one Python object at a
    time, which was most of a chart's frame building. Anything else (naive, mixed or several zones) goes the old way,
    and so does a result whose type would differ from it."""
    tz = stamps[0].tzinfo
    if tz is not None and all(s.tzinfo is tz for s in stamps):
        expected = pd.to_datetime(pd.Series(stamps[:1])).dtype
        micros = np.fromiter(((s - _EPOCH) // _MICROSECOND for s in stamps), dtype=np.int64, count=len(stamps))
        unit = getattr(expected, "unit", "ns")
        fast = pd.DatetimeIndex(micros.astype("datetime64[us]").astype(f"datetime64[{unit}]")).tz_localize("UTC").tz_convert(tz)
        if fast.dtype == expected:
            return fast
    return pd.DatetimeIndex(pd.to_datetime(pd.Series(stamps)))


def bars_to_dataframe(bars: List[OHLCVBar]) -> pd.DataFrame:
    if not bars:
        df = pd.DataFrame([b.model_dump() for b in bars])        # as before: no columns (callers check for bars first)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        return df[["open", "high", "low", "close", "volume"]]
    # LD-7: built column by column from the attributes, with a fast exact timestamp index - the same frame as
    # `pd.DataFrame([b.model_dump() ...])` + `pd.to_datetime` (tests/test_ld7_api_processes.py), in a fraction of the time
    # on a chart's few thousand bars.
    df = pd.DataFrame({"open": [b.open for b in bars], "high": [b.high for b in bars], "low": [b.low for b in bars],
                       "close": [b.close for b in bars], "volume": [b.volume for b in bars]},
                      index=_timestamp_index([b.timestamp for b in bars]))
    df.index.name = "timestamp"
    return df.sort_index()[["open", "high", "low", "close", "volume"]]


# The signal intake routes (TradingView webhook, public API) refuse a level at or over this (#309 review). Not the
# PAPER ticket's 1e7: BTCINR trades above Rs 1 crore. It keeps every level well inside the trade columns' Numeric(18, 4).
MAX_SIGNAL_PRICE = 1e10


def unset_if_not_finite(value: Any) -> Any:
    """For an optional level of an intake payload: Pine Script renders an `na` plot as NaN, so a NaN (or infinite) value
    there means "not set" (None), not a refusal of the whole alert. Required levels are not passed through this."""
    if isinstance(value, str) and value.strip().lower().lstrip("+-") in ("nan", "inf", "infinity"):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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
    # Strategy templates (open question TPL-Q2): where a signal came from - the template id and version, the settings hash
    # and the setup instance - for the server only. Never serialised (`exclude`): no API answer, stored row, alert or
    # notification carries it, so a user never sees an internal id; the reasons hold only the structure-word sentence.
    provenance: Optional[Dict[str, str]] = Field(default=None, exclude=True)

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
    # Phase V1 (Risk Guardian): the rules the engine keeps whatever built the strategy.
    max_portfolio_risk_pct: float = 6.0    # R4: loss if every open stop hits + this trade, % of capital
    stop_cooldown_minutes: int = 30        # R10: no re-entry in an underlying this long after a stop-out
    dd_level_1_pct: float = 5.0            # P2: this far below the equity peak, risk per trade is halved
    dd_level_2_pct: float = 10.0           # P3: this far below, new entries pause until reviewed
    event_size_cut_pct: float = 50.0       # M8: default size cut on an event day flagged SIZE_CUT


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
    # None only for derived-contract trades (Phase F3), whose targets are on the underlying.
    target1: Optional[float] = None
    target2: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    charges: float = 0.0
    # Execution quality (master prompt V4.14): what the signal expected vs what filled, and how
    # long the entry took. None for backtest trades.
    expected_price: Optional[float] = None
    entry_latency_ms: Optional[int] = None


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
    # Phase J2: analytics views (monthly, day/hour, exit reasons, streaks, ratios, drawdown curve).
    analytics: Optional[Dict[str, Any]] = None
    exit_rules: Optional[str] = None
    run_id: Optional[int] = None
    # Phase W: set by the option backtest engine - pricing model, conventions, skipped signals,
    # per-structure legs (app/backtest/options_engine.py). None for an underlying backtest.
    options: Optional[Dict[str, Any]] = None
    # Realism C4: engine / code / data / config / result hashes - what produced this run (app/backtest/repro.py).
    reproducibility: Optional[Dict[str, Any]] = None
    # N7 C2c: what non-default execution models did (margin refusals and calls, model-priced strikes); None on a default
    # run (and then left out of the result hash, so default hashes stay as they were).
    execution: Optional[Dict[str, Any]] = None
