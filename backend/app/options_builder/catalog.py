"""P1-b: the template gallery - 38 named structures in five families (Bullish, Bearish, Neutral, Volatility, and Stock
for structures with an underlying leg). The 13 Trade templates (`templates.build_ready_made_strategy`) stay as they
are; this catalog is the builder's wider set and uses the same leg shape.

A template leg is (direction, kind, offset, lots, expiry): kind CE / PE / FUT, the strike `offset` in wing widths from
ATM (+1 = one width above), a lot multiple (ratio spreads use 2), and the expiry slot (0 = the near expiry, 1 = the
next one - calendars and diagonals). Legs come out hedge first (every BUY before every SELL).

Notes for the caller:
- A calendar or diagonal has a far leg that is still alive at the near expiry: draw it with `model.value_curve` on the
  near expiry, not with the expiry payoff (which assumes every leg expires together).
- Ratio structures have unequal lots; `build_strategy_result_from_legs` (execution with one lot count) refuses them -
  they are modelled and shown, and placed as a basket with per-leg quantities (OB-3).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.options_builder.templates import hedge_first

TLeg = Tuple[str, str, float, int, int]          # direction, kind, offset in widths, lots, expiry slot

CATALOG: Dict[str, Dict[str, Any]] = {}


def _t(name: str, family: str, legs: List[TLeg], what: str) -> None:
    CATALOG[name] = {"family": family, "legs": legs, "what": what}


# Bullish
_t("Buy Call", "Bullish", [("BUY", "CE", 0, 1, 0)], "Long call at the money")
_t("Sell Put", "Bullish", [("SELL", "PE", 0, 1, 0)], "Short put at the money (undefined risk below)")
_t("Bull Call Spread", "Bullish", [("BUY", "CE", 0, 1, 0), ("SELL", "CE", 1, 1, 0)], "Debit call spread")
_t("Bull Put Spread", "Bullish", [("BUY", "PE", -1, 1, 0), ("SELL", "PE", 0, 1, 0)], "Credit put spread")
_t("Call Ratio Back Spread", "Bullish", [("BUY", "CE", 1, 2, 0), ("SELL", "CE", 0, 1, 0)], "Sell 1 ATM call, buy 2 OTM calls")
_t("Long Synthetic Future", "Bullish", [("BUY", "CE", 0, 1, 0), ("SELL", "PE", 0, 1, 0)], "Long call + short put at the same strike")
_t("Bullish Risk Reversal", "Bullish", [("BUY", "CE", 1, 1, 0), ("SELL", "PE", -1, 1, 0)], "Buy an OTM call, sell an OTM put")
_t("Bull Call Ladder", "Bullish", [("BUY", "CE", 0, 1, 0), ("SELL", "CE", 1, 1, 0), ("SELL", "CE", 2, 1, 0)], "Bull call spread with one more call sold above")
# Bearish
_t("Buy Put", "Bearish", [("BUY", "PE", 0, 1, 0)], "Long put at the money")
_t("Sell Call", "Bearish", [("SELL", "CE", 0, 1, 0)], "Short call at the money (undefined risk above)")
_t("Bear Put Spread", "Bearish", [("BUY", "PE", 0, 1, 0), ("SELL", "PE", -1, 1, 0)], "Debit put spread")
_t("Bear Call Spread", "Bearish", [("BUY", "CE", 1, 1, 0), ("SELL", "CE", 0, 1, 0)], "Credit call spread")
_t("Put Ratio Back Spread", "Bearish", [("BUY", "PE", -1, 2, 0), ("SELL", "PE", 0, 1, 0)], "Sell 1 ATM put, buy 2 OTM puts")
_t("Short Synthetic Future", "Bearish", [("BUY", "PE", 0, 1, 0), ("SELL", "CE", 0, 1, 0)], "Long put + short call at the same strike")
_t("Bearish Risk Reversal", "Bearish", [("BUY", "PE", -1, 1, 0), ("SELL", "CE", 1, 1, 0)], "Buy an OTM put, sell an OTM call")
_t("Bear Put Ladder", "Bearish", [("BUY", "PE", 0, 1, 0), ("SELL", "PE", -1, 1, 0), ("SELL", "PE", -2, 1, 0)], "Bear put spread with one more put sold below")
# Neutral
_t("Short Straddle", "Neutral", [("SELL", "CE", 0, 1, 0), ("SELL", "PE", 0, 1, 0)], "Sell call and put at the money (undefined risk)")
_t("Short Strangle", "Neutral", [("SELL", "CE", 1, 1, 0), ("SELL", "PE", -1, 1, 0)], "Sell OTM call and put (undefined risk)")
_t("Iron Condor", "Neutral", [("BUY", "CE", 2, 1, 0), ("BUY", "PE", -2, 1, 0), ("SELL", "CE", 1, 1, 0), ("SELL", "PE", -1, 1, 0)], "Short strangle with wings")
_t("Iron Butterfly", "Neutral", [("BUY", "CE", 1, 1, 0), ("BUY", "PE", -1, 1, 0), ("SELL", "CE", 0, 1, 0), ("SELL", "PE", 0, 1, 0)], "Short straddle with wings")
_t("Long Call Butterfly", "Neutral", [("BUY", "CE", -1, 1, 0), ("BUY", "CE", 1, 1, 0), ("SELL", "CE", 0, 2, 0)], "Buy wings, sell 2 at the money (calls)")
_t("Long Put Butterfly", "Neutral", [("BUY", "PE", -1, 1, 0), ("BUY", "PE", 1, 1, 0), ("SELL", "PE", 0, 2, 0)], "Buy wings, sell 2 at the money (puts)")
_t("Call Ratio Spread", "Neutral", [("BUY", "CE", 0, 1, 0), ("SELL", "CE", 1, 2, 0)], "Buy 1 ATM call, sell 2 OTM calls")
_t("Put Ratio Spread", "Neutral", [("BUY", "PE", 0, 1, 0), ("SELL", "PE", -1, 2, 0)], "Buy 1 ATM put, sell 2 OTM puts")
_t("Jade Lizard", "Neutral", [("BUY", "CE", 2, 1, 0), ("SELL", "CE", 1, 1, 0), ("SELL", "PE", -1, 1, 0)], "Short put + short call spread (no upside risk if the credit covers the spread)")
_t("Reverse Jade Lizard", "Neutral", [("BUY", "PE", -2, 1, 0), ("SELL", "PE", -1, 1, 0), ("SELL", "CE", 1, 1, 0)], "Short call + short put spread")
_t("Long Call Condor", "Neutral", [("BUY", "CE", -2, 1, 0), ("BUY", "CE", 2, 1, 0), ("SELL", "CE", -1, 1, 0), ("SELL", "CE", 1, 1, 0)], "Call condor: a butterfly with a flat top")
# Volatility
_t("Long Straddle", "Volatility", [("BUY", "CE", 0, 1, 0), ("BUY", "PE", 0, 1, 0)], "Buy call and put at the money")
_t("Long Strangle", "Volatility", [("BUY", "CE", 1, 1, 0), ("BUY", "PE", -1, 1, 0)], "Buy OTM call and put")
_t("Short Iron Condor", "Volatility", [("BUY", "CE", 1, 1, 0), ("BUY", "PE", -1, 1, 0), ("SELL", "CE", 2, 1, 0), ("SELL", "PE", -2, 1, 0)], "Long strangle with short wings (defined risk)")
_t("Short Iron Butterfly", "Volatility", [("BUY", "CE", 0, 1, 0), ("BUY", "PE", 0, 1, 0), ("SELL", "CE", 1, 1, 0), ("SELL", "PE", -1, 1, 0)], "Long straddle with short wings (defined risk)")
_t("Call Calendar", "Volatility", [("BUY", "CE", 0, 1, 1), ("SELL", "CE", 0, 1, 0)], "Sell the near-expiry call, buy the next-expiry call, same strike")
_t("Put Calendar", "Volatility", [("BUY", "PE", 0, 1, 1), ("SELL", "PE", 0, 1, 0)], "Sell the near-expiry put, buy the next-expiry put, same strike")
_t("Call Diagonal", "Volatility", [("BUY", "CE", 0, 1, 1), ("SELL", "CE", 1, 1, 0)], "Buy the next-expiry ATM call, sell a near-expiry OTM call")
_t("Put Diagonal", "Volatility", [("BUY", "PE", 0, 1, 1), ("SELL", "PE", -1, 1, 0)], "Buy the next-expiry ATM put, sell a near-expiry OTM put")
# Stock options (an underlying leg: FUT for an index or stock future, or shares held)
_t("Covered Call", "Stock", [("BUY", "FUT", 0, 1, 0), ("SELL", "CE", 1, 1, 0)], "Own the underlying, sell an OTM call")
_t("Protective Put", "Stock", [("BUY", "FUT", 0, 1, 0), ("BUY", "PE", -1, 1, 0)], "Own the underlying, buy an OTM put")
_t("Collar", "Stock", [("BUY", "FUT", 0, 1, 0), ("BUY", "PE", -1, 1, 0), ("SELL", "CE", 1, 1, 0)], "Own the underlying, buy a put, sell a call")

FAMILIES = ("Bullish", "Bearish", "Neutral", "Volatility", "Stock")


def families() -> Dict[str, List[str]]:
    return {f: [n for n, t in CATALOG.items() if t["family"] == f] for f in FAMILIES}


def build_template(name: str, atm_strike: float, width: float, near_expiry: str, next_expiry: Optional[str] = None,
                   lots: int = 1) -> Optional[List[Dict[str, Any]]]:
    """The legs of a catalog template (hedge first), without premiums. None for an unknown name, or for a calendar /
    diagonal without `next_expiry`. A FUT leg carries the ATM strike as its reference price until the caller fills the
    real futures price into `premium` (its entry)."""
    t = CATALOG.get(name)
    if t is None:
        return None
    legs: List[Dict[str, Any]] = []
    for direction, kind, offset, mult, slot in t["legs"]:
        if slot == 1 and not next_expiry:
            return None
        legs.append({"direction": direction, "option_type": kind, "strike": float(atm_strike + offset * width),
                     "lots": lots * mult, "expiry": next_expiry if slot == 1 else near_expiry})
    return [dict(leg) for leg in hedge_first(legs)]
