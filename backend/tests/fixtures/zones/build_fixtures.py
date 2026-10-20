"""Rebuild the zone parity fixtures (CSV) - deterministic, seeded, no network. Run from backend/:

    python tests/fixtures/zones/build_fixtures.py
    # Then pin the new content (public repo guard, ALLOWED_TEST_DATA in scripts/check_public_repo.py):
    python3 ../scripts/check_public_repo.py --hash tests/fixtures/zones/*.csv

The generators are copied from the Trade repository's tests @eafc872 (see README.md); outputs are rounded to 2 decimals
(a price tick) and frozen as CSV, so the CSVs - not this script - are the fixtures. No NIFTY market data is used.
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def _ohlc(ts, o, h, l, c) -> pd.DataFrame:
    df = pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": l, "close": c})
    for k in ("open", "high", "low", "close"):
        df[k] = df[k].astype(float).round(2)
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def bn_daily() -> pd.DataFrame:
    """Trade tests/test_banknifty_independent_test.py::_csv (synthetic BANKNIFTY-like daily bars, seed 3), first 400 days."""
    rng = np.random.default_rng(3)
    ts = pd.bdate_range("2021-09-01", "2024-06-28")
    c = 30000 * np.exp(np.cumsum(rng.normal(0, 0.012, len(ts))))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.002, len(ts)))
    df = _ohlc(ts, o, np.maximum(o, c) * 1.005, np.minimum(o, c) * 0.995, c)
    return df.iloc[:400].reset_index(drop=True)


def market_5m() -> pd.DataFrame:
    """Trade tests/test_sr_levels_v3.py::_market (days=10, seed=3): a session-shaped 5m sine market, 75 bars a day."""
    rng = np.random.default_rng(3)
    stamps = []
    for d in pd.bdate_range("2026-09-01", periods=10):
        stamps += list(pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=75, freq="5min"))
    n = len(stamps)
    close = 24100.0 + 100.0 * np.sin(np.arange(n) * 2 * np.pi / 150) + rng.normal(0, 3, n)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return _ohlc(stamps, open_, np.maximum(open_, close) + 2, np.minimum(open_, close) - 2, close)


def walk_1m() -> pd.DataFrame:
    """Trade tests/test_sr_dynamic.py::_walk (seed 1, 900 bars) + open = previous close and 1-minute stamps."""
    rng = np.random.default_rng(1)
    n = 900
    close = 22000 + np.cumsum(rng.normal(0, 6, n))
    high = close + rng.uniform(1, 8, n)
    low = close - rng.uniform(1, 8, n)
    close, high, low = np.round(close, 2), np.round(high, 2), np.round(low, 2)
    open_ = np.r_[close[0], close[:-1]]
    return _ohlc(pd.date_range("2026-01-05 09:15", periods=n, freq="1min"), open_, high, low, close)


def session_15m() -> pd.DataFrame:
    """Session-shaped 15m bars (Trade tests/test_major_levels.py::_nse layout: 25 bars from 09:15 per weekday) on a
    BANKNIFTY-like price, with tight bases before impulsive candles and overnight gaps planted (seed 7)."""
    rng = np.random.default_rng(7)
    price = 48000.0
    rows = []
    for day, d in enumerate(pd.bdate_range("2026-03-02", periods=30)):
        if day and rng.random() < 0.4:
            price *= 1 + rng.choice([-1.0, 1.0]) * rng.uniform(0.004, 0.009)
        k = 0
        while k < 25:
            ts = d + pd.Timedelta(hours=9, minutes=15 + 15 * k)
            if rng.random() < 0.08 and k <= 21:
                for _ in range(3):
                    o = price
                    c = o + rng.normal(0, 3)
                    rows.append((ts, o, max(o, c) + rng.uniform(0, 4), min(o, c) - rng.uniform(0, 4), c))
                    price, k = c, k + 1
                    ts = d + pd.Timedelta(hours=9, minutes=15 + 15 * k)
                o = price
                c = o + rng.choice([-1.0, 1.0]) * rng.uniform(150, 260)
                rows.append((ts, o, max(o, c) + rng.uniform(5, 20), min(o, c) - rng.uniform(5, 20), c))
            else:
                o = price
                c = o + rng.normal(0, 35)
                rows.append((ts, o, max(o, c) + rng.uniform(3, 25), min(o, c) - rng.uniform(3, 25), c))
            price, k = c, k + 1
    ts, o, h, l, c = zip(*rows)
    return _ohlc(list(ts), o, h, l, c)


FIXTURES = {"bn_daily.csv": bn_daily, "market_5m.csv": market_5m, "walk_1m.csv": walk_1m, "session_15m.csv": session_15m}


if __name__ == "__main__":
    for name, fn in FIXTURES.items():
        fn().to_csv(os.path.join(HERE, name), index=False)
        print("wrote", name)
