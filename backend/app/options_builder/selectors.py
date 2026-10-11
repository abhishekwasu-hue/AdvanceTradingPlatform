"""Strike selection by rule (PoP target, fixed distance from ATM, ITM depth) and position sizing from a risk cap.

Ported from Trade `strategy.py`; same results. Input chain shape (the broker's raw option chain, as the Trade repo
reads it): `[{"strike_price": K, "expiry": ..., "call_options": {"instrument_key", "market_data": {"ltp"},
"option_greeks": {"pop"}}, "put_options": {...}}, ...]`. `pop` is the broker's probability of profit (0-1).

Instrument-specific numbers - the strike step and the hedge width - are required parameters (the caller reads them
from the instrument master); the Trade defaults were NIFTY's. Every selector returns None when the chain cannot give a
complete, positive-credit (or positive-debit) structure - never a partial one.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

Chain = Sequence[Mapping[str, Any]]
DIRECTIONS = ("BULLISH", "BEARISH")


def _pop_lookup(raw_chain: Chain, side: str, strike: float) -> Optional[Dict[str, Any]]:
    """LTP, PoP and instrument key of one strike on one side (call_options / put_options); None when absent."""
    for item in raw_chain:
        if item.get("strike_price") == strike:
            opt = item.get(side, {}) or {}
            greeks = opt.get("option_greeks", {}) or {}
            ltp = (opt.get("market_data", {}) or {}).get("ltp")
            return {"strike": strike, "instrument_key": opt.get("instrument_key"), "pop": greeks.get("pop"), "ltp": ltp}
    return None


def select_iron_condor(raw_chain: Chain, atm_strike: float, step: float, hedge_width_points: float, pop_threshold_pct: float,
                       max_widen_steps: int = 12) -> Optional[Dict[str, Any]]:
    """Short call and put move out from ATM together, one step at a time, until the combined PoP reaches the threshold;
    wings `hedge_width_points` beyond each short. Combined PoP ≈ short_call_pop + short_put_pop − 1 (an approximation).
    Legs are listed hedge first."""
    strikes_available = sorted({item["strike_price"] for item in raw_chain if item.get("strike_price") is not None})
    if not strikes_available:
        return None
    for n in range(1, max_widen_steps + 1):
        short_call_strike = atm_strike + n * step
        short_put_strike = atm_strike - n * step
        if short_call_strike not in strikes_available or short_put_strike not in strikes_available:
            continue
        short_call = _pop_lookup(raw_chain, "call_options", short_call_strike)
        short_put = _pop_lookup(raw_chain, "put_options", short_put_strike)
        if not short_call or not short_put or short_call["pop"] is None or short_put["pop"] is None:
            continue
        if short_call["ltp"] is None or short_put["ltp"] is None:
            continue
        combined_pop = max(0.0, short_call["pop"] + short_put["pop"] - 1) * 100
        if combined_pop < pop_threshold_pct:
            continue
        long_call_strike = short_call_strike + hedge_width_points
        long_put_strike = short_put_strike - hedge_width_points
        if long_call_strike not in strikes_available or long_put_strike not in strikes_available:
            continue
        long_call = _pop_lookup(raw_chain, "call_options", long_call_strike)
        long_put = _pop_lookup(raw_chain, "put_options", long_put_strike)
        if not long_call or not long_put or long_call["ltp"] is None or long_put["ltp"] is None:
            continue
        net_credit = (short_call["ltp"] - long_call["ltp"]) + (short_put["ltp"] - long_put["ltp"])
        call_side_width = long_call_strike - short_call_strike
        put_side_width = short_put_strike - long_put_strike
        max_loss = max(call_side_width, put_side_width) - net_credit
        if net_credit <= 0 or max_loss <= 0:
            continue
        return {
            "strategy": "IRON_CONDOR",
            "legs": [
                {"role": "long_call_wing", "strike": long_call_strike, "instrument_key": long_call["instrument_key"], "transaction_type": "BUY", "ltp": long_call["ltp"]},
                {"role": "long_put_wing", "strike": long_put_strike, "instrument_key": long_put["instrument_key"], "transaction_type": "BUY", "ltp": long_put["ltp"]},
                {"role": "short_call", "strike": short_call_strike, "instrument_key": short_call["instrument_key"], "transaction_type": "SELL", "ltp": short_call["ltp"]},
                {"role": "short_put", "strike": short_put_strike, "instrument_key": short_put["instrument_key"], "transaction_type": "SELL", "ltp": short_put["ltp"]},
            ],
            "net_credit": round(net_credit, 2), "max_profit": round(net_credit, 2), "max_loss": round(max_loss, 2),
            "combined_pop_pct": round(combined_pop, 1),
        }
    return None


def select_iron_butterfly(raw_chain: Chain, atm_strike: float, hedge_width_points: float, pop_threshold_pct: float) -> Optional[Dict[str, Any]]:
    """Both shorts at ATM, wings at ATM ± hedge_width_points. The combined PoP here is estimated from the wings' PoP -
    a proxy for "price stays inside the wings" (an approximation, as in the source). Legs are listed hedge first."""
    short_call = _pop_lookup(raw_chain, "call_options", atm_strike)
    short_put = _pop_lookup(raw_chain, "put_options", atm_strike)
    long_call_strike = atm_strike + hedge_width_points
    long_put_strike = atm_strike - hedge_width_points
    long_call = _pop_lookup(raw_chain, "call_options", long_call_strike)
    long_put = _pop_lookup(raw_chain, "put_options", long_put_strike)
    if not short_call or not short_put or not long_call or not long_put:
        return None
    if any(x["ltp"] is None for x in (short_call, short_put, long_call, long_put)):
        return None
    if long_call["pop"] is None or long_put["pop"] is None:
        return None
    combined_pop = max(0.0, long_call["pop"] + long_put["pop"] - 1) * 100
    if combined_pop < pop_threshold_pct:
        return None
    net_credit = (short_call["ltp"] - long_call["ltp"]) + (short_put["ltp"] - long_put["ltp"])
    max_loss = hedge_width_points - net_credit
    if net_credit <= 0 or max_loss <= 0:
        return None
    return {
        "strategy": "IRON_BUTTERFLY",
        "legs": [
            {"role": "long_call_wing", "strike": long_call_strike, "instrument_key": long_call["instrument_key"], "transaction_type": "BUY", "ltp": long_call["ltp"]},
            {"role": "long_put_wing", "strike": long_put_strike, "instrument_key": long_put["instrument_key"], "transaction_type": "BUY", "ltp": long_put["ltp"]},
            {"role": "short_call_atm", "strike": atm_strike, "instrument_key": short_call["instrument_key"], "transaction_type": "SELL", "ltp": short_call["ltp"]},
            {"role": "short_put_atm", "strike": atm_strike, "instrument_key": short_put["instrument_key"], "transaction_type": "SELL", "ltp": short_put["ltp"]},
        ],
        "net_credit": round(net_credit, 2), "max_profit": round(net_credit, 2), "max_loss": round(max_loss, 2),
        "combined_pop_pct": round(combined_pop, 1),
    }


def select_credit_spread(raw_chain: Chain, direction: str, hedge_width_points: float, pop_threshold_pct: float) -> Optional[Dict[str, Any]]:
    """A credit spread by the broker's PoP: BULLISH -> bull put spread (sell the nearest put whose PoP reaches the
    threshold, buy one at least hedge_width_points further out); BEARISH -> bear call spread (the mirror)."""
    if direction not in DIRECTIONS:
        return None
    side = "put_options" if direction == "BULLISH" else "call_options"
    candidates: List[Dict[str, Any]] = []
    for item in raw_chain:
        opt = item.get(side, {}) or {}
        greeks = opt.get("option_greeks", {}) or {}
        pop = greeks.get("pop")
        ltp = (opt.get("market_data", {}) or {}).get("ltp")
        instrument_key = opt.get("instrument_key")
        strike = item.get("strike_price")
        if pop is None or ltp is None or not instrument_key or strike is None or ltp <= 0:
            continue
        candidates.append({"strike": strike, "instrument_key": instrument_key, "pop": pop, "ltp": ltp})
    if not candidates:
        return None
    # the short leg: from the strike nearest the money outwards, the first whose PoP reaches the threshold
    if direction == "BULLISH":
        candidates_sorted = sorted(candidates, key=lambda c: -c["strike"])
    else:
        candidates_sorted = sorted(candidates, key=lambda c: c["strike"])
    short_leg = next((c for c in candidates_sorted if c["pop"] * 100 >= pop_threshold_pct), None)
    if short_leg is None:
        return None
    # the long leg (hedge): the nearest strike at least hedge_width_points further out
    if direction == "BULLISH":
        target = short_leg["strike"] - hedge_width_points
        pool = [c for c in candidates if c["strike"] <= target]
        long_leg = max(pool, key=lambda c: c["strike"]) if pool else None
    else:
        target = short_leg["strike"] + hedge_width_points
        pool = [c for c in candidates if c["strike"] >= target]
        long_leg = min(pool, key=lambda c: c["strike"]) if pool else None
    if long_leg is None:
        return None
    net_credit = short_leg["ltp"] - long_leg["ltp"]
    spread_width = abs(short_leg["strike"] - long_leg["strike"])
    max_profit = net_credit
    max_loss = spread_width - net_credit
    if net_credit <= 0 or max_loss <= 0:
        return None
    return {
        "strategy": "BULL_PUT_SPREAD" if direction == "BULLISH" else "BEAR_CALL_SPREAD",
        "short_leg": short_leg, "long_leg": long_leg, "net_credit": round(net_credit, 2), "spread_width": spread_width,
        "max_profit": round(max_profit, 2), "max_loss": round(max_loss, 2), "short_pop_pct": round(short_leg["pop"] * 100, 1),
    }


def _find_leg(raw_chain: Chain, side: str, strike: float, with_pop: bool) -> Optional[Dict[str, Any]]:
    option_type = "CE" if side == "call_options" else "PE"
    for item in raw_chain:
        if item.get("strike_price") == strike:
            opt = item.get(side, {}) or {}
            ltp = (opt.get("market_data", {}) or {}).get("ltp")
            instrument_key = opt.get("instrument_key")
            if ltp and instrument_key and ltp > 0:
                leg: Dict[str, Any] = {"strike": strike, "instrument_key": instrument_key, "ltp": ltp}
                if with_pop:
                    leg["pop"] = (opt.get("option_greeks", {}) or {}).get("pop")
                leg["option_type"] = option_type
                leg["expiry"] = item.get("expiry")
                return leg
    return None


def _spread_result(direction: str, short_leg: Dict[str, Any], long_leg: Dict[str, Any], hedge_width_points: float) -> Optional[Dict[str, Any]]:
    net_credit = short_leg["ltp"] - long_leg["ltp"]
    if net_credit <= 0:
        return None
    return {
        "strategy": "BULL_PUT_SPREAD" if direction == "BULLISH" else "BEAR_CALL_SPREAD",
        "short_leg": short_leg, "long_leg": long_leg,
        "net_credit": round(net_credit, 2), "spread_width": hedge_width_points,
        "max_profit": round(net_credit, 2), "max_loss": round(hedge_width_points - net_credit, 2),
        "short_pop_pct": round(short_leg["pop"] * 100, 1) if short_leg["pop"] else None,
    }


def select_credit_spread_fixed_strikes(raw_chain: Chain, direction: str, atm_strike: float, *, hedge_width_points: float,
                                       step: float, strikes_otm: int = 2) -> Optional[Dict[str, Any]]:
    """A credit spread at a fixed distance instead of a PoP search (for price-action / indicator setups): the short leg
    `strikes_otm` strikes OTM from ATM, the hedge `hedge_width_points` further out. BULLISH -> bull put spread,
    BEARISH -> bear call spread. max_profit == net_credit, so a target of 30 % of max profit is 30 % of the credit."""
    if direction not in DIRECTIONS:
        return None
    side = "put_options" if direction == "BULLISH" else "call_options"
    if direction == "BULLISH":
        short_strike = atm_strike - strikes_otm * step
        long_strike = short_strike - hedge_width_points
    else:
        short_strike = atm_strike + strikes_otm * step
        long_strike = short_strike + hedge_width_points
    short_leg, long_leg = _find_leg(raw_chain, side, short_strike, True), _find_leg(raw_chain, side, long_strike, True)
    if short_leg is None or long_leg is None:
        return None
    return _spread_result(direction, short_leg, long_leg, hedge_width_points)


def select_credit_spread_itm(raw_chain: Chain, direction: str, atm_strike: float, *, itm_depth_points: float,
                             hedge_width_points: float, step: float) -> Optional[Dict[str, Any]]:
    """Like the fixed-strike spread, but the short leg sits `itm_depth_points` IN the money from ATM (rounded to the
    strike step): BULLISH -> a put above ATM, BEARISH -> a call below ATM; more premium, the same defined risk. 0 puts
    the short at ATM; a negative depth moves it OTM."""
    if direction not in DIRECTIONS:
        return None
    side = "put_options" if direction == "BULLISH" else "call_options"
    itm_offset = round(itm_depth_points / step) * step
    if direction == "BULLISH":
        short_strike = atm_strike + itm_offset
        long_strike = short_strike - hedge_width_points
    else:
        short_strike = atm_strike - itm_offset
        long_strike = short_strike + hedge_width_points
    short_leg, long_leg = _find_leg(raw_chain, side, short_strike, True), _find_leg(raw_chain, side, long_strike, True)
    if short_leg is None or long_leg is None:
        return None
    return _spread_result(direction, short_leg, long_leg, hedge_width_points)


def select_naked_option_itm(raw_chain: Chain, direction: str, atm_strike: float, itm_depth_points: float, *,
                            hedge_width_points: float, step: float, hedge_enabled: bool = False) -> Optional[Dict[str, Any]]:
    """Buy an ITM option (BULLISH -> call below ATM, BEARISH -> put above ATM; 0 = ATM, negative = OTM). With
    `hedge_enabled` a further-OTM option is sold `hedge_width_points` away, making it a debit spread. Default: no hedge
    (a plain long option: max loss is the premium paid, max profit open-ended -> None)."""
    if direction not in DIRECTIONS:
        return None
    side = "call_options" if direction == "BULLISH" else "put_options"
    itm_offset = round(itm_depth_points / step) * step
    buy_strike = (atm_strike - itm_offset) if direction == "BULLISH" else (atm_strike + itm_offset)
    buy_leg = _find_leg(raw_chain, side, buy_strike, False)
    if buy_leg is None:
        return None
    strategy = "NAKED_CALL" if direction == "BULLISH" else "NAKED_PUT"
    if not hedge_enabled:
        return {"strategy": strategy, "buy_leg": buy_leg, "net_credit": -buy_leg["ltp"], "max_profit": None,
                "max_loss": round(buy_leg["ltp"], 2)}
    hedge_strike = (buy_strike + hedge_width_points) if direction == "BULLISH" else (buy_strike - hedge_width_points)
    hedge_leg = _find_leg(raw_chain, side, hedge_strike, False)
    if hedge_leg is None:
        return None
    net_debit = buy_leg["ltp"] - hedge_leg["ltp"]
    if net_debit <= 0:
        return None
    return {
        "strategy": strategy, "buy_leg": buy_leg, "hedge_leg": hedge_leg,
        "net_credit": round(-net_debit, 2), "spread_width": hedge_width_points,
        "max_profit": round(hedge_width_points - net_debit, 2), "max_loss": round(net_debit, 2),
    }


def compute_position_size(available_margin: float, risk_pct: float, max_loss_per_unit: float, lot_size: float) -> Tuple[int, float]:
    """(lots, risk amount): the whole lots whose max loss fits in available_margin x risk_pct %. 0 lots when even one
    lot would exceed the cap (never rounded up), or with no margin / no defined loss."""
    if not available_margin or max_loss_per_unit <= 0 or lot_size <= 0:
        return 0, 0.0
    risk_amount = available_margin * (risk_pct / 100.0)
    max_loss_per_lot = max_loss_per_unit * lot_size
    lots = int(risk_amount // max_loss_per_lot) if max_loss_per_lot > 0 else 0
    return lots, risk_amount
