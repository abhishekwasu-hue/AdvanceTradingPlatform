from typing import List, Literal, Optional

import pandas as pd

from app.price_action.models import MarketStructureResult, StructureEvent, SwingPoint, TrendState
from app.price_action.swings import alternate_swings, find_swings, swing_indices


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


def _push_swing(known: List[SwingPoint], swing: SwingPoint) -> bool:
    """One step of `alternate_swings` + `_label_swings`, done incrementally: appends the swing, or replaces the last one
    when it is the same kind and more extreme, and labels it against the previous swing of its kind. False when the
    swing is dropped (same kind, not more extreme). After each push `known` equals what `alternate_swings` and
    `_label_swings` give on the swings pushed so far."""
    if known and known[-1].kind == swing.kind:
        last = known[-1]
        more = swing.price > last.price if swing.kind == "HIGH" else swing.price < last.price
        if not more:
            return False
        known.pop()
    previous = next((s for s in reversed(known) if s.kind == swing.kind), None)
    if previous is not None:
        if swing.kind == "HIGH":
            swing.label = "HH" if swing.price > previous.price else "LH"
        else:
            swing.label = "HL" if swing.price > previous.price else "LL"
    known.append(swing)
    return True


def _detect_break_events(df: pd.DataFrame, window: int) -> List[StructureEvent]:
    """Walks bars chronologically, tracking the most recent swing high/low and the trend implied by swing labels so
    far, and emits a BOS when price breaks in the direction of that trend or a CHoCH when it breaks against it - each
    level fires at most once per break.

    Z10-a (U3 G2): a swing is used only from the bar that CONFIRMS it - `window` bars after the pivot, the last bar
    `find_swings` needs to call it a swing - never from the pivot bar itself. Before, a swing counted from its own bar,
    so the event history depended on bars that had not happened yet: a break seen in real time could vanish (or a new
    one appear) once later bars arrived. Now the events up to any bar are exactly those `analyze_market_structure`
    gives on the bars up to it (the no-look-ahead harness checks this), and events need two swings known at that bar."""
    events: List[StructureEvent] = []
    high_idx, low_idx = swing_indices(df, window)
    highs, lows = df["high"].to_numpy(float), df["low"].to_numpy(float)
    # find_swings order: by bar, a swing high before a swing low on the same bar
    raw = sorted([(i, 0) for i in high_idx] + [(i, 1) for i in low_idx])
    known: List[SwingPoint] = []
    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None
    broken_high = False
    broken_low = False
    current_trend = TrendState.RANGE
    k = 0
    close = df["close"].to_numpy(float)

    for i, ts in enumerate(df.index):
        while k < len(raw) and raw[k][0] + window <= i:
            j, low_side = raw[k]
            swing = SwingPoint(timestamp=df.index[j], price=float(lows[j] if low_side else highs[j]), kind="LOW" if low_side else "HIGH")
            k += 1
            if not _push_swing(known, swing):
                continue
            if swing.kind == "HIGH":
                last_swing_high = swing.price
                broken_high = False
            else:
                last_swing_low = swing.price
                broken_low = False
            current_trend = _trend_from_labels(known, len(known) - 1)

        if len(known) < 2:
            continue
        price = close[i]
        if last_swing_high is not None and not broken_high and price > last_swing_high:
            event_type: Literal["BOS", "CHoCH"] = "BOS" if current_trend == TrendState.UPTREND else "CHoCH"
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
    events = _detect_break_events(df, window)

    return MarketStructureResult(trend=trend, swings=swings, events=events)
