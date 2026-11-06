"""FN2-a (docs/design/FN2_GAP_AUDIT.md row "Indicators"): CCI, MFI, Williams %R, OBV and Aroon up / down - in
app/indicators and in the ScreenQL registry as true per-bar series.

Each indicator is checked against a straightforward reference written here (plain loops over Python lists, no pandas
windows), against a few hand-computed values on a small made-up series, is causal (a run on bars[0..t] gives exactly the
full run's value at t, for many t), and its ScreenQL entry is a series (not LAST_BAR_ONLY) with its default window and
its unit."""
import math

import numpy as np
import pandas as pd
import pytest

from app.indicators import oscillators, volume
from app.quality.lookahead import synthetic_bars
from app.screener import compile_screen, parse
from app.screener.registry import FUNCTIONS, LAST_BAR_ONLY
from app.screener.runtime import SymbolData, evaluate

NEW = {"CCI": ("index", 20), "MFI": ("index", 14), "WilliamsR": ("index", 14), "OBV": ("volume", None), "AroonUp": ("index", 25),
       "AroonDown": ("index", 25)}


def _bars(seed=3, n=400, tf="15m"):
    return synthetic_bars(n, tf=tf, seed=seed)


def _cols(df):
    return df["high"].tolist(), df["low"].tolist(), df["close"].tolist(), df["volume"].tolist()


# --- references (plain Python) ----------------------------------------------------------------------------------------

def ref_cci(h, l, c, n):
    tp = [(a + b + d) / 3 for a, b, d in zip(h, l, c)]
    out = []
    for t in range(len(tp)):
        if t + 1 < n:
            out.append(math.nan)
            continue
        w = tp[t - n + 1:t + 1]
        mean = sum(w) / n
        md = sum(abs(x - mean) for x in w) / n
        out.append((tp[t] - mean) / (0.015 * md) if md > 0 else math.nan)
    return out


def ref_williams_r(h, l, c, n):
    out = []
    for t in range(len(c)):
        if t + 1 < n:
            out.append(math.nan)
            continue
        hh, ll = max(h[t - n + 1:t + 1]), min(l[t - n + 1:t + 1])
        out.append(-100 * (hh - c[t]) / (hh - ll) if hh > ll else math.nan)
    return out


def ref_aroon(h, l, n):
    up, down = [], []
    for t in range(len(h)):
        if t < n:
            up.append(math.nan)
            down.append(math.nan)
            continue
        wh, wl = h[t - n:t + 1], l[t - n:t + 1]                 # n + 1 bars, oldest first
        hi = max(range(n + 1), key=lambda i: (wh[i], i))        # the most recent of equal highs
        lo = max(range(n + 1), key=lambda i: (-wl[i], i))
        up.append(100 * (n - (n - hi)) / n)
        down.append(100 * (n - (n - lo)) / n)
    return up, down


def ref_obv(c, v):
    out, total = [0.0], 0.0
    for t in range(1, len(c)):
        total += v[t] if c[t] > c[t - 1] else -v[t] if c[t] < c[t - 1] else 0.0
        out.append(total)
    return out


def ref_mfi(h, l, c, v, n):
    tp = [(a + b + d) / 3 for a, b, d in zip(h, l, c)]
    pos = [math.nan] + [tp[t] * v[t] if tp[t] > tp[t - 1] else 0.0 for t in range(1, len(tp))]
    neg = [math.nan] + [tp[t] * v[t] if tp[t] < tp[t - 1] else 0.0 for t in range(1, len(tp))]
    moved = [False] + [tp[t] != tp[t - 1] for t in range(1, len(tp))]
    out = []
    for t in range(len(tp)):
        if t < n:
            out.append(math.nan)
            continue
        p, q = sum(pos[t - n + 1:t + 1]), sum(neg[t - n + 1:t + 1])
        if not any(moved[t - n + 1:t + 1]):
            out.append(50.0)
        elif q == 0:
            out.append(100.0)
        else:
            out.append(100 - 100 / (1 + p / q))
    return out


def _same(a, b, tol=1e-9):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b)), np.flatnonzero(np.isnan(a) != np.isnan(b))[:5]
    ok = ~np.isnan(a)
    assert np.allclose(a[ok], b[ok], rtol=0, atol=tol), np.max(np.abs(a[ok] - b[ok]))


# --- parity with the references ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3])
def test_cci_matches_reference(seed):
    df = _bars(seed)
    h, l, c, _ = _cols(df)
    for n in (2, 5, 14, 20):
        _same(oscillators.cci(df, n), ref_cci(h, l, c, n), 1e-7)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_williams_r_matches_reference_and_stays_in_its_range(seed):
    df = _bars(seed)
    h, l, c, _ = _cols(df)
    for n in (1, 5, 14, 30):
        got = oscillators.williams_r(df, n)
        _same(got, ref_williams_r(h, l, c, n))
        assert got.dropna().between(-100, 0).all()
    x = oscillators.williams_r(df, 14).dropna().to_numpy()
    assert not (np.signbit(x) & (x == 0)).any()                  # the close at the highest high reads 0, not -0


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_aroon_matches_reference(seed):
    df = _bars(seed)
    df["high"] = df["high"].round(0)                            # whole numbers: equal highs / lows occur (the tie rule)
    df["low"] = df["low"].round(0)
    h, l, _, _ = _cols(df)
    for n in (1, 5, 14, 25):
        got = oscillators.aroon(df, n)
        up, down = ref_aroon(h, l, n)
        _same(got["up"], up)
        _same(got["down"], down)
        assert got.dropna().stack().between(0, 100).all()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_obv_and_mfi_match_reference(seed):
    df = _bars(seed)
    h, l, c, v = _cols(df)
    _same(volume.obv(df), ref_obv(c, v), 1e-6)
    for n in (2, 5, 14):
        got = volume.mfi(df, n)
        _same(got, ref_mfi(h, l, c, v, n), 1e-7)
        assert got.dropna().between(0, 100).all()


# --- hand-computed values on a small made-up series ---------------------------------------------------------------------

def _frame(highs, lows, closes, vols=None):
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes,
                         "volume": vols if vols is not None else [100.0] * len(closes)}, dtype=float)


def test_hand_computed_values():
    df = _frame([3, 5, 4, 6], [1, 2, 2, 3], [2, 4, 3, 5], [10, 20, 30, 40])
    # TP = 2, 11/3, 3, 14/3; CCI(3) on bar 2: mean 26/9, |dev| = 8/9, 7/9, 1/9 -> md 16/27; (3 - 26/9) / (0.015 x 16/27)
    assert oscillators.cci(df, 3).iloc[2] == pytest.approx((1 / 9) / (0.015 * 16 / 27))
    # Williams %R(3) on bar 3: highest 6, lowest 2, close 5 -> -100 x 1 / 4
    assert oscillators.williams_r(df, 3).iloc[3] == pytest.approx(-25.0)
    assert oscillators.williams_r(df, 3).iloc[1] != oscillators.williams_r(df, 3).iloc[1]          # warm-up: missing
    # Aroon(2) on bar 3: the highest of highs 5, 4, 6 is this bar (up 100); the lowest of 2, 2, 3 - the more recent 2,
    # one bar ago -> down 100 x (2 - 1) / 2
    ar = oscillators.aroon(df, 2)
    assert ar["up"].iloc[3] == 100.0 and ar["down"].iloc[3] == 50.0 and np.isnan(ar["up"].iloc[1])
    # OBV: 0, +20 (close up), -30 + 20 (down), +40 (up)
    assert volume.obv(df).tolist() == [0.0, 20.0, -10.0, 30.0]
    # MFI(2) on bar 3: flows bar 2 (TP 3 < 11/3: negative 90), bar 3 (14/3 > 3: positive 560/3)
    assert volume.mfi(df, 2).iloc[3] == pytest.approx(100 - 100 / (1 + (560 / 3) / 90))
    assert volume.mfi(df, 2).iloc[:2].isna().all() and volume.mfi(df, 1).iloc[1] == 100.0 and volume.mfi(df, 1).iloc[2] == 0.0


def test_flat_bars_edge_cases():
    flat = _frame([5.0] * 8, [5.0] * 8, [5.0] * 8)
    assert oscillators.cci(flat, 3).isna().all()                  # no deviation: no value
    assert oscillators.williams_r(flat, 3).isna().all()           # no range: no value
    assert volume.obv(flat).eq(0.0).all()
    assert volume.mfi(flat, 3).iloc[3:].eq(50.0).all()            # no rise and no fall: neutral (the RSI convention)
    assert oscillators.aroon(flat, 3)["up"].iloc[3:].eq(100.0).all()   # equal highs: the most recent one (this bar)


def test_a_missing_bar_gives_no_value_in_its_windows():
    df = _bars(4, n=80)
    df.iloc[40, df.columns.get_loc("close")] = np.nan
    assert np.isnan(oscillators.cci(df, 5).iloc[40:45]).all() and not np.isnan(oscillators.cci(df, 5).iloc[45])
    assert np.isnan(volume.mfi(df, 5).iloc[40:46]).all() and not np.isnan(volume.mfi(df, 5).iloc[46])
    obv = volume.obv(df)
    assert obv.iloc[40] == obv.iloc[39] and obv.iloc[41] == obv.iloc[40] and obv.notna().all()   # it adds nothing there


# --- causal: a run on bars[0..t] gives the full run's value at t --------------------------------------------------------

CAUSAL = {
    "cci": lambda f: oscillators.cci(f, 20),
    "williams_r": lambda f: oscillators.williams_r(f, 14),
    "aroon": lambda f: oscillators.aroon(f, 25),
    "obv": lambda f: volume.obv(f),
    "mfi": lambda f: volume.mfi(f, 14),
}


@pytest.mark.parametrize("name", sorted(CAUSAL))
def test_truncation_no_look_ahead(name):
    df = _bars(7, n=300)
    full = CAUSAL[name](df)
    for t in range(3, len(df), 7):
        part = CAUSAL[name](df.iloc[:t + 1])
        a = np.asarray(part.iloc[-1], dtype=float)
        b = np.asarray(full.iloc[t], dtype=float)
        assert np.array_equal(np.isnan(a), np.isnan(b)) and np.array_equal(a[~np.isnan(a)], b[~np.isnan(b)]), (name, t)


# --- the ScreenQL entries ----------------------------------------------------------------------------------------------

def test_registered_as_series_with_their_unit_and_default_window():
    for name, (unit, default) in NEW.items():
        spec = FUNCTIONS[name]
        assert name not in LAST_BAR_ONLY and spec.timeframed and spec.unit == unit, name
        assert [a.default for a in spec.args] == ([] if default is None else [default]), name


def test_screenql_values_equal_the_indicators():
    df = _bars(9)
    d = SymbolData("S", {"15m": df})
    ev = lambda text: evaluate(parse(text), d, base_tf="15m")   # noqa: E731
    _same(ev("CCI(14)"), oscillators.cci(df, 14), 0)
    _same(ev("CCI()"), oscillators.cci(df, 20), 0)
    _same(ev("MFI(10)"), volume.mfi(df, 10), 0)
    _same(ev("WilliamsR()"), oscillators.williams_r(df, 14), 0)
    _same(ev("OBV()"), volume.obv(df), 0)
    _same(ev("AroonUp(14)"), oscillators.aroon(df, 14)["up"], 0)
    _same(ev("AroonDown()"), oscillators.aroon(df, 25)["down"], 0)


@pytest.mark.parametrize("text", ["CCI(20) > 100", "MFI(14) < 20", "WilliamsR(14) < -80", "OBV() > SMA(OBV(), 20)",
                                  "AroonUp(25) > 70 AND AroonDown(25) < 30", "CrossAbove(AroonUp(14), AroonDown(14))",
                                  "CCI(20)@1h > 0", "MFI(14) > RSI(14)"])
def test_screens_validate(text):
    ast, validated = compile_screen(text, base_tf="15m")
    assert validated.ok, validated.problems


@pytest.mark.parametrize("text", ["OBV() > RSI(14)", "OBV(5) > 0"])
def test_unit_and_arity_are_checked(text):
    _, validated = compile_screen(text, base_tf="15m")
    assert not validated.ok


def test_aroon_and_mfi_need_one_bar_beyond_their_window():
    _, v = compile_screen("AroonUp(25) > 50", base_tf="15m")
    _, w = compile_screen("WilliamsR(25) < -50", base_tf="15m")
    assert v.lookback["15m"] == w.lookback["15m"] + 1
