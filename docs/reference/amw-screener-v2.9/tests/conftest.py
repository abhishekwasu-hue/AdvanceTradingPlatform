"""SYNTHETIC TEST FIXTURES - deterministic, hand-built OHLCV. NOT market data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import default_settings


def pytest_configure(config):
    """Generated report files go to a temporary folder, never into the project's data/ directory."""
    import os
    import tempfile
    os.environ.setdefault("AMW_REPORTS_DIR", tempfile.mkdtemp(prefix="amw-test-reports-"))


def make_ohlc(closes, tf: str = "1D", spread: float = 1.0, start="2024-01-01", vol=1000.0, tz="Asia/Kolkata") -> pd.DataFrame:
    """Build OHLC from a close path: open = previous close, high/low = max/min(open, close) +/- spread."""
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + spread
    l = np.minimum(o, c) - spread
    freq = {"1W": "W-MON", "1D": "B", "1H": "h", "30m": "30min", "15m": "15min", "5m": "5min"}[tf]
    idx = pd.date_range(start, periods=len(c), freq=freq, tz=tz)
    v = np.full(len(c), vol) if np.isscalar(vol) else np.asarray(vol, float)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)


def zigzag(points: list[float], seg: int = 6) -> list[float]:
    """Linear interpolation between turning points (each leg `seg` bars)."""
    out: list[float] = []
    for a, b in zip(points[:-1], points[1:]):
        out += list(np.linspace(a, b, seg, endpoint=False))
    out.append(points[-1])
    return out


@pytest.fixture
def cfg():
    c = default_settings()
    c["data"]["min_bars"] = {k: 20 for k in c["data"]["min_bars"]}
    return c


def intraday_session(closes, tf="15m", day="2026-10-16", vol=1000.0, spread=0.3):
    """One NSE session starting 09:15 (SYNTHETIC)."""
    df = make_ohlc(closes, tf=tf, start=f"{day} 09:15", spread=spread, vol=vol)
    return df


class FakeProvider:
    """Offline provider returning SYNTHETIC fixtures. Raises for timeframes listed in `fail`."""
    name = "fake"
    data_kind = "simulated"

    def __init__(self, data: dict, fail: tuple = ()):
        self.data, self.fail, self.calls = data, fail, 0

    def fetch_many(self, tickers, tf):
        self.calls += 1
        if tf in self.fail:
            raise RuntimeError("simulated network error")
        return {t: self.data.get((t, tf), self.data.get(t)) for t in tickers}

    def health(self):
        return {"provider": "fake", "data_kind": "simulated"}


@pytest.fixture
def now_after():
    return pd.Timestamp("2035-01-01 12:00", tz="Asia/Kolkata")
