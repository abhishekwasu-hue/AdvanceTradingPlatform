"""P1-b: the platform's option chain (`BrokerInterface.get_option_chain` -> `OptionChain`, any broker) as the raw chain
the selectors read (`selectors.py`: `[{"strike_price", "expiry", "call_options": {...}, "put_options": {...}}]`).

- `instrument_key` is a builder key, `<underlying>|<expiry>|<strike>|CE` - it names the contract for the builder and is
  resolved to the broker's instrument by the instrument master when an order is prepared; it is never sent to a broker.
- `market_data.ltp` is the row's LTP (missing stays missing; the selectors skip it).
- `option_greeks.pop` (OB-1) is the model's probability that a SELLER of that option profits at expiry - the shape
  the selectors expect from Upstox - under a lognormal price at the option's own volatility: its quoted IV when the
  broker sends one in (0, 5), else solved from its LTP; None when neither gives a usable volatility, never invented.
  `pop_source` says it came from the model.
- `option_greeks.iv` / `delta` carry the volatility used and the broker's delta (if any).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app.brokers.models import OptionChain
from app.core.config import RISK_FREE_RATE
from app.option_chain.greeks import implied_volatility, time_to_expiry_years
from app.option_chain.models import OptionType
from app.options_builder.model import _lognormal_cdf

POP_SOURCE = "model: lognormal, the option's own IV (an estimate)"


def builder_key(underlying: str, expiry: str, strike: float, right: str) -> str:
    return f"{underlying}|{expiry}|{strike:g}|{right}"


def _sigma(iv: Optional[float], ltp: Optional[float], spot: float, k: float, t: float, r: float, ot: OptionType) -> Optional[float]:
    if iv is not None and 0 < iv < 5:
        return float(iv)
    if ltp is None or ltp <= 0:
        return None
    return implied_volatility(ltp, spot, k, t, r, ot)


def seller_pop(right: str, strike: float, premium: float, spot: float, sigma: float, t: float, r: float = RISK_FREE_RATE) -> float:
    """P(a short option makes money at expiry): a short call while the price ends below strike + premium, a short put
    while it ends above strike - premium."""
    if right == "CE":
        return _lognormal_cdf(strike + premium, spot, sigma, t, r)
    return 1.0 - _lognormal_cdf(strike - premium, spot, sigma, t, r)


def raw_chain(chain: OptionChain, *, as_of: date, r: float = RISK_FREE_RATE) -> List[Dict[str, Any]]:
    """The selectors' raw chain from a broker chain. Needs the chain's underlying price and expiry for PoP; without
    them every PoP is None (the PoP selectors then find nothing, the fixed-strike ones still work)."""
    spot = chain.underlying_ltp
    try:
        expiry: Optional[date] = date.fromisoformat(chain.expiry[:10]) if chain.expiry else None
    except ValueError:
        expiry = None
    t = time_to_expiry_years(expiry, as_of) if expiry and expiry > as_of else 0.0
    out: List[Dict[str, Any]] = []
    for row in chain.rows:
        item: Dict[str, Any] = {"strike_price": row.strike, "expiry": chain.expiry}
        for right, side, ltp, iv, delta, ot in (("CE", "call_options", row.call_ltp, row.call_iv, row.call_delta, OptionType.CALL),
                                               ("PE", "put_options", row.put_ltp, row.put_iv, row.put_delta, OptionType.PUT)):
            sigma = _sigma(iv, ltp, spot, row.strike, t, r, ot) if spot and t > 0 else None
            pop = seller_pop(right, row.strike, ltp, spot, sigma, t, r) if sigma and spot and ltp is not None and ltp > 0 else None
            item[side] = {
                "instrument_key": builder_key(chain.underlying, chain.expiry, row.strike, right),
                "market_data": {"ltp": ltp},
                "option_greeks": {"pop": pop, "iv": sigma, "delta": delta, "pop_source": POP_SOURCE if pop is not None else None},
            }
        out.append(item)
    return out
