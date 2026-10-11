"""P1-b: the platform's option chain (`BrokerInterface.get_option_chain` -> `OptionChain`, any broker) as the raw chain
the selectors read (`selectors.py`: `[{"strike_price", "expiry", "call_options": {...}, "put_options": {...}}]`).

- `instrument_key` is a builder key, `<underlying>|<expiry>|<strike>|CE` - it names the contract for the builder and is
  resolved to the broker's instrument by the instrument master when an order is prepared; it is never sent to a broker.
- `market_data.ltp` is the row's LTP (missing stays missing; the selectors skip it).
- `option_greeks.pop` (OB-1) is the model's probability that a SELLER of that option profits at expiry - the shape
  the selectors expect from Upstox - under a lognormal price at the option's own volatility (`option_iv`); None when
  no usable volatility exists, never invented. `pop_source` says it came from the model.
- Time: `as_of` may be an aware datetime, so an expiry-day (0DTE) chain still has its hours of life; a plain date
  other than today is that day's close (on the expiry date: expired, no PoP).
- `option_greeks.iv` / `delta` carry the volatility used and the broker's delta (if any).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union

from app.brokers.models import OptionChain
from app.core.config import RISK_FREE_RATE
from app.option_chain.greeks import BSInputs, black_scholes, implied_volatility
from app.option_chain.models import OptionType
from app.options_builder.model import _lognormal_cdf, years_left

POP_SOURCE = "model: lognormal, the option's own IV (an estimate)"


REPRICE_TOLERANCE = 0.05     # a quoted IV is trusted when it reprices the LTP within 5 % (or 0.05 in price)
PERCENT_CUTOFF = 3.0         # without an LTP: below this a quote is a fraction (0.14), above it a percentage (14.2)


def builder_key(underlying: str, expiry: str, strike: float, right: str) -> str:
    return f"{underlying}|{expiry}|{strike:.10g}|{right}"


def option_iv(iv: Optional[float], ltp: Optional[float], spot: float, k: float, t: float, r: float, ot: OptionType) -> Optional[float]:
    """The option's volatility as a fraction. Brokers quote IV as a fraction (0.142) or a percentage (14.2, or 2.5 for
    a deep-ITM strike), so the quote is read both ways and kept only if one reading reprices the LTP; otherwise the IV
    is solved from the LTP. With no LTP, a quote below PERCENT_CUTOFF is a fraction (as strike_selection reads it)."""
    readings = [x for x in ((iv, iv / 100.0) if iv is not None and iv > 0 else ()) if 0 < x < 5]
    if ltp is None or ltp <= 0:
        if iv is None or iv <= 0:
            return None
        return float(iv) if iv < PERCENT_CUTOFF else float(iv) / 100.0
    for sigma in readings:
        price = black_scholes(BSInputs(spot, k, t, r, sigma, ot)).theoretical_price
        if abs(price - ltp) <= max(0.05, REPRICE_TOLERANCE * ltp):
            return float(sigma)
    return implied_volatility(ltp, spot, k, t, r, ot)


def seller_pop(right: str, strike: float, premium: float, spot: float, sigma: float, t: float, r: float = RISK_FREE_RATE) -> float:
    """P(a short option makes money at expiry): a short call while the price ends below strike + premium, a short put
    while it ends above strike - premium."""
    if right == "CE":
        return _lognormal_cdf(strike + premium, spot, sigma, t, r)
    return 1.0 - _lognormal_cdf(strike - premium, spot, sigma, t, r)


def raw_chain(chain: OptionChain, *, as_of: Union[date, datetime], r: float = RISK_FREE_RATE) -> List[Dict[str, Any]]:
    """The selectors' raw chain from a broker chain. Needs the chain's underlying price and expiry for PoP; without
    them every PoP is None (the PoP selectors then find nothing, the fixed-strike ones still work)."""
    spot = chain.underlying_ltp
    try:
        expiry: Optional[date] = date.fromisoformat(chain.expiry[:10]) if chain.expiry else None
    except ValueError:
        expiry = None
    t = (years_left(expiry, as_of) if expiry else None) or 0.0
    out: List[Dict[str, Any]] = []
    for row in chain.rows:
        item: Dict[str, Any] = {"strike_price": row.strike, "expiry": chain.expiry}
        for right, side, ltp, iv, delta, ot in (("CE", "call_options", row.call_ltp, row.call_iv, row.call_delta, OptionType.CALL),
                                               ("PE", "put_options", row.put_ltp, row.put_iv, row.put_delta, OptionType.PUT)):
            sigma = option_iv(iv, ltp, spot, row.strike, t, r, ot) if spot and t > 0 else None
            pop = seller_pop(right, row.strike, ltp, spot, sigma, t, r) if sigma and spot and ltp is not None and ltp > 0 else None
            item[side] = {
                "instrument_key": builder_key(chain.underlying, chain.expiry, row.strike, right),
                "market_data": {"ltp": ltp},
                "option_greeks": {"pop": pop, "iv": sigma, "delta": delta, "pop_source": POP_SOURCE if pop is not None else None},
            }
        out.append(item)
    return out
