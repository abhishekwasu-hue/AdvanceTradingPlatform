"""Z10-a (U3 G1): the shipped indicators against reference formulas (tests/golden_reference.py - plain loops, no
pandas), value by value, on generated session-shaped bars (flat bars, zero-volume bars and overnight gaps included):

* app.indicators (what the strategies, the scanner and the backtests compute);
* the Strategy Builder operands (`declarative.Operand`: VWAP, session levels, opening range ...);
* the ScreenQL functions that compute a number per bar (through the runtime).

Plus hand-checked small cases with exact numbers, the Wilder-seed convergence against TA-Lib / TradingView's SMA seed
(OPEN_QUESTIONS Z10-2), and a canonical shape (fires) and a near miss (does not) for each candlestick pattern."""
from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence

import numpy as np
import pandas as pd
import pytest

from app.indicators import directional, momentum, trend, volatility, vwap
from app.price_action.candlestick_patterns import pattern_masks
from app.quality.lookahead import synthetic_bars
from app.screener.parser import parse
from app.screener.registry import PATTERN_NAMES
from app.screener.runtime import SymbolData, evaluate
from app.strategy_engine.declarative import Operand
from tests import golden_reference as ref

RTOL = 1e-9


def assert_matches(got: Any, want: Sequence[Optional[float]], what: str, rtol: float = RTOL) -> None:
    values = got.to_numpy(dtype=float) if isinstance(got, pd.Series) else np.asarray(got, dtype=float)
    assert len(values) == len(want), (what, len(values), len(want))
    for i, (g, w) in enumerate(zip(values, want)):
        if w is None:
            assert math.isnan(g), f"{what} at bar {i}: {g} where the reference has no value yet"
        else:
            assert not math.isnan(g), f"{what} at bar {i}: no value where the reference has {w}"
            assert math.isclose(g, w, rel_tol=rtol, abs_tol=1e-9), f"{what} at bar {i}: {g} vs reference {w}"


def _cols(f: pd.DataFrame):
    return (list(map(float, f[k])) for k in ("open", "high", "low", "close", "volume"))


DATA = [("5m", 21), ("5m", 22), ("1d", 23)]


@pytest.fixture(params=DATA, ids=[f"{tf}-seed{s}" for tf, s in DATA])
def bars(request) -> pd.DataFrame:
    tf, seed = request.param
    return synthetic_bars(500 if tf == "5m" else 300, tf=tf, seed=seed)


# ----------------------------------------------------------------------------------------------- hand-checked values
def test_hand_checked_small_cases():
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert_matches(trend.sma(s, 3), [None, None, 2, 3, 4, 5, 6, 7, 8, 9], "SMA 3")
    # EMA 3: alpha 0.5 seeded on 1 -> 1, 1.5, 2.25, 3.125, 4.0625 (shown from the third value)
    assert_matches(trend.ema(s.iloc[:5], 3), [None, None, 2.25, 3.125, 4.0625], "EMA 3")
    # RSI 2 of 1,2,1,2,1: gains 1,0,1,0 / losses 0,1,0,1 smoothed with alpha 0.5 -> RS 1, 3, 0.6
    assert_matches(momentum.rsi(pd.Series([1.0, 2, 1, 2, 1]), 2), [None, None, 50.0, 75.0, 37.5], "RSI 2")
    assert momentum.rsi(pd.Series(np.arange(1.0, 40.0)), 14).iloc[-1] == 100.0              # never a loss
    assert momentum.rsi(pd.Series(np.arange(40.0, 1.0, -1)), 14).iloc[-1] == 0.0              # never a gain
    df = pd.DataFrame({"open": [9.0, 9, 11], "high": [10.0, 12, 11], "low": [8.0, 9, 7], "close": [9.0, 11, 8]})
    assert_matches(volatility.true_range(df), [2.0, 3.0, 4.0], "TR")                          # 10-8; 12-9; |7-11|
    assert_matches(volatility.atr(df, 2), [None, 2.5, 3.25], "ATR 2")
    bb = volatility.bollinger(pd.Series([1.0, 2, 3, 4, 5]), 3, 2.0)                           # sd of 1,2,3 = 1
    assert_matches(bb["upper"], [None, None, 4.0, 5.0, 6.0], "BB upper")
    assert_matches(bb["lower"], [None, None, 0.0, 1.0, 2.0], "BB lower")


# ------------------------------------------------------------------------------------------------- app.indicators
def test_indicators_match_the_reference_formulas(bars):
    o, h, lo, c, v = _cols(bars)
    close = bars["close"]
    for n in (5, 14, 20):
        assert_matches(trend.sma(close, n), ref.sma(c, n), f"SMA {n}")
        assert_matches(trend.ema(close, n), ref.ema(c, n), f"EMA {n}")
        assert_matches(momentum.rsi(close, n), ref.rsi(c, n), f"RSI {n}")
        assert_matches(volatility.atr(bars, n), ref.atr(h, lo, c, n), f"ATR {n}")
        got, want = directional.adx(bars, n), ref.adx(h, lo, c, n)
        for k in ("plus_di", "minus_di", "adx"):
            assert_matches(got[k], want[k], f"ADX {n} {k}")
    assert_matches(volatility.true_range(bars), ref.true_range(h, lo, c), "TR")
    for n, mult in ((10, 3.0), (7, 2.5)):
        got, want = volatility.supertrend(bars, n, mult), ref.supertrend(h, lo, c, n, mult)
        assert_matches(got["supertrend"], want["supertrend"], f"Supertrend {n},{mult}")
        assert list(got["trend"]) == want["trend"], f"Supertrend {n},{mult} direction"
        assert {1, -1} <= set(want["trend"]), "the data never flipped: not a test"
    got_bb, want_bb = volatility.bollinger(close, 20, 2.0), ref.bollinger(c, 20, 2.0)
    for k in ("mid", "upper", "lower"):
        assert_matches(got_bb[k], want_bb[k], f"Bollinger {k}")
    got_m, want_m = trend.macd(close), ref.macd(c, 12, 26, 9)
    for k in ("macd", "signal", "hist"):
        assert_matches(got_m[k], want_m[k], f"MACD {k}")
    stamps = list(bars.index.to_pydatetime())
    assert_matches(vwap.session_vwap(bars), ref.session_vwap(stamps, h, lo, c, v, twap_without_volume=False), "session VWAP")


def test_session_vwap_restarts_on_the_ist_date():
    """G1 fix (Z10-a): app.indicators.session_vwap grouped by the index's own date - the UTC date on a tz-aware index -
    while the operand, the strategies and the chart restart at the IST date. They differed wherever a session crosses
    UTC midnight (05:30 IST: 24x7 venues, early stamps). It now uses the IST trading date like the rest."""
    idx = pd.date_range("2026-03-02 16:00", periods=12, freq="1h", tz="UTC")                 # crosses 18:30 UTC = IST midnight
    f = pd.DataFrame({"open": 100.0, "high": np.arange(101.0, 113.0), "low": 99.0, "close": 100.0, "volume": 10.0}, index=idx)
    o, h, lo, c, v = _cols(f)
    want = ref.session_vwap(list(idx.to_pydatetime()), h, lo, c, v, twap_without_volume=False)
    assert_matches(vwap.session_vwap(f), want, "VWAP across IST midnight")
    assert want[2] != want[3]                                                               # 18:00 vs 19:00 UTC: a new IST day
    naive = f.tz_convert("Asia/Kolkata").tz_localize(None)                                  # exchange-time stamps
    assert_matches(vwap.session_vwap(naive), want, "VWAP on a naive IST index")


def test_wilder_seed_converges_to_the_sma_seed():
    """ATP seeds Wilder smoothing with the first value (pandas ewm(adjust=False)); TA-Lib and TradingView seed with
    the SMA of the first n. Early values differ; the difference decays by (1 - 1/n) a bar (OPEN_QUESTIONS Z10-2)."""
    f = synthetic_bars(800, tf="5m", seed=31)
    _, h, lo, c, _ = _cols(f)
    n = 14
    ours, theirs = ref.rsi(c, n), ref.rsi(c, n, smoother=ref.wilder_sma_seeded)
    assert_matches(momentum.rsi(f["close"], n), ours, "RSI (ATP seed)")
    diff = [abs(a - b) for a, b in zip(ours, theirs) if a is not None and b is not None]
    assert diff[0] > 0.1                                                    # they do differ at the start
    assert max(diff[10 * n:]) < 0.05 and max(diff[20 * n:]) < 1e-4          # and agree after 10 / 20 periods
    a_ours, a_theirs = ref.atr(h, lo, c, n), ref.atr(h, lo, c, n, smoother=ref.wilder_sma_seeded)
    rel = [abs(a - b) / b for a, b in zip(a_ours, a_theirs) if a is not None and b is not None]
    assert max(rel[10 * n:]) < 1e-3 and max(rel[20 * n:]) < 1e-6


# --------------------------------------------------------------------------------------------------------- operands
def test_strategy_builder_operands_match_the_reference(bars):
    if bars.index[1] - bars.index[0] >= pd.Timedelta(days=1):
        pytest.skip("session operands are intraday")
    o, h, lo, c, v = _cols(bars)
    stamps = list(bars.index.to_pydatetime())

    def op(name: str, period: int = 14, mult: float = 3.0) -> pd.Series:
        return Operand(type="indicator", indicator=name, period=period, multiplier=mult).series(bars, "5m")
    assert_matches(op("VWAP"), ref.session_vwap(stamps, h, lo, c, v, twap_without_volume=True), "VWAP")
    assert_matches(op("DAY_OPEN"), ref.day_open(stamps, o), "DAY_OPEN")
    assert_matches(op("PDH"), ref.previous_session(stamps, h, "max"), "PDH")
    assert_matches(op("PDL"), ref.previous_session(stamps, lo, "min"), "PDL")
    assert_matches(op("PDC"), ref.previous_session(stamps, c, "last"), "PDC")
    for minutes in (5, 15, 30):
        assert_matches(op("OR_HIGH", minutes), ref.opening_range(stamps, h, minutes, 5, "max"), f"OR_HIGH {minutes}")
        assert_matches(op("OR_LOW", minutes), ref.opening_range(stamps, lo, minutes, 5, "min"), f"OR_LOW {minutes}")
    assert_matches(op("VOLUME_SMA", 20), ref.sma(v, 20), "VOLUME_SMA")
    assert_matches(op("EMA", 9), ref.ema(c, 9), "EMA")
    assert_matches(op("RSI", 14), ref.rsi(c, 14), "RSI")
    assert_matches(op("ATR", 14), ref.atr(h, lo, c, 14), "ATR")
    assert_matches(op("PLUS_DI", 14), ref.adx(h, lo, c, 14)["plus_di"], "PLUS_DI")
    assert_matches(op("SUPERTREND", 10, 3.0), ref.supertrend(h, lo, c, 10, 3.0)["supertrend"], "SUPERTREND")
    assert_matches(op("BB_LOWER", 20, 2.0), ref.bollinger(c, 20, 2.0)["lower"], "BB_LOWER")
    # a symbol without volume (an index): the session's running mean of the typical price
    no_vol = bars.assign(volume=0.0)
    assert_matches(Operand(type="indicator", indicator="VWAP").series(no_vol, "5m"),
                   ref.session_vwap(stamps, h, lo, c, [0.0] * len(c), twap_without_volume=True), "VWAP without volume")


# --------------------------------------------------------------------------------------------------------- ScreenQL
def _sql(text: str, f: pd.DataFrame, tf: str) -> pd.Series:
    return evaluate(parse(text), SymbolData("S", {tf: f}), base_tf=tf)


def test_screenql_functions_match_the_reference(bars):
    tf = "1d" if bars.index[1] - bars.index[0] >= pd.Timedelta(days=1) else "5m"
    o, h, lo, c, v = _cols(bars)
    assert_matches(_sql("SMA(close, 10)", bars, tf), ref.sma(c, 10), "SMA")
    assert_matches(_sql("EMA(high, 12)", bars, tf), ref.ema(h, 12), "EMA")
    assert_matches(_sql("RSI(9)", bars, tf), ref.rsi(c, 9), "RSI")
    assert_matches(_sql("ATR(14)", bars, tf), ref.atr(h, lo, c, 14), "ATR")
    want = ref.adx(h, lo, c, 14)
    assert_matches(_sql("ADX(14)", bars, tf), want["adx"], "ADX")
    assert_matches(_sql("MinusDI(14)", bars, tf), want["minus_di"], "MinusDI")
    assert_matches(_sql("Supertrend(10, 3)", bars, tf), ref.supertrend(h, lo, c, 10, 3.0)["supertrend"], "Supertrend")
    assert_matches(_sql("BBUpper(20, 2)", bars, tf), ref.bollinger(c, 20, 2.0)["upper"], "BBUpper")
    assert_matches(_sql("Max(high, 7)", bars, tf), [None if i < 6 else max(h[i - 6:i + 1]) for i in range(len(h))], "Max")
    assert_matches(_sql("Min(low, 7)", bars, tf), [None if i < 6 else min(lo[i - 6:i + 1]) for i in range(len(lo))], "Min")
    assert_matches(_sql("Lag(close, 3)", bars, tf), [None] * 3 + c[:-3], "Lag")
    assert_matches(_sql("PctChange(close, 5)", bars, tf), [None] * 5 + [(c[i] / c[i - 5] - 1) * 100 for i in range(5, len(c))],
                   "PctChange", rtol=1e-7)
    z: List[Optional[float]] = []
    for i in range(len(c)):
        if i < 19:
            z.append(None)
            continue
        w = c[i - 19:i + 1]
        mu = sum(w) / 20
        sd = math.sqrt(sum((x - mu) ** 2 for x in w) / 19)
        z.append(None if sd == 0 else (c[i] - mu) / sd)
    assert_matches(_sql("ZScore(close, 20)", bars, tf), z, "ZScore", rtol=1e-6)
    streak, run = [], 0
    for oo, cc in zip(o, c):
        run = run + 1 if cc > oo else 0
        streak.append(float(run))
    assert_matches(_sql("CountStreak(close > open)", bars, tf), streak, "CountStreak")
    ups = [1.0 if cc > oo else 0.0 for oo, cc in zip(o, c)]
    assert_matches(_sql("Count(5, close > open)", bars, tf), [None if i < 4 else sum(ups[i - 4:i + 1]) for i in range(len(ups))], "Count")
    sma10 = ref.sma(c, 10)
    cross = [i > 0 and sma10[i] is not None and sma10[i - 1] is not None and c[i] > sma10[i] and c[i - 1] <= sma10[i - 1]  # type: ignore[operator]
             for i in range(len(c))]
    got = list(_sql("CrossAbove(close, SMA(close, 10))", bars, tf))
    # a close equal to its SMA (0.05 ticks make that common) is a floating-point tie either way: those bars are skipped
    tie = [s_ is not None and abs(x - s_) < 1e-9 for x, s_ in zip(c, sma10)]
    checked = [i for i in range(1, len(c)) if not (tie[i] or tie[i - 1])]
    assert [got[i] for i in checked] == [cross[i] for i in checked] and any(cross)
    if tf == "5m":
        stamps = list(bars.index.to_pydatetime())
        assert_matches(_sql("VWAP()", bars, tf), ref.session_vwap(stamps, h, lo, c, v, twap_without_volume=True), "VWAP")
        assert_matches(_sql("PDC()", bars, tf), ref.previous_session(stamps, c, "last"), "PDC")


# --------------------------------------------------------------------------------------------------------- patterns
NEUTRAL = (100.0, 101.0, 99.0, 100.5)          # o, h, l, c: a plain small bar
CANONICAL = {
    # name: (bars before the pattern bar(s), the pattern bar(s), a near miss for the last bar)
    "doji": ([], [(100, 102, 98, 100.1)], (100, 102, 98, 101)),                                   # body 0.1 / range 4; near: 1/4
    "hammer": ([], [(100, 101.2, 95, 101)], (100, 102.5, 95, 101)),                               # upper 0.2 <= 0.3 body; near: upper 1.5
    "shooting_star": ([], [(100, 106, 98.8, 99)], (100, 106, 97.5, 99)),
    "bullish_engulfing": ([(102, 102.5, 99.5, 100)], [(99.8, 103, 99.5, 102.5)], (100.2, 103, 99.5, 102.5)),
    "bearish_engulfing": ([(100, 102.5, 99.5, 102)], [(102.2, 102.5, 99, 99.5)], (101.8, 102.5, 99, 99.5)),
    "morning_star": ([(110, 110.5, 99.5, 100)], [(99, 99.5, 98, 99.5), (100, 107, 99.5, 106)], (100, 107, 99.5, 104)),
    "evening_star": ([(100, 110.5, 99.5, 110)], [(111, 112, 110.5, 110.5), (110, 110.5, 103, 104)], (110, 110.5, 103, 106)),
    "bullish_pin_bar": ([], [(103, 104, 96, 104)], (103, 106, 100, 103.5)),                       # lower 7 >= 0.6 x 8; near: 3/6
    "bearish_pin_bar": ([], [(97, 104, 96, 96)], (103, 106, 100, 102.5)),
    "inside_bar": ([(100, 105, 95, 102)], [(101, 104, 96, 103)], (101, 105.5, 96, 103)),
    "bullish_outside_bar": ([(100, 102, 99, 101)], [(99.5, 102.5, 98.5, 102)], (99.5, 101.5, 98.5, 102)),
    "bearish_outside_bar": ([(100, 102, 99, 101)], [(102, 102.5, 98.5, 99)], (102, 102.5, 99.5, 99)),
    "bullish_rejection": ([], [(103, 104, 96, 103.5)], (103, 106, 100, 103.5)),
    "bearish_rejection": ([], [(97, 104, 96, 96.5)], (103, 106, 100, 102.5)),
}


def _pattern_frame(rows: List[tuple]) -> pd.DataFrame:
    idx = pd.date_range("2026-03-02 03:45", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame([list(r) for r in rows], columns=["open", "high", "low", "close"], index=idx).astype(float).assign(volume=100.0)


@pytest.mark.parametrize("name", PATTERN_NAMES)
def test_each_pattern_fires_on_its_shape_and_not_on_a_near_miss(name):
    before, bars_, near = CANONICAL[name]
    rows = [NEUTRAL, NEUTRAL, *before, *bars_]
    hit = pattern_masks(_pattern_frame(rows))[name]
    assert hit[-1], f"{name} did not fire on its canonical shape"
    miss = pattern_masks(_pattern_frame(rows[:-1] + [near]))[name]
    assert not miss[-1], f"{name} fired on a near miss"
    assert not hit[:2].any()                                               # the first two bars never carry a pattern


def test_every_pattern_has_a_canonical_case():
    assert set(CANONICAL) == set(PATTERN_NAMES)


def test_vwap_of_a_session_without_volume_is_missing_not_a_crash():
    """G1 fix (Z10-a): an index has no volume. session_vwap raised TypeError there (pd.NA in an object column), and
    with it the S/R engine behind the scanner's zone filters and signal scoring. Now: no value until volume arrives."""
    from app.support_resistance.engine import SupportResistanceEngine
    f = synthetic_bars(150, tf="5m", seed=5).assign(volume=0.0)
    assert vwap.session_vwap(f).isna().all()
    assert all(z.source != "vwap" for z in SupportResistanceEngine().build_zones(f, "5m"))
    first_day = f.index.normalize() == f.index.normalize()[0]
    later = f.assign(volume=np.where(first_day, 0.0, 10.0))                 # volume only from the second session
    got = vwap.session_vwap(later)
    assert got[first_day].isna().all() and got[~first_day].notna().all()
