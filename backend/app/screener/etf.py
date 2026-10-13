"""S10 J (ETF; SCREENER_SPEC §1 "ETF/MF (J)" and §5 S10): an ETF screen over NSE-listed ETFs from the shared end-of-day
history - behind the `screener_etf` flag (off by default).

For every ETF the universe lists (`securities.is_etf`, listed on the as-of day) it reads the lake's NSE daily bars and
the tracked index's daily bars (`NSE_INDEX:<code>`, from `securities.underlying`), as known at `now`, closed sessions
only, and gives:

* the last close and its session, the average daily traded value over `LIQUIDITY_SESSIONS` (close x volume);
* the ETF's % change over `n` sessions and the index's over the same sessions (aligned on the ETF's own sessions), and
  their difference - the **tracking difference** over the window (an ETF also carries its expense ratio and cash
  drag, so a small negative difference is expected);
* why a column is empty (no index mapped, too few sessions).

Filters: minimum traded value, a text match on the name or underlying, a maximum |tracking difference|. Sorted by
traded value (liquidity) by default. Information only: a table of facts on the data shown - never a ranking of which ETF
to hold, a recommendation or advice. Mutual funds (AMFI NAVs) are not ingested yet (OPEN_QUESTIONS S10-1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SecurityRecord

FLAG = "screener_etf"
DAILY = "day"
DEFAULT_SESSIONS = 20
MAX_SESSIONS = 250
LIQUIDITY_SESSIONS = 20
HISTORY_DAYS = 420
MAX_ETFS = 400
NOTE = ("Facts from the shared end-of-day history: closes, traded value and how each ETF moved against the index it "
        "tracks over the window. Not a ranking of which ETF to hold and not advice; an ETF's expense ratio and cash make a "
        "small negative tracking difference normal.")
SORTS = ("traded_value", "change_pct", "tracking_diff_pct", "symbol")


def index_keys(underlying: Optional[str]) -> List[str]:
    """Lake keys to try for an ETF's underlying text ("NIFTY 50 Index", "Nifty Bank TRI", ...), most specific first."""
    from app.universe.fo_eod import code_for
    if not underlying or not underlying.strip():
        return []
    text = re.sub(r"\s+", " ", underlying.strip())
    variants = [text]
    stripped = re.sub(r"\b(TOTAL RETURNS?|TRI|INDEX)\b", "", text, flags=re.IGNORECASE)
    stripped = re.sub(r"\s+", " ", stripped).strip(" -")
    if stripped and stripped != text:
        variants.append(stripped)
    return list(dict.fromkeys(f"NSE_INDEX:{code_for(v)}" for v in variants))


def _closes(bars: Sequence[Any]) -> pd.DataFrame:
    rows = [(b.ts, float(b.close), float(b.volume or 0)) for b in bars]
    frame = pd.DataFrame(rows, columns=["ts", "close", "volume"])
    if frame.empty:
        return frame
    from app.market_data.calendar import IST
    frame["day"] = [pd.Timestamp(t).tz_convert(IST).date() for t in frame.pop("ts")]
    return frame.drop_duplicates("day", keep="last").set_index("day").sort_index()


@dataclass
class EtfRow:
    symbol: str
    name: str
    underlying: Optional[str]
    session: Optional[str] = None
    close: Optional[float] = None
    traded_value: Optional[float] = None
    change_pct: Optional[float] = None
    index_change_pct: Optional[float] = None
    tracking_diff_pct: Optional[float] = None
    index_key: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def measure(row: EtfRow, etf: pd.DataFrame, index: Optional[pd.DataFrame], sessions: int) -> EtfRow:
    """Fills a row from the ETF's and the index's daily closes (pure; closed sessions only)."""
    if etf.empty:
        row.notes.append("no daily bars in the shared history")
        return row
    row.session = etf.index[-1].isoformat()
    row.close = round(float(etf["close"].iloc[-1]), 4)
    recent = etf.iloc[-LIQUIDITY_SESSIONS:]
    row.traded_value = round(float((recent["close"] * recent["volume"]).mean()), 2)
    if len(etf) <= sessions:
        row.notes.append(f"{len(etf)} sessions in the shared history; {sessions + 1} needed for the window")
        return row
    start_day = etf.index[-1 - sessions]
    base = float(etf["close"].iloc[-1 - sessions])
    row.change_pct = round((float(etf["close"].iloc[-1]) / base - 1.0) * 100.0, 2) if base else None
    if index is None or index.empty:
        row.notes.append("the tracked index is not in the shared history" if row.index_key is None else "no index bars")
        return row
    try:
        i_end = float(index.loc[etf.index[-1], "close"])
        i_start = float(index.loc[start_day, "close"])
    except KeyError:
        row.notes.append("the index has no close on the ETF's first or last session of the window")
        return row
    row.index_change_pct = round((i_end / i_start - 1.0) * 100.0, 2) if i_start else None
    if row.change_pct is not None and row.index_change_pct is not None:
        row.tracking_diff_pct = round(row.change_pct - row.index_change_pct, 2)
    return row


@dataclass
class EtfHistory:
    """One ETF's daily closes and its tracked index's (as known at the load time): loaded once, measured per request."""
    symbol: str
    name: str
    underlying: Optional[str]
    etf: pd.DataFrame
    index_key: Optional[str]
    index: Optional[pd.DataFrame]


async def load(session: AsyncSession, *, now: Optional[datetime] = None) -> List[EtfHistory]:
    """Every listed ETF's daily closes and its index's, from the shared history as known at `now` (the expensive part:
    one lake read per ETF and per index). Shared reference data - the same for every organisation."""
    from app.market_lake.adjust import candles
    now = now or datetime.now(timezone.utc)
    from app.market_data.calendar import IST
    day: date = now.astimezone(IST).date()
    etfs = list(await session.scalars(
        select(SecurityRecord).where(SecurityRecord.is_etf.is_(True), SecurityRecord.status == "ACTIVE",
                                     or_(SecurityRecord.listing_date.is_(None), SecurityRecord.listing_date <= day),
                                     or_(SecurityRecord.delisting_date.is_(None), SecurityRecord.delisting_date > day))
        .order_by(SecurityRecord.symbol).limit(MAX_ETFS)))
    start = now - timedelta(days=HISTORY_DAYS)
    index_cache: Dict[str, pd.DataFrame] = {}
    out: List[EtfHistory] = []
    for sec in etfs:
        frame = _closes(await candles(session, f"NSE:{sec.symbol}", DAILY, start, now, symbol=sec.symbol, exchange="NSE", adjusted=True,
                                      as_of=now))
        index_key: Optional[str] = None
        index: Optional[pd.DataFrame] = None
        for key in index_keys(sec.underlying):
            if key not in index_cache:
                index_cache[key] = _closes(await candles(session, key, DAILY, start, now, symbol=key, exchange="NSE", kind="INDEX",
                                                         adjusted=False, as_of=now))
            if not index_cache[key].empty:
                index_key, index = key, index_cache[key]
                break
        out.append(EtfHistory(sec.symbol, sec.name, sec.underlying, frame, index_key, index))
    return out


def rows_from(history: List[EtfHistory], sessions: int) -> List[EtfRow]:
    """The rows for a window of `sessions` (pure and cheap: the loaded closes, measured)."""
    return [measure(EtfRow(h.symbol, h.name, h.underlying, index_key=h.index_key), h.etf, h.index, sessions) for h in history]


async def etf_rows(session: AsyncSession, *, sessions: int = DEFAULT_SESSIONS, now: Optional[datetime] = None) -> List[EtfRow]:
    return rows_from(await load(session, now=now), sessions)


def screen(rows: List[EtfRow], *, min_traded_value: float = 0.0, query: str = "", max_abs_tracking: Optional[float] = None,
           sort: str = "traded_value") -> List[EtfRow]:
    q = query.strip().lower()
    picked = [r for r in rows if (r.traded_value or 0.0) >= min_traded_value
              and (not q or q in r.symbol.lower() or q in r.name.lower() or q in (r.underlying or "").lower())
              and (max_abs_tracking is None or (r.tracking_diff_pct is not None and abs(r.tracking_diff_pct) <= max_abs_tracking))]
    if sort == "symbol":
        return sorted(picked, key=lambda r: r.symbol)
    key = {"traded_value": lambda r: r.traded_value, "change_pct": lambda r: r.change_pct,
           "tracking_diff_pct": lambda r: abs(r.tracking_diff_pct) if r.tracking_diff_pct is not None else None}[sort]
    present = [r for r in picked if key(r) is not None]
    missing = [r for r in picked if key(r) is None]
    reverse = sort != "tracking_diff_pct"                        # liquidity / change: high first; tracking: closest first
    return sorted(present, key=lambda r: (key(r), r.symbol), reverse=reverse) + sorted(missing, key=lambda r: r.symbol)


__all__ = ["FLAG", "NOTE", "SORTS", "DEFAULT_SESSIONS", "MAX_SESSIONS", "EtfRow", "index_keys", "measure", "etf_rows", "screen"]
