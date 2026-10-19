"""Technical indicators. Insufficient history -> NaN (never artificial zeros)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    out = s.ewm(span=n, adjust=False, min_periods=n).mean()
    return out


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    au, ad = wilder(up, n), wilder(dn, n)
    rs = au / ad
    r = 100 - 100 / (1 + rs)
    r = r.where(ad != 0, 100.0).where(~(au.isna() | ad.isna()))
    r[(au == 0) & (ad == 0)] = 50.0
    return r


def macd(close: pd.Series, fast=12, slow=26, sig=9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    signal = line.ewm(span=sig, adjust=False, min_periods=sig).mean()
    return pd.DataFrame({"macd": line, "macd_signal": signal, "macd_hist": line - signal})


def true_range(df: pd.DataFrame) -> pd.Series:
    h, lo, c = (df[k].to_numpy(dtype=float) for k in ("High", "Low", "Close"))
    pc = np.r_[np.nan, c[:-1]]
    with np.errstate(invalid="ignore"):
        tr = np.fmax(np.fmax(h - lo, np.abs(h - pc)), np.abs(lo - pc))   # fmax ignores the missing previous close
    if len(tr):
        tr[0] = h[0] - lo[0]
    return pd.Series(tr, index=df.index)


def atr(df: pd.DataFrame, n: int = 14, tr: pd.Series | None = None) -> pd.Series:
    return wilder(true_range(df) if tr is None else tr, n)


def adx(df: pd.DataFrame, n: int = 14, tr_raw: pd.Series | None = None) -> pd.DataFrame:
    up = df.High.diff()
    dn = -df.Low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    plus_dm.iloc[0] = minus_dm.iloc[0] = np.nan
    tr = wilder(true_range(df) if tr_raw is None else tr_raw, n)
    pdi = 100 * wilder(plus_dm, n) / tr
    mdi = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return pd.DataFrame({"plus_di": pdi, "minus_di": mdi, "adx": wilder(dx, n)})


def bollinger(close: pd.Series, n=20, k=2.0) -> pd.DataFrame:
    mid = close.rolling(n, min_periods=n).mean()
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_up": mid + k * sd, "bb_low": mid - k * sd,
                         "bb_width": (2 * k * sd) / mid})


def relative_volume(vol: pd.Series, n: int = 20) -> pd.Series:
    """Volume / mean of the PRIOR n completed bars (current bar excluded)."""
    base = vol.shift(1).rolling(n, min_periods=n).mean()
    return vol / base


def vwap_intraday(df: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP; NaN for a session if any volume is missing."""
    tp = (df.High + df.Low + df.Close) / 3
    day = df.index.date
    pv = (tp * df.Volume).groupby(day).cumsum()
    v = df.Volume.groupby(day).cumsum()
    out = pv / v
    bad_days = df.Volume.isna().groupby(day).transform("any")
    return out.where(~bad_days)


def relative_strength(stock_close: pd.Series, bench_close: pd.Series, n: int = 50) -> float | None:
    """n-bar return of stock minus benchmark (aligned on common timestamps). None if not computable."""
    j = pd.concat([stock_close.rename("s"), bench_close.rename("b")], axis=1, join="inner").dropna()
    if len(j) <= n:
        return None
    rs = j.s.iloc[-1] / j.s.iloc[-1 - n] - j.b.iloc[-1] / j.b.iloc[-1 - n]
    return float(rs)


def fib_levels(low: float, high: float, direction: str) -> dict[str, float]:
    """Retracement levels of the impulse low->high (bullish) or high->low (bearish)."""
    rng = high - low
    ratios = (0.236, 0.382, 0.5, 0.618, 0.786)
    if direction == "bullish":
        return {f"{r * 100:.1f}%": high - r * rng for r in ratios}
    return {f"{r * 100:.1f}%": low + r * rng for r in ratios}


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0, tr: pd.Series | None = None) -> pd.DataFrame:
    """Supertrend (ATR(n) Wilder, multiplier `mult`), standard band-ratchet rules. `st_dir` +1 = up, -1 = down;
    NaN until ATR is available. Uses only bars <= t at every t (no look-ahead)."""
    h, lo, c = (df[k].to_numpy(dtype=float) for k in ("High", "Low", "Close"))
    a = atr(df, n, tr).to_numpy()
    hl2 = (h + lo) / 2
    up_l, dn_l, c_l, a_l, m_l = (hl2 - mult * a).tolist(), (hl2 + mult * a).tolist(), c.tolist(), a.tolist(), hl2.tolist()
    nan = float("nan")
    st, d = [nan] * len(c_l), [nan] * len(c_l)
    fu = fd = nan
    started = False
    for i in range(len(c_l)):
        ai = a_l[i]
        if ai != ai:                                  # NaN ATR (warm-up)
            continue
        if not started:
            fu, fd, d[i], started = up_l[i], dn_l[i], (1.0 if c_l[i] >= m_l[i] else -1.0), True
        else:
            pc = c_l[i - 1]
            fu = max(up_l[i], fu) if pc > fu else up_l[i]
            fd = min(dn_l[i], fd) if pc < fd else dn_l[i]
            prev = d[i - 1] if d[i - 1] == d[i - 1] else 1.0
            d[i] = 1.0 if (prev < 0 and c_l[i] > fd) else -1.0 if (prev > 0 and c_l[i] < fu) else prev
        st[i] = fu if d[i] > 0 else fd
    return pd.DataFrame({"supertrend": st, "st_dir": d}, index=df.index)


def fib_extensions(low: float, high: float, direction: str) -> dict[str, float]:
    """Extension targets of the impulse low->high (bullish: above high) or high->low (bearish: below low)."""
    rng = high - low
    ratios = (1.272, 1.618, 2.0)
    if direction == "bullish":
        return {f"{r * 100:.1f}%": low + r * rng for r in ratios}
    return {f"{r * 100:.1f}%": high - r * rng for r in ratios}


def add_indicators(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Return a copy with all indicator columns (built in one concat; true range computed once)."""
    c, v = df.Close, df.Volume
    cols: dict[str, pd.Series | np.ndarray] = {}
    cols["ema20"], cols["ema50"], cols["ema200"] = ema(c, 20), ema(c, 50), ema(c, 200)
    cols["rsi14"] = rsi(c, 14)
    m = macd(c)
    cols.update({k: m[k] for k in m.columns})
    tr = true_range(df)
    a14, a50 = atr(df, 14, tr), atr(df, 50, tr)
    cols["atr14"], cols["atr50"] = a14, a50
    cols["atr_pct"] = a14 / c * 100
    ax = adx(df, 14, tr)
    cols.update({k: ax[k] for k in ax.columns})
    bb = bollinger(c)
    cols.update({k: bb[k] for k in bb.columns})
    cols["sma20"], cols["sma50"] = sma(c, 20), sma(c, 50)
    cols["vol_ma20"] = v.shift(1).rolling(20, min_periods=20).mean()
    rv = relative_volume(v, 20)
    cols["rvol"] = rv
    state = np.where(rv >= 1.5, "expansion", np.where(rv <= 0.6, "contraction", "normal")).astype(object)
    state[rv.isna().to_numpy()] = None
    cols["vol_state"] = pd.Series(state, index=df.index, dtype=object)
    stx = supertrend(df, 10, 3.0, tr)
    cols["supertrend"], cols["st_dir"] = stx["supertrend"], stx["st_dir"]
    cols["dist_ema20_atr"] = (c - cols["ema20"]) / a14
    cols["dist_ema50_atr"] = (c - cols["ema50"]) / a14
    if tf not in ("1D", "1W") and v.notna().mean() > 0.9:
        cols["vwap"] = vwap_intraday(df)
    else:
        cols["vwap"] = pd.Series(np.nan, index=df.index)
    return pd.concat([df, pd.DataFrame(cols, index=df.index)], axis=1)
