"""Market-data provider abstraction.

* `DataProvider` - interface every source implements (`fetch_many`, `data_kind`).
* `YFinanceProvider` - research/prototype source: DELAYED/HISTORICAL data, never described as real-time.
* `BrokerProviderTemplate` - adapter skeleton for an authorised broker/vendor feed (not connected).
Caching key = (provider, ticker, timeframe, interval, period). Retries are bounded with exponential backoff.
Concurrent identical requests are de-duplicated with a per-key lock.
"""
from __future__ import annotations

import hashlib
import logging
import pickle
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from ..config import TIMEFRAME_INFO

log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

TIMEFRAME_MAP: dict[str, tuple[str, str]] = {k: (v["yf_interval"], v["period"]) for k, v in TIMEFRAME_INFO.items()}


def tf_to_yf(tf: str) -> tuple[str, str]:
    if tf not in TIMEFRAME_MAP:
        raise ValueError(f"unsupported timeframe {tf!r}; allowed {list(TIMEFRAME_MAP)}")
    return TIMEFRAME_MAP[tf]


class DataProvider(ABC):
    name = "abstract"
    data_kind = "unknown"   # one of: live | delayed/historical | simulated

    @abstractmethod
    def fetch_many(self, tickers: list[str], tf: str) -> dict[str, pd.DataFrame | None]:
        """Raw OHLCV per ticker; None when unavailable. Must never fabricate data."""

    def health(self) -> dict:
        return {"provider": self.name, "data_kind": self.data_kind}


class DiskCache:
    def __init__(self, root: Path, ttl_minutes: dict[str, float], namespace: str = "yf"):
        self.root = Path(root) / "ohlcv"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl, self.ns = ttl_minutes, namespace
        self.hits = self.misses = 0

    def key(self, ticker: str, tf: str) -> str:
        interval, period = TIMEFRAME_MAP[tf]
        raw = f"{self.ns}|{ticker}|{tf}|{interval}|{period}"
        safe = "".join(ch if ch.isalnum() else "_" for ch in ticker)
        return f"{safe}__{tf}__{hashlib.sha1(raw.encode()).hexdigest()[:10]}"

    def _path(self, ticker: str, tf: str) -> Path:
        return self.root / f"{self.key(ticker, tf)}.pkl"

    def get(self, ticker: str, tf: str) -> pd.DataFrame | None:
        p = self._path(ticker, tf)
        if not p.exists() or (time.time() - p.stat().st_mtime) / 60 > self.ttl.get(tf, 10):
            self.misses += 1
            return None
        try:
            with open(p, "rb") as f:
                self.hits += 1
                return pickle.load(f)
        except Exception:  # noqa: BLE001
            self.misses += 1
            return None

    def put(self, ticker: str, tf: str, df: pd.DataFrame) -> None:
        with open(self._path(ticker, tf), "wb") as f:
            pickle.dump(df, f)

    def stats(self) -> dict:
        files = list(self.root.glob("*.pkl"))
        return {"files": len(files), "size_mb": round(sum(f.stat().st_size for f in files) / 1e6, 2),
                "hits": self.hits, "misses": self.misses}

    def clear(self) -> int:
        n = 0
        for f in self.root.glob("*.pkl"):
            f.unlink()
            n += 1
        return n


class YFinanceProvider(DataProvider):
    name = "yfinance (Yahoo Finance)"
    data_kind = "delayed/historical"

    def __init__(self, cfg: dict, cache: DiskCache | None = None, downloader: Callable | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self.cfg = cfg["data"]
        self.cache = cache
        self._sleep = sleeper
        if downloader is None:
            import yfinance as yf
            downloader = yf.download
        self._dl = downloader
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.last_error: str | None = None
        self.last_success: float | None = None
        self.requests = 0

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _download(self, tickers: list[str], tf: str) -> pd.DataFrame | None:
        interval, period = tf_to_yf(tf)
        for attempt in range(self.cfg["max_retries"] + 1):
            try:
                self.requests += 1
                out = self._dl(tickers, period=period, interval=interval, group_by="ticker", auto_adjust=False,
                               progress=False, threads=True, timeout=self.cfg["timeout"])
                self.last_success = time.time()
                return out
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                delay = self.cfg["backoff_base"] * (2 ** attempt)
                log.warning("download failed (%s), retry %d in %.1fs", self.last_error, attempt + 1, delay)
                if attempt < self.cfg["max_retries"]:
                    self._sleep(delay)
        log.error("download gave up for %d tickers", len(tickers))
        return None

    @staticmethod
    def _split(raw: pd.DataFrame | None, tickers: list[str]) -> dict[str, pd.DataFrame | None]:
        out: dict[str, pd.DataFrame | None] = {t: None for t in tickers}
        if raw is None or raw.empty:
            return out
        if isinstance(raw.columns, pd.MultiIndex):
            lv0 = set(raw.columns.get_level_values(0))
            for t in tickers:
                if t in lv0:
                    sub = raw[t].dropna(how="all")
                    out[t] = sub if len(sub) else None
        elif len(tickers) == 1:
            sub = raw.dropna(how="all")
            out[tickers[0]] = sub if len(sub) else None
        return out

    def fetch_many(self, tickers: list[str], tf: str) -> dict[str, pd.DataFrame | None]:
        tickers = list(dict.fromkeys(tickers))          # de-duplicate request
        key = f"{tf}|{','.join(sorted(tickers))}"
        with self._lock(key):                           # identical concurrent requests wait for the first
            result, todo = {}, []
            for t in tickers:
                c = self.cache.get(t, tf) if self.cache else None
                if c is not None:
                    result[t] = c
                else:
                    todo.append(t)
            if todo:
                for t, df in self._split(self._download(todo, tf), todo).items():
                    result[t] = df
                    if df is not None and self.cache:
                        self.cache.put(t, tf, df)
            return result

    def health(self) -> dict:
        return {"provider": self.name, "data_kind": self.data_kind, "requests": self.requests,
                "last_success_epoch": self.last_success, "last_error": self.last_error,
                "cache": self.cache.stats() if self.cache else None}


class BrokerProviderTemplate(DataProvider):
    """Skeleton for an AUTHORISED broker/market-data API. Not connected.

    To implement: authenticate with credentials from environment variables (never hard-code), map
    timeframes to the vendor's intervals, return tz-aware OHLCV frames with columns Open/High/Low/Close/Volume,
    respect the vendor's rate limits and licence, and set `data_kind` to "live" only if the feed is real-time.
    """
    name = "broker-template (not connected)"
    data_kind = "live"

    def fetch_many(self, tickers: list[str], tf: str) -> dict[str, pd.DataFrame | None]:
        raise NotImplementedError("Broker data adapter is not configured")


def batched(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]
