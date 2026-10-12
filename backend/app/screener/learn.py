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
  next (the % change of the close after 5, 10 and 20 sessions) is labelled as after the bar and is never part of the
  match. A split or bonus inside the window drops the example (unadjusted prices would jump). Three examples are
  illustrations, not a statistic, a forecast or advice - the note says so.
* **Fork.** A pre-built screen (or its mirror) is copied into the organisation's saved screens with `forked_from` and
  `forked_version`; the copy is the user's to edit and never follows later versions.

Research only (ADR-0006): nothing here places, changes or blocks an order.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


def pick(candidates: List[Tuple[str, int, pd.DataFrame]], blocked: Dict[str, List[Tuple[datetime, datetime]]]) -> List[Example]:
    """The most recent EXAMPLES matches with max(AFTER) sessions after them, MIN_GAP sessions apart per symbol, outside
    any window with a split or bonus."""
    horizon = max(AFTER)
    out: List[Example] = []
    last_bar: Dict[str, int] = {}
    ordered = sorted(candidates, key=lambda c: c[2].index[c[1]], reverse=True)
    for symbol, i, base in ordered:
        if i + horizon >= len(base):
            continue
        if symbol in last_bar and last_bar[symbol] - i < MIN_GAP:
            continue
        start, end = base.index[max(0, i - 260)], base.index[i + horizon]
        if any(lo <= end and hi >= start for lo, hi in blocked.get(symbol, [])):
            continue
        close = float(base["close"].iloc[i])
        after = {str(k): round((float(base["close"].iloc[i + k]) / close - 1.0) * 100.0, 2) if close else None for k in AFTER}
        out.append(Example(symbol, i, base.index[i].to_pydatetime(), close, after))
        last_bar[symbol] = i
        if len(out) >= EXAMPLES:
            break
    return out


async def examples(session: AsyncSession, ast: Any, validated: Any, base_tf: str, symbols: Sequence[str], name: str,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    from app.market_lake.adjust import actions_as_of, candles
    from app.market_lake.bhavcopy_backfill import TIMEFRAME
    from app.screener import evidence_store
    from app.screener.evidence import build_pack
    from app.screener.runtime import SymbolData
    now = now or datetime.now(timezone.utc)
    if base_tf not in DAILY_TFS:
        return {"examples": [], "problems": [f"Past examples use the end-of-day history: daily or weekly screens only ({base_tf} given)."],
                "scanned": 0, "note": NOTE}
    problems: List[str] = []
    candidates: List[Tuple[str, int, pd.DataFrame]] = []
    frames_by: Dict[str, Dict[str, pd.DataFrame]] = {}
    blocked: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for symbol in symbols:
        bars = await candles(session, f"NSE:{symbol}", TIMEFRAME, now - timedelta(days=HISTORY_DAYS), now, symbol=symbol, exchange="NSE",
                             adjusted=False, as_of=now)
        daily = _frame(bars)
        if len(daily) < 60:
            problems.append(f"{symbol}: {len(daily)} daily bars in the shared history")
            continue
        frames = _frames(daily, base_tf)
        frames_by[symbol] = frames
        actions = [a for a in await actions_as_of(session, symbol, "NSE", as_of=now) if a.action in ("SPLIT", "BONUS")]
        blocked[symbol] = [(pd.Timestamp(a.ex_date, tz="UTC").to_pydatetime(), pd.Timestamp(a.ex_date, tz="UTC").to_pydatetime())
                           for a in actions]
        try:
            hits = matches_of(ast, frames, base_tf, symbol)
        except Exception as exc:  # noqa: BLE001 - one symbol's data never fails the others
            problems.append(f"{symbol}: could not be evaluated ({exc.__class__.__name__})")
            continue
        candidates += [(symbol, i, frames[base_tf]) for i in hits]
    chosen = pick(candidates, blocked)
    out: List[Dict[str, Any]] = []
    for ex in chosen:
        frames = frames_by[ex.symbol]
        base = frames[base_tf]
        from app.screener.runtime import bar_close
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
    return {"examples": out, "problems": problems, "scanned": len(symbols), "matches_seen": len(candidates), "note": NOTE,
            "after_sessions": list(AFTER)}


def lint() -> List[str]:
    return [f"advice wording {tx.advice_words(t)}" for t in (NOTE, METHOD_PAGE_NOTE) if tx.advice_words(t)]


__all__ = ["FLAG", "NOTE", "EXAMPLES", "AFTER", "MAX_SYMBOLS", "provenance", "method_of", "examples", "matches_of", "pick",
           "text_sha256", "lint"]

