from datetime import date
from typing import List, Optional

from app.brokers.models import OptionChain, OptionChainRow
from app.core.config import RISK_FREE_RATE
from app.option_chain.greeks import BSInputs, black_scholes, implied_volatility, time_to_expiry_years
from app.option_chain.models import (
    GreeksResult,
    Moneyness,
    OIActivity,
    OptionChainAnalysis,
    OptionChainBias,
    OptionType,
    StrikeAnalysis,
)


def _moneyness(strike: float, underlying_ltp: Optional[float], atm_strike: Optional[float], side: str) -> Moneyness:
    """`atm_strike` is the strike nearest the real underlying price (computed once in
    analyze_option_chain) - comparing against it, not against the raw underlying_ltp directly,
    is what actually marks a strike ATM. A real (non-integer) underlying price almost never
    equals any strike exactly, so an `strike == underlying_ltp` check would never fire and no
    strike would ever come out ATM in this per-strike breakdown.
    """
    if underlying_ltp is None:
        return Moneyness.ATM
    if atm_strike is not None and strike == atm_strike:
        return Moneyness.ATM
    if side == "CALL":
        return Moneyness.ITM if strike < underlying_ltp else Moneyness.OTM
    return Moneyness.ITM if strike > underlying_ltp else Moneyness.OTM


def _activity(change_oi: Optional[float], side: str) -> OIActivity:
    if not change_oi:
        return OIActivity.FLAT
    if side == "CALL":
        return OIActivity.CALL_WRITING if change_oi > 0 else OIActivity.CALL_UNWINDING
    return OIActivity.PUT_WRITING if change_oi > 0 else OIActivity.PUT_UNWINDING


def _strike_greeks(
    strike: float, ltp: Optional[float], iv: Optional[float], option_type: OptionType,
    underlying_ltp: Optional[float], expiry_date: Optional[date], as_of: date, risk_free_rate: float,
) -> Optional[GreeksResult]:
    """Computed only from real inputs - a quoted `iv` if the broker supplied one, else solved
    from a real quoted `ltp` - never fabricated. Returns None (not a guessed value) whenever
    there isn't enough real data to compute from, or the quote is outside no-arbitrage bounds.
    """
    if underlying_ltp is None or expiry_date is None:
        return None

    t = time_to_expiry_years(expiry_date, as_of)
    sigma = iv
    if sigma is None:
        if ltp is None or ltp <= 0:
            return None
        sigma = implied_volatility(ltp, underlying_ltp, strike, t, risk_free_rate, option_type)
        if sigma is None:
            return None

    try:
        bs = black_scholes(BSInputs(underlying_ltp, strike, t, risk_free_rate, sigma, option_type))
    except ValueError:
        return None

    return GreeksResult(
        implied_volatility=sigma, theoretical_price=bs.theoretical_price,
        delta=bs.delta, gamma=bs.gamma, theta=bs.theta, vega=bs.vega,
    )


def compute_max_pain(rows: List[OptionChainRow]) -> Optional[float]:
    """The strike where option writers' aggregate payout to holders is smallest - the price
    the market is statistically drawn toward as options approach expiry.
    """
    if not rows:
        return None

    best_strike: Optional[float] = None
    best_payout: Optional[float] = None
    for candidate in rows:
        payout = 0.0
        for row in rows:
            call_oi = row.call_oi or 0.0
            put_oi = row.put_oi or 0.0
            payout += call_oi * max(0.0, candidate.strike - row.strike)
            payout += put_oi * max(0.0, row.strike - candidate.strike)
        if best_payout is None or payout < best_payout:
            best_payout = payout
            best_strike = candidate.strike
    return best_strike


def analyze_option_chain(
    chain: OptionChain, top_n: int = 3, risk_free_rate: float = RISK_FREE_RATE, as_of: Optional[date] = None,
) -> OptionChainAnalysis:
    """Turns a raw OptionChain (as fetched via BrokerInterface.get_option_chain) into the
    derived analytics the brief calls for: PCR, Max Pain, ATM/ITM/OTM, OI buildup/unwinding,
    call/put concentration zones, a bias that never relies on PCR alone - it only comes out
    BULLISH/BEARISH when both the PCR reading and the OI-change reading agree, CONFLICTING when
    they disagree, and NEUTRAL whenever either signal is inconclusive or unavailable - and, per
    strike, Black-Scholes Greeks (app/option_chain/greeks.py) computed from the chain's own real
    `expiry`/`underlying_ltp`/`call_iv`or`call_ltp`/`put_iv`or`put_ltp` fields whenever there's
    enough real data to compute from (None otherwise, never fabricated).
    """
    as_of = as_of or date.today()
    try:
        expiry_date = date.fromisoformat(chain.expiry) if chain.expiry else None
    except ValueError:
        expiry_date = None

    rows = chain.rows
    total_call_oi = sum(r.call_oi or 0.0 for r in rows)
    total_put_oi = sum(r.put_oi or 0.0 for r in rows)
    pcr = (total_put_oi / total_call_oi) if total_call_oi else None

    has_call_changes = any(r.call_change_oi is not None for r in rows)
    has_put_changes = any(r.put_change_oi is not None for r in rows)
    total_call_oi_change = sum(r.call_change_oi or 0.0 for r in rows) if has_call_changes else None
    total_put_oi_change = sum(r.put_change_oi or 0.0 for r in rows) if has_put_changes else None

    atm_strike = None
    if chain.underlying_ltp is not None and rows:
        atm_strike = min((r.strike for r in rows), key=lambda s: abs(s - chain.underlying_ltp))

    max_pain = compute_max_pain(rows)

    strikes = [
        StrikeAnalysis(
            strike=r.strike,
            call_oi=r.call_oi, call_change_oi=r.call_change_oi,
            call_activity=_activity(r.call_change_oi, "CALL"),
            call_moneyness=_moneyness(r.strike, chain.underlying_ltp, atm_strike, "CALL"),
            call_greeks=_strike_greeks(
                r.strike, r.call_ltp, r.call_iv, OptionType.CALL,
                chain.underlying_ltp, expiry_date, as_of, risk_free_rate,
            ),
            put_oi=r.put_oi, put_change_oi=r.put_change_oi,
            put_activity=_activity(r.put_change_oi, "PUT"),
            put_moneyness=_moneyness(r.strike, chain.underlying_ltp, atm_strike, "PUT"),
            put_greeks=_strike_greeks(
                r.strike, r.put_ltp, r.put_iv, OptionType.PUT,
                chain.underlying_ltp, expiry_date, as_of, risk_free_rate,
            ),
        )
        for r in rows
    ]

    call_resistance_strikes = [r.strike for r in sorted(rows, key=lambda r: -(r.call_oi or 0.0))[:top_n]]
    put_support_strikes = [r.strike for r in sorted(rows, key=lambda r: -(r.put_oi or 0.0))[:top_n]]

    reasons: List[str] = []
    pcr_signal = None
    if pcr is not None:
        if pcr > 1.2:
            pcr_signal = "BULLISH"
            reasons.append(f"PCR {pcr:.2f} > 1.2 (put writing dominance)")
        elif pcr < 0.8:
            pcr_signal = "BEARISH"
            reasons.append(f"PCR {pcr:.2f} < 0.8 (call writing dominance)")
        else:
            pcr_signal = "NEUTRAL"
            reasons.append(f"PCR {pcr:.2f} in the neutral 0.8-1.2 band")

    oi_signal = None
    if total_call_oi_change is not None and total_put_oi_change is not None:
        if total_put_oi_change > 0 and total_call_oi_change <= 0:
            oi_signal = "BULLISH"
            reasons.append("Put OI building while Call OI is flat/unwinding")
        elif total_call_oi_change > 0 and total_put_oi_change <= 0:
            oi_signal = "BEARISH"
            reasons.append("Call OI building while Put OI is flat/unwinding")
        else:
            oi_signal = "NEUTRAL"
            reasons.append("No one-sided OI buildup between calls and puts")
    else:
        reasons.append("OI-change data unavailable - bias cannot be confirmed from PCR alone")

    if pcr_signal is None or oi_signal is None:
        bias = OptionChainBias.NEUTRAL
    elif pcr_signal == oi_signal and pcr_signal in ("BULLISH", "BEARISH"):
        bias = OptionChainBias(pcr_signal)
    elif {pcr_signal, oi_signal} == {"BULLISH", "BEARISH"}:
        bias = OptionChainBias.CONFLICTING
        reasons.append("PCR and OI-change signals disagree")
    else:
        bias = OptionChainBias.NEUTRAL

    return OptionChainAnalysis(
        underlying=chain.underlying, expiry=chain.expiry, underlying_ltp=chain.underlying_ltp,
        atm_strike=atm_strike, max_pain=max_pain, pcr=pcr,
        total_call_oi=total_call_oi, total_put_oi=total_put_oi,
        total_call_oi_change=total_call_oi_change, total_put_oi_change=total_put_oi_change,
        bias=bias, bias_reasons=reasons,
        call_resistance_strikes=call_resistance_strikes, put_support_strikes=put_support_strikes,
        strikes=strikes,
    )
