"""v2 analysis modules: Supertrend, Fibonacci extensions, Pin Bar / Strong Body / Rejection / Gap Reversal,
gap analysis, S/R role reversal + freshness. Synthetic hand-built fixtures; each definition has positive and
negative cases. No predictive value is implied by any pattern."""
import numpy as np
import pandas as pd
import pytest

from src.analysis.candles import detect_candles
from src.analysis.indicators import add_indicators, fib_extensions, supertrend
from src.analysis.structure import Swing, gaps, sr_levels


def frame(rows, atr=2.0, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(rows), freq="B", tz="Asia/Kolkata")
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 1000.0
    df["atr14"] = atr
    return df


def names(df, i=-1):
    n = len(df)
    return {(c["pattern"], c["bias"]) for c in detect_candles(df, lookback=3) if c["i"] == (n + i if i < 0 else i)}


def test_supertrend_follows_trend_and_flips_without_lookahead():
    up = np.linspace(100, 150, 60)
    down = np.linspace(150, 90, 30)
    c = np.r_[up, down]
    df = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1.0},
                      index=pd.date_range("2026-01-01", periods=len(c), freq="B"))
    st = supertrend(df, 10, 3.0)
    assert st.st_dir.iloc[:9].isna().all()                                      # ATR warm-up -> NaN, not 0
    assert (st.st_dir.iloc[20:60] == 1).all() and (st.supertrend.iloc[20:60] < df.Close.iloc[20:60]).all()
    assert st.st_dir.iloc[-1] == -1 and st.supertrend.iloc[-1] > df.Close.iloc[-1]
    part = supertrend(df.iloc[:70], 10, 3.0)                                    # value at t uses only bars <= t
    assert np.allclose(part.supertrend.to_numpy(), st.supertrend.iloc[:70].to_numpy(), equal_nan=True)
    assert {"supertrend", "st_dir"} <= set(add_indicators(df, "1D").columns)


def test_fib_extensions():
    e = fib_extensions(100, 200, "bullish")
    assert e == pytest.approx({"127.2%": 227.2, "161.8%": 261.8, "200.0%": 300.0})
    assert fib_extensions(100, 200, "bearish")["161.8%"] == pytest.approx(38.2)


def test_pin_bar_definition():
    base = [(100, 101, 99, 100)] * 3
    assert ("Pin Bar", "bullish") in names(frame(base + [(108.5, 109, 100, 109)]))      # lower wick 8.5/9, body 0.5
    assert ("Pin Bar", "bearish") in names(frame(base + [(100.5, 109, 100, 100)]))
    assert not {p for p, _ in names(frame(base + [(100, 109, 100, 108)]))} & {"Pin Bar"}  # big body: no pin


def test_strong_body_and_rejection():
    base = [(100, 101, 99, 100)] * 3
    assert ("Strong Body", "bullish") in names(frame(base + [(100, 105.2, 99.9, 105)]))   # body 5/5.3, range > ATR
    assert ("Strong Body", "bullish") not in names(frame(base + [(100, 101.2, 99.9, 101)], atr=3.0))  # range < ATR
    rej = names(frame(base + [(103.5, 106, 100, 105)]))                                   # wick 3.5/6 (< pin 2/3), close upper half
    assert ("Rejection", "bullish") in rej and ("Pin Bar", "bullish") not in rej


def test_gap_reversal_definition():
    base = [(100, 101, 99, 100)] * 3
    assert ("Gap Reversal", "bearish") in names(frame(base + [(102, 102.5, 98, 98.5)]))   # gap up 2 >= 0.5*ATR, closes below prior close
    assert ("Gap Reversal", "bullish") in names(frame(base + [(98, 102, 97.5, 101)]))
    assert ("Gap Reversal", "bearish") not in names(frame(base + [(102, 104, 101.5, 103)]))  # gap held -> no reversal


def test_gaps_detect_size_and_fill():
    df = frame([(100, 101, 99, 100), (103, 104, 102.5, 103.5), (103, 103.5, 99.5, 100)])   # gap up 3, filled on bar 2
    g = gaps(df, min_atr=0.5)
    assert len(g) == 1 and g[0]["direction"] == "up" and g[0]["size_atr"] == pytest.approx(1.5)
    assert g[0]["filled"] and g[0]["filled_i"] == 2
    assert not gaps(df.iloc[:2])[0]["filled"]                                               # no later bar yet


def test_sr_role_reversal_and_freshness():
    df = frame([(100, 101, 99, 100)] * 30)
    df.loc[df.index[25:], "Close"] = 112.0                                                 # price now above the old highs
    sw = [Swing(5, "H", 110.0, 8), Swing(12, "H", 110.4, 15)]
    lv = sr_levels(sw, atr_last=2.0, last_close=112.0, tol_atr=0.5, df=df)[0]
    assert lv["origin"] == "resistance" and lv["role"] == "support" and lv["role_reversal"] is True
    assert lv["bars_since_touch"] == 30 - 1 - 12 and lv["crosses"] == 1
    assert "origin" not in sr_levels(sw, 2.0, 112.0, 0.5)[0]                              # old signature unchanged
