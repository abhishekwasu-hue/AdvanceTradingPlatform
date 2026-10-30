import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

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
    # DEF-0 (owner, 28 Oct): every limit below is the trader's own setting; None = not set = off (no limit, no gate).
    # Nothing is pre-filled from code or config. `capital` None = not set: a % shown or checked then reads the PAPER
    # account balance where the caller has one (`with_capital`), else the %-based rules are off.
    capital: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    max_open_positions: Optional[int] = None
    max_consecutive_losses: Optional[int] = None
    # TT12-4 (owner, 24 Oct): no built-in minimum reward:risk - 0 = not set (no minimum), on LIVE, paper and both
    # backtest engines alike. Only a value the organisation sets itself filters entries (never exits, ADR-0004).
    min_risk_reward: float = 0.0
    lot_size: int = 1
    # Phase V1 (Risk Guardian): the rules the engine keeps whatever built the strategy - each off until set (DEF-0).
    max_portfolio_risk_pct: Optional[float] = None    # R4: loss if every open stop hits + this trade, % of capital
    stop_cooldown_minutes: Optional[int] = None       # R10: no re-entry in an underlying this long after a stop-out
    dd_level_1_pct: Optional[float] = None            # P2: this far below the equity peak, risk per trade is halved
    dd_level_2_pct: Optional[float] = None            # P3: this far below, new entries pause until reviewed
    event_size_cut_pct: Optional[float] = None        # M8: size cut on an event day flagged SIZE_CUT
    # CP6-15: when the losses-in-a-row pause ends - at the next Indian trading session, or when the trader resumes
    # (app/risk_engine/loss_streak.py). Only used once `max_consecutive_losses` is set.
    loss_streak_pause: Literal["next_session", "until_resume"] = "next_session"

    def with_capital(self, balance: Optional[float]) -> "RiskConfig":
        """DEF-0 D1: the trader's own capital when set, else `balance` (the PAPER account balance) - never a built-in
        figure. Unchanged when neither is known."""
        if self.capital is not None or balance is None or not balance > 0:
            return self
        return self.model_copy(update={"capital": float(balance)})

    def risk_amount(self) -> Optional[float]:
        """Risk per trade in money, or None when the trader set no risk per trade (or no capital is known)."""
        if self.risk_per_trade_pct is None or self.capital is None:
            return None
        return self.capital * self.risk_per_trade_pct / 100.0

    def daily_loss_amount(self) -> Optional[float]:
        """The daily loss limit in money (positive), or None when not set."""
        if self.max_daily_loss_pct is None or self.capital is None:
            return None
        return abs(self.capital * self.max_daily_loss_pct / 100.0)


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
    # M55 T3: where the candles came from, as the server loaded them (broker:<name> / lake:daily) or "uploaded".
    data_source: str | None = None
    # Phase W: set by the option backtest engine - pricing model, conventions, skipped signals,
    # per-structure legs (app/backtest/options_engine.py). None for an underlying backtest.
    options: Optional[Dict[str, Any]] = None
    # Realism C4: engine / code / data / config / result hashes - what produced this run (app/backtest/repro.py).
    reproducibility: Optional[Dict[str, Any]] = None
    # N7 C2c: what non-default execution models did (margin refusals and calls, model-priced strikes); None on a default
    # run (and then left out of the result hash, so default hashes stay as they were).
    execution: Optional[Dict[str, Any]] = None
    # CP6-15: what the losses-in-a-row pause did in this run (app/risk_engine/loss_streak.BacktestLossStreak.report); None
    # when the limit was never reached (and then left out of the result hash).
    loss_streak: Optional[Dict[str, Any]] = None
    # REV-S3: a template run's history before the test window (app/backtest/warmup.py): {"note": one plain sentence or None,
    # "needed_days", "warmup_days"}; None for every other strategy. Never in the result hash (the data hash covers the bars).
    history: dict[str, Any] | None = None
