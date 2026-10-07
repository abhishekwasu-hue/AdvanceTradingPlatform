"""P0.8-B: grounding checks shared by every LLM narration (thesis, Copilot answers, the knowledge guide, Telegram).

Two rules, applied after the model answers and before a human sees the text:

* **numbers-check** - every number the model wrote must be one of the numbers in the facts it was given. The facts'
  numbers come from *numeric* fields only (`numbers_in_values`) or from our own rule-generated sentences
  (`numbers_in_text`); digits inside untrusted strings such as news headlines are never evidence. Comparison keeps
  the sign (a -1.2% day may not become +1.2%), and the model's shorthand is normalised first: `25k`, `1.2 lakh`,
  `1.2 लाख`, `2 cr`, `25,200.00`, `73%`.
* **ticker-check** - a symbol the model names must appear in the facts or the question (`tickers_in`); indicator and
  platform acronyms are not symbols.

`wrap_untrusted` puts third-party text (headlines) into an `<untrusted_data>` block with the closing tag escaped, so a
headline that addresses the model is data, not an instruction.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Set, Tuple

_NUMBER = re.compile(
    r"(?<!\d)(?<!\d\.)(?<!\d,)[-+]?\d[\d,]*(?:\.\d+)?\s*(%|(?:k|K|lakh|lakhs|lac|L|cr|crore|crores|हजार|लाख|कोटी)(?![A-Za-z]))?"
)
_MULTIPLIERS = {"k": 1e3, "K": 1e3, "हजार": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "L": 1e5, "लाख": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7, "कोटी": 1e7}

# Upper-case tokens that are vocabulary, not tradable symbols.
ACRONYMS = frozenset({
    "RSI", "EMA", "SMA", "ATR", "ADX", "VWAP", "MACD", "OI", "PCR", "IV", "VIX", "OHLC", "ORB", "PDH", "PDL", "PDC", "BB", "DI", "R", "RR",
    "AI", "API", "NSE", "BSE", "MCX", "SEBI", "RBI", "FII", "DII", "GST", "STT", "INR", "USD", "UPI", "LTP", "P&L", "PNL", "SL", "TP", "CE", "PE",
    "LIVE", "PAPER", "LONG", "SHORT", "BUY", "SELL", "HOLD", "FLAT", "OK", "NO", "YES", "AND", "OR", "NOT", "THE", "FOR", "ON", "IN", "AT", "TO",
    "BULLISH", "BEARISH", "NEUTRAL", "UPTREND", "DOWNTREND", "RANGING", "TRENDING", "VOLATILE", "UNKNOWN", "ACTIVE", "PAUSED", "STOPPED",
    "RISK", "ON", "OFF", "EOD", "IST", "UTC", "ET", "FOMC", "MPC", "CPI", "GDP", "PMI", "WPI", "IIP", "US", "UK", "EU", "FX", "ETF", "IPO", "F&O",
    "ADR", "TOTP", "MFA", "JSON", "HTML", "URL", "ID", "QTY", "PF", "WIN", "LOSS", "DD", "ROI", "YTD", "MTD", "WTD", "CAGR", "ROE", "PE",
})
_TICKER = re.compile(r"\b[A-Z][A-Z&]{1,11}(?:\s?(?:50|100|200|500|BANK|NEXT ?50|FIN ?SERVICE|MIDCAP ?(?:50|100|SELECT)))?\b")


def _norm(value: float) -> Set[str]:
    out = {f"{value:.2f}".rstrip("0").rstrip("."), f"{value:.1f}".rstrip("0").rstrip("."), f"{value:.4f}".rstrip("0").rstrip(".")}
    out.add(str(int(value)) if float(value).is_integer() else f"{value:.4f}".rstrip("0").rstrip("."))
    return {o if o not in ("-0", "") else "0" for o in out}


def numbers_in_values(obj) -> Set[str]:
    """Normalised strings of every *numeric* leaf (int/float, not bool, not digits inside strings), sign kept."""
    found: Set[str] = set()

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            found.update(_norm(float(v)))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple, set)):
            for x in v:
                walk(x)
    walk(obj)
    return found


def numbers_in_text(text: str) -> List[Tuple[str, float]]:
    """(raw, value) for every number in `text`, shorthand expanded (`25k` -> 25000, `1.2 लाख` -> 120000); the sign kept."""
    out: List[Tuple[str, float]] = []
    for m in _NUMBER.finditer(text or ""):
        raw = m.group(0).strip()
        suffix = (m.group(1) or "").strip()
        body = raw[: len(raw) - len(suffix)].strip() if suffix else raw
        body = body.replace(",", "")
        try:
            value = float(body)
        except ValueError:
            continue
        if suffix and suffix != "%":
            value *= _MULTIPLIERS.get(suffix, 1.0)
        out.append((raw, value))
    return out


def allowed_from_text(text: str) -> Set[str]:
    """The normalised numbers in our own (trusted) sentences - rule lines, the question."""
    allowed: Set[str] = set()
    for _raw, value in numbers_in_text(text):
        allowed.update(_norm(value))
    return allowed


def check_numbers(text: str, allowed: Set[str]) -> Tuple[bool, List[str]]:
    """True when every number in `text` is in `allowed` (sign included); otherwise the offending raw strings."""
    bad: List[str] = []
    for raw, value in numbers_in_text(text):
        if not (_norm(value) & allowed):
            bad.append(raw)
    return (not bad), bad


def tickers_in(text: str) -> Set[str]:
    found: Set[str] = set()
    for m in _TICKER.finditer(text or ""):
        token = re.sub(r"\s+", " ", m.group(0).strip())
        head = token.split(" ")[0]
        if head in ACRONYMS or len(head) < 3 and token == head:
            continue
        found.add(token)
    return found


def check_tickers(text: str, allowed_text: str) -> Tuple[bool, List[str]]:
    """Every symbol-looking token in `text` must appear in `allowed_text` (the facts and the question)."""
    allowed = {t.replace(" ", "") for t in tickers_in(allowed_text)}
    bad = sorted(t for t in tickers_in(text) if t.replace(" ", "") not in allowed and t.split(" ")[0] not in allowed)
    return (not bad), bad


def wrap_untrusted(name: str, lines: Iterable[str]) -> str:
    body = "\n".join(re.sub(r"(?i)</?\s*untrusted_data", "[untrusted_data", str(line))[:400] for line in lines)
    return f'<untrusted_data name="{name}">\n{body}\n</untrusted_data>'


__all__ = ["numbers_in_values", "numbers_in_text", "allowed_from_text", "check_numbers", "tickers_in", "check_tickers", "wrap_untrusted", "ACRONYMS"]
