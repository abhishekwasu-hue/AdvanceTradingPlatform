"""P2-8 J1-3: the legs of one spread are ONE journal entry.

`spread_summary(legs)` (pure; same legs -> same answer) gives the structure's combined numbers from its own legs and the
structure facts it was placed with (`trades.group_meta`, written by the multi-leg path):

* `net_pnl`        the legs' P&L added up, once every leg is closed (None while one is open);
* `credit`         what the entry fills took in (+) or paid (-), from each leg's entry price and quantity;
* `planned_max_loss` the structure's own max loss per unit x its 1x quantity (None when the structure has no defined
                   maximum, e.g. a short straddle);
* `actual_loss`    the loss taken when the closed structure lost (None otherwise), beside the planned one.

`fold_rows(rows)` turns the journal list's one-row-per-leg into one row per structure (legs kept under `legs`).
Numbers only; nothing here judges the structure.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any


def _meta(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def spread_summary(legs: Sequence[Any]) -> dict[str, Any]:
    """`legs`: trade rows of one `leg_group_id` (symbol, direction, entry_price, quantity, pnl, exit_time, group_meta)."""
    legs = sorted(legs, key=lambda leg: leg.id)
    meta: dict[str, Any] = {}
    for leg in legs:
        meta = _meta(leg.group_meta)
        if meta:
            break
    closed = bool(legs) and all(leg.pnl is not None and leg.exit_time is not None for leg in legs)
    net = round(sum(float(leg.pnl) for leg in legs if leg.pnl is not None), 2) if closed else None
    credit = 0.0
    for leg in legs:
        value = float(leg.entry_price) * abs(float(leg.quantity))
        credit += value if leg.direction == "SHORT" else -value
    per_unit = _num(meta.get("max_loss"))
    unit_qty = _num(meta.get("quantity"))
    planned = round(abs(per_unit) * abs(unit_qty), 2) if per_unit is not None and unit_qty else None
    return {
        "group_id": legs[0].leg_group_id if legs else None, "structure": _words(legs[0].option_strategy) if legs else None,
        "underlying": (legs[0].underlying_symbol or None) if legs else None,
        "legs": len(legs), "closed": closed, "net_pnl": net, "credit": round(credit, 2),
        "credit_kind": "credit" if credit > 0 else "debit" if credit < 0 else None,
        "planned_max_loss": planned, "actual_loss": round(-net, 2) if net is not None and net < 0 else None,
        "charges": round(sum(float(leg.charges or 0.0) for leg in legs), 2),
    }


def fold_rows(rows: list[dict[str, Any]], summaries: dict[str, dict[str, Any]], outcome: str | None = None) -> list[dict[str, Any]]:
    """The list with every structure's legs folded into its first leg's row (order kept: the first leg's place). With an
    outcome filter, a structure is kept only when the STRUCTURE has that outcome (as the statistics count it), so a
    losing leg of a winning structure is not listed under losses."""
    out: list[dict[str, Any]] = []
    folded: dict[str, dict[str, Any]] = {}
    for r in rows:
        gid = r.get("leg_group_id")
        if r.get("kind") != "trade" or not gid or gid not in summaries:
            out.append(r)
            continue
        leg = {k: r.get(k) for k in ("id", "symbol", "direction", "entry_price", "exit_price", "quantity", "pnl")}
        head = folded.get(gid)
        if head is None:
            s = summaries[gid]
            head = dict(r)
            head.update(spread=s, legs=[leg], pnl=s["net_pnl"], outcome=_outcome(s["net_pnl"]), symbol=_title(r, s))
            folded[gid] = head
            out.append(head)
            continue
        head["legs"].append(leg)
        head["id"] = min(head["id"], r["id"])            # the entry sheet opens on the first leg
        head["tags"] = head["tags"] + [t for t in r.get("tags", []) if t not in head["tags"]]
        head["entry_time"] = min(x for x in (head.get("entry_time"), r.get("entry_time")) if x) if (head.get("entry_time") or r.get("entry_time")) else None
    for head in folded.values():
        head["legs"].sort(key=lambda leg: leg["id"])
    if outcome:
        out = [r for r in out if not r.get("spread") or r["outcome"] == outcome]
    return out


def _outcome(pnl: float | None) -> str:
    if pnl is None:
        return "open"
    return "win" if pnl > 0 else "loss" if pnl < 0 else "flat"


def _words(strategy: str | None) -> str | None:
    """`IRON_CONDOR` -> `Iron condor` (None for a plain single-leg name)."""
    text = (strategy or "").strip().replace("_", " ").lower()
    return text[:1].upper() + text[1:] if text and text != "single" else None


def _title(row: dict[str, Any], s: dict[str, Any]) -> str:
    """`NIFTY Iron condor` / `NIFTY spread`: the underlying (else the first leg's symbol) and the structure's name."""
    base = s.get("underlying") or str(row.get("symbol") or "")
    return f"{base} {s.get('structure') or 'spread'}"


__all__ = ["spread_summary", "fold_rows"]
