from typing import List

import numpy as np
import pandas as pd

from app.price_action.candlestick_patterns import (
    detect_bearish_engulfing,
    detect_bullish_engulfing,
    detect_doji,
    detect_hammer,
    detect_inside_bar,
    detect_outside_bar,
    detect_shooting_star,
    detect_strong_rejection,
)
from app.price_action.market_structure import analyze_market_structure
from app.price_action.models import TrendState
from app.price_action.swings import alternate_swings, find_swings
from tests.utils import make_series


def _zigzag(turning_points: List[float], bars_per_leg: int = 8) -> List[float]:
    prices: List[float] = []
    for a, b in zip(turning_points[:-1], turning_points[1:]):
        leg = list(np.linspace(a, b, bars_per_leg, endpoint=False))
        prices.extend(leg)
    prices.append(turning_points[-1])
    return prices


def _row(**kwargs) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02 09:15", periods=1, freq="1min")
    row = {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 1000, **kwargs}
    return pd.DataFrame([row], index=idx)


# --- swings --------------------------------------------------------------------

def test_find_swings_detects_zigzag_turning_points():
    prices = _zigzag([100, 130, 90, 140, 80])
    df = make_series(prices)
    swings = find_swings(df, window=3)
    kinds = [s.kind for s in swings]
    assert "HIGH" in kinds and "LOW" in kinds


def test_alternate_swings_collapses_consecutive_same_kind():
    from app.price_action.models import SwingPoint
    ts = pd.date_range("2024-01-02 09:15", periods=4, freq="1min")
    swings = [
        SwingPoint(timestamp=ts[0], price=100, kind="HIGH"),
        SwingPoint(timestamp=ts[1], price=105, kind="HIGH"),  # more extreme, should replace
        SwingPoint(timestamp=ts[2], price=90, kind="LOW"),
        SwingPoint(timestamp=ts[3], price=95, kind="LOW"),  # less extreme, should be dropped
    ]
    result = alternate_swings(swings)
    assert len(result) == 2
    assert result[0].price == 105
    assert result[1].price == 90


# --- market structure --------------------------------------------------------------------

def test_analyze_market_structure_detects_uptrend():
    prices = _zigzag([100, 110, 105, 118, 112, 126, 120, 135])
    df = make_series(prices)
    result = analyze_market_structure(df, window=3)
    assert result.trend == TrendState.UPTREND
    assert any(e.event == "BOS" and e.direction == "BULLISH" for e in result.events)


def test_analyze_market_structure_detects_downtrend():
    prices = _zigzag([135, 120, 126, 112, 118, 105, 110, 100])
    df = make_series(prices)
    result = analyze_market_structure(df, window=3)
    assert result.trend == TrendState.DOWNTREND
    assert any(e.event == "BOS" and e.direction == "BEARISH" for e in result.events)


def test_market_structure_labels_are_valid():
    prices = _zigzag([100, 110, 105, 118, 112, 126])
    df = make_series(prices)
    result = analyze_market_structure(df, window=3)
    for swing in result.swings:
        if swing.label is not None:
            assert swing.label in ("HH", "HL", "LH", "LL")


# --- candlestick patterns --------------------------------------------------------------------

def test_detect_hammer():
    df = _row(open=100, close=102, high=102.5, low=95)
    match = detect_hammer(df, 0)
    assert match is not None
    assert match.direction == "BULLISH"
    assert 0 <= match.confidence <= 100


def test_detect_shooting_star():
    df = _row(open=100, close=98, high=105, low=97.5)
    match = detect_shooting_star(df, 0)
    assert match is not None
    assert match.direction == "BEARISH"


def test_detect_doji():
    df = _row(open=100, close=100.05, high=101, low=99)
    match = detect_doji(df, 0)
    assert match is not None
    assert match.direction == "NEUTRAL"


def test_detect_bullish_engulfing():
    idx = pd.date_range("2024-01-02 09:15", periods=2, freq="1min")
    df = pd.DataFrame([
        {"open": 102, "close": 100, "high": 103, "low": 99, "volume": 1000},
        {"open": 99, "close": 103, "high": 104, "low": 98, "volume": 1200},
    ], index=idx)
    match = detect_bullish_engulfing(df, 1)
    assert match is not None
    assert match.direction == "BULLISH"


def test_detect_bearish_engulfing():
    idx = pd.date_range("2024-01-02 09:15", periods=2, freq="1min")
    df = pd.DataFrame([
        {"open": 98, "close": 101, "high": 102, "low": 97, "volume": 1000},
        {"open": 102, "close": 97, "high": 103, "low": 96, "volume": 1200},
    ], index=idx)
    match = detect_bearish_engulfing(df, 1)
    assert match is not None
    assert match.direction == "BEARISH"


def test_detect_inside_bar():
    idx = pd.date_range("2024-01-02 09:15", periods=2, freq="1min")
    df = pd.DataFrame([
        {"open": 100, "close": 102, "high": 110, "low": 90, "volume": 1000},
        {"open": 100, "close": 102, "high": 105, "low": 95, "volume": 1000},
    ], index=idx)
    match = detect_inside_bar(df, 1)
    assert match is not None
    assert match.direction == "NEUTRAL"


def test_detect_outside_bar():
    idx = pd.date_range("2024-01-02 09:15", periods=2, freq="1min")
    df = pd.DataFrame([
        {"open": 97, "close": 98, "high": 100, "low": 95, "volume": 1000},
        {"open": 92, "close": 104, "high": 105, "low": 90, "volume": 1000},
    ], index=idx)
    match = detect_outside_bar(df, 1)
    assert match is not None
    assert match.direction == "BULLISH"


def test_detect_strong_rejection():
    df = _row(open=100, close=101, high=110, low=99.5)
    match = detect_strong_rejection(df, 0)
    assert match is not None
    assert match.direction == "BEARISH"


def test_no_pattern_on_plain_candle():
    df = _row(open=100, close=100.5, high=101, low=99.8)
    # a small, unremarkable candle shouldn't fire hammer/shooting-star/rejection
    assert detect_hammer(df, 0) is None
    assert detect_shooting_star(df, 0) is None
