from typing import Callable, Dict, List, Literal, Optional

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


# --- AMW-3: candle v2 (Strong Body, Gap Reversal) and the next-bar confirmation status -------------------------------
# Re-implemented from the AMW screener's described behaviour (docs/design/AMW_PORT.md, row PA-6); no code copied.
# Both read ATR(14) of the bars up to this one (app/indicators/volatility.atr), so they are false on the first 13 bars.
# They are kept out of DETECTORS on purpose: PatternBullish / PatternBearish, the scanner and signal scoring keep their
# pattern set; the v2 candles are reached through ScreenQL `Pattern(name)` behind the `screenql_amw_functions` flag.
V2_ATR_PERIOD = 14
STRONG_BODY_RANGE = 0.7          # body >= 70 % of the bar's range
STRONG_BODY_ATR = 1.0            # range >= 1 x ATR(14)
GAP_REVERSAL_ATR = 0.5           # |open - previous close| >= 0.5 x ATR(14)
# the screen names of the v2 masks and the direction each one carries
V2_PATTERN_NAMES = ("bullish_strong_body", "bearish_strong_body", "bullish_gap_reversal", "bearish_gap_reversal")
# the direction of every screen pattern name (`pattern_masks` and the v2 masks); NEUTRAL ones have no confirmation
PATTERN_DIRECTION: Dict[str, str] = {
    "doji": "NEUTRAL", "inside_bar": "NEUTRAL", "hammer": "BULLISH", "shooting_star": "BEARISH", "morning_star": "BULLISH",
    "evening_star": "BEARISH", **{f"{d}_{k}": d.upper() for d in ("bullish", "bearish")
                                  for k in ("engulfing", "pin_bar", "outside_bar", "rejection", "strong_body", "gap_reversal")},
}
CONFIRM_STATES = ("any", "confirmed", "failed", "unconfirmed")


def _v2_atr(df: pd.DataFrame) -> np.ndarray:
    from app.indicators.volatility import atr
    return atr(df[["open", "high", "low", "close"]].astype(float), V2_ATR_PERIOD).to_numpy(float)


def detect_strong_body(df: pd.DataFrame, i: int, atr_values: Optional[np.ndarray] = None) -> Optional[PatternMatch]:
    """A wide-range bar that is nearly all body: body >= 70 % of the range and the range >= 1 x ATR(14) (ATR of the bars
    up to this one). Bullish when it closes above its open, bearish below."""
    m = _metrics(df.iloc[i])
    a = (atr_values if atr_values is not None else _v2_atr(df.iloc[:i + 1]))[i]
    if m["range"] <= 0 or not np.isfinite(a):
        return None
    if m["body"] >= STRONG_BODY_RANGE * m["range"] and m["range"] >= STRONG_BODY_ATR * a:
        direction: Literal["BULLISH", "BEARISH"] = "BULLISH" if df["close"].iloc[i] > df["open"].iloc[i] else "BEARISH"
        confidence = int(min(100, round(m["body"] / m["range"] * 100)))
        return PatternMatch(timestamp=df.index[i], pattern="Strong Body", direction=direction, confidence=confidence)
    return None


def detect_gap_reversal(df: pd.DataFrame, i: int, atr_values: Optional[np.ndarray] = None) -> Optional[PatternMatch]:
    """The bar opened beyond the previous close by >= 0.5 x ATR(14) and closed back on the other side of that close:
    a gap up closing below the previous close is bearish, a gap down closing above it is bullish."""
    if i == 0:
        return None
    a = (atr_values if atr_values is not None else _v2_atr(df.iloc[:i + 1]))[i]
    if not np.isfinite(a):
        return None
    prev_close, o, c = float(df["close"].iloc[i - 1]), float(df["open"].iloc[i]), float(df["close"].iloc[i])
    gap = o - prev_close
    direction: Literal["BULLISH", "BEARISH"]
    if gap >= GAP_REVERSAL_ATR * a and c < prev_close:
        direction = "BEARISH"
    elif -gap >= GAP_REVERSAL_ATR * a and c > prev_close:
        direction = "BULLISH"
    else:
        return None
    confidence = int(min(100, round(50 + abs(gap) / a * 20))) if a > 0 else 50
    return PatternMatch(timestamp=df.index[i], pattern="Gap Reversal", direction=direction, confidence=confidence)


V2_DETECTORS: List[Callable[..., Optional[PatternMatch]]] = [detect_strong_body, detect_gap_reversal]


def pattern_masks_v2(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """AMW-3: the v2 candles on every bar at once, by screen name (the same rules as detect_strong_body /
    detect_gap_reversal bar by bar - parity-tested). Bars without an ATR yet (the first 13) are False."""
    o, h, lo_, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = _v2_atr(df)
    with np.errstate(invalid="ignore"):
        body, rng = np.abs(c - o), h - lo_
        has_atr = np.isfinite(a)
        strong = has_atr & (rng > 0) & (body >= STRONG_BODY_RANGE * rng) & (rng >= STRONG_BODY_ATR * a)
        pc = np.r_[np.nan, c[:-1]]
        gap = o - pc
        bear_gap = has_atr & (gap >= GAP_REVERSAL_ATR * a) & (c < pc)
        bull_gap = has_atr & ~bear_gap & (-gap >= GAP_REVERSAL_ATR * a) & (c > pc)
    return {"bullish_strong_body": strong & (c > o), "bearish_strong_body": strong & ~(c > o),
            "bullish_gap_reversal": bull_gap, "bearish_gap_reversal": bear_gap}


def confirmation_masks(df: pd.DataFrame, mask: np.ndarray, direction: str) -> Dict[str, np.ndarray]:
    """AMW-3: the next-bar status of a directional pattern, known on the bar AFTER the pattern (never on the pattern bar
    itself, so it is causal). With the pattern on bar t-1, on bar t:

    - confirmed:   the close went beyond the pattern bar's far side (bullish: close > its high; bearish: close < its low);
    - failed:      the close went beyond the other side (bullish: close < its low; bearish: close > its high);
    - unconfirmed: neither - the close stayed inside the pattern bar's range (ends included).

    A NEUTRAL pattern (doji, inside bar) has no side to confirm: every status is False (the validator refuses it)."""
    n = len(mask)
    empty = {k: np.zeros(n, dtype=bool) for k in CONFIRM_STATES[1:]}
    if direction not in ("BULLISH", "BEARISH") or n < 2:
        return empty
    h, lo_, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    was = np.r_[False, np.asarray(mask, dtype=bool)[:-1]]
    ph, pl = np.r_[np.nan, h[:-1]], np.r_[np.nan, lo_[:-1]]
    with np.errstate(invalid="ignore"):
        up, down = c > ph, c < pl
    good, bad = (up, down) if direction == "BULLISH" else (down, up)
    return {"confirmed": was & good, "failed": was & bad, "unconfirmed": was & ~good & ~bad}


def detect_patterns(df: pd.DataFrame) -> List[PatternMatch]:
    matches: List[PatternMatch] = []
    for i in range(len(df)):
        matches.extend(detect_patterns_at(df, i))
    return matches
