"""M71 PL-3: what one store-first read returns (`BarsResult`), the gaps it reports and the REST source it may top up from.

Plain data only - the reads live in `app.market_store.reader`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from app.core.models import OHLCVBar

FLAG = "market_store_reads"
# M71 PL-5 option 2 (OPEN_QUESTIONS M71Q-2): a licensed market-data vendor as a source of shared intraday bars. OFF;
# nothing reads a vendor today, the setting only keeps the place for it (the store never serves one user's broker bars
# to another user).
VENDOR_SOURCE = (os.environ.get("MARKET_STORE_VENDOR_SOURCE", "") or "").strip().lower() in ("1", "true", "on", "yes")

DAILY = "1d"
WEEKLY = "1w"
INTRADAY_MINUTES: dict[str, int] = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
_ALIASES: dict[str, str] = {
    "1min": "1m", "1minute": "1m", "minute": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m",
    "60min": "1h", "60m": "1h", "1hour": "1h", "hour": "1h",
    "day": "1d", "1day": "1d", "d": "1d", "daily": "1d",
    "week": "1w", "1week": "1w", "weekly": "1w",
}
FRAME_COLS = ["open", "high", "low", "close", "volume"]

# gap kinds
OLDER = "older"        # history before the store's first bar of the window: reported and queued, never fetched per run
NEWEST = "newest"      # bars after the store's last bar that were not read (no own session / budget used / daily file)
COLD = "cold"          # the store holds nothing of the window for this reader

CONNECT_FOR_INTRADAY = "Connect your broker for intraday bars."
NEWEST_DAILY = "The exchange's daily file for the newest session is not stored yet."
NEWEST_BUDGET = "The newest bars wait for the broker's limit - run again shortly."
NOT_STORED = "History for this symbol is not stored yet."


class UnknownInterval(ValueError):
    """An interval the store cannot serve."""


def canonical(interval: str) -> str:
    """'60min' / '1h' / 'day' / '1d' / 'week' ... -> the store's own name (1m 3m 5m 15m 30m 1h 1d 1w)."""
    raw = (interval or "").strip().lower()
    name = _ALIASES.get(raw, raw)
    if name in INTRADAY_MINUTES or name in (DAILY, WEEKLY):
        return name
    raise UnknownInterval(f"the store serves 1m, 3m, 5m, 15m, 30m, 1h, 1d and 1w bars, not {interval!r}")


def is_eod(interval: str) -> bool:
    return canonical(interval) in (DAILY, WEEKLY)


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=FRAME_COLS, index=pd.DatetimeIndex([], tz="UTC", name="timestamp"), dtype=float)


@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime
    kind: str                  # OLDER | NEWEST | COLD
    reason: str = ""
    queued: bool = False       # handed to the gap-fill queue (PL-1c `request_gap_fill`) when it exists

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "kind": self.kind, "reason": self.reason,
                "queued": self.queued}


@dataclass(frozen=True)
class RestSource:
    """The requesting reader's OWN broker session the newest bars may be topped up from (M71Q-2): the adapter, the
    credential row it belongs to (its API budget is shared per credential) and the broker's name."""
    adapter: Any
    record_id: int
    broker: str
    wait_seconds: float | None = None


@dataclass
class BarsResult:
    """One (symbol, interval) read: closed bars only, START-stamped UTC index (the frame a broker's candles give), the
    gaps the store could not fill and where the bars came from. Gaps are never filled with zeros."""
    symbol: str
    exchange: str
    interval: str
    frame: pd.DataFrame = field(default_factory=empty_frame)
    gaps: list[Gap] = field(default_factory=list)
    source: str = ""           # lake:daily | lake:intraday | lake:intraday+broker:<name> | "" (nothing stored)
    rest_calls: int = 0        # broker history calls this read made (0 on a store hit or a cached top-up)
    stored_bars: int = 0
    topped_up_bars: int = 0

    @property
    def served(self) -> bool:
        return not self.frame.empty

    @property
    def cold(self) -> bool:
        return any(g.kind == COLD for g in self.gaps) and self.frame.empty

    def bars(self) -> list[OHLCVBar]:
        return [OHLCVBar(timestamp=ts.to_pydatetime(), open=float(r.open), high=float(r.high), low=float(r.low),
                         close=float(r.close), volume=float(r.volume)) for ts, r in self.frame.iterrows()]

    def gap_lines(self) -> list[str]:
        return list(dict.fromkeys(g.reason for g in self.gaps if g.reason))


__all__ = ["FLAG", "VENDOR_SOURCE", "DAILY", "WEEKLY", "INTRADAY_MINUTES", "OLDER", "NEWEST", "COLD", "CONNECT_FOR_INTRADAY",
           "NEWEST_DAILY", "NEWEST_BUDGET", "NOT_STORED", "UnknownInterval", "canonical", "is_eod", "empty_frame", "Gap",
           "RestSource", "BarsResult"]
