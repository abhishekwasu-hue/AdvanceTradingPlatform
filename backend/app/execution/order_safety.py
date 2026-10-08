"""Order-path safety helpers - pure functions, no broker calls, no state files.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, order_safety.py (G3): `market_protection_pct` /
`apply_market_protection`, `is_full_failure`, `pending_order_ids`, `plan_exit_resend`. Trade's JSON state files, fcntl
locks, Upstox order-book parsing and Telegram alerts are NOT ported: ATP keeps order state in the database
(OrderRecord / TradeRecord) and reconciles against the broker book (app/reconciliation).

Shapes (broker-neutral):
  order       {"symbol", "side": "BUY" | "SELL", "quantity", "product"?, "order_type"?}
  leg result  {"symbol", "status", "filled_quantity", "order_id"?}       (status: COMPLETE / REJECTED / CANCELLED / OPEN ...)
  position    {"symbol", "quantity" (net, signed), "product"?}
"""
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

TERMINAL_NOFILL = ("REJECTED", "CANCELLED", "CANCELED")
TERMINAL_ALL = ("COMPLETE", "FILLED", "TRADED") + TERMINAL_NOFILL
PROTECTION_MIN, PROTECTION_MAX = 1, 25
PROTECTED_TYPES = ("MARKET", "SL-M")
AUTO_PROTECTION = -1                     # Kite and Upstox: the broker's automatic market-protection band


def market_protection_pct(value: Any) -> Optional[int]:
    """A setting -> 1..25 (int) or None. None, a bool, a non-number or an out-of-range value -> None (send no field)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return v if PROTECTION_MIN <= v <= PROTECTION_MAX else None


def apply_market_protection(payload: Dict[str, Any], value: Any, auto: bool = False) -> Dict[str, Any]:
    """A copy with `market_protection` on a MARKET / SL-M order: the valid percentage (1-25) when one is set, else -1
    (the broker's automatic band) when `auto`; otherwise - and for limit orders, which carry their own price - the
    payload unchanged."""
    if str(payload.get("order_type", "")).upper() not in PROTECTED_TYPES:
        return payload
    pct = market_protection_pct(value)
    if pct is None:
        if not auto:
            return payload
        pct = AUTO_PROTECTION
    return {**payload, "market_protection": pct}


def _qty(x: Any) -> Optional[float]:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return None


def is_full_failure(legs: Optional[Sequence[Dict[str, Any]]] = None, status_code: Optional[int] = None,
                    order_ids: Iterable[str] = ()) -> bool:
    """True only for a CERTAIN complete failure: every leg terminal with nothing filled, or - with no leg information - a
    clear 4xx from the broker and no order id. Timeouts, 5xx, open / unknown legs, a cancelled-but-partly-filled leg ->
    False: a blind retry could double the exit or open a reverse position."""
    if legs:
        def _nofill(lg: Dict[str, Any]) -> bool:
            q = _qty(lg.get("filled_quantity"))
            return q is not None and q == 0 and str(lg.get("status", "")).upper() in TERMINAL_NOFILL
        return all(_nofill(lg) for lg in legs)
    if not isinstance(status_code, int) or not 400 <= status_code < 500:
        return False
    return not any(order_ids)


def pending_order_ids(legs: Sequence[Dict[str, Any]]) -> List[str]:
    """Order ids still working (or of unknown state) - an exit must not be re-sent while any of these is live."""
    return [str(lg["order_id"]) for lg in legs if lg.get("order_id") and str(lg.get("status", "")).upper() not in TERMINAL_ALL]


def _net(positions: Sequence[Dict[str, Any]], symbol: str, product: Optional[str]) -> Optional[int]:
    hits = [p for p in positions if p.get("symbol") == symbol and (not product or not p.get("product") or p.get("product") == product)]
    if not hits:
        return None                                              # missing is not flat
    try:
        return sum(int(round(float(p.get("quantity") or 0))) for p in hits)
    except (TypeError, ValueError):
        return None


def plan_exit_resend(close_orders: Sequence[Dict[str, Any]], positions: Optional[Sequence[Dict[str, Any]]], *,
                     previous_failed: bool, previous_nofill: bool = False, pending_ids: Sequence[str] = (),
                     order_status: Optional[Callable[[str], Optional[str]]] = None,
                     shared_symbols: Iterable[str] = ()) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """What the next exit attempt may send after an earlier one failed. Returns (orders, None) or (None, reason) - with
    a reason send NOTHING (unknown state; reconcile first). An empty list = every leg is already flat at the broker.
    No earlier failure -> the close orders unchanged.

    - an earlier order still pending / of unknown status -> stop;
    - another open trade on the same instrument -> stop (whose net quantity is it?);
    - broker positions available -> only the legs still open, at their remaining quantity; a leg missing from the
      positions, on the wrong side or larger than expected -> stop;
    - positions unavailable -> the same orders again only if the earlier attempt was a CERTAIN no-fill, else stop."""
    if not previous_failed:
        return list(close_orders), None
    for oid in pending_ids:
        status = None
        if order_status is not None:
            try:
                status = order_status(oid)
            except Exception:  # noqa: BLE001 - unknown is treated as working
                status = None
        if str(status or "").upper() not in TERMINAL_ALL:
            return None, f"earlier exit order {oid} is not terminal (status: {status or 'unknown'})"
    shared = sorted({o.get("symbol") for o in close_orders} & set(shared_symbols or ()))
    if shared:
        return None, f"another open trade on {', '.join(map(str, shared))} - the broker's net quantity is ambiguous"
    if positions is None:
        if not previous_nofill:
            return None, "broker positions unavailable and the earlier attempt may have filled some legs"
        return list(close_orders), None
    out: List[Dict[str, Any]] = []
    for o in close_orders:
        sym, side = o.get("symbol"), str(o.get("side", "")).upper()
        net = _net(positions, str(sym), o.get("product"))
        if net is None:
            return None, f"{sym}: not in the broker positions (missing is not closed)"
        if net == 0:
            continue                                             # this leg is already flat
        expect_long = side == "SELL"                             # a SELL closes a long
        if (net > 0) != expect_long:
            return None, f"{sym}: broker net quantity {net} is on the other side"
        try:
            want = int(o.get("quantity") or 0)
        except (TypeError, ValueError):
            return None, f"{sym}: invalid order quantity"
        if abs(net) == want:
            out.append(dict(o))
        elif abs(net) < want:
            out.append({**o, "quantity": abs(net)})              # partly closed already: only the remainder
        else:
            return None, f"{sym}: broker net quantity {abs(net)} is above the expected {want}"
    return out, None


def wing_fill_complete(requested: float, filled: float) -> bool:
    """A protective wing counts only when filled in full: a partial wing would leave part of the short naked."""
    return filled + 1e-9 >= requested
