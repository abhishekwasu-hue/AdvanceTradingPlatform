"""Leg Greeks for the builder from the platform's own model (app/option_chain/greeks.py) instead of the broker's
numbers: per-unit delta / gamma / theta (per day) / vega (per IV point), so `compute_combined_greeks` (the Trade rule:
sign by direction, scale by lots x lot size) nets them. The IV is the leg's own, or solved from its premium; a premium
outside the no-arbitrage bounds gives no Greeks (ValueError), never an invented number.

IV is a DECIMAL (0.145 for 14.5 %), as in app/option_chain/greeks.py; brokers often quote percent, so a value outside
(0, 5) is refused rather than silently read as 1,450 %.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Mapping, Optional

from app.core.config import RISK_FREE_RATE
from app.option_chain.greeks import BSInputs, black_scholes, implied_volatility, time_to_expiry_years
from app.option_chain.models import OptionType


def leg_with_model_greeks(leg: Mapping[str, Any], *, underlying_price: float, expiry: date, as_of: date,
                          iv: Optional[float] = None, risk_free_rate: float = RISK_FREE_RATE) -> Dict[str, Any]:
    """The leg with its per-unit Greeks and the IV used added (the leg itself is not changed). The IV is `iv`, else
    the leg's own `iv`, else solved from its premium."""
    option_type = OptionType.CALL if leg["option_type"] == "CE" else OptionType.PUT
    t = time_to_expiry_years(expiry, as_of)
    sigma = iv if iv is not None else leg.get("iv")
    if sigma is not None and not 0 < float(sigma) < 5:
        raise ValueError(f"IV {sigma} is not a decimal volatility (0.145 means 14.5 %)")
    if sigma is None and leg.get("premium") is None:
        raise ValueError(f"No IV and no premium for {leg['option_type']} {leg['strike']} - one of them is needed")
    if sigma is None:
        sigma = implied_volatility(market_price=float(leg["premium"]), underlying_price=underlying_price, strike=float(leg["strike"]),
                                   time_to_expiry_years=t, risk_free_rate=risk_free_rate, option_type=option_type)
        if sigma is None:
            raise ValueError(f"No implied volatility for {leg['option_type']} {leg['strike']}: premium {leg['premium']} is outside "
                             "the no-arbitrage bounds")
    bs = black_scholes(BSInputs(underlying_price, float(leg["strike"]), t, risk_free_rate, float(sigma), option_type))
    return {**leg, "iv": sigma, "delta": bs.delta, "gamma": bs.gamma, "theta": bs.theta, "vega": bs.vega}
