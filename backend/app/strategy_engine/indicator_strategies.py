from typing import Any, Dict, List

import pandas as pd

from app.core.enums import SignalDirection, StrategyCategory
from app.core.models import Signal
from app.indicators.directional import adx as adx_indicator
from app.indicators.momentum import rsi as rsi_indicator
from app.indicators.trend import ema
from app.indicators.volatility import atr as atr_indicator
from app.indicators.volatility import supertrend as supertrend_indicator
from app.strategy_engine.base import BaseStrategy


class EmaRsiScalper(BaseStrategy):
    """EMA crossover for entry timing, RSI as momentum-direction filter. Single timeframe scalper."""

    id = "ema_rsi_scalper_1m"
    name = "EMA + RSI Scalper"
    description = "Fast/slow EMA crossover confirmed by RSI momentum bias, for single-timeframe scalping."
    category = StrategyCategory.INDICATOR_BASED

    default_params: Dict[str, Any] = {
        "tf": "1min",
        "ema_fast": 9,
        "ema_slow": 21,
        "rsi_period": 14,
        "rsi_mid": 50,
        "atr_period": 14,
        "atr_mult_sl": 1.0,
        "target_rr": (1.5, 2.0),
        "min_rr": 1.2,
    }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.timeframes = [self.params["tf"]]

    def min_history(self) -> Dict[str, int]:
        p = self.params
        return {p["tf"]: max(p["ema_slow"], p["rsi_period"], p["atr_period"]) + 5}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])

        p = self.params
        df = data[p["tf"]]
        timestamp = df.index[-1]

        ema_fast_s = ema(df["close"], p["ema_fast"])
        ema_slow_s = ema(df["close"], p["ema_slow"])
        rsi_s = rsi_indicator(df["close"], p["rsi_period"])
        atr_s = atr_indicator(df, p["atr_period"])

        close = df["close"].iloc[-1]
        fast_last, fast_prev = ema_fast_s.iloc[-1], ema_fast_s.iloc[-2]
        slow_last, slow_prev = ema_slow_s.iloc[-1], ema_slow_s.iloc[-2]
        rsi_last = rsi_s.iloc[-1]
        atr_last = atr_s.iloc[-1]

        if any(pd.isna(v) for v in [fast_last, fast_prev, slow_last, slow_prev, rsi_last, atr_last]):
            return self.no_trade(symbol, timestamp, ["Indicators not yet warmed up"])

        bullish_cross = fast_prev <= slow_prev and fast_last > slow_last
        bearish_cross = fast_prev >= slow_prev and fast_last < slow_last

        rsi_distance = abs(rsi_last - p["rsi_mid"])
        rsi_score = min(30.0, rsi_distance)
        separation_score = min(30.0, (abs(fast_last - slow_last) / max(atr_last, 1e-9)) * 15)

        if bullish_cross and rsi_last > p["rsi_mid"]:
            reasons = [
                f"EMA{p['ema_fast']} crossed above EMA{p['ema_slow']}",
                f"RSI({p['rsi_period']})={rsi_last:.1f} confirms bullish momentum",
            ]
            score = int(round(40 + rsi_score + separation_score))
            stop_loss = close - atr_last * p["atr_mult_sl"]
            return self.build_signal(
                symbol, timestamp, SignalDirection.LONG, close, stop_loss, min(score, 100), reasons, p["target_rr"]
            )

        if bearish_cross and rsi_last < p["rsi_mid"]:
            reasons = [
                f"EMA{p['ema_fast']} crossed below EMA{p['ema_slow']}",
                f"RSI({p['rsi_period']})={rsi_last:.1f} confirms bearish momentum",
            ]
            score = int(round(40 + rsi_score + separation_score))
            stop_loss = close + atr_last * p["atr_mult_sl"]
            return self.build_signal(
                symbol, timestamp, SignalDirection.SHORT, close, stop_loss, min(score, 100), reasons, p["target_rr"]
            )

        return self.no_trade(symbol, timestamp, ["No fresh EMA crossover aligned with RSI momentum"])


class SupertrendAdxScalper(BaseStrategy):
    """Supertrend flip for entry/exit direction, ADX as trend-strength filter to avoid chop."""

    id = "supertrend_adx_scalper_1m"
    name = "Supertrend + ADX Scalper"
    description = "Supertrend trend-flip entries confirmed by ADX trend strength. Single timeframe scalper."
    category = StrategyCategory.INDICATOR_BASED

    default_params: Dict[str, Any] = {
        "tf": "1min",
        "st_period": 10,
        "st_mult": 3.0,
        "adx_period": 14,
        "adx_min": 20,
        "atr_mult_sl": 0.3,
        "target_rr": (1.5, 2.5),
        "min_rr": 1.2,
    }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.timeframes = [self.params["tf"]]

    def min_history(self) -> Dict[str, int]:
        p = self.params
        return {p["tf"]: max(p["st_period"], p["adx_period"]) + 5}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])

        p = self.params
        df = data[p["tf"]]
        timestamp = df.index[-1]

        st_df = supertrend_indicator(df, p["st_period"], p["st_mult"])
        adx_df = adx_indicator(df, p["adx_period"])
        atr_s = atr_indicator(df, p["st_period"])

        close = df["close"].iloc[-1]
        trend_last, trend_prev = st_df["trend"].iloc[-1], st_df["trend"].iloc[-2]
        st_line_last = st_df["supertrend"].iloc[-1]
        adx_last = adx_df["adx"].iloc[-1]
        atr_last = atr_s.iloc[-1]

        if any(pd.isna(v) for v in [trend_last, trend_prev, st_line_last, adx_last, atr_last]):
            return self.no_trade(symbol, timestamp, ["Indicators not yet warmed up"])

        flipped_bullish = trend_prev == -1 and trend_last == 1
        flipped_bearish = trend_prev == 1 and trend_last == -1
        adx_strong = adx_last >= p["adx_min"]
        adx_score = max(0.0, min(40.0, 20.0 + (adx_last - p["adx_min"])))

        if flipped_bullish and adx_strong:
            reasons = [
                f"Supertrend({p['st_period']},{p['st_mult']}) flipped bullish",
                f"ADX({p['adx_period']})={adx_last:.1f} confirms trend strength",
            ]
            score = int(round(40 + adx_score))
            stop_loss = st_line_last - atr_last * p["atr_mult_sl"]
            return self.build_signal(
                symbol, timestamp, SignalDirection.LONG, close, stop_loss, min(score, 100), reasons, p["target_rr"]
            )

        if flipped_bearish and adx_strong:
            reasons = [
                f"Supertrend({p['st_period']},{p['st_mult']}) flipped bearish",
                f"ADX({p['adx_period']})={adx_last:.1f} confirms trend strength",
            ]
            score = int(round(40 + adx_score))
            stop_loss = st_line_last + atr_last * p["atr_mult_sl"]
            return self.build_signal(
                symbol, timestamp, SignalDirection.SHORT, close, stop_loss, min(score, 100), reasons, p["target_rr"]
            )

        reasons = ["No fresh Supertrend flip with sufficient ADX strength"]
        if not adx_strong:
            reasons.append(f"ADX({p['adx_period']})={adx_last:.1f} below threshold {p['adx_min']}")
        return self.no_trade(symbol, timestamp, reasons)


class RsiAdxMomentumScalper(BaseStrategy):
    """RSI momentum cross through midline confirmed by directional movement (+DI/-DI) and ADX strength."""

    id = "rsi_adx_momentum_5m"
    name = "RSI + ADX Momentum Scalper"
    description = "RSI midline cross confirmed by DI dominance and ADX strength. Single timeframe scalper."
    category = StrategyCategory.INDICATOR_BASED

    default_params: Dict[str, Any] = {
        "tf": "5min",
        "rsi_period": 14,
        "rsi_mid": 50,
        "adx_period": 14,
        "adx_min": 20,
        "atr_period": 14,
        "atr_mult_sl": 1.0,
        "target_rr": (1.5, 2.0),
        "min_rr": 1.2,
    }

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.timeframes = [self.params["tf"]]

    def min_history(self) -> Dict[str, int]:
        p = self.params
        return {p["tf"]: max(p["rsi_period"], p["adx_period"], p["atr_period"]) + 5}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])

        p = self.params
        df = data[p["tf"]]
        timestamp = df.index[-1]

        rsi_s = rsi_indicator(df["close"], p["rsi_period"])
        adx_df = adx_indicator(df, p["adx_period"])
        atr_s = atr_indicator(df, p["atr_period"])

        close = df["close"].iloc[-1]
        rsi_last, rsi_prev = rsi_s.iloc[-1], rsi_s.iloc[-2]
        plus_di, minus_di = adx_df["plus_di"].iloc[-1], adx_df["minus_di"].iloc[-1]
        adx_last = adx_df["adx"].iloc[-1]
        atr_last = atr_s.iloc[-1]

        if any(pd.isna(v) for v in [rsi_last, rsi_prev, plus_di, minus_di, adx_last, atr_last]):
            return self.no_trade(symbol, timestamp, ["Indicators not yet warmed up"])

        rsi_cross_up = rsi_prev <= p["rsi_mid"] < rsi_last
        rsi_cross_down = rsi_prev >= p["rsi_mid"] > rsi_last
        adx_strong = adx_last >= p["adx_min"]
        adx_score = max(0.0, min(40.0, 20.0 + (adx_last - p["adx_min"])))
        di_gap = abs(plus_di - minus_di)
        di_score = min(30.0, di_gap)

        if rsi_cross_up and plus_di > minus_di and adx_strong:
            reasons = [
                f"RSI({p['rsi_period']}) crossed up through {p['rsi_mid']}",
                f"+DI ({plus_di:.1f}) dominant over -DI ({minus_di:.1f})",
                f"ADX({p['adx_period']})={adx_last:.1f} confirms trend strength",
            ]
            score = int(round(30 + di_score + adx_score))
            stop_loss = close - atr_last * p["atr_mult_sl"]
            return self.build_signal(
                symbol, timestamp, SignalDirection.LONG, close, stop_loss, min(score, 100), reasons, p["target_rr"]
            )

        if rsi_cross_down and minus_di > plus_di and adx_strong:
            reasons = [
                f"RSI({p['rsi_period']}) crossed down through {p['rsi_mid']}",
                f"-DI ({minus_di:.1f}) dominant over +DI ({plus_di:.1f})",
                f"ADX({p['adx_period']})={adx_last:.1f} confirms trend strength",
            ]
            score = int(round(30 + di_score + adx_score))
            stop_loss = close + atr_last * p["atr_mult_sl"]
            return self.build_signal(
                symbol, timestamp, SignalDirection.SHORT, close, stop_loss, min(score, 100), reasons, p["target_rr"]
            )

        return self.no_trade(symbol, timestamp, ["No aligned RSI/DI/ADX momentum setup"])


def build_indicator_strategies() -> List[BaseStrategy]:
    return [EmaRsiScalper(), SupertrendAdxScalper(), RsiAdxMomentumScalper()]
