from typing import Any, Dict, List

import pandas as pd

from app.core.enums import SignalDirection, StrategyCategory
from app.core.models import Signal
from app.indicators.directional import adx as adx_indicator
from app.indicators.momentum import rsi as rsi_indicator
from app.indicators.trend import ema
from app.indicators.volatility import atr as atr_indicator
from app.strategy_engine.base import BaseStrategy


class MTFTrendPullbackScalper(BaseStrategy):
    """Higher-timeframe trend filter + lower-timeframe pullback/reversal trigger.

    Entry logic (LONG mirrors SHORT):
      HTF close above a rising HTF EMA (trend filter)
      + LTF fast EMA above slow EMA (structure alignment)
      + price pulled back close to the LTF fast EMA (entry zone)
      + RSI crossing back up through the long trigger level (reversal confirmation)
      + ADX on LTF at/above the strength threshold (avoids choppy/no-trend chop)
    """

    category = StrategyCategory.MULTI_TIMEFRAME

    default_params: Dict[str, Any] = {
        "ema_fast": 9,
        "ema_slow": 21,
        "htf_ema_period": 20,
        "rsi_period": 14,
        "rsi_long_trigger": 45,
        "rsi_short_trigger": 55,
        "adx_period": 14,
        "adx_min": 18,
        "atr_period": 14,
        "atr_mult_sl": 1.2,
        "pullback_atr_mult": 0.5,
        "target_rr": (1.5, 2.5),
        "min_rr": 1.2,
    }

    def __init__(self, ltf: str, htf: str, id_: str, name: str, description: str, **params: Any) -> None:
        self.ltf = ltf
        self.htf = htf
        self.id = id_
        self.name = name
        self.description = description
        self.timeframes = [ltf, htf]
        super().__init__(**params)

    def min_history(self) -> Dict[str, int]:
        p = self.params
        ltf_min = max(p["ema_slow"], p["rsi_period"], p["adx_period"], p["atr_period"]) + 5
        htf_min = p["htf_ema_period"] + 3
        return {self.ltf: ltf_min, self.htf: htf_min}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history for one or more timeframes"])

        p = self.params
        htf_df = data[self.htf]
        ltf_df = data[self.ltf]
        timestamp = ltf_df.index[-1]

        htf_ema = ema(htf_df["close"], p["htf_ema_period"])
        htf_close = htf_df["close"].iloc[-1]
        htf_ema_last = htf_ema.iloc[-1]
        htf_ema_prev = htf_ema.iloc[-2]

        htf_bullish = bool(htf_close > htf_ema_last and htf_ema_last > htf_ema_prev)
        htf_bearish = bool(htf_close < htf_ema_last and htf_ema_last < htf_ema_prev)

        ema_fast_s = ema(ltf_df["close"], p["ema_fast"])
        ema_slow_s = ema(ltf_df["close"], p["ema_slow"])
        rsi_s = rsi_indicator(ltf_df["close"], p["rsi_period"])
        adx_df = adx_indicator(ltf_df, p["adx_period"])
        atr_s = atr_indicator(ltf_df, p["atr_period"])

        close = ltf_df["close"].iloc[-1]
        ema_fast_last = ema_fast_s.iloc[-1]
        ema_slow_last = ema_slow_s.iloc[-1]
        rsi_last = rsi_s.iloc[-1]
        rsi_prev = rsi_s.iloc[-2]
        adx_last = adx_df["adx"].iloc[-1]
        atr_last = atr_s.iloc[-1]

        if any(pd.isna(v) for v in [ema_fast_last, ema_slow_last, rsi_last, rsi_prev, adx_last, atr_last]):
            return self.no_trade(symbol, timestamp, ["Indicators not yet warmed up"])

        structure_bullish = ema_fast_last > ema_slow_last
        structure_bearish = ema_fast_last < ema_slow_last
        distance_to_fast_ema = abs(close - ema_fast_last)
        pullback_ok = distance_to_fast_ema <= atr_last * p["pullback_atr_mult"]
        adx_strong = adx_last >= p["adx_min"]

        rsi_cross_up = rsi_prev <= p["rsi_long_trigger"] < rsi_last
        rsi_cross_down = rsi_prev >= p["rsi_short_trigger"] > rsi_last

        def adx_score() -> float:
            return max(0.0, min(20.0, 10.0 + (adx_last - p["adx_min"])))

        def pullback_score() -> float:
            band = atr_last * p["pullback_atr_mult"]
            if band <= 0:
                return 0.0
            return max(0.0, 15.0 * (1 - distance_to_fast_ema / band))

        long_ready = htf_bullish and structure_bullish and pullback_ok and rsi_cross_up and adx_strong
        short_ready = htf_bearish and structure_bearish and pullback_ok and rsi_cross_down and adx_strong

        if long_ready:
            reasons: List[str] = [
                f"{self.htf} trend bullish (close above rising EMA{p['htf_ema_period']})",
                f"{self.ltf} structure bullish (EMA{p['ema_fast']} > EMA{p['ema_slow']})",
                "Price pulled back into fast EMA zone",
                f"RSI({p['rsi_period']}) crossed up through {p['rsi_long_trigger']}",
                f"ADX({p['adx_period']})={adx_last:.1f} confirms trending strength",
            ]
            score = int(round(30 + 15 + pullback_score() + 20 + adx_score()))
            score = min(score, 100)
            stop_loss = close - atr_last * p["atr_mult_sl"]
            return self.build_signal(
                symbol, timestamp, SignalDirection.LONG, close, stop_loss, score, reasons, p["target_rr"]
            )

        if short_ready:
            reasons = [
                f"{self.htf} trend bearish (close below falling EMA{p['htf_ema_period']})",
                f"{self.ltf} structure bearish (EMA{p['ema_fast']} < EMA{p['ema_slow']})",
                "Price pulled back into fast EMA zone",
                f"RSI({p['rsi_period']}) crossed down through {p['rsi_short_trigger']}",
                f"ADX({p['adx_period']})={adx_last:.1f} confirms trending strength",
            ]
            score = int(round(30 + 15 + pullback_score() + 20 + adx_score()))
            score = min(score, 100)
            stop_loss = close + atr_last * p["atr_mult_sl"]
            return self.build_signal(
                symbol, timestamp, SignalDirection.SHORT, close, stop_loss, score, reasons, p["target_rr"]
            )

        reasons = ["No aligned setup"]
        if not (htf_bullish or htf_bearish):
            reasons.append(f"{self.htf} trend not clearly directional")
        if not adx_strong:
            reasons.append(f"ADX({p['adx_period']})={adx_last:.1f} below strength threshold {p['adx_min']}")
        if not pullback_ok:
            reasons.append("Price not in pullback entry zone")
        return self.no_trade(symbol, timestamp, reasons)


MTF_COMBOS = [
    dict(ltf="1min", htf="5min", id_="mtf_1m_5m_trend_pullback", name="MTF Scalper 1m/5m",
         description="1-minute entry timing filtered by 5-minute trend, for ultra-fast scalping."),
    dict(ltf="1min", htf="15min", id_="mtf_1m_15m_trend_pullback", name="MTF Scalper 1m/15m",
         description="1-minute entry timing filtered by 15-minute trend."),
    dict(ltf="5min", htf="30min", id_="mtf_5m_30m_trend_pullback", name="MTF Scalper 5m/30m",
         description="5-minute entry timing filtered by 30-minute trend."),
    dict(ltf="5min", htf="60min", id_="mtf_5m_60m_trend_pullback", name="MTF Scalper 5m/60m",
         description="5-minute entry timing filtered by 60-minute (hourly) trend."),
]


def build_mtf_strategies() -> List[MTFTrendPullbackScalper]:
    return [MTFTrendPullbackScalper(**combo) for combo in MTF_COMBOS]
