"""Analyze a chart, Advanced analysis from the picture alone (owner order M84): the symbol and the timeframe are read
from what is PRINTED on the uploaded chart (the header of a TradingView, broker or Kite screenshot), then mapped to
ATP's own instruments and timeframes.

- The read is the same AI picture read that "AI only" runs (the engine's image-only instructions and parser; its
  `recognised` block carries the instrument name, the exchange and the timeframe label as printed). Nothing else of
  that reply is used here, and no price is ever taken from the picture: every number of the reading still comes from
  ATP's stored candles (hybrid.read).
- The name maps to ATP's instruments: an index by the names ATP stores its index candles under, a stock by the
  instrument master (`app.instruments.master`: the NSE equity with that trading symbol, or the one NSE equity with
  that company name). The label maps to ATP's timeframes (5m / 15m / 1h / 1d). Only what is printed is used: a value
  that is missing, unclear, ambiguous or not one ATP reads is never guessed - the answer names that one value and why,
  and the user is asked for that value only.
- The user's own value always wins over the read one (an optional override on the form or on the result).
Reference only: nothing here places, proposes or changes an order.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstrumentRecord

FIELDS = ("symbol", "timeframe")
TF_IDS = {5: "5m", 15: "15m", 60: "1h", 1440: "1d"}
TF_ATP_WORDS = "5-minute, 15-minute, 1-hour or Daily"
ASK = {"symbol": "Type the chart's symbol to continue.", "timeframe": "Pick the chart's timeframe to continue."}
SYMBOL_NOT_PRINTED = "The symbol could not be read from your chart."
TF_NOT_PRINTED = "The timeframe could not be read from your chart."
MAX_TEXT = 40
_NAME_TAIL = re.compile(r"(?:\s+(?:LTD\.?|LIMITED))$")
_SUFFIX = re.compile(r"(?:\s+INDEX|-EQ|\s+EQ)$")
_SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,40}$")
# One timeframe label as charts print it: "15", "15m", "15 min", "1H", "1h", "60", "D", "1D", "Daily", "W", "1W", "1M".
_TF = re.compile(r"^(?P<n>\d{1,4})?\s*(?P<unit>[A-Za-z]*)$")
_MIN = {"m", "min", "mins", "minute", "minutes"}
_HOUR = {"h", "hr", "hrs", "hour", "hours", "hourly"}
_DAY = {"d", "day", "days", "daily"}
_WEEK = {"w", "wk", "week", "weeks", "weekly"}
_MONTH = {"mo", "mon", "month", "months", "monthly"}


def clean(value: Any) -> str | None:
    """One printed value as a short single line, or None."""
    if not isinstance(value, str):
        return None
    out = " ".join(value.replace("\x00", " ").replace("·", " ").split())[:MAX_TEXT].strip()
    return out or None


def printed(raw: Any) -> dict[str, str | None]:
    """The symbol, exchange and timeframe label as printed on the chart, from the engine's parsed picture read."""
    rec = raw.get("recognised") if isinstance(raw, dict) else None
    rec = rec if isinstance(rec, dict) else {}
    return {"symbol_text": clean(rec.get("symbol")), "timeframe_text": clean(rec.get("timeframe")), "exchange": clean(rec.get("exchange"))}


def _quote(text: str) -> str:
    return f"“{text}”"


def map_timeframe(text: str | None) -> tuple[str | None, str | None]:
    """(ATP's timeframe id, None) or (None, the exact reason). A bare number is minutes (how TradingView prints it);
    a capital "M" alone is a month (TradingView's "1M"), any other "m" spelling is minutes."""
    if not text:
        return None, TF_NOT_PRINTED
    m = _TF.fullmatch(text.strip())
    if m is None or (m.group("n") is None and not m.group("unit")):
        return None, f"{_quote(text)} on your chart is not a timeframe ATP recognises."
    n = int(m.group("n") or 1)
    unit = m.group("unit")
    low = unit.lower()
    if n == 0:
        return None, f"{_quote(text)} on your chart is not a timeframe ATP recognises."
    if unit == "M" or low in _MONTH:
        minutes = None
        word = "monthly" if n == 1 else f"{n}-month"
    elif unit == "" or low in _MIN:
        minutes, word = n, f"{n}-minute"
    elif low in _HOUR:
        minutes, word = n * 60, f"{n}-hour"
    elif low in _DAY:
        minutes, word = n * 1440, ("daily" if n == 1 else f"{n}-day")
    elif low in _WEEK:
        minutes, word = None, ("weekly" if n == 1 else f"{n}-week")
    else:
        return None, f"{_quote(text)} on your chart is not a timeframe ATP recognises."
    tf = TF_IDS.get(minutes) if minutes is not None else None
    if tf is None:
        return None, f"Your chart is {word} ({text}); Advanced analysis reads {TF_ATP_WORDS} charts."
    return tf, None


def _nospace(value: str) -> str:
    return value.replace(" ", "")


def _index_aliases() -> dict[str, str]:
    from app.ai.tools.copilot import INDEX_NAMES
    from app.instruments.master import INDEX_EXCHANGE, INDEX_SYMBOLS, UNDERLYING_ALIASES
    names = set(INDEX_NAMES) | set(UNDERLYING_ALIASES) | set(INDEX_EXCHANGE) | set(INDEX_SYMBOLS.values())
    return {_nospace(n): n for n in names}


def _is_index_key(key: str) -> bool:
    return key.startswith(("NSE_INDEX:", "BSE_INDEX:"))


def _index_symbol(alias: str) -> tuple[str | None, str]:
    """The name ATP stores this index's candles under (or None) and the index's display name."""
    from app.ai.tools.copilot import INDEX_NAMES, lake_key
    from app.instruments.master import INDEX_SYMBOLS, underlying_of
    display = INDEX_SYMBOLS.get(underlying_of(alias)) or INDEX_NAMES.get(alias) or alias
    for name in (INDEX_SYMBOLS.get(underlying_of(alias)), INDEX_NAMES.get(alias), alias):
        if name and _is_index_key(lake_key(name)):
            return name, display
    return None, display


def _plain_name(value: str) -> str:
    return _NAME_TAIL.sub("", " ".join(value.upper().replace(".", " ").split())).strip()


async def map_symbol(session: AsyncSession, text: str | None) -> tuple[str | None, str | None]:
    """(ATP's symbol, None) or (None, the exact reason)."""
    if not text:
        return None, SYMBOL_NOT_PRINTED
    norm = " ".join(text.upper().split())
    if ":" in norm:                                   # "NSE:RELIANCE" - the exchange prefix as TradingView prints it
        norm = norm.split(":", 1)[1].strip()
    norm = _SUFFIX.sub("", norm).strip()
    if not norm:
        return None, SYMBOL_NOT_PRINTED
    alias = _index_aliases().get(_nospace(norm))
    if alias is not None:
        symbol, display = _index_symbol(alias)
        return (symbol, None) if symbol else (None, f"ATP does not read the {display} index yet.")
    if _SYMBOL.fullmatch(norm):
        rows = list(await session.scalars(select(InstrumentRecord).where(InstrumentRecord.tradingsymbol == norm).limit(20)))
        if any(r.instrument_type == "EQ" and r.exchange == "NSE" for r in rows):
            return norm, None
        if any(r.instrument_type in ("FUT", "CE", "PE") for r in rows):
            return None, f"{_quote(text)} on your chart is a futures or option contract; Advanced analysis reads the index or the stock itself."
        if any(r.instrument_type == "INDEX" for r in rows):
            return None, f"ATP does not read the {norm} index yet."
    wanted = _plain_name(norm)
    rows = list(await session.scalars(select(InstrumentRecord).where(
        InstrumentRecord.exchange == "NSE", InstrumentRecord.instrument_type == "EQ",
        func.upper(InstrumentRecord.name).like(f"%{wanted}%")).limit(50)))
    exact = sorted({r.tradingsymbol for r in rows if r.name and _plain_name(r.name) == wanted})
    if len(exact) == 1 and _SYMBOL.fullmatch(exact[0]):
        return exact[0], None
    if len(exact) > 1:
        return None, f"{_quote(text)} from your chart matches more than one instrument ({', '.join(exact[:3])})."
    loaded = await session.scalar(select(func.count()).select_from(InstrumentRecord).where(
        InstrumentRecord.exchange == "NSE", InstrumentRecord.instrument_type == "EQ"))
    if not loaded:
        return None, f"ATP's instrument list is not loaded yet, so {_quote(text)} from your chart cannot be matched."
    return None, f"{_quote(text)} from your chart is not in ATP's instrument list."


async def resolve(session: AsyncSession, override: dict[str, str | None], seen: dict[str, str | None] | None) -> dict[str, Any]:
    """The pick for the reading: the user's value where he gave one, else the value read from the chart.
    `read` holds what the chart showed (as printed and as mapped), `source` where each value came from, and `needs`
    the values still missing - each with its exact reason and the one question to ask (empty when the pick is whole)."""
    read: dict[str, Any] = {"symbol": None, "timeframe": None, "symbol_text": None, "timeframe_text": None}
    reasons: dict[str, str] = {}
    if seen is not None:
        read["symbol_text"], read["timeframe_text"] = seen.get("symbol_text"), seen.get("timeframe_text")
        if not override.get("symbol"):
            read["symbol"], why = await map_symbol(session, seen.get("symbol_text"))
            if why:
                reasons["symbol"] = why
        if not override.get("timeframe"):
            read["timeframe"], why = map_timeframe(seen.get("timeframe_text"))
            if why:
                reasons["timeframe"] = why
    pick: dict[str, str | None] = {f: override.get(f) or read[f] for f in FIELDS}
    source = {f: ("you" if override.get(f) else "chart" if read[f] else None) for f in FIELDS}
    needs = [{"field": f, "reason": reasons.get(f) or (SYMBOL_NOT_PRINTED if f == "symbol" else TF_NOT_PRINTED), "ask": ASK[f]}
             for f in FIELDS if not pick[f]]
    return {"pick": pick, "read": read if seen is not None else None, "source": source, "needs": needs}


def unread(reason: str) -> list[dict[str, str]]:
    """Both values asked for, with the one reason the picture could not be read at all."""
    return [{"field": f, "reason": reason, "ask": ASK[f]} for f in FIELDS]


__all__ = ["FIELDS", "ASK", "SYMBOL_NOT_PRINTED", "TF_NOT_PRINTED", "printed", "map_timeframe", "map_symbol", "resolve", "unread", "clean"]
