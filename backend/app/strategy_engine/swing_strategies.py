"""Swing strategies (Phase AS): daily-candle setups held overnight, for a trader who cannot watch
the screen. Both decide on *closed* daily bars (the market-data service serves completed days for
the `day` interval) and the position is entered in the next session and held - as CNC for cash
equity, NRML for futures / bought options - until its stop or targets, never squared off at the
close (deployment `holding="SWING"`).

* Swing EMA pullback - in an EMA(20) > EMA(50) uptrend, a pullback that touches EMA(20) and closes
  back above it on a bullish day, with RSI still healthy. The professional's "buy the dip in an
  uptrend". Mirror for downtrends (shorts need futures / options - cash equity cannot be held short).
* Swing breakout - a close beyond the previous 20 sessions' range with a trending ADX and, where
  the symbol has volume, above-average volume. The "new 20-day high" momentum entry.

Overnight gaps can jump a stop, so both use wide, structure-based stops (at least one ATR) and
bigger targets (2R / 3R and more); the interview sizes swing risk below intraday risk for the same
reason.
"""
from typing import Any, Dict

import pandas as pd

from app.core.enums import SignalDirection, StrategyCategory
from app.core.models import Signal
from app.indicators.directional import adx as adx_frame
from app.indicators.momentum import rsi as rsi_indicator
from app.indicators.trend import ema
from app.indicators.volatility import atr as atr_indicator
from app.strategy_engine.base import BaseStrategy


class _Daily(BaseStrategy):
    category = StrategyCategory.INDICATOR_BASED

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.timeframes = ["day"]

    def _frame(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        return data["day"]


class SwingEmaPullback(_Daily):
    id = "swing_ema_pullback_d"
    name = "Swing EMA Pullback (daily)"
    description = ("Daily uptrend (EMA20 above EMA50): buy the pullback that touches EMA20 and closes back above it on a "
                   "bullish day, RSI healthy. Stop below the pullback low (at least 1 ATR); held overnight.")
    default_params: Dict[str, Any] = {
        "ema_fast": 20, "ema_slow": 50, "rsi_period": 14, "rsi_long_min": 40, "rsi_short_max": 60,
        "touch_pct": 0.5, "atr_period": 14, "atr_buffer": 0.25, "min_stop_atr": 1.0, "target_rr": (2.0, 3.0), "min_rr": 1.5,
    }

    def min_history(self) -> Dict[str, int]:
        p = self.params
        return {"day": max(p["ema_slow"], p["rsi_period"], p["atr_period"]) + 10}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])
        p = self.params
        df = self._frame(data)
        ts = df.index[-1]
        close, open_ = float(df["close"].iloc[-1]), float(df["open"].iloc[-1])
        fast, slow = ema(df["close"], p["ema_fast"]), ema(df["close"], p["ema_slow"])
        rsi_s = rsi_indicator(df["close"], p["rsi_period"])
        atr_s = atr_indicator(df, p["atr_period"])
        f, s, r, a = fast.iloc[-1], slow.iloc[-1], rsi_s.iloc[-1], atr_s.iloc[-1]
        if any(pd.isna(v) for v in (f, s, r, a)):
            return self.no_trade(symbol, ts, ["Indicators not yet warmed up"])
        touch = p["touch_pct"] / 100.0
        low2, high2 = float(df["low"].iloc[-2:].min()), float(df["high"].iloc[-2:].max())
        fast2 = float(fast.iloc[-2:].max())
        uptrend, downtrend = f > s and close > s, f < s and close < s
        if uptrend and low2 <= fast2 * (1 + touch) and close > f and close > open_ and r >= p["rsi_long_min"]:
            stop = min(low2 - a * p["atr_buffer"], close - a * p["min_stop_atr"])
            reasons = [f"Daily uptrend: EMA{p['ema_fast']} {f:.2f} above EMA{p['ema_slow']} {s:.2f}",
                       f"Pulled back to EMA{p['ema_fast']} and closed back above it on a bullish day", f"RSI({p['rsi_period']}) {r:.0f}"]
            score = int(round(min(100, 55 + min(25.0, (f - s) / max(a, 1e-9) * 10) + min(20.0, max(r - 40, 0) / 2))))
            return self.build_signal(symbol, ts, SignalDirection.LONG, close, stop, score, reasons, p["target_rr"])
        fast2_min = float(fast.iloc[-2:].min())
        if downtrend and high2 >= fast2_min * (1 - touch) and close < f and close < open_ and r <= p["rsi_short_max"]:
            stop = max(high2 + a * p["atr_buffer"], close + a * p["min_stop_atr"])
            reasons = [f"Daily downtrend: EMA{p['ema_fast']} {f:.2f} below EMA{p['ema_slow']} {s:.2f}",
                       f"Rallied to EMA{p['ema_fast']} and closed back below it on a bearish day", f"RSI({p['rsi_period']}) {r:.0f}"]
            score = int(round(min(100, 55 + min(25.0, (s - f) / max(a, 1e-9) * 10) + min(20.0, max(60 - r, 0) / 2))))
            return self.build_signal(symbol, ts, SignalDirection.SHORT, close, stop, score, reasons, p["target_rr"])
        return self.no_trade(symbol, ts, ["No pullback-and-resume in a daily trend"])


class SwingBreakout(_Daily):
    id = "swing_breakout_d"
    name = "Swing Breakout (daily)"
    description = ("A daily close beyond the previous 20 sessions' high (or low) with ADX trending and, where there is volume, "
                   "above-average volume. Stop back inside the old range (at least 1 ATR); held overnight.")
    default_params: Dict[str, Any] = {
        "lookback": 20, "adx_period": 14, "adx_min": 20, "volume_mult": 1.5, "atr_period": 14,
        "stop_atr_inside": 1.0, "max_stop_atr": 3.0, "min_stop_atr": 1.0, "target_rr": (2.0, 3.5), "min_rr": 1.5,
    }

    def min_history(self) -> Dict[str, int]:
        p = self.params
        return {"day": max(p["lookback"], p["adx_period"] * 2, p["atr_period"]) + 10}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])
        p = self.params
        df = self._frame(data)
        ts = df.index[-1]
        close = float(df["close"].iloc[-1])
        prior = df.iloc[-(p["lookback"] + 1):-1]
        prior_high, prior_low = float(prior["high"].max()), float(prior["low"].min())
        a = float(atr_indicator(df, p["atr_period"]).iloc[-1])
        adx_val = float(adx_frame(df, p["adx_period"])["adx"].iloc[-1])
        if pd.isna(a) or pd.isna(adx_val):
            return self.no_trade(symbol, ts, ["Indicators not yet warmed up"])
        volume_ok, volume_note = True, "no volume on this symbol - volume check skipped"
        if "volume" in df and float(prior["volume"].mean() or 0) > 0:
            avg = float(prior["volume"].mean())
            today = float(df["volume"].iloc[-1])
            volume_ok = today >= avg * p["volume_mult"]
            volume_note = f"volume {today:,.0f} vs 20-day average {avg:,.0f}"
        if adx_val < p["adx_min"]:
            return self.no_trade(symbol, ts, [f"ADX {adx_val:.1f} below {p['adx_min']}: no trend to break out with"])
        if not volume_ok:
            return self.no_trade(symbol, ts, [f"Breakout without volume ({volume_note})"])
        if close > prior_high:
            stop = max(prior_high - a * p["stop_atr_inside"], close - a * p["max_stop_atr"])
            stop = min(stop, close - a * p["min_stop_atr"])
            reasons = [f"Closed {close:.2f} above the {p['lookback']}-day high {prior_high:.2f}", f"ADX {adx_val:.1f}: trending", volume_note]
            score = int(round(min(100, 55 + min(25.0, (adx_val - p["adx_min"]) * 1.5) + min(20.0, (close - prior_high) / max(a, 1e-9) * 20))))
            return self.build_signal(symbol, ts, SignalDirection.LONG, close, stop, score, reasons, p["target_rr"])
        if close < prior_low:
            stop = min(prior_low + a * p["stop_atr_inside"], close + a * p["max_stop_atr"])
            stop = max(stop, close + a * p["min_stop_atr"])
            reasons = [f"Closed {close:.2f} below the {p['lookback']}-day low {prior_low:.2f}", f"ADX {adx_val:.1f}: trending", volume_note]
            score = int(round(min(100, 55 + min(25.0, (adx_val - p["adx_min"]) * 1.5) + min(20.0, (prior_low - close) / max(a, 1e-9) * 20))))
            return self.build_signal(symbol, ts, SignalDirection.SHORT, close, stop, score, reasons, p["target_rr"])
        return self.no_trade(symbol, ts, [f"Inside the {p['lookback']}-day range {prior_low:.2f}-{prior_high:.2f}"])


def build_swing_strategies():
    return [SwingEmaPullback(), SwingBreakout()]
