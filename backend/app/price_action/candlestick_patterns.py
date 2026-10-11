from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from app.price_action.models import PatternMatch


def _metrics(row: pd.Series) -> Dict[str, float]:
    body = abs(row["close"] - row["open"])
    rng = row["high"] - row["low"]
    upper_wick = row["high"] - max(row["open"], row["close"])
    lower_wick = min(row["open"], row["close"]) - row["low"]
    return {"body": body, "range": rng, "upper_wick": upper_wick, "lower_wick": lower_wick}


def detect_doji(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    m = _metrics(df.iloc[i])
    if m["range"] <= 0:
        return None
    ratio = m["body"] / m["range"]
    if ratio <= 0.1:
        confidence = int(max(40, min(100, round((1 - ratio / 0.1) * 100))))
        return PatternMatch(timestamp=df.index[i], pattern="Doji", direction="NEUTRAL", confidence=confidence)
    return None


def detect_hammer(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    m = _metrics(df.iloc[i])
    if m["range"] <= 0 or m["body"] <= 0:
        return None
    if m["lower_wick"] >= 2 * m["body"] and m["upper_wick"] <= 0.3 * m["body"]:
        confidence = int(min(100, 50 + (m["lower_wick"] / m["body"]) * 10))
        return PatternMatch(timestamp=df.index[i], pattern="Hammer", direction="BULLISH", confidence=confidence)
    return None


def detect_shooting_star(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    m = _metrics(df.iloc[i])
    if m["range"] <= 0 or m["body"] <= 0:
        return None
    if m["upper_wick"] >= 2 * m["body"] and m["lower_wick"] <= 0.3 * m["body"]:
        confidence = int(min(100, 50 + (m["upper_wick"] / m["body"]) * 10))
        return PatternMatch(timestamp=df.index[i], pattern="Shooting Star", direction="BEARISH", confidence=confidence)
    return None


def detect_bullish_engulfing(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    if i == 0:
        return None
    prev, cur = df.iloc[i - 1], df.iloc[i]
    prev_bearish = prev["close"] < prev["open"]
    cur_bullish = cur["close"] > cur["open"]
    if prev_bearish and cur_bullish and cur["open"] <= prev["close"] and cur["close"] >= prev["open"]:
        prev_body = abs(prev["close"] - prev["open"])
        cur_body = abs(cur["close"] - cur["open"])
        confidence = int(min(100, 40 + (cur_body / max(prev_body, 1e-9)) * 20))
        return PatternMatch(timestamp=df.index[i], pattern="Bullish Engulfing", direction="BULLISH", confidence=confidence)
    return None


def detect_bearish_engulfing(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    if i == 0:
        return None
    prev, cur = df.iloc[i - 1], df.iloc[i]
    prev_bullish = prev["close"] > prev["open"]
    cur_bearish = cur["close"] < cur["open"]
    if prev_bullish and cur_bearish and cur["open"] >= prev["close"] and cur["close"] <= prev["open"]:
        prev_body = abs(prev["close"] - prev["open"])
        cur_body = abs(cur["close"] - cur["open"])
        confidence = int(min(100, 40 + (cur_body / max(prev_body, 1e-9)) * 20))
        return PatternMatch(timestamp=df.index[i], pattern="Bearish Engulfing", direction="BEARISH", confidence=confidence)
    return None


def detect_morning_star(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    if i < 2:
        return None
    a, b, c = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    a_body = abs(a["close"] - a["open"])
    b_body = abs(b["close"] - b["open"])
    c_body = abs(c["close"] - c["open"])
    if a_body <= 0:
        return None
    midpoint = (a["open"] + a["close"]) / 2
    if a["close"] < a["open"] and b_body <= 0.4 * a_body and c["close"] > c["open"] and c["close"] >= midpoint:
        confidence = int(min(100, 50 + (c_body / a_body) * 20))
        return PatternMatch(timestamp=df.index[i], pattern="Morning Star", direction="BULLISH", confidence=confidence)
    return None


def detect_evening_star(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    if i < 2:
        return None
    a, b, c = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    a_body = abs(a["close"] - a["open"])
    b_body = abs(b["close"] - b["open"])
    c_body = abs(c["close"] - c["open"])
    if a_body <= 0:
        return None
    midpoint = (a["open"] + a["close"]) / 2
    if a["close"] > a["open"] and b_body <= 0.4 * a_body and c["close"] < c["open"] and c["close"] <= midpoint:
        confidence = int(min(100, 50 + (c_body / a_body) * 20))
        return PatternMatch(timestamp=df.index[i], pattern="Evening Star", direction="BEARISH", confidence=confidence)
    return None


def detect_pin_bar(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    m = _metrics(df.iloc[i])
    if m["range"] <= 0:
        return None
    if m["lower_wick"] >= 0.6 * m["range"] and m["body"] <= 0.3 * m["range"]:
        confidence = int(min(100, (m["lower_wick"] / m["range"]) * 100))
        return PatternMatch(timestamp=df.index[i], pattern="Pin Bar", direction="BULLISH", confidence=confidence)
    if m["upper_wick"] >= 0.6 * m["range"] and m["body"] <= 0.3 * m["range"]:
        confidence = int(min(100, (m["upper_wick"] / m["range"]) * 100))
        return PatternMatch(timestamp=df.index[i], pattern="Pin Bar", direction="BEARISH", confidence=confidence)
    return None


def detect_inside_bar(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    if i == 0:
        return None
    prev, cur = df.iloc[i - 1], df.iloc[i]
    prev_range = prev["high"] - prev["low"]
    if prev_range <= 0:
        return None
    if cur["high"] <= prev["high"] and cur["low"] >= prev["low"]:
        containment = 1 - (cur["high"] - cur["low"]) / prev_range
        confidence = int(max(40, min(100, containment * 100)))
        return PatternMatch(timestamp=df.index[i], pattern="Inside Bar", direction="NEUTRAL", confidence=confidence)
    return None


def detect_outside_bar(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    if i == 0:
        return None
    prev, cur = df.iloc[i - 1], df.iloc[i]
    prev_range = prev["high"] - prev["low"]
    if prev_range <= 0:
        return None
    if cur["high"] >= prev["high"] and cur["low"] <= prev["low"]:
        direction = "BULLISH" if cur["close"] > cur["open"] else "BEARISH"
        expansion = (cur["high"] - cur["low"]) / prev_range
        confidence = int(min(100, 40 + expansion * 15))
        return PatternMatch(timestamp=df.index[i], pattern="Outside Bar", direction=direction, confidence=confidence)
    return None


def detect_strong_rejection(df: pd.DataFrame, i: int) -> Optional[PatternMatch]:
    m = _metrics(df.iloc[i])
    if m["range"] <= 0:
        return None
    dominant_wick = max(m["upper_wick"], m["lower_wick"])
    wick_ratio = dominant_wick / m["range"]
    if wick_ratio >= 0.6 and m["body"] / m["range"] <= 0.3:
        direction = "BEARISH" if m["upper_wick"] > m["lower_wick"] else "BULLISH"
        confidence = int(min(100, wick_ratio * 100))
        return PatternMatch(
            timestamp=df.index[i], pattern="Strong Rejection Candle", direction=direction, confidence=confidence
        )
    return None


DETECTORS: List[Callable[[pd.DataFrame, int], Optional[PatternMatch]]] = [
    detect_doji,
    detect_hammer,
    detect_shooting_star,
    detect_bullish_engulfing,
    detect_bearish_engulfing,
    detect_morning_star,
    detect_evening_star,
    detect_pin_bar,
    detect_inside_bar,
    detect_outside_bar,
    detect_strong_rejection,
]


def detect_patterns_at(df: pd.DataFrame, i: int) -> List[PatternMatch]:
    matches = []
    for detector in DETECTORS:
        match = detector(df, i)
        if match is not None:
            matches.append(match)
    return matches


def pattern_masks(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """S5-A: every detector above on every bar at once (numpy over shifted columns), as booleans by screen name, with the
    direction split where a detector has two. Same rules as the detectors bar by bar (parity-tested); the first two bars
    are False, since the multi-bar patterns need the bars before them."""
    o, h, lo_, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))

    def shift(a: np.ndarray, k: int) -> np.ndarray:
        out = np.full_like(a, np.nan)
        out[k:] = a[:-k] if k < len(a) else out[k:]
        return out

    with np.errstate(divide="ignore", invalid="ignore"):
        body, rng = np.abs(c - o), h - lo_
        upper, lower = h - np.maximum(o, c), np.minimum(o, c) - lo_
        ok = rng > 0
        po, pc, ph, pl = shift(o, 1), shift(c, 1), shift(h, 1), shift(lo_, 1)
        ao, ac = shift(o, 2), shift(c, 2)
        a_body, b_body, mid = np.abs(ac - ao), np.abs(pc - po), (ao + ac) / 2
        pin_bull = ok & (lower >= 0.6 * rng) & (body <= 0.3 * rng)
        pin_bear = ok & ~pin_bull & (upper >= 0.6 * rng) & (body <= 0.3 * rng)
        rejection = ok & (np.maximum(upper, lower) / rng >= 0.6) & (body / rng <= 0.3)
        outside = (ph - pl > 0) & (h >= ph) & (lo_ <= pl)
        masks = {
            "doji": ok & (body / rng <= 0.1),
            "hammer": ok & (body > 0) & (lower >= 2 * body) & (upper <= 0.3 * body),
            "shooting_star": ok & (body > 0) & (upper >= 2 * body) & (lower <= 0.3 * body),
            "bullish_engulfing": (pc < po) & (c > o) & (o <= pc) & (c >= po),
            "bearish_engulfing": (pc > po) & (c < o) & (o >= pc) & (c <= po),
            "morning_star": (a_body > 0) & (ac < ao) & (b_body <= 0.4 * a_body) & (c > o) & (c >= mid),
            "evening_star": (a_body > 0) & (ac > ao) & (b_body <= 0.4 * a_body) & (c < o) & (c <= mid),
            "bullish_pin_bar": pin_bull,
            "bearish_pin_bar": pin_bear,
            "inside_bar": (ph - pl > 0) & (h <= ph) & (lo_ >= pl),
            "bullish_outside_bar": outside & (c > o),
            "bearish_outside_bar": outside & ~(c > o),
            "bullish_rejection": rejection & ~(upper > lower),
            "bearish_rejection": rejection & (upper > lower),
        }
    for m in masks.values():
        m[:2] = False
    return masks


def detect_patterns(df: pd.DataFrame) -> List[PatternMatch]:
    matches: List[PatternMatch] = []
    for i in range(len(df)):
        matches.extend(detect_patterns_at(df, i))
    return matches
