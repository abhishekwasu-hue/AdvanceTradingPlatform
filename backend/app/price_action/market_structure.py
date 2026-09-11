from typing import List, Optional

import pandas as pd

from app.price_action.models import MarketStructureResult, StructureEvent, SwingPoint, TrendState
from app.price_action.swings import alternate_swings, find_swings


def _label_swings(swings: List[SwingPoint]) -> None:
    last_high: Optional[float] = None
    last_low: Optional[float] = None
    for swing in swings:
        if swing.kind == "HIGH":
            if last_high is not None:
                swing.label = "HH" if swing.price > last_high else "LH"
            last_high = swing.price
        else:
            if last_low is not None:
                swing.label = "HL" if swing.price > last_low else "LL"
            last_low = swing.price


def _trend_from_labels(swings: List[SwingPoint], upto_index: int) -> TrendState:
    last_high_label = next(
        (s.label for s in reversed(swings[: upto_index + 1]) if s.kind == "HIGH" and s.label), None
    )
    last_low_label = next(
        (s.label for s in reversed(swings[: upto_index + 1]) if s.kind == "LOW" and s.label), None
    )
    if last_high_label == "HH" and last_low_label == "HL":
        return TrendState.UPTREND
    if last_high_label == "LH" and last_low_label == "LL":
        return TrendState.DOWNTREND
    return TrendState.RANGE


def _detect_break_events(df: pd.DataFrame, swings: List[SwingPoint]) -> List[StructureEvent]:
    """Walks bars chronologically, tracking the most recent confirmed swing high/low and the
    trend implied by swing labels so far, and emits a BOS when price breaks in the direction of
    that trend or a CHoCH when it breaks against it - each level fires at most once per break.
    """
    events: List[StructureEvent] = []
    if len(swings) < 2:
        return events

    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None
    broken_high = False
    broken_low = False
    current_trend = TrendState.RANGE
    swing_idx = 0
    close = df["close"]

    for i, ts in enumerate(df.index):
        while swing_idx < len(swings) and swings[swing_idx].timestamp <= ts:
            swing = swings[swing_idx]
            if swing.kind == "HIGH":
                last_swing_high = swing.price
                broken_high = False
            else:
                last_swing_low = swing.price
                broken_low = False
            current_trend = _trend_from_labels(swings, swing_idx)
            swing_idx += 1

        price = close.iloc[i]
        if last_swing_high is not None and not broken_high and price > last_swing_high:
            event_type = "BOS" if current_trend == TrendState.UPTREND else "CHoCH"
            events.append(StructureEvent(
                timestamp=ts, event=event_type, direction="BULLISH", level=last_swing_high,
                note=f"Close {price:.2f} broke above swing high {last_swing_high:.2f}",
            ))
            broken_high = True
        if last_swing_low is not None and not broken_low and price < last_swing_low:
            event_type = "BOS" if current_trend == TrendState.DOWNTREND else "CHoCH"
            events.append(StructureEvent(
                timestamp=ts, event=event_type, direction="BEARISH", level=last_swing_low,
                note=f"Close {price:.2f} broke below swing low {last_swing_low:.2f}",
            ))
            broken_low = True

    return events


def analyze_market_structure(df: pd.DataFrame, window: int = 3) -> MarketStructureResult:
    raw_swings = find_swings(df, window)
    swings = alternate_swings(raw_swings)
    _label_swings(swings)

    trend = _trend_from_labels(swings, len(swings) - 1) if swings else TrendState.RANGE
    events = _detect_break_events(df, swings)

    return MarketStructureResult(trend=trend, swings=swings, events=events)
