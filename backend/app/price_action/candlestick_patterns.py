from typing import Callable, Dict, List, Optional

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


def detect_patterns(df: pd.DataFrame) -> List[PatternMatch]:
    matches: List[PatternMatch] = []
    for i in range(len(df)):
        matches.extend(detect_patterns_at(df, i))
    return matches
