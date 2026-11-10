"""M94 PR-3 (S9 Circuit Watch): `BandDistancePct(side)` - how far the last price is from the day's price band.

* Source: the user's OWN broker quote (`credential_for_reader`: only a credential he linked himself, never a teammate's),
  read by the broker's multi-quote call (M90: one call per `quote_batch_max` instruments, each taking one token of the
  credential's quote budget, as the watchlist's batch quotes do). Only the adapters that carry the band in their quote
  (`BrokerInterface.price_bands`: Upstox and Kite, OPEN_QUESTIONS M94-Q5) are asked; any other broker is "not available
  from your broker" without a call.
* The band is the exchange's, as the quote reports it (`Quote.upper_circuit_limit` / `lower_circuit_limit`); ATP never
  computes one. The last price is the same quote's `ltp`.
* Last bar only (registry LAST_BAR_ONLY): a quote is known now, no band history is stored, so it is a scanner input
  only - the validator refuses reading it back in time and the history / backtest / template paths refuse it
  (`routes.missing_data` without `band=True`).
* Never guessed: no quote, no band in it, or a broker without bands is "not checked: price band not available" with
  the reason (ScreenRuntimeError -> `reasons.NOT_CHECKED`), never a pass, a no-match or a 0.
Read-only market data: no order path is reached. Research only - a distance on the chart's terms, never a buy / sell word.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from app.screener import nodes

logger = logging.getLogger(__name__)

LOADED = frozenset({"BandDistancePct"})
SIDES = ("upper", "lower")
NOT_AVAILABLE = "not checked: price band not available"
FROM_BROKER = "not available from your broker"
NOT_IN_QUOTE = "not in your broker's quote for this symbol"
NO_QUOTE = "no quote from your broker"
BUDGET = "your broker's request limit is in use - run again in a minute"
QUOTE_WAIT_SECONDS = 2.0          # per batch call: the wait for a token of the credential's quote budget


def calls(ast: Any) -> list[str]:
    """The band functions a screen uses, in order of appearance."""
    out: list[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        if isinstance(node, nodes.Call) and node.name in LOADED and node.name not in out:
            out.append(node.name)
    return out


def distance_pct(quote: Any, side: str) -> float | None:
    """(upper band - last price) / last price x 100, or (last price - lower band) / last price x 100, from one quote.
    None when the quote has no such band or no last price (never a computed band). Negative when the last price is past
    the band as reported (the quote's own numbers, shown as they are)."""
    if side not in SIDES:
        raise ValueError(f"unknown price band side {side!r}")
    if quote is None:
        return None
    band = getattr(quote, "upper_circuit_limit" if side == "upper" else "lower_circuit_limit", None)
    try:
        ltp = float(getattr(quote, "ltp", None))      # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if band is None or not math.isfinite(ltp) or ltp <= 0:
        return None
    return (float(band) - ltp) / ltp * 100.0 if side == "upper" else (ltp - float(band)) / ltp * 100.0


def value(data: Any, side: str) -> float:
    """The runtime's answer for one symbol (the last bar): the distance, or ScreenRuntimeError with the reason."""
    from app.screener.runtime import ScreenRuntimeError
    if side not in SIDES:
        raise ScreenRuntimeError(f"unknown price band side {side!r}")
    quote = getattr(data, "quote", None)
    out = distance_pct(quote, side)
    if out is None:
        why = getattr(data, "quote_reason", None) or (NOT_IN_QUOTE if quote is not None else NO_QUOTE)
        raise ScreenRuntimeError(f"{NOT_AVAILABLE} ({why})")
    return out


def _mark(universe: list[Any], reason: str) -> None:
    for d in universe:
        d.quote, d.quote_reason = None, reason


async def attach(session: Any, universe: list[Any], *, tenant_id: int, user_id: int | None, exchange: str) -> None:
    """Sets `quote` (or `quote_reason`) on each SymbolData from the user's own broker quote, batched."""
    if not universe:
        return
    from fastapi import HTTPException

    from app.brokers.token_lifecycle import build_adapter
    from app.market_data import api_budget
    from app.market_data.quotes_routes import _batch_keys, _innermost
    from app.market_data.reader_credential import credential_for_reader
    try:
        record = await credential_for_reader(session, tenant_id, user_id)
    except HTTPException:
        _mark(universe, NO_QUOTE)
        return
    adapter = build_adapter(record)
    if not getattr(type(_innermost(adapter)), "price_bands", False):
        _mark(universe, FROM_BROKER)
        return
    _mark(universe, NOT_IN_QUOTE)
    pairs = list(dict.fromkeys((exchange, d.symbol) for d in universe))
    got: dict[tuple[str, str], Any] = {}
    failed: dict[tuple[str, str], str] = {}
    size = _batch_keys(adapter)
    for start in range(0, len(pairs), size):
        chunk = pairs[start:start + size]
        if not await api_budget.take(record.id, record.broker_name, QUOTE_WAIT_SECONDS):
            failed.update({p: BUDGET for p in pairs[start:]})
            break
        try:
            got.update(await adapter.get_quotes_batch(chunk) or {})
        except Exception as exc:  # noqa: BLE001 - one failed call: its symbols say so, the screen still runs
            logger.info("band quotes failed via %s: %s: %s", record.broker_name, type(exc).__name__, exc)
            failed.update({p: NO_QUOTE for p in chunk})
    for d in universe:
        pair = (exchange, d.symbol)
        if pair in got:
            d.quote, d.quote_reason = got[pair], None
        elif pair in failed:
            d.quote_reason = failed[pair]


__all__ = ["LOADED", "SIDES", "NOT_AVAILABLE", "FROM_BROKER", "NOT_IN_QUOTE", "NO_QUOTE", "calls", "distance_pct", "value", "attach"]
