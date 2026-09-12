from typing import List

import pandas as pd

from app.core.resampling import resample_ohlc
from app.indicators.vwap import session_vwap
from app.price_action.models import SwingPoint
from app.support_resistance.models import SRZone


def _level_zone(kind: str, price: float, buffer: float, timeframe: str, source: str, score: int) -> SRZone:
    return SRZone(
        kind=kind, lower=price - buffer, upper=price + buffer, mid=price,
        strength_score=score, timeframe=timeframe, source=source,
    )


def previous_day_levels(df: pd.DataFrame) -> List[SRZone]:
    daily = resample_ohlc(df, "1D")
    if len(daily) < 2:
        return []
    prev_day = daily.iloc[-2]
    buffer = (prev_day["high"] - prev_day["low"]) * 0.001 or prev_day["high"] * 0.0005
    return [
        _level_zone("RESISTANCE", prev_day["high"], buffer, "1D", "prev_day_high", 50),
        _level_zone("SUPPORT", prev_day["low"], buffer, "1D", "prev_day_low", 50),
    ]


def previous_week_levels(df: pd.DataFrame) -> List[SRZone]:
    weekly = resample_ohlc(df, "1W")
    if len(weekly) < 2:
        return []
    prev_week = weekly.iloc[-2]
    buffer = (prev_week["high"] - prev_week["low"]) * 0.001 or prev_week["high"] * 0.0005
    return [
        _level_zone("RESISTANCE", prev_week["high"], buffer, "1W", "prev_week_high", 50),
        _level_zone("SUPPORT", prev_week["low"], buffer, "1W", "prev_week_low", 50),
    ]


def opening_range_zone(df: pd.DataFrame, minutes: int = 15) -> List[SRZone]:
    last_date = df.index[-1].date()
    day_df = df[df.index.date == last_date]
    if day_df.empty:
        return []
    session_start = day_df.index[0]
    window = day_df[day_df.index < session_start + pd.Timedelta(minutes=minutes)]
    if window.empty:
        return []

    high, low = window["high"].max(), window["low"].min()
    if high <= low:
        return []
    buffer = (high - low) * 0.05
    return [
        _level_zone("RESISTANCE", high, buffer, "opening_range", "opening_range_high", 45),
        _level_zone("SUPPORT", low, buffer, "opening_range", "opening_range_low", 45),
    ]


def pivot_levels(df: pd.DataFrame) -> List[SRZone]:
    daily = resample_ohlc(df, "1D")
    if len(daily) < 2:
        return []
    prev = daily.iloc[-2]
    pp = (prev["high"] + prev["low"] + prev["close"]) / 3
    day_range = prev["high"] - prev["low"]

    levels = [
        ("PP", pp, "RESISTANCE"),
        ("R1", 2 * pp - prev["low"], "RESISTANCE"),
        ("R2", pp + day_range, "RESISTANCE"),
        ("R3", prev["high"] + 2 * (pp - prev["low"]), "RESISTANCE"),
        ("S1", 2 * pp - prev["high"], "SUPPORT"),
        ("S2", pp - day_range, "SUPPORT"),
        ("S3", prev["low"] - 2 * (prev["high"] - pp), "SUPPORT"),
    ]
    zones = []
    for name, price, kind in levels:
        buffer = abs(price) * 0.001 or 0.01
        zones.append(_level_zone(kind, price, buffer, "1D", f"pivot_{name}", 55))
    return zones


def fibonacci_levels(df: pd.DataFrame, alternating_swings: List[SwingPoint]) -> List[SRZone]:
    recent_high = next((s for s in reversed(alternating_swings) if s.kind == "HIGH"), None)
    recent_low = next((s for s in reversed(alternating_swings) if s.kind == "LOW"), None)
    if recent_high is None or recent_low is None or recent_high.price <= recent_low.price:
        return []

    diff = recent_high.price - recent_low.price
    kind = "SUPPORT" if recent_low.timestamp < recent_high.timestamp else "RESISTANCE"

    zones = []
    for ratio in (0.236, 0.382, 0.5, 0.618, 0.786):
        level = recent_high.price - diff * ratio
        buffer = diff * 0.005
        zones.append(_level_zone(kind, level, buffer, "fibonacci", f"fib_{ratio}", 45))
    return zones


def vwap_zone(df: pd.DataFrame) -> List[SRZone]:
    vwap = session_vwap(df)
    latest_vwap = vwap.iloc[-1]
    if pd.isna(latest_vwap):
        return []
    latest_close = df["close"].iloc[-1]
    kind = "SUPPORT" if latest_close >= latest_vwap else "RESISTANCE"
    buffer = latest_vwap * 0.001
    return [_level_zone(kind, latest_vwap, buffer, "session", "vwap", 50)]
