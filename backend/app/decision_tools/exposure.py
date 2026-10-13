"""N2-4 (P1 spec 1.5 "correlation / exposure view across positions"): the caller's own open positions by underlying
and sector, each underlying's beta to the NIFTY 50, and how the underlyings' daily returns move together - from the
shared daily lake. Information only: nothing here ranks, sizes, places or closes anything.

Per underlying (the what-if's grouping: cash, futures and options on it together):
- the legs and the modes;
- the entry notional of cash and futures (|quantity| × entry price, with the net direction);
- the net option premium (bought − sold, at entry);
- the risk at the stops: Σ |entry − stop| × quantity over the legs that carry a stop, plus the legs without one listed.

The by-sector totals sum those figures. Correlations are Pearson's r of the aligned daily log returns over the
look-back, needing at least `MIN_POINTS` common days. A pair at or above `MOVE_TOGETHER` (|r|) is listed as moving
together; the threshold is shown with it.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.db.models import TradeRecord
from app.decision_tools import stats
from app.decision_tools.whatif import underlying_of

MIN_POINTS = 20
MOVE_TOGETHER = 0.7
MAX_COMPUTED = 40          # review (DoS): beta and pairs for at most this many underlyings, largest risk first


def computed_names(rows: Mapping[str, Mapping[str, Any]], limit: Optional[int] = None) -> List[str]:
    """The underlyings that get market data, beta and correlations: the `limit` with the largest risk at the stops (then
    money at stake - notional plus the absolute option premium, so an options-only underlying with no stop is not ranked
    last - then name). The pair work grows with the square of this number, so it is capped."""
    ranked = sorted(rows.values(), key=lambda r: (-float(r["risk_at_stop"]), -(float(r["notional"]) + abs(float(r["option_premium"]))),
                                                  r["underlying"]))
    return [r["underlying"] for r in ranked[:MAX_COMPUTED if limit is None else limit]]


def book(trades: Sequence[TradeRecord]) -> Dict[str, Dict[str, Any]]:
    """{underlying: figures} from the trades alone (no market data)."""
    out: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        name = underlying_of(t)
        row = out.setdefault(name, {"underlying": name, "legs": 0, "modes": set(), "notional": 0.0, "net_quantity": 0.0,
                                    "option_premium": 0.0, "risk_at_stop": 0.0, "legs_without_stop": []})
        qty = abs(float(t.quantity))
        sign = 1.0 if t.direction == "LONG" else -1.0
        row["legs"] += 1
        row["modes"].add(t.mode)
        if t.instrument_kind == "OPTION":
            row["option_premium"] += sign * qty * float(t.entry_price)
        else:
            row["notional"] += qty * float(t.entry_price)
            row["net_quantity"] += sign * qty
        if t.stop_loss is not None and float(t.stop_loss) > 0:
            row["risk_at_stop"] += abs(float(t.entry_price) - float(t.stop_loss)) * qty
        else:
            row["legs_without_stop"].append(t.symbol)
    for row in out.values():
        row["modes"] = sorted(row["modes"])
        for k in ("notional", "net_quantity", "option_premium", "risk_at_stop"):
            row[k] = round(row[k], 2)
    return out


def correlations(returns: Mapping[str, Mapping[Any, float]], *, min_points: int = MIN_POINTS) -> List[Dict[str, Any]]:
    """Every pair of underlyings with enough common days: r and the number of days."""
    out = []
    for a, b in combinations(sorted(returns), 2):
        xa, xb = stats.aligned(returns[a], returns[b])
        r = stats.correlation(xa, xb) if len(xa) >= min_points else None
        out.append({"a": a, "b": b, "r": None if r is None else round(r, 3), "days": len(xa)})
    return out


def assemble(rows: Dict[str, Dict[str, Any]], sectors: Mapping[str, str], returns: Mapping[str, Mapping[Any, float]],
             market: Optional[Mapping[Any, float]], *, days: int) -> Dict[str, Any]:
    """The exposure view from the book, the sectors and the daily returns (market = the NIFTY 50's). Only the names in
    `returns` (at most MAX_COMPUTED, `computed_names`) get beta and pairs; the rest are listed as not computed."""
    if len(returns) > MAX_COMPUTED:
        raise ValueError(f"at most {MAX_COMPUTED} underlyings get beta and correlations")
    underlyings = []
    for name in sorted(rows):
        row = dict(rows[name])
        row["sector"] = sectors.get(name, "Unclassified")
        mine = returns.get(name) or {}
        b = None
        if market:
            xa, xm = stats.aligned(mine, market)
            b = stats.beta(xa, xm) if len(xa) >= MIN_POINTS else None
        row["beta"] = None if b is None else round(b, 3)
        row["return_days"] = len(mine)
        row["computed"] = name in returns
        underlyings.append(row)
    by_sector: Dict[str, Dict[str, Any]] = {}
    for row in underlyings:
        s = by_sector.setdefault(row["sector"], {"sector": row["sector"], "underlyings": 0, "notional": 0.0, "option_premium": 0.0,
                                                 "risk_at_stop": 0.0})
        s["underlyings"] += 1
        for k in ("notional", "option_premium", "risk_at_stop"):
            s[k] = round(s[k] + row[k], 2)
    pairs = correlations({k: v for k, v in returns.items() if k in rows})
    total_risk = round(sum(r["risk_at_stop"] for r in underlyings), 2)
    return {
        "underlyings": underlyings, "sectors": sorted(by_sector.values(), key=lambda s: (-s["risk_at_stop"], s["sector"])),
        "pairs": pairs, "move_together": [p for p in pairs if p["r"] is not None and abs(p["r"]) >= MOVE_TOGETHER],
        "move_together_threshold": MOVE_TOGETHER, "min_points": MIN_POINTS, "lookback_days": days,
        "not_computed": sorted(n for n in rows if n not in returns), "max_computed": MAX_COMPUTED,
        "totals": {"underlyings": len(underlyings), "legs": sum(r["legs"] for r in underlyings), "risk_at_stop": total_risk,
                   "notional": round(sum(r["notional"] for r in underlyings), 2)},
        "market": "NIFTY 50", "market_days": len(market or {}),
        "note": "From your own open positions at their entry prices and the exchange's daily closes. Beta and correlation "
                "describe past daily moves, not future ones.",
    }
