"""SCR-FIX B1 / B3: why a symbol could not be read or checked, in plain words.

A screen run keeps every symbol it could not read (a broker refusal, an ended login, no bars, too little history) apart
from the symbols that were read and did not match. Each such symbol carries a `Reason`: a plain sentence for the user, a
machine code (`code`) and the ONE action that may fix it (`action`: log in, connect a broker, retry, another timeframe).
An exception's class name or raw text never reaches the user; the full text goes to the log.

`Reason` is a `str` (the sentence), so every caller that keeps per-symbol problems as text (`fetch_frames`' problems,
`Match.reason`, stored runs) is unchanged; the code and action ride along as attributes.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# the one action a reason offers (the page words them: "Log in", "Connect broker", "Retry", "Try another timeframe")
LOGIN, CONNECT, RETRY, TIMEFRAME = "login", "connect", "retry", "timeframe"
ACTIONS: Tuple[str, ...] = (LOGIN, CONNECT, RETRY, TIMEFRAME)

# machine codes
LOGIN_ENDED = "login_ended"
BROKER_REFUSED = "broker_refused"
TIMEFRAME_REFUSED = "timeframe_refused"
RATE_LIMITED = "rate_limited"
UNREACHABLE = "unreachable"
NO_BARS = "no_bars"
SESSION_BUDGET = "session_budget"
SHORT_HISTORY = "short_history"
NO_VALUE = "no_value"
NOT_CHECKED = "not_checked"
NOT_READ = "not_read"
NOT_STORED = "not_stored"              # stored daily history (before the broker login) lacks the symbol
NOTHING_READ = "nothing_read"          # the run-level error: not one symbol could be read

_SENTENCE = {
    LOGIN_ENDED: "Your broker login has ended. Log in again to read the bars.",
    BROKER_REFUSED: "The broker did not send the bars.",
    TIMEFRAME_REFUSED: "The broker does not send bars for this timeframe.",
    RATE_LIMITED: "The broker's request limit was reached. Run again in a minute.",
    UNREACHABLE: "The broker could not be reached.",
    NO_BARS: "No closed bars came back for this timeframe.",
    NO_VALUE: "The screen has no value on the last bar (too little history for its indicators).",
    NOT_CHECKED: "This stock's bars could not be checked.",
    NOT_READ: "The bars could not be read.",
}
_ACTION = {
    LOGIN_ENDED: LOGIN, BROKER_REFUSED: RETRY, TIMEFRAME_REFUSED: TIMEFRAME, RATE_LIMITED: RETRY, UNREACHABLE: RETRY,
    NO_BARS: TIMEFRAME, NOT_STORED: LOGIN, SESSION_BUDGET: RETRY, SHORT_HISTORY: TIMEFRAME, NO_VALUE: TIMEFRAME, NOT_CHECKED: RETRY, NOT_READ: RETRY,
}
# a tie between two groups of the same size: the action that fixes most first
_PRIORITY = {LOGIN: 0, CONNECT: 1, TIMEFRAME: 2, RETRY: 3}


class Reason(str):
    """A plain sentence (the str itself) with its machine `code` and its one `action`."""

    code: str
    action: str

    def __new__(cls, text: str, code: str, action: Optional[str] = None) -> "Reason":
        obj = super().__new__(cls, text)
        obj.code = code
        obj.action = action or _ACTION.get(code, RETRY)
        return obj

    def __getnewargs__(self) -> Tuple[str, str, str]:            # type: ignore[override]  # copy / pickle keep the code
        return str(self), self.code, self.action


def of(code: str, text: Optional[str] = None) -> Reason:
    """The reason for `code`, with its own sentence unless `text` is given."""
    return Reason(text or _SENTENCE.get(code, _SENTENCE[NOT_READ]), code)


def as_reason(value: Any) -> Reason:
    """Any per-symbol problem as a Reason - one already is; plain text from an older path keeps its words (NOT_READ)."""
    if isinstance(value, Reason):
        return value
    return Reason(str(value or _SENTENCE[NOT_READ]), NOT_READ)


def short_history(timeframes: Iterable[str]) -> Reason:
    return Reason(f"not enough history on {', '.join(sorted(timeframes))}", SHORT_HISTORY)


def from_exception(exc: BaseException, *, symbol: str = "", wait_text: Optional[str] = None) -> Reason:
    """A broker read's exception -> its Reason. The full text (never cut) goes to the log; the user gets the sentence.
    The budget is caught by class (`api_budget.BudgetUsed`, `BrokerBudgetUsed`), never by name. `wait_text`: the
    sentence a budget wait keeps (the page offers to run those symbols again)."""
    from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError, BrokerBudgetUsed
    from app.market_data.api_budget import BudgetUsed
    if isinstance(exc, (BudgetUsed, BrokerBudgetUsed)):
        return of(RATE_LIMITED, wait_text)
    logger.warning("screen read failed for %s: %s: %s", symbol or "?", type(exc).__name__, exc)
    if isinstance(exc, BrokerAuthenticationError):
        return of(LOGIN_ENDED)
    if isinstance(exc, BrokerAPIError):
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            return of(LOGIN_ENDED)
        if status == 429:
            return of(RATE_LIMITED)
        if "interval" in str(exc).lower():
            return of(TIMEFRAME_REFUSED)
        return of(BROKER_REFUSED)
    try:
        import httpx
        network: Tuple[type, ...] = (httpx.TransportError, asyncio.TimeoutError, ConnectionError, TimeoutError)
    except ImportError:                                      # pragma: no cover - httpx ships with the backend
        network = (asyncio.TimeoutError, ConnectionError, TimeoutError)
    if isinstance(exc, network):
        return of(UNREACHABLE)
    return of(NOT_READ)


def not_checked(exc: BaseException, symbol: str) -> Reason:
    """A screen that raised for one symbol (bad data): logged in full, the user reads a plain sentence."""
    logger.warning("screen evaluation failed for %s: %s: %s", symbol, type(exc).__name__, exc, exc_info=exc)
    return of(NOT_CHECKED)


def row(symbol: str, matched: bool, reason: Optional[str]) -> Dict[str, Any]:
    """One result row: `reason` (the sentence), `code` and `action` when the symbol could not be read or checked."""
    out: Dict[str, Any] = {"symbol": symbol, "matched": matched, "reason": str(reason) if reason is not None else None}
    if reason is not None:
        r = as_reason(reason)
        out["code"], out["action"] = r.code, r.action
    return out


def unread_rows(results: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows that could not be read or checked (a reason, never matched) - never counted as "did not match"."""
    return [r for r in results if not r.get("matched") and r.get("reason")]


def summary(results: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The grouped reason in one line + ONE action: the largest group of unread rows (a tie: the action that fixes most
    first). None when every symbol was read."""
    rows = unread_rows(results)
    if not rows:
        return None
    groups = Counter((r.get("code") or NOT_READ, str(r["reason"])) for r in rows)
    by_code: Counter = Counter()
    for (code, _text), count in groups.items():
        by_code[code] += count
    code = min(by_code, key=lambda c: (-by_code[c], _PRIORITY.get(_ACTION.get(c, RETRY), 9), c))
    text = max(((t, n) for (c, t), n in groups.items() if c == code), key=lambda tn: tn[1])[0]
    action = next((r.get("action") for r in rows if (r.get("code") or NOT_READ) == code and r.get("action")), _ACTION.get(code, RETRY))
    return {"code": code, "text": text, "action": action, "count": by_code[code]}


__all__ = ["Reason", "ACTIONS", "of", "as_reason", "short_history", "from_exception", "not_checked", "row", "unread_rows", "summary",
           "LOGIN_ENDED", "BROKER_REFUSED", "TIMEFRAME_REFUSED", "RATE_LIMITED", "UNREACHABLE", "NO_BARS", "SESSION_BUDGET",
           "SHORT_HISTORY", "NO_VALUE", "NOT_CHECKED", "NOT_READ", "NOT_STORED", "NOTHING_READ"]
