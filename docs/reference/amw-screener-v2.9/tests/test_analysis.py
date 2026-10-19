"""Indicator / candle / structure / pattern / regime tests on SYNTHETIC fixtures."""
import numpy as np
import pandas as pd
import pytest

from src.analysis.candles import detect_candles
from src.analysis.context import analyze_tf, rsi_divergence
from src.analysis.indicators import (add_indicators, adx, atr, bollinger, ema, fib_levels, macd, relative_strength,
                                     relative_volume, rsi, sma, vwap_intraday)
from src.analysis.patterns import detect_patterns
from src.analysis.regime import classify_regime
from src.analysis.structure import find_swings, structure_events
from tests.conftest import intraday_session, make_ohlc, zigzag


def test_reference_values():
    s = pd.Series(np.arange(1, 31, dtype=float))
    e = ema(s, 10)
    assert e.iloc[:9].isna().all() and e.iloc[-1] == pytest.approx(s.ewm(span=10, adjust=False).mean().iloc[-1])
    assert sma(s, 5).iloc[-1] == pytest.approx(28.0)
    assert rsi(s, 14).iloc[-1] == pytest.approx(100.0) and rsi(s[::-1].reset_index(drop=True), 14).iloc[-1] == pytest.approx(0.0)
    df = make_ohlc([100.0] * 40, spread=1.0)
    assert atr(df, 14).iloc[-1] == pytest.approx(2.0)
    bb = bollinger(pd.Series([10.0] * 25))
    assert bb.bb_up.iloc[-1] == pytest.approx(10.0) and bb.bb_width.iloc[-1] == pytest.approx(0.0)
    m = macd(s)
    assert m.macd.iloc[-1] == pytest.approx(ema(s, 12).iloc[-1] - ema(s, 26).iloc[-1])


def test_adx_strong_uptrend():
    df = make_ohlc(np.linspace(100, 200, 80), spread=0.5)
    a = adx(df, 14)
    assert a.adx.iloc[-1] > 40 and a.plus_di.iloc[-1] > a.minus_di.iloc[-1]


def test_rvol_prior_bars_and_vwap():
    v = pd.Series([100.0] * 20 + [300.0])
    assert relative_volume(v, 20).iloc[-1] == pytest.approx(3.0)
    df = intraday_session([100, 102, 104], vol=[100.0, 100.0, 200.0], spread=0.0)
    tp = (df.High + df.Low + df.Close) / 3
    assert vwap_intraday(df).iloc[-1] == pytest.approx((tp * df.Volume).sum() / df.Volume.sum())
    df.iloc[1, df.columns.get_loc("Volume")] = np.nan
    assert vwap_intraday(df).isna().all()         # incomplete volume -> no VWAP for the session


def test_rs_and_fib():
    s = pd.Series(np.linspace(100, 120, 60))
    b = pd.Series(np.linspace(100, 110, 60))
    assert relative_strength(s, b, 50) > 0 and relative_strength(s.iloc[:10], b.iloc[:10], 50) is None
    f = fib_levels(100, 200, "bullish")
    assert f["50.0%"] == pytest.approx(150) and f["61.8%"] == pytest.approx(138.2)


def test_no_artificial_zeros_on_short_history():
    out = add_indicators(make_ohlc(np.linspace(100, 110, 30)), "1D")
    assert out.ema200.isna().all() and out.ema20.notna().iloc[-1] and out.vwap.isna().all()


def _frame(rows, prior=None):
    pre = make_ohlc(prior if prior is not None else np.linspace(120, 100, 12), spread=0.5)
    ex = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"])
    ex["Volume"] = 1000.0
    ex.index = pd.date_range(pre.index[-1] + pd.Timedelta(days=1), periods=len(rows), freq="B")
    df = pd.concat([pre, ex])
    df["atr14"] = atr(df, 14)
    return df


def test_candles_in_context():
    assert "Bullish Engulfing" in {p["pattern"] for p in detect_candles(_frame([[100, 100.5, 98, 98.5], [98.2, 101.5, 98.0, 101.2]]))}
    assert "Hammer" in {p["pattern"] for p in detect_candles(_frame([[100, 101.2, 95, 101]]))}
    assert "Shooting Star" in {p["pattern"] for p in detect_candles(_frame([[120, 125, 118.8, 119]], prior=np.linspace(100, 120, 12)))}
    # same hammer shape in an UPTREND is not a hammer
    assert "Hammer" not in {p["pattern"] for p in detect_candles(_frame([[120, 121.2, 115, 121]], prior=np.linspace(100, 120, 12)))}
    res = detect_candles(_frame([[100, 104, 96, 101], [101, 103, 98, 100], [100, 106, 95, 105.5], [105.5, 108, 105, 107.5]]))
    ob = [p for p in res if p["pattern"] == "Outside Bar" and p["i"] == 14][0]
    assert ob["status"] == "confirmed"


def test_swings_confirmation_and_non_repainting():
    closes = zigzag([100, 110, 104, 116, 108, 122], seg=6)
    sw = find_swings(make_ohlc(closes, spread=0.2), 3)
    assert all(s.confirmed_at == s.i + 3 for s in sw) and {"HH", "HL"} <= {s.label for s in sw}
    sw2 = find_swings(make_ohlc(closes + [121, 119, 117, 115], spread=0.2), 3)
    assert [(s.i, s.price) for s in sw] == [(s.i, s.price) for s in sw2[:len(sw)]]


def test_bos_choch_close_only():
    df = make_ohlc(zigzag([100, 110, 104, 116, 108], seg=6) + [112, 118, 117, 116, 114] + list(np.linspace(108, 95, 8)), spread=0.2)
    ev, trend = structure_events(df, find_swings(df, 3))
    assert ("BOS", "bullish") in [(e.type, e.direction) for e in ev] and ("CHoCH", "bearish") in [(e.type, e.direction) for e in ev]
    assert trend == "down"
    for e in ev:  # event never uses a swing before it was confirmed
        assert e.i >= e.swing_i + 3
    dw = make_ohlc(zigzag([100, 110, 104], seg=6) + [105, 106, 107, 108, 109, 109.5], spread=0.2)
    sw = find_swings(dw, 3)
    hi = [s for s in sw if s.kind == "H"][0]
    dw.iloc[-1, dw.columns.get_loc("High")] = hi.price + 2
    assert not any(e.direction == "bullish" for e in structure_events(dw, sw)[0])


def test_double_top_status():
    df = make_ohlc(zigzag([90, 110, 100, 110.3, 96, 92], seg=8), spread=0.5)
    df["atr14"] = atr(df, 14)
    dt = [p for p in detect_patterns(df, find_swings(df, 3), float(df.atr14.iloc[-1])) if p["pattern"] == "Double Top"]
    assert dt and dt[0]["status"] in ("confirmed", "completed")


def test_regime_classification():
    up = make_ohlc(np.linspace(100, 200, 260) + np.sin(np.arange(260)), spread=0.5)
    assert classify_regime(analyze_tf(up, "1D", {"analysis": _A})) ["regime"] == "trending_bullish"
    flat = make_ohlc(100 + 2 * np.sin(np.arange(260) / 3), spread=0.5)
    assert classify_regime(analyze_tf(flat, "1D", {"analysis": _A}))["regime"] in ("range_bound", "transition")
    assert classify_regime(None)["regime"] == "uncertain"


def test_rsi_divergence_detected():
    # fast crash to low 1, bounce, slow grind to a slightly lower low 2 -> RSI higher at low 2
    path = list(np.linspace(150, 100, 15)) + list(np.linspace(100, 115, 8)) + list(np.linspace(115, 98, 25)) + list(np.linspace(98, 108, 8))
    t = analyze_tf(make_ohlc(path, spread=0.3), "1D", {"analysis": _A})
    out = rsi_divergence(t, "bullish", max_age=30)
    assert out is not None and "bullish RSI divergence" in out
    assert rsi_divergence(t, "bearish", max_age=30) is None


_A = {"swing_k": 3, "equal_level_atr": 0.15, "sr_cluster_atr": 0.5, "zone_departure_atr": 1.3, "pattern_tolerance_atr": 0.6,
      "compression_ratio": 0.75, "rs_lookback": 50}
