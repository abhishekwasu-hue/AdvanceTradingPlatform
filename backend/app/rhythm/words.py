"""The daily rhythm's language guard and its structure words (CLAUDE.md "Signal language").

Every line a digest shows passes `clean()`: a line with a trade-call word (buy / sell / target / stop / stop-loss / SL,
the Analyze-a-chart list in app/chart_analyze/signal_language.py) or an advice phrase is dropped, never shown. A setup
line (the user's own strategy or scan) must also pass `numberless()`: no digit at all once the symbol's own name is
taken out, so no price, level or other number is ever part of a signal's text.

"Buyer area" / "seller area" are structure words (the method's area sides), not calls; they are allowed.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

from app.chart_analyze.signal_language import call_words

# Advice phrases on top of the call words: a digest describes, it never tells the user what to do.
_ADVICE = re.compile(
    r"\b(?:you should|you must|should (?:buy|exit|enter|hold|book)|recommend\w*|advis\w*|go long|go short|enter now|exit now|"
    r"book (?:profits?|losses?)|take (?:profits?|the trade)|add (?:more|to it)|average down|sure[- ]shot|guaranteed)\b",
    re.IGNORECASE,
)
_DIGIT = re.compile(r"\d")


def clean(text: Optional[str]) -> Optional[str]:
    """The text, or None when it carries a trade-call word or an advice phrase (or is empty)."""
    value = " ".join(str(text or "").split())
    if not value or call_words(value) or _ADVICE.search(value):
        return None
    return value


def numberless(text: str, names: Iterable[str] = ()) -> bool:
    """True when `text` has no digit once each of `names` (a symbol such as "NIFTY 50") is removed."""
    rest = text
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        rest = re.sub(re.escape(name), " ", rest, flags=re.IGNORECASE)
    return not _DIGIT.search(rest)


def signal_line(text: str, names: Iterable[str] = ()) -> Optional[str]:
    """A setup line: clean and without any number (the symbol's own name excepted)."""
    value = clean(text)
    return value if value is not None and numberless(value, names) else None


# --- structure words -------------------------------------------------------------------------------------------------

TREND_WORDS: Dict[str, str] = {
    "UPTREND": "higher highs and higher lows",
    "DOWNTREND": "lower highs and lower lows",
    "RANGE": "no clear swing sequence (sideways)",
    "SIDEWAYS": "no clear swing sequence (sideways)",
}

_EVENT_WORDS: Dict[tuple, str] = {
    ("BOS", "BULLISH"): "the last swing high was taken out, so the up-structure continued",
    ("BOS", "BEARISH"): "the last swing low was taken out, so the down-structure continued",
    ("CHOCH", "BULLISH"): "a change of character upward: a down-structure's last swing high was taken out",
    ("CHOCH", "BEARISH"): "a change of character downward: an up-structure's last swing low was taken out",
}

SIDE_WORDS: Dict[str, str] = {"LONG": "buyer-side", "BUY": "buyer-side", "SHORT": "seller-side", "SELL": "seller-side"}


def trend_words(trend: Optional[str]) -> str:
    return TREND_WORDS.get(str(trend or "").upper(), "no clear swing sequence (sideways)")


def event_words(event: Optional[Dict[str, Any]]) -> Optional[str]:
    if not event:
        return None
    key = (str(event.get("event") or "").upper(), str(event.get("direction") or "").upper())
    return _EVENT_WORDS.get(key)


def _inside(price: float, area: Optional[Dict[str, Any]]) -> bool:
    if not area:
        return False
    try:
        return float(area["low"]) <= price <= float(area["high"])
    except (KeyError, TypeError, ValueError):
        return False


def area_words(price: Optional[float], support: Optional[Dict[str, Any]], resistance: Optional[Dict[str, Any]]) -> Optional[str]:
    """Where price sits between the nearest buyer area (support) below and seller area (resistance) above - in words; the
    numbers are used here and never shown."""
    if price is None:
        return None
    if _inside(price, support):
        return "price is inside a buyer area"
    if _inside(price, resistance):
        return "price is inside a seller area"
    try:
        below = price - float(support["high"]) if support else None
        above = float(resistance["low"]) - price if resistance else None
    except (KeyError, TypeError, ValueError):
        return None
    if below is not None and above is not None and below >= 0 and above >= 0:
        return ("price is closer to the buyer area below than to the seller area above" if below < above
                else "price is closer to the seller area above than to the buyer area below")
    if below is not None and below >= 0:
        return "price is above a buyer area"
    if above is not None and above >= 0:
        return "price is below a seller area"
    return None


def evidence_words(grade: Optional[str]) -> Optional[str]:
    """A signal's grade in plain words (no score number)."""
    g = str(grade or "").strip().upper()
    if g in ("A1", "A", "HIGH QUALITY"):
        return "strong evidence"
    if g in ("B", "VALID"):
        return "good evidence"
    if g in ("C", "WEAK"):
        return "some evidence"
    return None


__all__ = ["clean", "numberless", "signal_line", "trend_words", "event_words", "area_words", "evidence_words", "SIDE_WORDS"]
