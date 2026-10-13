"""U9 TX4 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §3 "learn mode" and "versioning and provenance"): what a library
screen is, where it comes from, three past examples with their evidence and what the price did next, and forking a
pre-built screen into "My screens" - behind the `screener_tx4` flag (off by default).

* **Provenance.** `provenance()` gives a screen's version, change log and source note from the reviewed taxonomy file;
  a Legends screen's source line is its card's cited sources. The screen's ScreenQL text and its SHA-256 identify
  exactly what a fork copied.
* **Past examples (education).** `examples()` runs the screen on the shared end-of-day history (the lake's NSE daily
  bars, unadjusted, as known now) for up to `MAX_SYMBOLS` symbols and returns the most recent `EXAMPLES` matches that
  have `max(AFTER)` sessions after them. No look-ahead in the signal: every example is re-checked on the bars up to its
  own bar only (and dropped if that disagrees), and its evidence pack is built `as_of` that bar's close. What happened
  next (the % change of the close after 5, 10 and 20 sessions - daily sessions for a weekly screen too, from the
  week's last session) is labelled as after the bar and is never part of the match. The work runs in a worker thread:
  one full-series evaluation per symbol, then only the newest candidates are re-checked until three are confirmed,
  within `TIME_LIMIT_SECONDS` (review of TX4). A screen answered for the last bar only says so. A split or bonus inside the window drops the example (unadjusted prices would jump). Three examples are
  illustrations, not a statistic, a forecast or advice - the note says so.
* **Fork.** A pre-built screen (or its mirror) is copied into the organisation's saved screens with `forked_from` and
  `forked_version`; the copy is the user's to edit and never follows later versions.

Research only (ADR-0006): nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.screener import taxonomy as tx

FLAG = "screener_tx4"
EXAMPLES = 3
MAX_SYMBOLS = 10
AFTER: Tuple[int, ...] = (5, 10, 20)               # sessions after the matched bar
MIN_GAP = 20                                      # sessions between two examples of one symbol
HISTORY_DAYS = 5 * 366
TIME_LIMIT_SECONDS = 8.0                          # wall-clock cap of one examples request's CPU work (review of TX4)
DAILY_TFS = ("1d", "1w")
NOTE = ("Education: past matches of this screen on the shared end-of-day history and what the close did after them. Three "
        "examples are illustrations - not a statistic, not a forecast and not advice.")
METHOD_PAGE_NOTE = "The method page (P1 §1.6) is linked here once the Learn pages are available."


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def provenance(screen: tx.Screen, text: Optional[str]) -> Dict[str, Any]:
    source_line = screen.source_note
    sources: List[Dict[str, Any]] = []
    if screen.source == "legends":
        from app.screener import legends as lg
        library = lg.load()
        card = library.card(screen.id)
        if card is not None:
            payload = lg.card_payload(card, library)
            source_line, sources = payload["source_line"], payload["sources"]
    return {"id": screen.id, "version": screen.version, "changes": [dict(c) for c in screen.changes], "source_note": source_line,
            "sources": sources, "text": text, "text_sha256": text_sha256(text) if text else None}


def method_of(taxonomy: tx.Taxonomy, screen: tx.Screen) -> Dict[str, Any]:
    m = next((a for a in taxonomy.methods if a.id == screen.method), None)
    s = next((a for a in taxonomy.setups if a.id == screen.setup), None)
    return {"method": m.as_dict() if m else None, "setup": s.as_dict() if s else None, "page": None, "page_note": METHOD_PAGE_NOTE}


# --- past examples ---------------------------------------------------------------------------------------------------

@dataclass
class Example:
    symbol: str
    bar: int
    bar_time: datetime
    close: float
    after: Dict[str, Optional[float]]

    def as_dict(self) -> Dict[str, Any]:
        from app.market_data.calendar import IST
        return {"symbol": self.symbol, "date": self.bar_time.astimezone(IST).date().isoformat(), "close": round(self.close, 2),
                "after": dict(self.after)}


def _frame(bars: Sequence[Any]) -> pd.DataFrame:
    rows = [(b.ts, b.open, b.high, b.low, b.close, float(b.volume or 0)) for b in bars]
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    if frame.empty:
        return frame
    # the lake labels a daily bar by its END (15:30 IST); the runtime indexes bars by their START (the session's day)
    from app.market_data.calendar import IST
    starts = [pd.Timestamp(datetime.combine(pd.Timestamp(t).tz_convert(IST).date(), datetime.min.time()), tz=IST).tz_convert("UTC")
              for t in frame.pop("ts")]
    frame.index = pd.DatetimeIndex(starts)
    return frame.sort_index()


def _frames(daily: pd.DataFrame, base_tf: str) -> Dict[str, pd.DataFrame]:
    if base_tf == "1d":
        return {"1d": daily}
    from app.screener.runtime import resample
    return {"1d": daily, base_tf: resample(daily, "1d", base_tf)}


def matches_of(ast: Any, frames: Dict[str, pd.DataFrame], base_tf: str, symbol: str) -> List[int]:
    """The bars (positions in the base frame) where the screen holds - each one confirmed on its own prefix."""
    import warnings

    from app.screener.runtime import SymbolData, evaluate
    base = frames[base_tf]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        series = evaluate(ast, SymbolData(symbol, frames), base_tf=base_tf)
    if not isinstance(series, pd.Series):
        return []
    hits = [i for i, v in enumerate(series.reindex(base.index).tolist()) if isinstance(v, (bool, np.bool_)) and bool(v)]
    confirmed: List[int] = []
    for i in hits:
        cut = base.index[i]
        prefix = {tf: f[f.index <= cut] for tf, f in frames.items()}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            value = evaluate(ast, SymbolData(symbol, prefix), base_tf=base_tf)
        last = value.iloc[-1] if isinstance(value, pd.Series) and len(value) else value
        if isinstance(last, (bool, np.bool_)) and bool(last):
            confirmed.append(i)
    return confirmed


def _session_pos(symbol: str, i: int, base: pd.DataFrame, base_tf: str, daily: Optional[Dict[str, pd.DataFrame]]) -> Tuple[pd.DataFrame, int]:
    """(the daily frame, the position of the example's close in it). Review of TX4: "after" is counted in SESSIONS for a
    weekly screen too - from the last daily bar of the matched week, not in weeks."""
    if daily is None or base_tf == "1d" or symbol not in daily:
        return base, i
    from app.screener.runtime import bar_close
    closes_at = bar_close(base.index[i:i + 1], base_tf)[0]
    d = daily[symbol]
    j = int(d.index.searchsorted(closes_at, side="left")) - 1           # the last daily bar starting before the week closed
    return d, j


def pick(candidates: List[Tuple[str, int, pd.DataFrame]], blocked: Dict[str, List[Tuple[datetime, datetime]]],
         confirm: Optional[Callable[[str, int], bool]] = None, *, base_tf: str = "1d",
         daily: Optional[Dict[str, pd.DataFrame]] = None, deadline: Optional[float] = None) -> List[Example]:
    """The most recent EXAMPLES matches with max(AFTER) sessions after them, MIN_GAP bars apart per symbol, outside any
    window with a split or bonus - each confirmed on its own bars (`confirm`) only when it would be taken, newest first,
    stopping at EXAMPLES (review of TX4: never every hit) or at `deadline` (time.monotonic())."""
    import time as _time
    horizon = max(AFTER)
    out: List[Example] = []
    last_bar: Dict[str, int] = {}
    ordered = sorted(candidates, key=lambda c: c[2].index[c[1]], reverse=True)
    for symbol, i, base in ordered:
        if deadline is not None and _time.monotonic() > deadline:
            break
        frame, j = _session_pos(symbol, i, base, base_tf, daily)
        if j < 0 or j + horizon >= len(frame):
            continue
        if symbol in last_bar and last_bar[symbol] - i < MIN_GAP:
            continue
        start, end = base.index[max(0, i - 260)], frame.index[j + horizon]
        if any(lo <= end and hi >= start for lo, hi in blocked.get(symbol, [])):
            continue
        if confirm is not None and not confirm(symbol, i):
            continue                                                      # does not hold on its own bars: not an example
        close = float(frame["close"].iloc[j])
        after = {str(k): round((float(frame["close"].iloc[j + k]) / close - 1.0) * 100.0, 2) if close else None for k in AFTER}
        out.append(Example(symbol, i, base.index[i].to_pydatetime(), close, after))
        last_bar[symbol] = i
        if len(out) >= EXAMPLES:
            break
    return out


def _confirmed_on_prefix(ast: Any, frames: Dict[str, pd.DataFrame], base_tf: str, symbol: str, i: int) -> bool:
    import warnings

    from app.screener.runtime import SymbolData, evaluate
    cut = frames[base_tf].index[i]
    prefix = {tf: f[f.index <= cut] for tf, f in frames.items()}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        value = evaluate(ast, SymbolData(symbol, prefix), base_tf=base_tf)
    last = value.iloc[-1] if isinstance(value, pd.Series) and len(value) else value
    return isinstance(last, (bool, np.bool_)) and bool(last)


def last_bar_only(ast: Any) -> List[str]:
    from app.screener import nodes as nn
    from app.screener.registry import LAST_BAR_ONLY
    return sorted({node.name for node in nn.walk(ast) if isinstance(node, nn.Call) and node.name in LAST_BAR_ONLY})


def choose(ast: Any, frames_by: Dict[str, Dict[str, pd.DataFrame]], base_tf: str, blocked: Dict[str, List[Tuple[datetime, datetime]]],
           time_limit: float = TIME_LIMIT_SECONDS) -> Tuple[List[Example], List[str], int]:
    """CPU part of `examples` (run in a worker thread): one full-series evaluation per symbol, then the newest candidates
    confirmed on their own bars until EXAMPLES are found or `time_limit` seconds pass. -> (examples, problems, hits)."""
    import time as _time
    import warnings

    from app.screener.runtime import SymbolData, evaluate
    deadline = _time.monotonic() + time_limit
    problems: List[str] = []
    candidates: List[Tuple[str, int, pd.DataFrame]] = []
    scalar = last_bar_only(ast)
    for symbol, frames in frames_by.items():
        if _time.monotonic() > deadline:
            problems.append(f"{symbol}: not read (time limit of {time_limit:g} s)")
            continue
        base = frames[base_tf]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                series = evaluate(ast, SymbolData(symbol, frames), base_tf=base_tf)
        except Exception as exc:  # noqa: BLE001 - one symbol's data never fails the others
            problems.append(f"{symbol}: could not be evaluated ({exc.__class__.__name__})")
            continue
        if not isinstance(series, pd.Series):
            # review of TX4: a last-bar-only answer cannot be read back in time - say so instead of "no example"
            problems.append(f"{symbol}: {', '.join(n + '()' for n in scalar) or 'this screen'} is answered for the last bar only, "
                            "so past matches cannot be found on the history")
            continue
        candidates += [(symbol, i, base) for i, v in enumerate(series.reindex(base.index).tolist())
                       if isinstance(v, (bool, np.bool_)) and bool(v)]
    daily = {s: f["1d"] for s, f in frames_by.items()} if base_tf != "1d" else None
    chosen = pick(candidates, blocked, lambda sym, i: _confirmed_on_prefix(ast, frames_by[sym], base_tf, sym, i),
                  base_tf=base_tf, daily=daily, deadline=deadline)
    if _time.monotonic() > deadline and len(chosen) < EXAMPLES:
        problems.append(f"stopped after {time_limit:g} s: fewer past examples than {EXAMPLES} were confirmed in time")
    return chosen, problems, len(candidates)


async def examples(session: AsyncSession, ast: Any, validated: Any, base_tf: str, symbols: Sequence[str], name: str,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    import asyncio

    from app.market_lake.adjust import actions_as_of, candles
    from app.market_lake.bhavcopy_backfill import TIMEFRAME
    now = now or datetime.now(timezone.utc)
    if base_tf not in DAILY_TFS:
        return {"examples": [], "problems": [f"Past examples use the end-of-day history: daily or weekly screens only ({base_tf} given)."],
                "scanned": 0, "note": NOTE}
    problems: List[str] = []
    frames_by: Dict[str, Dict[str, pd.DataFrame]] = {}
    blocked: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for symbol in symbols:                                   # the reads (async); the work runs in a thread below
        bars = await candles(session, f"NSE:{symbol}", TIMEFRAME, now - timedelta(days=HISTORY_DAYS), now, symbol=symbol, exchange="NSE",
                             adjusted=False, as_of=now)
        daily = _frame(bars)
        if len(daily) < 60:
            problems.append(f"{symbol}: {len(daily)} daily bars in the shared history")
            continue
        frames_by[symbol] = _frames(daily, base_tf)
        actions = [a for a in await actions_as_of(session, symbol, "NSE", as_of=now) if a.action in ("SPLIT", "BONUS")]
        blocked[symbol] = [(pd.Timestamp(a.ex_date, tz="UTC").to_pydatetime(), pd.Timestamp(a.ex_date, tz="UTC").to_pydatetime())
                           for a in actions]
    # Review of TX4 (MED): CPU-bound - off the event loop, newest candidates confirmed only until 3 are found, time-capped.
    # The reads are done: end the read transaction first, so no pooled connection sits idle in a transaction for up to
    # TIME_LIMIT_SECONDS (TX4 re-check; nothing here writes).
    await session.rollback()
    chosen, more, seen = await asyncio.to_thread(choose, ast, frames_by, base_tf, blocked)
    problems += more
    out = await asyncio.to_thread(_packs, ast, validated, chosen, frames_by, base_tf, name, problems)
    return {"examples": out, "problems": problems, "scanned": len(symbols), "matches_seen": seen, "note": NOTE,
            "after_sessions": list(AFTER)}


def _packs(ast: Any, validated: Any, chosen: List[Example], frames_by: Dict[str, Dict[str, pd.DataFrame]], base_tf: str, name: str,
           problems: List[str]) -> List[Dict[str, Any]]:
    from app.screener import evidence_store
    from app.screener.evidence import build_pack
    from app.screener.runtime import SymbolData, bar_close
    out: List[Dict[str, Any]] = []
    for ex in chosen:
        frames = frames_by[ex.symbol]
        base = frames[base_tf]
        closes_at = bar_close(base.index[ex.bar:ex.bar + 1], base_tf)[0].to_pydatetime()
        item = ex.as_dict()
        try:
            pack = build_pack(ast, validated, SymbolData(ex.symbol, frames), base_tf=base_tf, as_of=closes_at, exchange="NSE",
                              data_source="lake:nse_bhav", screen_name=name)
            item["pack"] = evidence_store.view(pack)
        except Exception as exc:  # noqa: BLE001 - an example without its chart still shows its numbers
            item["pack"] = None
            problems.append(f"{ex.symbol} {item['date']}: no evidence chart ({exc.__class__.__name__})")
        out.append(item)
    return out


def lint() -> List[str]:
    return [f"advice wording {tx.advice_words(t)}" for t in (NOTE, METHOD_PAGE_NOTE) if tx.advice_words(t)]


__all__ = ["FLAG", "NOTE", "EXAMPLES", "AFTER", "MAX_SYMBOLS", "TIME_LIMIT_SECONDS", "choose", "last_bar_only", "provenance", "method_of",
           "examples", "matches_of", "pick",
           "text_sha256", "lint"]

