"""The signal language: ONE formatter and ONE checker for every place that shows a signal (handover 11l, CLAUDE.md
"Signal language and the marketplace").

A signal is described in STRUCTURE WORDS only ("the market is in a downtrend; the pullback reached the seller area; a
handover is forming inside the area"). Two places, two rules:

* PUBLIC / EDUCATION surfaces (Idea Library, Copilot teacher, brief text, learning examples, marketing, share cards):
  no security names and no prices.
* The user's OWN signals (his adopted strategy, his chosen symbols): the symbol is shown, and the AREA may be shown as a
  price range ("Seller area: 23,000–23,100"). No other number, no single price.
* Everywhere a signal is described: no buy / sell / target / stop-loss wording.

What the user's automation did (orders, lots, fills) is a factual record on the Orders / Positions pages and is never
part of the signal text, so nothing here formats an order.

The engine may use numbers internally; this module governs only what is SHOWN. The frontend mirror is
`frontend/src/signals/language.ts`; both run the shared cases in `tests/fixtures/signal_language.json` (copied byte for
byte to `frontend/src/signals/__fixtures__/`), so the two sides cannot drift.

The older guards now route through here with their exact behaviour kept (tests/test_signal_language.py proves the
outputs are identical on a corpus): `call_words` / `is_clean` (Analyze a chart, #351) and `strict_guard` (the strategy
templates' token guard, #343). Places whose shown text changes for users do so only behind the flag
`signal_language_v2` (FLAG; OFF by default) - with it off every output is byte-identical.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, List, Literal, Optional, Sequence, Tuple

FLAG = "signal_language_v2"

# --- 1. trade-call words (Analyze a chart, #351; the frontend's chartAnalyze/signalLanguage.ts runs the same list) -------
# Whole words only, any case, except "SL", which is matched only in capitals (a lower-case "sl" is not a trading word).
# "buyer" / "seller" (the method's area sides) are structure words and are NOT matched.
CALL_WORDS = re.compile(
    r"\b(?:buy|buys|buying|bought|sell|sells|selling|sold|targets?|targeted|targeting|stops?|stop[\s-]?loss(?:es)?|stoploss(?:es)?)\b",
    re.IGNORECASE,
)
SL_WORD = re.compile(r"\bSL\b")


def call_words(text: str) -> List[str]:
    """The trade-call words in `text`, lower-cased, in order (empty when the text is clean)."""
    if not text:
        return []
    found = [m.group(0).lower() for m in CALL_WORDS.finditer(text)]
    found += ["sl" for _ in SL_WORD.finditer(text)]
    return found


def is_clean(text: str) -> bool:
    return not call_words(text)


# --- 2. the strict token guard (strategy templates, #343) ---------------------------------------------------------------
# Words a template phrase may never carry: trade instructions, levels and outcome words. Matched as whole words,
# case-insensitively, also inside hyphenated words ("stop-loss").
STRICT_WORDS: Tuple[str, ...] = (
    "buy", "buying", "bought", "sell", "selling", "sold", "target", "targets", "stop", "stops", "stoploss", "sl",
    "loss", "profit", "profits", "entry", "enter", "exit", "long", "short", "order", "trade", "trades", "price",
    "guaranteed", "sure", "recommend", "recommended", "should", "must")
_LETTERS = re.compile(r"[A-Za-z]+")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def strict_guard(text: str, *, timeframe_words: Iterable[str], placeholders: Sequence[str],
                 forbidden: Sequence[str] = STRICT_WORDS) -> List[str]:
    """The problems of a template phrase or rendered text (empty = clean): a forbidden word, a digit outside a timeframe
    name (a price or a value), an unknown placeholder, or a character outside plain English text."""
    problems: List[str] = []
    for word in _LETTERS.findall(text):
        if word.lower() in forbidden:
            problems.append(f"forbidden word {word!r}")
    for name in _PLACEHOLDER.findall(text):
        if name not in placeholders:
            problems.append(f"unknown placeholder {{{name}}}")
    tf_digits = re.compile("|".join(re.escape(w) for w in sorted(timeframe_words, key=len, reverse=True)))
    rest = tf_digits.sub("", _PLACEHOLDER.sub("", text))
    if re.search(r"[0-9]", rest):
        problems.append("a number (signal text carries no prices or values)")
    if re.search(r"[^A-Za-z0-9 ,;.\-'{}_()]", text):
        problems.append("a character outside plain English text")
    return problems


# --- 3. the formatter -----------------------------------------------------------------------------------------------------
Surface = Literal["signal_own", "signal_public", "education"]
SURFACES: Tuple[str, ...] = ("signal_own", "signal_public", "education")

# The timeframe names a signal may carry (the only digits allowed outside an own area range).
TIMEFRAME_WORDS: Tuple[str, ...] = ("1-minute", "3-minute", "5-minute", "15-minute", "30-minute", "1-hour", "4-hour")
TIMEFRAMES = {"1m": "1-minute", "3m": "3-minute", "5m": "5-minute", "15m": "15-minute", "30m": "30-minute", "1h": "1-hour",
              "4h": "4-hour", "1d": "Daily", "1w": "Weekly", "1M": "Monthly"}

TRENDS = {
    "up": "the market is in an uptrend",
    "down": "the market is in a downtrend",
    "range": "the market is moving sideways in a range",
}
SIDES = {"buyer": "buyer", "seller": "seller"}
# Steps of the reading, in structure words. "{side}" is the area side; "{the}" is "the" (own) or "a" (public).
STEPS = {
    "pullback_reached_area": "the pullback reached {the} {side} area",
    "rally_reached_area": "the move reached {the} {side} area",
    "handover_forming": "a handover is forming inside the area",
    "handover_complete": "the handover inside the area is complete",
    "swept_and_reclaimed": "the move swept beyond the area and came back inside",
    "range_edge": "the move reached the edge of the range",
    "channel_edge": "the move reached the edge of the channel",
    "breakout_retest": "the breakout is being retested from the other side",
    "structure_shift": "the structure shifted against the last swing",
}
SETUPS = {
    "trend_continuation": "a trend-continuation setup has formed",
    "reversal": "a reversal setup has formed",
    "range_reversion": "a range setup has formed at the edge",
    "breakout_retest": "a breakout-and-retest setup has formed",
    "sweep_reclaim": "a sweep-and-reclaim setup has formed",
}
EVIDENCE = {
    "ltf_choch": "lower-timeframe change of character",
    "bos": "break of structure",
    "bullish_divergence": "bullish RSI divergence",
    "bearish_divergence": "bearish RSI divergence",
    "liquidity_sweep": "liquidity sweep",
    "rejection_candle": "a rejection candle",
    "volume_dry_up": "volume drying up in the pullback",
    "engulfing_candle": "an engulfing candle",
}
RANGE_DASH = "–"            # an en dash between the two edges of an area


@dataclass(frozen=True)
class SignalStructure:
    """What the engine read, as token ids (never free text). `symbol` and `area_range` are shown on the user's OWN
    signals only; on a public surface they are left out whatever is passed."""
    trend: Optional[str] = None
    side: Optional[str] = None                       # "buyer" / "seller": the area's side
    steps: Tuple[str, ...] = ()
    setup: Optional[str] = None
    evidence: Tuple[str, ...] = ()
    timeframe: Optional[str] = None                  # "1h" etc.: "On the 1-hour chart, ..."
    symbol: Optional[str] = None
    area_range: Optional[Tuple[float, float]] = None

    def unknown(self) -> List[str]:
        """Token ids this module has no phrase for (a formatter refuses them rather than guessing)."""
        bad: List[str] = []
        for kind, value, table in (("trend", self.trend, TRENDS), ("side", self.side, SIDES), ("setup", self.setup, SETUPS),
                                   ("timeframe", self.timeframe, TIMEFRAMES)):
            if value is not None and value not in table:
                bad.append(f"{kind} {value}")
        bad += [f"step {s}" for s in self.steps if s not in STEPS]
        bad += [f"evidence {e}" for e in self.evidence if e not in EVIDENCE]
        return bad


def group_in(value: float) -> str:
    """A number in Indian digit grouping (23,000; 1,23,456), with at most two decimals and no trailing zeros."""
    total = math.floor(abs(float(value)) * 100 + 0.5)        # half up, as the frontend's Math.round (parity)
    whole, cents = divmod(int(total), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        pairs: List[str] = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join([head, *pairs, tail])
    frac = f"{cents:02d}".rstrip("0") if cents else ""
    return ("-" if value < 0 and (whole or cents) else "") + digits + (f".{frac}" if frac else "")


def area_label(side: str, low: float, high: float) -> str:
    """'Seller area: 23,000–23,100' (the edges in order)."""
    lo, hi = sorted((float(low), float(high)))
    return f"{SIDES[side].capitalize()} area: {group_in(lo)}{RANGE_DASH}{group_in(hi)}"


def _sentence(parts: Sequence[str]) -> str:
    text = "; ".join(p for p in parts if p)
    return (text[:1].upper() + text[1:] + ".") if text else ""


def format_signal(s: SignalStructure, surface: Surface) -> str:
    """The text of a signal. Raises ValueError for a token without a phrase, or a step that names the area without a
    side. Public surfaces never show the symbol or the area range."""
    bad = s.unknown()
    if bad:
        raise ValueError("no phrase for: " + ", ".join(bad))
    if surface not in ("signal_own", "signal_public"):
        raise ValueError(f"not a signal surface: {surface}")
    own = surface == "signal_own"
    show_area = own and s.area_range is not None and s.side is not None
    words = {"the": "this" if show_area else "the" if own else "a", "side": SIDES.get(s.side or "", "")}
    parts: List[str] = []
    if s.trend:
        parts.append(TRENDS[s.trend])
    for step in s.steps:
        if "{side}" in STEPS[step] and not s.side:
            raise ValueError(f"step {step} needs the area side")
        parts.append(STEPS[step].format(**words))
    if s.setup:
        parts.append(SETUPS[s.setup])
    body = _sentence(parts)
    if s.timeframe and body:
        body = f"On the {TIMEFRAMES[s.timeframe]} chart, " + body[:1].lower() + body[1:]
    if s.evidence:
        body = (body + " " if body else "") + "Evidence: " + ", ".join(EVIDENCE[e] for e in s.evidence) + "."
    head: List[str] = []
    if own and s.symbol:
        head.append(s.symbol.strip().upper())
    if show_area and s.area_range is not None and s.side:
        head.append(area_label(s.side, *s.area_range) + ".")
    if head:
        lead = " · ".join(head)
        if not body:
            return lead
        return f"{lead} {body}" if show_area else f"{lead} · {body}"
    return body


# --- 4. the checker -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Problem:
    kind: str           # call_word / number / currency / security_name
    detail: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail}


_NUMBER = r"\d[\d,]*(?:\.\d+)?"
AREA_RANGE = re.compile(rf"\b(?:Buyer|Seller) area: {_NUMBER}\s?[{RANGE_DASH}-]\s?{_NUMBER}")
_TF_WORDS = re.compile("|".join(re.escape(w) for w in sorted(TIMEFRAME_WORDS, key=len, reverse=True)))
CURRENCY = re.compile(r"₹|\bRs\.?(?=\s?\d)|\bINR\b|\$(?=\s?\d)")


def check(text: str, surface: Surface, *, names: Iterable[str] = (), own_symbols: Iterable[str] = ()) -> List[Problem]:
    """The policy problems of a shown text (empty = clean).

    * signal_own: no trade-call word, no currency, no digit outside an area range ("Seller area: 23,000–23,100") or a
      timeframe name. His own symbol may be named.
    * signal_public: the same, but no area range either, and none of `names` (security names) may appear.
    * education: none of `names` and no currency amount (teaching what a stop-loss is, or that RSI runs 0-100, is
      education, so call words and plain figures are allowed there).
    `names` are matched as whole words, case-sensitively (symbols are written in capitals). `own_symbols` (signal_own
    only) are the user's own symbols: their digits ("NIFTY 50") are not counted as numbers."""
    if surface not in SURFACES:
        raise ValueError(f"unknown surface: {surface}")
    text = text or ""
    problems: List[Problem] = []
    if surface != "education":
        problems += [Problem("call_word", w) for w in call_words(text)]
        rest = AREA_RANGE.sub(" ", text) if surface == "signal_own" else text
        if surface == "signal_own":
            for own in sorted({o.strip().upper() for o in own_symbols if o and o.strip()}, key=len, reverse=True):
                rest = re.sub(rf"(?<![A-Za-z0-9&-]){re.escape(own)}(?![A-Za-z0-9&-])", " ", rest)
        rest = _TF_WORDS.sub(" ", rest)
        problems += [Problem("number", m.group(0)) for m in re.finditer(_NUMBER, rest)]
    problems += [Problem("currency", m.group(0)) for m in CURRENCY.finditer(text)]
    if surface != "signal_own":
        for name in sorted({n.strip() for n in names if n and n.strip()}):
            if re.search(rf"(?<![A-Za-z0-9&-]){re.escape(name)}(?![A-Za-z0-9&-])", text):
                problems.append(Problem("security_name", name))
    return problems


def shown_lines(lines: Iterable[str], surface: Surface, *, names: Iterable[str] = (), own_symbols: Iterable[str] = ()) -> List[str]:
    """The lines that pass `check` (a line with a problem is left out, never rewritten)."""
    wanted, own = list(names), list(own_symbols)
    return [line for line in lines if not check(line, surface, names=wanted, own_symbols=own)]


# --- 5. fixed notices built from the formatter's rules -------------------------------------------------------------------

SIDE_OF_DIRECTION = {"LONG": "an upside", "SHORT": "a downside"}


def setup_notice(label: str, symbol: str, direction: str, strategy_name: str = "", reasons: Sequence[str] = ()) -> Tuple[str, str]:
    """(title, message) of a "notify only" setup on the user's OWN symbol (an execution intent routed to a notification):
    which symbol, which side of the market and which of his strategies - no single price, no buy / sell / target / stop
    wording. A strategy name or a "why" line that is not in the signal language is left out."""
    sym = (symbol or "").strip().upper()
    side = SIDE_OF_DIRECTION.get(str(direction or "").upper(), "a")
    name = (strategy_name or "").strip()
    whose = f"your strategy {name}" if name and not check(name, "signal_own", own_symbols=[sym]) else "your strategy"
    why = shown_lines([str(r) for r in reasons if str(r).strip()], "signal_own", own_symbols=[sym])[:3]
    message = f"{label}: {sym} - {side} setup from {whose} has formed."
    if why:
        message += " Why: " + "; ".join(w.rstrip(".") for w in why) + "."
    return f"{label}: {sym}", message + " Notify only: nothing is proposed or placed."


WITHHELD = "(This text was withheld: it read as advice rather than a description.)"


def withheld(text: str, surface: Surface, *, names: Iterable[str] = (), own_symbols: Iterable[str] = ()) -> str:
    """The text itself, or the fixed withheld line when it has a policy problem."""
    return WITHHELD if check(text, surface, names=names, own_symbols=own_symbols) else text


__all__ = ["FLAG", "CALL_WORDS", "SL_WORD", "call_words", "is_clean", "STRICT_WORDS", "strict_guard", "Surface", "SURFACES",
           "TIMEFRAME_WORDS", "TIMEFRAMES", "TRENDS", "SIDES", "STEPS", "SETUPS", "EVIDENCE", "SignalStructure", "group_in",
           "area_label", "format_signal", "Problem", "AREA_RANGE", "check", "shown_lines", "setup_notice", "WITHHELD", "withheld"]
