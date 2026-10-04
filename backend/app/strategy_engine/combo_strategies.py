"""Indicator-combination strategies (Phase AO): four widely used intraday setups, each pairing an
entry trigger with a filter so a lone indicator never decides alone.

* MACD + EMA trend      - MACD/signal cross taken only in the direction of the EMA(200) trend.
* Bollinger + RSI       - mean reversion: a close back inside the band after an RSI extreme.
* VWAP + Supertrend     - trend entries on a Supertrend flip / VWAP reclaim, both agreeing.
* Opening range breakout - the first close beyond the 09:15-09:30 IST range, volume-confirmed.

All are single-timeframe (5-minute by default), use an ATR- or structure-based stop and the shared
`build_signal` targets, so the backtest engine, the trading worker and the chart run them exactly
like the original seven.
"""
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection, StrategyCategory
from app.core.models import Signal
from app.indicators.momentum import rsi as rsi_indicator
from app.indicators.trend import ema, macd as macd_indicator
from app.indicators.volatility import atr as atr_indicator
from app.indicators.volatility import bollinger as bollinger_indicator
from app.indicators.volatility import supertrend as supertrend_indicator
from app.strategy_engine.base import BaseStrategy

IST = "Asia/Kolkata"


def _ist_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(IST)


def session_vwap_or_mean(df: pd.DataFrame) -> pd.Series:
    """Session VWAP restarted each IST day. Indices carry no volume, so a session with no volume
    falls back to the running mean of the typical price (the volume-free equivalent)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    days = pd.Series(_ist_index(df).date, index=df.index)
    volume = df["volume"].fillna(0.0) if "volume" in df else pd.Series(0.0, index=df.index)
    cum_pv = (typical * volume).groupby(days).cumsum()
    cum_v = volume.groupby(days).cumsum()
    running_mean = typical.groupby(days).expanding().mean().reset_index(level=0, drop=True)
    vwap = cum_pv / cum_v.replace(0.0, np.nan)
    return vwap.fillna(running_mean)


class _SingleTf(BaseStrategy):
    category = StrategyCategory.INDICATOR_BASED

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.timeframes = [self.params["tf"]]

    def _frame(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        return data[self.params["tf"]]


class MacdEmaTrend(_SingleTf):
    id = "macd_ema_trend_5m"
    name = "MACD + EMA Trend"
    description = "MACD crossing its signal line, taken only in the direction of the EMA(200) trend. ATR stop."
    default_params: Dict[str, Any] = {
        "tf": "5min", "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "ema_trend": 200,
        "atr_period": 14, "atr_mult_sl": 1.2, "target_rr": (1.5, 2.5), "min_rr": 1.2,
    }

    def min_history(self) -> Dict[str, int]:
        p = self.params
        return {p["tf"]: max(p["ema_trend"], p["macd_slow"] + p["macd_signal"], p["atr_period"]) + 5}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])
        p = self.params
        df = self._frame(data)
        ts = df.index[-1]
        m = macd_indicator(df["close"], p["macd_fast"], p["macd_slow"], p["macd_signal"])
        trend = ema(df["close"], p["ema_trend"])
        atr_s = atr_indicator(df, p["atr_period"])
        close = float(df["close"].iloc[-1])
        line, sig, line_prev, sig_prev = m["macd"].iloc[-1], m["signal"].iloc[-1], m["macd"].iloc[-2], m["signal"].iloc[-2]
        trend_last, atr_last = trend.iloc[-1], atr_s.iloc[-1]
        if any(pd.isna(v) for v in [line, sig, line_prev, sig_prev, trend_last, atr_last]):
            return self.no_trade(symbol, ts, ["Indicators not yet warmed up"])
        cross_up = line_prev <= sig_prev and line > sig
        cross_down = line_prev >= sig_prev and line < sig
        trend_gap = abs(close - trend_last) / max(atr_last, 1e-9)
        score = int(round(min(100, 45 + min(25.0, trend_gap * 8) + min(30.0, abs(line - sig) / max(atr_last, 1e-9) * 60))))
        if cross_up and close > trend_last:
            reasons = [f"MACD({p['macd_fast']},{p['macd_slow']},{p['macd_signal']}) crossed above its signal line",
                       f"Close {close:.2f} above EMA{p['ema_trend']} {trend_last:.2f}: uptrend"]
            return self.build_signal(symbol, ts, SignalDirection.LONG, close, close - atr_last * p["atr_mult_sl"], score, reasons, p["target_rr"])
        if cross_down and close < trend_last:
            reasons = [f"MACD({p['macd_fast']},{p['macd_slow']},{p['macd_signal']}) crossed below its signal line",
                       f"Close {close:.2f} below EMA{p['ema_trend']} {trend_last:.2f}: downtrend"]
            return self.build_signal(symbol, ts, SignalDirection.SHORT, close, close + atr_last * p["atr_mult_sl"], score, reasons, p["target_rr"])
        return self.no_trade(symbol, ts, ["No MACD cross in the direction of the EMA trend"])


class BollingerRsiReversion(_SingleTf):
    id = "bb_rsi_reversion_5m"
    name = "Bollinger + RSI Reversion"
    description = "Mean reversion: a close back inside the Bollinger band right after an RSI extreme outside it."
    default_params: Dict[str, Any] = {
        "tf": "5min", "bb_period": 20, "bb_k": 2.0, "rsi_period": 14, "rsi_low": 30, "rsi_high": 70,
        "atr_period": 14, "atr_buffer": 0.2, "target_rr": (1.2, 2.0), "min_rr": 1.2,
    }

    def min_history(self) -> Dict[str, int]:
        p = self.params
        return {p["tf"]: max(p["bb_period"], p["rsi_period"], p["atr_period"]) + 5}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])
        p = self.params
        df = self._frame(data)
        ts = df.index[-1]
        bb = bollinger_indicator(df["close"], p["bb_period"], p["bb_k"])
        rsi_s = rsi_indicator(df["close"], p["rsi_period"])
        atr_s = atr_indicator(df, p["atr_period"])
        close, prev_close = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
        lower, upper, lower_prev, upper_prev = bb["lower"].iloc[-1], bb["upper"].iloc[-1], bb["lower"].iloc[-2], bb["upper"].iloc[-2]
        rsi_prev, atr_last = rsi_s.iloc[-2], atr_s.iloc[-1]
        if any(pd.isna(v) for v in [lower, upper, lower_prev, upper_prev, rsi_prev, atr_last]):
            return self.no_trade(symbol, ts, ["Indicators not yet warmed up"])
        recent_low = float(df["low"].iloc[-3:].min())
        recent_high = float(df["high"].iloc[-3:].max())
        if prev_close < lower_prev and rsi_prev <= p["rsi_low"] and close > lower:
            reasons = [f"Close back inside the lower Bollinger band ({p['bb_period']},{p['bb_k']})",
                       f"RSI({p['rsi_period']}) was oversold at {rsi_prev:.1f}"]
            score = int(round(min(100, 55 + (p["rsi_low"] - rsi_prev) * 2)))
            return self.build_signal(symbol, ts, SignalDirection.LONG, close, recent_low - atr_last * p["atr_buffer"], score, reasons, p["target_rr"])
        if prev_close > upper_prev and rsi_prev >= p["rsi_high"] and close < upper:
            reasons = [f"Close back inside the upper Bollinger band ({p['bb_period']},{p['bb_k']})",
                       f"RSI({p['rsi_period']}) was overbought at {rsi_prev:.1f}"]
            score = int(round(min(100, 55 + (rsi_prev - p["rsi_high"]) * 2)))
            return self.build_signal(symbol, ts, SignalDirection.SHORT, close, recent_high + atr_last * p["atr_buffer"], score, reasons, p["target_rr"])
        return self.no_trade(symbol, ts, ["No re-entry into the Bollinger band after an RSI extreme"])


class VwapSupertrend(_SingleTf):
    id = "vwap_supertrend_5m"
    name = "VWAP + Supertrend"
    description = "Trend entries when Supertrend flips (or price reclaims VWAP) with Supertrend and VWAP agreeing."
    default_params: Dict[str, Any] = {
        "tf": "5min", "st_period": 10, "st_mult": 3.0, "atr_buffer": 0.2, "target_rr": (1.5, 2.5), "min_rr": 1.2,
    }

    def min_history(self) -> Dict[str, int]:
        return {self.params["tf"]: self.params["st_period"] + 5}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])
        p = self.params
        df = self._frame(data)
        ts = df.index[-1]
        st = supertrend_indicator(df, p["st_period"], p["st_mult"])
        vwap = session_vwap_or_mean(df)
        atr_s = atr_indicator(df, p["st_period"])
        close, prev_close = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
        trend, trend_prev, line = st["trend"].iloc[-1], st["trend"].iloc[-2], st["supertrend"].iloc[-1]
        vw, vw_prev, atr_last = vwap.iloc[-1], vwap.iloc[-2], atr_s.iloc[-1]
        if any(pd.isna(v) for v in [line, vw, vw_prev, atr_last]) or trend == 0 or trend_prev == 0:
            return self.no_trade(symbol, ts, ["Indicators not yet warmed up"])
        flip_up, flip_down = trend_prev == -1 and trend == 1, trend_prev == 1 and trend == -1
        reclaim_up = trend == 1 and prev_close <= vw_prev and close > vw
        reclaim_down = trend == -1 and prev_close >= vw_prev and close < vw
        if (flip_up and close > vw) or reclaim_up:
            reasons = ["Supertrend flipped bullish" if flip_up else "Price reclaimed VWAP in a Supertrend uptrend",
                       f"Close {close:.2f} above VWAP {vw:.2f}"]
            return self.build_signal(symbol, ts, SignalDirection.LONG, close, float(line) - atr_last * p["atr_buffer"], 70 if flip_up else 60, reasons, p["target_rr"])
        if (flip_down and close < vw) or reclaim_down:
            reasons = ["Supertrend flipped bearish" if flip_down else "Price lost VWAP in a Supertrend downtrend",
                       f"Close {close:.2f} below VWAP {vw:.2f}"]
            return self.build_signal(symbol, ts, SignalDirection.SHORT, close, float(line) + atr_last * p["atr_buffer"], 70 if flip_down else 60, reasons, p["target_rr"])
        return self.no_trade(symbol, ts, ["Supertrend and VWAP do not agree on a fresh entry"])


class OpeningRangeBreakout(_SingleTf):
    id = "orb_15m_5m"
    name = "Opening Range Breakout"
    description = "First close beyond the 09:15-09:30 IST opening range, volume-confirmed when volume exists; stop at the range middle."
    default_params: Dict[str, Any] = {
        "tf": "5min", "range_minutes": 15, "last_entry": "14:30", "volume_mult": 1.5, "volume_lookback": 20,
        "target_rr": (1.5, 2.5), "min_rr": 1.2,
    }

    def min_history(self) -> Dict[str, int]:
        # The opening-range window itself is checked in analyze(); two bars is enough to compare.
        return {self.params["tf"]: 2}

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        if not self.has_enough_history(data):
            return self.no_trade(symbol, pd.Timestamp.utcnow(), ["Insufficient history"])
        p = self.params
        df = self._frame(data)
        ts = df.index[-1]
        ist = _ist_index(df)
        today = ist[-1].date()
        open_t = pd.Timestamp(f"{today} 09:15", tz=IST)
        range_end = open_t + pd.Timedelta(minutes=p["range_minutes"])
        hh, mm = (int(x) for x in str(p["last_entry"]).split(":"))
        if not (range_end <= ist[-1] <= pd.Timestamp(f"{today} {hh:02d}:{mm:02d}", tz=IST)):
            return self.no_trade(symbol, ts, ["Outside the opening-range breakout window"])
        in_range = (ist >= open_t) & (ist < range_end)
        if not in_range.any():
            return self.no_trade(symbol, ts, ["No opening-range bars for today"])
        r_high, r_low = float(df["high"][in_range].max()), float(df["low"][in_range].min())
        after = (ist >= range_end) & (np.arange(len(df)) < len(df) - 1)
        closes_before = df["close"][after]
        close = float(df["close"].iloc[-1])
        mid = (r_high + r_low) / 2.0
        vol = df["volume"].fillna(0.0) if "volume" in df else pd.Series(0.0, index=df.index)
        avg_vol = float(vol.iloc[-p["volume_lookback"] - 1:-1].mean()) if len(vol) > 1 else 0.0
        vol_ok = avg_vol <= 0 or float(vol.iloc[-1]) >= p["volume_mult"] * avg_vol
        vol_note = "no volume on this instrument (index): volume check skipped" if avg_vol <= 0 else f"volume {float(vol.iloc[-1]):.0f} vs avg {avg_vol:.0f}"
        if close > r_high and not (closes_before > r_high).any() and vol_ok:
            reasons = [f"First close above the opening range high {r_high:.2f}", vol_note]
            return self.build_signal(symbol, ts, SignalDirection.LONG, close, mid, 65, reasons, p["target_rr"])
        if close < r_low and not (closes_before < r_low).any() and vol_ok:
            reasons = [f"First close below the opening range low {r_low:.2f}", vol_note]
            return self.build_signal(symbol, ts, SignalDirection.SHORT, close, mid, 65, reasons, p["target_rr"])
        return self.no_trade(symbol, ts, [f"No fresh breakout of the opening range {r_low:.2f}-{r_high:.2f}"])


def build_combo_strategies() -> List[BaseStrategy]:
    return [MacdEmaTrend(), BollingerRsiReversion(), VwapSupertrend(), OpeningRangeBreakout()]
