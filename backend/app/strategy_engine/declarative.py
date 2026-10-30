"""A declarative, condition-based strategy engine - the backend for the no-code Strategy
Builder. Instead of writing Python, a user composes AND-combined conditions comparing an
indicator against a fixed value or another indicator (optionally as a crossover), separately
for long and short entries. A DeclarativeStrategy instance produces a standard Signal through
the exact same BaseStrategy.build_signal() path every inbuilt strategy uses, so it works
transparently through /signal, /signal/enrich, /paper-execute and /backtest.

Phase AW: the operands a market strategist needs - session levels (VWAP, the day's open, the
opening range, the previous day's high / low / close), Bollinger bands and volume - and a
higher-timeframe filter on any operand (`timeframe`, e.g. "EMA(50) on 15min"). A higher-timeframe
value is the last *completed* higher-timeframe bar at the close of the base bar, resampled from the
base candles, so live trading and backtests see exactly the same thing and never the future.
"""
import math
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.enums import SignalDirection, StrategyCategory, grade_from_score
from app.core.models import Signal
from app.indicators.directional import adx as adx_indicator
from app.indicators.momentum import rsi as rsi_indicator
from app.indicators.trend import ema, sma
from app.indicators.volatility import atr as atr_indicator
from app.indicators.volatility import supertrend as supertrend_indicator
from app.strategy_engine.base import BaseStrategy

IndicatorName = Literal["EMA", "SMA", "RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR", "SUPERTREND", "CLOSE", "OPEN", "HIGH", "LOW",
                        "VWAP", "DAY_OPEN", "OR_HIGH", "OR_LOW", "PDH", "PDL", "PDC", "BB_UPPER", "BB_MID", "BB_LOWER", "VOLUME", "VOLUME_SMA"]
Operator = Literal["GT", "LT", "GTE", "LTE", "CROSSES_ABOVE", "CROSSES_BELOW"]

_PRICE_COLUMNS = {"CLOSE": "close", "OPEN": "open", "HIGH": "high", "LOW": "low"}
_PERIODLESS = {"CLOSE", "OPEN", "HIGH", "LOW", "VWAP", "DAY_OPEN", "PDH", "PDL", "PDC", "VOLUME"}
_SESSION = {"VWAP", "DAY_OPEN", "OR_HIGH", "OR_LOW", "PDH", "PDL", "PDC"}
_IST_OPEN_UTC = pd.Timedelta(hours=3, minutes=45)   # 09:15 IST
_IST_CLOSE_UTC = pd.Timedelta(hours=10)              # 15:30 IST
_SESSION_MINUTES = 375                                # 09:15-15:30


def tf_minutes(tf: Optional[str]) -> Optional[int]:
    if not tf:
        return None
    tf = tf.strip().lower()
    if tf in ("day", "1d"):
        return 375
    m = re.fullmatch(r"(\d+)\s*(min|m)", tf)
    return int(m.group(1)) if m else None


def _utc_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(df.index)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def _sessions(df: pd.DataFrame) -> pd.Series:
    """The IST trading date of each bar."""
    return pd.Series(_utc_index(df).tz_convert("Asia/Kolkata").date, index=df.index)


def _base_minutes(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 1.0
    diffs = pd.Series(_utc_index(df)).diff().dropna()
    diffs = diffs[diffs > pd.Timedelta(0)]
    return max(1.0, diffs.min().total_seconds() / 60.0) if len(diffs) else 1.0


def _session_vwap(df: pd.DataFrame, sess: pd.Series) -> pd.Series:
    """VWAP per session; a symbol without volume (an index) gets the session's running average price."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)
    cum_v = vol.groupby(sess).cumsum()
    vwap = (tp * vol).groupby(sess).cumsum() / cum_v.where(cum_v > 0)
    twap = tp.groupby(sess).expanding().mean().reset_index(level=0, drop=True).reindex(df.index)
    return vwap.where(cum_v > 0, twap).astype(float)


def _prev_session(df: pd.DataFrame, sess: pd.Series, column: str, how: str) -> pd.Series:
    per_day = getattr(df[column].groupby(sess), how)()
    return sess.map(per_day.shift(1)).astype(float)


def _opening_range(df: pd.DataFrame, sess: pd.Series, minutes: int, column: str) -> pd.Series:
    """The opening range's high (or low), visible only from the close of the bar that completes it."""
    idx = _utc_index(df)
    first = pd.Series(idx, index=df.index).groupby(sess).transform("min")
    base = pd.Timedelta(minutes=_base_minutes(df))
    start = pd.Series(idx, index=df.index)
    in_range = start < first + pd.Timedelta(minutes=minutes)
    agg = "max" if column == "high" else "min"
    level = df[column].where(in_range).groupby(sess).transform(agg)
    complete = start + base >= first + pd.Timedelta(minutes=minutes)
    return level.where(complete).astype(float)


class Operand(BaseModel):
    """Either a fixed numeric value, or an indicator computed from the candle series."""

    type: Literal["value", "indicator"]
    value: float = 0.0
    indicator: IndicatorName = "CLOSE"
    # A period of 0 (or negative) reaches pandas .ewm(alpha=1/period, ...)/.rolling(period) and
    # raises ZeroDivisionError/ValueError deep inside analyze() rather than at strategy-creation
    # time - bounding it here makes that a normal 422 on POST /api/custom-strategies instead.
    period: int = Field(default=14, gt=0, le=500)   # OR_HIGH / OR_LOW: the opening range in minutes
    multiplier: float = Field(default=3.0, gt=0)  # SUPERTREND multiplier; Bollinger bands: standard deviations
    # Phase AW: evaluate this operand on a higher timeframe (completed bars only); None = the strategy's own.
    timeframe: Optional[str] = Field(default=None, max_length=10)

    def label(self) -> str:
        if self.type == "value":
            return f"{self.value:g}"
        if self.indicator in _PERIODLESS:
            text = self.indicator
        elif self.indicator == "SUPERTREND":
            text = f"SUPERTREND({self.period},{self.multiplier:g})"
        elif self.indicator.startswith("BB_"):
            text = f"{self.indicator}({self.period},{self.multiplier:g})"
        else:
            text = f"{self.indicator}({self.period})"
        return f"{text}[{self.timeframe}]" if self.timeframe else text

    def series(self, df: pd.DataFrame, base_tf: Optional[str] = None) -> pd.Series:
        if self.type == "value":
            return pd.Series(self.value, index=df.index)
        if self.timeframe and self.timeframe != base_tf:
            return self._higher_timeframe(df)
        name = self.indicator
        if name in _PRICE_COLUMNS:
            return df[_PRICE_COLUMNS[name]]
        if name in _SESSION:
            sess = _sessions(df)
            if name == "VWAP":
                return _session_vwap(df, sess)
            if name == "DAY_OPEN":
                return df["open"].groupby(sess).transform("first").astype(float)
            if name in ("OR_HIGH", "OR_LOW"):
                return _opening_range(df, sess, self.period, "high" if name == "OR_HIGH" else "low")
            return _prev_session(df, sess, {"PDH": "high", "PDL": "low", "PDC": "close"}[name], {"PDH": "max", "PDL": "min", "PDC": "last"}[name])
        if name.startswith("BB_"):
            from app.indicators.volatility import bollinger
            return bollinger(df["close"], self.period, self.multiplier)[{"BB_UPPER": "upper", "BB_MID": "mid", "BB_LOWER": "lower"}[name]]
        if name == "VOLUME":
            return df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)
        if name == "VOLUME_SMA":
            vol = df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)
            return sma(vol, self.period)
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

    def _higher_timeframe(self, df: pd.DataFrame) -> pd.Series:
        """This operand on `timeframe` bars resampled from `df`, mapped back so each base bar sees
        the last higher-timeframe bar that had *closed* by the end of the base bar."""
        from app.core.resampling import resample_ohlc
        htf = tf_minutes(self.timeframe)
        if not htf or len(df) == 0:
            return pd.Series(float("nan"), index=df.index)
        utc_df = df.copy()
        utc_df.index = _utc_index(df)
        origin = utc_df.index[0].normalize() + _IST_OPEN_UTC
        frame = resample_ohlc(utc_df, f"{htf}min", origin=origin)
        inner = self.model_copy(update={"timeframe": None}).series(frame)
        ends = frame.index + pd.Timedelta(minutes=htf)
        if htf < _SESSION_MINUTES and _SESSION_MINUTES % htf:
            # SCR-FIX A5 (as screener.runtime.bar_close): a 30m / 1h bucket that starts before the 15:30 IST close and
            # would end after it closes at the close, so the session's last bucket counts as closed - as Charts and
            # every broker show it.
            close = frame.index.normalize() + _IST_CLOSE_UTC
            ends = ends.where(~((frame.index < close) & (close < ends)), close)
        by_end = pd.Series(inner.values, index=ends)
        base_ends = utc_df.index + pd.Timedelta(minutes=_base_minutes(df))
        return pd.Series(by_end.reindex(base_ends, method="ffill").values, index=df.index, dtype=float)

    def warmup_bars(self, base_tf: Optional[str] = None) -> int:
        if self.type == "value" or self.indicator in ("CLOSE", "OPEN", "HIGH", "LOW", "VOLUME"):
            bars = 0
        elif self.indicator in ("VWAP", "DAY_OPEN", "OR_HIGH", "OR_LOW"):
            bars = 0
        elif self.indicator in ("PDH", "PDL", "PDC"):
            base = tf_minutes(base_tf) or 1
            return 375 // base + 5          # one full previous session
        else:
            bars = self.period + 5
        if self.timeframe and self.timeframe != base_tf:
            ratio = max(1, (tf_minutes(self.timeframe) or 1) // max(1, tf_minutes(base_tf) or 1))
            return (bars + 2) * ratio
        return bars


class Condition(BaseModel):
    left: Operand
    operator: Operator
    right: Operand

    def label(self) -> str:
        symbols = {"GT": ">", "LT": "<", "GTE": ">=", "LTE": "<=", "CROSSES_ABOVE": "crosses above", "CROSSES_BELOW": "crosses below"}
        return f"{self.left.label()} {symbols[self.operator]} {self.right.label()}"

    def warmup_bars(self, base_tf: Optional[str] = None) -> int:
        return max(self.left.warmup_bars(base_tf), self.right.warmup_bars(base_tf))

    def holds_series(self, df: pd.DataFrame, base_tf: Optional[str] = None) -> pd.Series:
        """Phase AW: the condition on every bar at once (causal: each value uses only that bar and
        earlier ones) - for fast screening; NaN inputs count as not holding."""
        left_s, right_s = self.left.series(df, base_tf), self.right.series(df, base_tf)
        valid = left_s.notna() & right_s.notna()
        if self.operator in ("CROSSES_ABOVE", "CROSSES_BELOW"):
            lp, rp = left_s.shift(1), right_s.shift(1)
            valid &= lp.notna() & rp.notna()
            out = (lp <= rp) & (left_s > right_s) if self.operator == "CROSSES_ABOVE" else (lp >= rp) & (left_s < right_s)
        else:
            out = {"GT": left_s > right_s, "LT": left_s < right_s, "GTE": left_s >= right_s, "LTE": left_s <= right_s}[self.operator]
        return (out & valid).fillna(False).astype(bool)

    def evaluate(self, df: pd.DataFrame, base_tf: Optional[str] = None) -> Tuple[bool, bool]:
        """Returns (holds, has_enough_data). `holds` is meaningless when has_enough_data is False."""
        left_s = self.left.series(df, base_tf)
        right_s = self.right.series(df, base_tf)
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


# P1-2b (TT12-4, "no hidden filter anywhere"): a custom strategy has no built-in minimum reward:risk - None = not set, no
# filter. Configs saved before then held the old default 1.2 in every config nobody changed (the Builder never showed it),
# so the TT12-1 rule applies: a stored 1.2 reads as not set, any other stored value is kept. A user who states exactly 1.2
# himself (his words, the Copilot builder) is stored one float step above it (`own_min_rr`), which applies as 1.2.
LEGACY_MIN_RR = 1.2
OWN_LEGACY_MIN_RR = math.nextafter(LEGACY_MIN_RR, math.inf)


def own_min_rr(value: Optional[float]) -> Optional[float]:
    """A minimum the user stated himself, in the form stored in a config (see LEGACY_MIN_RR)."""
    return OWN_LEGACY_MIN_RR if value is not None and float(value) == LEGACY_MIN_RR else value


def min_rr_in_force(value: Optional[float]) -> Optional[float]:
    """The minimum a config applies: None = no filter; the stored form of a stated 1.2 applies as 1.2."""
    if value is None:
        return None
    return LEGACY_MIN_RR if value == OWN_LEGACY_MIN_RR else float(value)


class CustomStrategyConfig(BaseModel):
    name: str
    timeframe: str = "5min"
    long_conditions: List[Condition] = Field(default_factory=list)
    short_conditions: List[Condition] = Field(default_factory=list)
    # DEF-0 / SB-UX SU4 (owner): the stop, the targets and the minimum R:R are the trader's own values; blank (None) =
    # off - no stop-loss exit, no target exit, no reward:risk filter. Nothing is pre-filled here or in the builder.
    stop_loss_atr_mult: float | None = Field(default=None, gt=0)
    atr_period: int = Field(default=14, gt=0, le=500)
    target_rr: tuple[float, float] | None = None
    min_rr: Optional[float] = Field(default=None, gt=0)

    @field_validator("min_rr", mode="before")
    @classmethod
    def _legacy_default_is_unset(cls, value: Any) -> Any:
        """TT12-1 rule: the old built-in 1.2 (stored or sent) counts as not set."""
        try:
            return None if value is not None and not isinstance(value, bool) and float(value) == LEGACY_MIN_RR else value
        except (TypeError, ValueError):
            return value

    @field_validator("target_rr")
    @classmethod
    def _targets_positive(cls, value: tuple[float, float] | None) -> tuple[float, float] | None:
        if value is not None and not all(float(v) > 0 for v in value):
            raise ValueError("Each target must be above 0 R (leave the target blank for none)")
        return value

    @model_validator(mode="after")
    def _r_needs_a_stop(self) -> "CustomStrategyConfig":
        # A target and a minimum reward:risk are multiples of the risk, which only a stop defines: said, never guessed.
        if self.target_rr is not None and self.stop_loss_atr_mult is None:
            raise ValueError("A target in R needs your stop-loss: set a stop, or leave the target blank")
        if self.min_rr is not None and self.target_rr is None:
            raise ValueError("A minimum reward:risk needs your target: set a target, or leave the minimum blank")
        return self

    def model_post_init(self, __context: Any) -> None:
        if not self.long_conditions and not self.short_conditions:
            raise ValueError("A custom strategy needs at least one long or short condition")


class OwnCustomStrategyConfig(CustomStrategyConfig):
    """A config the user saves himself (Strategy Builder create / edit): a minimum he typed, even exactly 1.2, is his own
    (stored as `own_min_rr`); empty = not set."""

    @field_validator("min_rr", mode="before")
    @classmethod
    def _legacy_default_is_unset(cls, value: Any) -> Any:
        try:
            return own_min_rr(float(value)) if value is not None and not isinstance(value, bool) else value
        except (TypeError, ValueError):
            return value


class DeclarativeStrategy(BaseStrategy):
    """Builds a Signal from a CustomStrategyConfig's AND-combined long/short condition sets."""

    category = StrategyCategory.INDICATOR_BASED

    def __init__(self, strategy_id: str, config: CustomStrategyConfig) -> None:
        self.id = strategy_id
        self.name = config.name
        self.description = "User-defined strategy built with the no-code Strategy Builder."
        self.timeframes = [config.timeframe]
        self.config = config
        super().__init__(min_rr=min_rr_in_force(config.min_rr))         # None = no reward:risk filter (P1-2b)

    def min_history(self) -> Dict[str, int]:
        tf = self.config.timeframe
        all_conditions = self.config.long_conditions + self.config.short_conditions
        needed = max((c.warmup_bars(tf) for c in all_conditions), default=0)
        return {tf: max(needed, self.config.atr_period) + 5}

    def _side_holds(self, conditions: List[Condition], df: pd.DataFrame) -> Tuple[bool, List[str]]:
        if not conditions:
            return False, []
        labels: List[str] = []
        for condition in conditions:
            holds, enough = condition.evaluate(df, self.config.timeframe)
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
        mult = self.config.stop_loss_atr_mult
        atr_last = float(atr_indicator(df, self.config.atr_period).iloc[-1]) if mult is not None else 0.0

        if mult is not None and pd.isna(atr_last):
            return self.no_trade(symbol, timestamp, ["ATR not yet warmed up"])

        for direction, conditions, side in ((SignalDirection.LONG, self.config.long_conditions, "Long"),
                                            (SignalDirection.SHORT, self.config.short_conditions, "Short")):
            holds, labels = self._side_holds(conditions, df)
            if not holds:
                continue
            reasons = [f"{side} rule matched: {label}" for label in labels]
            if mult is None:
                return self._signal_without_stop(symbol, timestamp, direction, close, reasons)
            sign = -1 if direction == SignalDirection.LONG else 1
            return self.build_signal(symbol, timestamp, direction, close, close + sign * atr_last * mult, score=70,
                                     reasons=reasons, target_rr=self.config.target_rr)

        return self.no_trade(symbol, timestamp, ["No custom rule set fully matched on the latest bar"])

    def _signal_without_stop(self, symbol: str, timestamp, direction: SignalDirection, close: float, reasons: List[str]) -> Signal:
        """DEF-0 / SB-UX SU4: the trader set no stop - the signal carries none (stop 0 = "no stop set", DEF-0 D7 in
        app/trading/exit_logic.py), so no target in R and no reward:risk either (the config refuses those without a stop)."""
        return Signal(symbol=symbol, strategy_id=self.id, strategy_name=self.name, direction=direction, timestamp=timestamp,
                      entry=round(float(close), 2), stop_loss=0.0, target1=None, target2=None, risk_reward=None,
                      score=70, grade=grade_from_score(70), reasons=reasons, timeframe_combo="/".join(self.timeframes))
