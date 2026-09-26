"""Black-Scholes Greeks (Delta/Gamma/Theta/Vega), per-leg and aggregated per-strategy.

Pure-Python (no scipy dependency) European-option Black-Scholes model - the standard
industry approximation used for Greeks estimation on index/equity options regardless of
American/European exercise style. Never fabricates an implied volatility: when a caller
doesn't supply one directly, it's solved from a real quoted option price via bisection
(monotonic in sigma, so bisection is both simple and guaranteed to converge within bounds) -
if the supplied price violates a no-arbitrage bound (below intrinsic value, or above the
theoretical maximum), no IV/Greeks are returned rather than inventing a number.
"""

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.option_chain.models import OptionType

_SQRT_2PI = math.sqrt(2 * math.pi)

#  Approximate risk-free rate used for discounting (see app.core.config.RISK_FREE_RATE for the
# configurable, documented default) - passed in explicitly here so this module has no import-time
# dependency on app.core.config and stays trivially unit-testable with any rate.
_MIN_TIME_TO_EXPIRY_YEARS = 1.0 / 365.0 / 24.0  # ~1 hour floor, avoids division by zero at/after expiry


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def time_to_expiry_years(expiry: date, as_of: date) -> float:
    days = (expiry - as_of).days
    return max(days / 365.0, _MIN_TIME_TO_EXPIRY_YEARS)


@dataclass
class BSInputs:
    underlying_price: float
    strike: float
    time_to_expiry_years: float
    risk_free_rate: float
    volatility: float
    option_type: OptionType


@dataclass
class BSResult:
    theoretical_price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 percentage point (0.01) change in volatility


def black_scholes(inputs: BSInputs) -> BSResult:
    s, k, t, r, sigma = (
        inputs.underlying_price, inputs.strike, inputs.time_to_expiry_years,
        inputs.risk_free_rate, inputs.volatility,
    )
    if sigma <= 0 or s <= 0 or k <= 0:
        raise ValueError("underlying_price, strike, and volatility must all be positive")

    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    discount = math.exp(-r * t)
    pdf_d1 = _norm_pdf(d1)

    if inputs.option_type == OptionType.CALL:
        price = s * _norm_cdf(d1) - k * discount * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta_annual = -(s * pdf_d1 * sigma) / (2 * sqrt_t) - r * k * discount * _norm_cdf(d2)
    else:
        price = k * discount * _norm_cdf(-d2) - s * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta_annual = -(s * pdf_d1 * sigma) / (2 * sqrt_t) + r * k * discount * _norm_cdf(-d2)

    gamma = pdf_d1 / (s * sigma * sqrt_t)
    vega = s * pdf_d1 * sqrt_t / 100.0  # per 1 percentage point of IV, the conventional quoting unit

    return BSResult(
        theoretical_price=price, delta=delta, gamma=gamma, theta=theta_annual / 365.0, vega=vega,
    )


def _intrinsic_value(s: float, k: float, option_type: OptionType) -> float:
    return max(s - k, 0.0) if option_type == OptionType.CALL else max(k - s, 0.0)


def implied_volatility(
    market_price: float, underlying_price: float, strike: float, time_to_expiry_years: float,
    risk_free_rate: float, option_type: OptionType,
    low: float = 0.001, high: float = 5.0, tolerance: float = 1e-6, max_iterations: int = 100,
) -> Optional[float]:
    """Solves for the volatility that reproduces `market_price` under Black-Scholes, via
    bisection (price is monotonically increasing in sigma, so this always converges within
    [low, high] if a solution exists there). Returns None - never a guessed number - when
    `market_price` is below intrinsic value or above the theoretical maximum, since that means
    the quote itself is stale/crossed/arbitrage-violating rather than solvable.
    """
    intrinsic = _intrinsic_value(underlying_price, strike, option_type)
    if market_price < intrinsic - tolerance:
        return None
    upper_bound = underlying_price if option_type == OptionType.CALL else strike
    if market_price > upper_bound + tolerance:
        return None

    def price_at(sigma: float) -> float:
        return black_scholes(BSInputs(underlying_price, strike, time_to_expiry_years, risk_free_rate, sigma, option_type)).theoretical_price

    lo, hi = low, high
    price_lo, price_hi = price_at(lo), price_at(hi)
    if not (price_lo - tolerance <= market_price <= price_hi + tolerance):
        return None

    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        price_mid = price_at(mid)
        if abs(price_mid - market_price) < tolerance:
            return mid
        if price_mid < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
