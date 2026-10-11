"""Ready-made strategy structures (with the hedge-first margin rule) and the per-lot strategy result used for
execution. Ported from Trade `strategy_payoff.py`; same results, English errors.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

Template = List[Tuple[str, str, float]]

READY_MADE_CATEGORIES: Dict[str, List[str]] = {
    "Bullish": ["Buy Call", "Sell Put", "Bull Call Spread", "Bull Put Spread"],
    "Bearish": ["Buy Put", "Sell Call", "Bear Put Spread", "Bear Call Spread"],
    "Neutral": ["Short Straddle", "Short Strangle", "Iron Condor"],
    "Others": ["Long Straddle", "Long Strangle"],
}


def _templates(atm: float, w: float) -> Dict[str, Template]:
    # Margin rule (from the Trade repo): a BUY (hedge) leg always comes before the SELL leg it protects, so the broker
    # sees the hedge first when it computes SPAN / exposure margin.
    return {
        "Buy Call": [("BUY", "CE", atm)],
        "Sell Put": [("SELL", "PE", atm)],
        "Bull Call Spread": [("BUY", "CE", atm), ("SELL", "CE", atm + w)],
        "Bull Put Spread": [("BUY", "PE", atm - w), ("SELL", "PE", atm)],
        "Buy Put": [("BUY", "PE", atm)],
        "Sell Call": [("SELL", "CE", atm)],
        "Bear Put Spread": [("BUY", "PE", atm), ("SELL", "PE", atm - w)],
        "Bear Call Spread": [("BUY", "CE", atm + w), ("SELL", "CE", atm)],
        "Short Straddle": [("SELL", "CE", atm), ("SELL", "PE", atm)],
        "Long Straddle": [("BUY", "CE", atm), ("BUY", "PE", atm)],
        "Short Strangle": [("SELL", "CE", atm + w), ("SELL", "PE", atm - w)],
        "Long Strangle": [("BUY", "CE", atm + w), ("BUY", "PE", atm - w)],
        "Iron Condor": [
            ("BUY", "CE", atm + 2 * w), ("BUY", "PE", atm - 2 * w),
            ("SELL", "CE", atm + w), ("SELL", "PE", atm - w),
        ],
    }


def build_ready_made_strategy(strategy_name: str, atm_strike: float, hedge_width: float) -> Optional[List[Dict[str, Any]]]:
    """The leg structure (direction, option type, strike, 1 lot) of a named strategy around `atm_strike`, with wings
    `hedge_width` points away (the caller takes it from the underlying's strike step). Premiums are not filled here -
    they come from the live chain. None for an unknown name."""
    template = _templates(atm_strike, hedge_width).get(strategy_name)
    if template is None:
        return None
    return [{"direction": d, "option_type": ot, "strike": float(s), "lots": 1} for d, ot, s in template]


def hedge_first(legs: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """The legs in placement order: every BUY (hedge) before every SELL, each group in its own order (stable).
    Used when a strategy becomes a basket, so margin sees the protection before the short."""
    return [leg for leg in legs if leg["direction"] == "BUY"] + [leg for leg in legs if leg["direction"] != "BUY"]


def build_strategy_result_from_legs(legs: Sequence[Mapping[str, Any]], payoff_curve: Sequence[float]) -> Dict[str, Any]:
    """The execution-ready result for a built strategy: per-lot net credit, max profit and max loss from the full
    payoff curve (no spread formula is assumed), with max_loss POSITIVE (the amount at risk) - the convention every
    other selector and the trading engine's stop formula use.

    Execution places one `lots` for every leg, so all legs must have equal lots (ValueError otherwise); the curve is
    divided by that lot count, so the per-lot figures do not grow with the lots chosen."""
    lot_size = legs[0]["lot_size"]
    lots_set = {int(leg["lots"]) for leg in legs}
    if len(lots_set) != 1:
        raise ValueError("Every leg must have the same lots - a strategy with uneven lots cannot be executed")
    leg_lots = lots_set.pop()
    net_credit_per_lot = sum(
        (leg["premium"] if leg["direction"] == "SELL" else -leg["premium"]) * leg["lots"] for leg in legs
    ) / leg_lots
    max_profit_total, max_loss_total = max(payoff_curve), min(payoff_curve)
    max_profit_per_lot = max_profit_total / (lot_size * leg_lots)
    max_loss_per_lot = abs(max_loss_total) / (lot_size * leg_lots)
    result_legs = [
        {"role": f"LEG{i + 1}_{leg['direction']}_{leg['option_type']}", "strike": leg["strike"],
         "instrument_key": leg["instrument_key"], "transaction_type": leg["direction"]}
        for i, leg in enumerate(legs)
    ]
    return {
        "legs": result_legs, "net_credit": net_credit_per_lot, "max_profit": max_profit_per_lot,
        "max_loss": max_loss_per_lot, "strategy": "CUSTOM_MULTI_LEG", "is_credit_strategy": net_credit_per_lot > 0,
    }
