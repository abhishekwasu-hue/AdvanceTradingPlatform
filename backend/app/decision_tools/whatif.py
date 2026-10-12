"""N2-3 (P1 spec 1.5, docs/design/N2_P1FGH.md): the what-if for a position - P&L against the underlying's price now
(T+0), on a chosen later day with an IV shift, and at the nearest expiry. Uses the builder's model
(`options_builder.model`, Black-Scholes per leg).

A subject is everything one user has open on one underlying (cash, futures and options together), grouped from their
own open trades. Each trade becomes a builder leg:
- direction: BUY for LONG, SELL for SHORT;
- lots: the quantity, with lot_size 1;
- premium: the entry price;
- option type: CE / PE read from the trading symbol; FUT for a future (to its expiry) and for a cash position (no
  expiry, so it is linear).

The spot and the IV are the user's inputs: a typed IV for every option leg, or one per trade. The page may prefill
them. Nothing is quoted or invented here. A leg that cannot be read (no strike or expiry) is listed in `skipped` and
left out. Reads only; nothing here changes, places or closes an order.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.db.models import TradeRecord
from app.option_chain.greeks import IST
from app.options_builder import model

POINTS = 121
_SPACED = re.compile(r"\s(\d{2,6}(?:\.\d+)?)\s+(?:CE|PE)\b")
_COMPACT = re.compile(r"[A-Z](\d{2,6}(?:\.\d+)?)(?:CE|PE)$")    # the digits right after a letter, e.g. ...OCT24500CE


class WhatIfError(ValueError):
    pass


def strike_and_right(symbol: str) -> Tuple[Optional[float], Optional[str]]:
    """Read from the symbol alone: 'NIFTY 24500 CE 01 OCT 26' and 'NIFTY24OCT24500CE' -> (24500.0, "CE"). A compact
    weekly symbol ('NIFTY26O0124500PE': year, month letter, day, then the strike) runs the day into the strike, so its
    strike is None here and comes from the instrument master (`masters` in `subjects`)."""
    upper = symbol.upper()
    compact = upper.replace(" ", "")
    tail = re.search(r"\d\s*(CE|PE)\b", upper) or re.search(r"\d(CE|PE)$", compact)     # 'RELIANCE' ends in CE, but no digit before it
    right = tail.group(1) if tail else None
    m = _SPACED.search(upper)
    if m:
        return float(m.group(1)), right
    m = _COMPACT.search(compact)
    return (float(m.group(1)) if m else None), right


def underlying_of(trade: TradeRecord) -> str:
    return (trade.underlying_symbol or trade.symbol) if trade.instrument_kind in ("OPTION", "FUTURE") else trade.symbol


def to_leg(trade: TradeRecord, master: Optional[Tuple[float, str]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(builder leg, None) or (None, why it is left out). `master`: (strike, right) from the instrument master, when known."""
    base = {"trade_id": trade.id, "direction": "BUY" if trade.direction == "LONG" else "SELL", "lots": float(trade.quantity),
            "lot_size": 1.0, "premium": float(trade.entry_price), "symbol": trade.symbol}
    if trade.instrument_kind == "OPTION":
        strike, right = master if master is not None else strike_and_right(trade.symbol)
        if strike is None or right is None:
            return None, f"cannot read the strike and right from {trade.symbol}"
        if trade.expiry is None:
            return None, f"{trade.symbol} has no expiry on the trade"
        return {**base, "option_type": right, "strike": strike, "expiry": trade.expiry.isoformat()}, None
    if trade.instrument_kind == "FUTURE":
        leg = {**base, "option_type": "FUT", "strike": 0.0}
        if trade.expiry is not None:
            leg["expiry"] = trade.expiry.isoformat()
        return leg, None
    return {**base, "option_type": "FUT", "strike": 0.0}, None       # cash: linear in the price, no expiry


def subjects(trades: Sequence[TradeRecord], masters: Optional[Mapping[str, Tuple[float, str]]] = None) -> List[Dict[str, Any]]:
    """The user's open positions grouped by underlying, each with its legs (and what was left out). `masters` maps an
    option's trading symbol to its (strike, right) from the instrument master."""
    groups: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        key = underlying_of(t)
        g = groups.setdefault(key, {"underlying": key, "legs": [], "skipped": [], "modes": set()})
        g["modes"].add(t.mode)
        leg, why = to_leg(t, (masters or {}).get(t.symbol.upper()))
        if leg is None:
            g["skipped"].append({"trade_id": t.id, "symbol": t.symbol, "reason": why})
        else:
            g["legs"].append(leg)
    out = []
    for key in sorted(groups):
        g = groups[key]
        expiries = sorted({leg["expiry"] for leg in g["legs"] if leg.get("expiry") and leg["option_type"] != "FUT"})
        out.append({"underlying": key, "legs": g["legs"], "skipped": g["skipped"], "modes": sorted(g["modes"]),
                    "has_options": any(leg["option_type"] in ("CE", "PE") for leg in g["legs"]), "expiries": expiries})
    return out


def _expiry_close(expiry_iso: str) -> datetime:
    return datetime.combine(datetime.fromisoformat(expiry_iso).date(), time(15, 30), tzinfo=IST)


def what_if(legs: Sequence[Mapping[str, Any]], *, spot: float, iv: Optional[float] = None, ivs: Optional[Mapping[int, float]] = None,
            days_forward: float = 0.0, iv_shift: float = 0.0, range_pct: float = 10.0, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Curves over spot ± range_pct %: `now` (T+0), `scenario` (days_forward later, IVs + iv_shift) and `expiry` (at the
    nearest option expiry's close, later legs by Black-Scholes). Plus the P&L at the spot on each, and the net Greeks
    at the scenario."""
    if not legs:
        raise WhatIfError("No position to model")
    if not (math.isfinite(spot) and 0 < spot <= 1e7):
        raise WhatIfError("Spot must be above 0 and at most 10,000,000")
    if not (math.isfinite(days_forward) and math.isfinite(iv_shift) and math.isfinite(range_pct)):
        raise WhatIfError("Days forward, IV shift and range must be finite numbers")
    if not 0.5 <= range_pct <= 50.0:
        raise WhatIfError("Range must be between 0.5 and 50 % of spot")
    if not -0.5 <= iv_shift <= 1.0:
        raise WhatIfError("IV shift must be between -0.50 and +1.00 (absolute)")
    now = now or datetime.now(timezone.utc)
    priced: List[Dict[str, Any]] = []
    for leg in legs:
        leg = dict(leg)
        if leg["option_type"] in ("CE", "PE"):
            value = (ivs or {}).get(int(leg["trade_id"]), iv)
            if value is None or not math.isfinite(float(value)) or not 0.01 <= float(value) <= 3.0:
                raise WhatIfError(f"{leg['symbol']}: an IV between 0.01 and 3.00 is needed (annualised, 0.14 = 14 %)")
            leg["iv"] = float(value)
        priced.append(leg)
    option_expiries = sorted({leg["expiry"] for leg in priced if leg["option_type"] in ("CE", "PE")})
    horizon = _expiry_close(option_expiries[0]) if option_expiries else None
    max_days = max(0.0, (horizon - now).total_seconds() / 86400.0) if horizon else 365.0
    if not 0.0 <= days_forward <= max_days + 1e-9:
        raise WhatIfError(f"Days forward must be between 0 and {max_days:.1f} (the nearest expiry)")
    lo, hi = spot * (1 - range_pct / 100.0), spot * (1 + range_pct / 100.0)
    prices = [round(lo + (hi - lo) * i / (POINTS - 1), 2) for i in range(POINTS)]
    scenario_at = now + timedelta(days=float(days_forward))

    def curve(on: datetime, shift: float) -> List[float]:
        return [round(v, 2) for v in model.pnl_on(priced, prices, on, iv_shift=shift)]
    curves = {"now": curve(now, 0.0), "scenario": curve(scenario_at, iv_shift)}
    if horizon is not None:
        curves["expiry"] = curve(horizon, 0.0)
    at_spot = {name: round(model.pnl_on(priced, [spot], on, iv_shift=shift)[0], 2)
               for name, on, shift in (("now", now, 0.0), ("scenario", scenario_at, iv_shift), ("expiry", horizon, 0.0)) if on is not None}
    greeks = {k: round(v, 4) for k, v in model.net_greeks(priced, spot, scenario_at, iv_shift=iv_shift).items()}
    return {"prices": prices, "curves": curves, "at_spot": at_spot, "greeks": greeks,
            "spot": spot, "days_forward": float(days_forward), "iv_shift": iv_shift, "max_days_forward": round(max_days, 2),
            "expiry": option_expiries[0] if option_expiries else None, "scenario_at": scenario_at.isoformat(), "as_of": now.isoformat(),
            "legs": [{k: leg.get(k) for k in ("trade_id", "symbol", "direction", "option_type", "strike", "expiry", "lots", "premium", "iv")}
                     for leg in priced],
            "note": "Black-Scholes estimates at the spot and IVs you entered; P&L against each trade's entry price, before charges. "
                    "A model, not a forecast."}
