"""SIMULATED data provider for offline UI/integration tests ONLY (AMW_PROVIDER=fixture).
Deterministic synthetic OHLCV (seeded); data_kind = "simulated" and shown as such everywhere. NOT market data."""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from .provider import DataProvider

FREQ = {"1W": "W-MON", "1D": "B", "1H": "h", "30m": "30min", "15m": "15min", "5m": "5min"}


class FixtureProvider(DataProvider):
    name = "FIXTURE (simulated, not market data)"
    data_kind = "simulated"

    def __init__(self, end: str | None = None, bars: int = 400):
        self.end = pd.Timestamp(end or pd.Timestamp.now(tz="Asia/Kolkata").normalize() - pd.Timedelta(days=1))
        self.bars = bars
        self.cache = None

    def fetch_many(self, tickers, tf):
        out = {}
        for t in tickers:
            if t.upper().startswith("ZZZ"):
                out[t] = None                     # simulates an unavailable symbol
                continue
            rng = np.random.default_rng(zlib.crc32(f"{t}|{tf}".encode()))
            drift = rng.normal(0.0004, 0.0008)
            c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.012, self.bars)))
            o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, self.bars))
            h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, self.bars)))
            l_ = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, self.bars)))
            idx = pd.date_range(end=self.end.tz_localize(None) if self.end.tzinfo else self.end, periods=self.bars, freq=FREQ[tf])
            if tf not in ("1D", "1W"):
                idx = idx[(idx.hour * 60 + idx.minute >= 555) & (idx.hour * 60 + idx.minute < 930)] if len(idx) else idx
                c, o, h, l_ = c[-len(idx):], o[-len(idx):], h[-len(idx):], l_[-len(idx):]
            out[t] = pd.DataFrame({"Open": o, "High": h, "Low": l_, "Close": c, "Volume": rng.integers(1e5, 1e6, len(idx)).astype(float)},
                                  index=idx.tz_localize("Asia/Kolkata") if tf not in ("1D", "1W") else idx)
        return out

    def health(self):
        return {"provider": self.name, "data_kind": self.data_kind}
