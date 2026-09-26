from datetime import date
from typing import List

from app.core.config import RISK_FREE_RATE
from app.option_chain.greeks import BSInputs, black_scholes, implied_volatility, time_to_expiry_years
from app.option_chain.models import GreeksResult, LegGreeksResult, OptionLegInput, StrategyGreeksResult


def _resolve_iv(leg: OptionLegInput, t: float) -> float:
    if leg.implied_volatility is not None:
        return leg.implied_volatility
    if leg.option_ltp is not None:
        solved = implied_volatility(
            market_price=leg.option_ltp, underlying_price=leg.underlying_ltp, strike=leg.strike,
            time_to_expiry_years=t, risk_free_rate=RISK_FREE_RATE, option_type=leg.option_type,
        )
        if solved is None:
            raise ValueError(
                f"Cannot solve implied volatility for strike {leg.strike} {leg.option_type.value}: "
                f"option_ltp {leg.option_ltp} is outside the no-arbitrage price bounds for this input"
            )
        return solved
    raise ValueError("Either option_ltp or implied_volatility must be supplied for each leg")


def compute_leg_greeks(leg: OptionLegInput) -> LegGreeksResult:
    as_of = date.fromisoformat(leg.as_of) if leg.as_of else date.today()
    expiry = date.fromisoformat(leg.expiry)
    t = time_to_expiry_years(expiry, as_of)

    sigma = _resolve_iv(leg, t)
    bs = black_scholes(
        BSInputs(leg.underlying_ltp, leg.strike, t, RISK_FREE_RATE, sigma, leg.option_type)
    )
    greeks = GreeksResult(
        implied_volatility=sigma, theoretical_price=bs.theoretical_price,
        delta=bs.delta, gamma=bs.gamma, theta=bs.theta, vega=bs.vega,
    )
    return LegGreeksResult(
        strike=leg.strike, option_type=leg.option_type, quantity=leg.quantity, greeks=greeks,
        position_delta=greeks.delta * leg.quantity, position_gamma=greeks.gamma * leg.quantity,
        position_theta=greeks.theta * leg.quantity, position_vega=greeks.vega * leg.quantity,
    )


def compute_strategy_greeks(legs: List[OptionLegInput]) -> StrategyGreeksResult:
    """Sums position-level Greeks across every leg of a multi-leg strategy (spread, straddle,
    strangle, ...) - a short leg's negative-scaled Greeks net correctly against a long leg's."""
    leg_results = [compute_leg_greeks(leg) for leg in legs]
    return StrategyGreeksResult(
        legs=leg_results,
        net_delta=sum(r.position_delta for r in leg_results),
        net_gamma=sum(r.position_gamma for r in leg_results),
        net_theta=sum(r.position_theta for r in leg_results),
        net_vega=sum(r.position_vega for r in leg_results),
    )
