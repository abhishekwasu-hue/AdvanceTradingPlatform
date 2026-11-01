"""M72 CP-0: builds `evals/sets/copilot_trader_tools.json`, the recorded tool results the trader question set runs on.

It starts from the Copilot chat evals' own recorded results (the committed sets/copilot_tools.json, read as it is: its
"default" and "no_data" contexts, unchanged, so both sets score the same recordings) and adds the read tools a trader's question needs that today's chat does not call yet
(the option-seller payoff and Greeks, a what-if, his orders, alerts and settings). Every value is synthetic and made by
code: the payoff, the Greeks and the what-if come from ATP's own options model (app/options_builder/model.py, the
platform's Black-Scholes) on legs priced from the made-up option chain of build_copilot_fixtures; the orders, alerts and
settings are hand-made and clearly fake (PAPER, eval ids). No broker, vendor, Trade or atp-private data.

Regenerate after a change to those shapes, then pin the new sha256 in scripts/check_public_repo.py ALLOWED_TEST_DATA:

    cd backend && python -m evals.build_trader_fixtures
    python3 ../scripts/check_public_repo.py --hash evals/sets/copilot_trader_tools.json
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

from evals import build_copilot_fixtures as base

OUT = Path(__file__).resolve().parent / "sets" / "copilot_trader_tools.json"
EXPIRY = "2026-10-27"
LOT = 75


def _row(strike: int) -> Any:
    return next(r for r in base._chain().rows if r.strike == strike)


def _strangle() -> List[Dict[str, Any]]:
    """A made-up short strangle, one lot each side: strikes and IVs from the synthetic chain's own rows, each premium the
    model's own value of the leg at the fixture's time (so the P&L "now" is zero and every what-if is the model's move)."""
    from app.options_builder import model
    call, put = _row(24950), _row(24750)
    legs = [{"direction": "SELL", "option_type": "CE", "strike": 24950.0, "premium": 0.0, "lots": 1, "lot_size": LOT,
             "expiry": EXPIRY, "iv": round(call.call_iv / 100.0, 4)},
            {"direction": "SELL", "option_type": "PE", "strike": 24750.0, "premium": 0.0, "lots": 1, "lot_size": LOT,
             "expiry": EXPIRY, "iv": round(put.put_iv / 100.0, 4)}]
    for leg in legs:
        leg["premium"] = round(model.leg_theoretical(leg, base._chain().underlying_ltp, base.NOW), 2)
    return legs


def _r(x: float, nd: int = 2) -> float:
    return round(float(x), nd)


def _payoff() -> Dict[str, Any]:
    from app.options_builder import model
    legs, spot = _strangle(), base._chain().underlying_ltp
    s = model.summary(legs, spot, as_of=base.NOW)
    ext = model.payoff_extremes(legs)
    zones = model.profitable_intervals(legs)
    credit = sum(leg["premium"] for leg in legs) * LOT
    g = s["greeks"]
    return {"symbol": "NIFTY", "expiry": EXPIRY, "spot": spot, "strategy": "short strangle (eval)",
            "legs": [{k: leg[k] for k in ("direction", "option_type", "strike", "premium", "lots", "lot_size")} for leg in legs],
            "net_credit": _r(credit), "max_profit": _r(ext["max_profit"]), "unbounded_loss": bool(ext["unbounded_loss"]),
            "breakevens": [_r(zones[0][0]), _r(zones[0][1])], "pop_pct": _r(100 * s["pop"], 1),
            "expected_move": _r(s["expected_move"]), "greeks": {k: _r(v, 2) for k, v in g.items() if k != "rho"},
            "model": s["model"]}


def _whatif() -> Dict[str, Any]:
    """The same strangle's P&L now, valued by the model under the scenarios a seller asks about (spot, IV, days)."""
    from datetime import timedelta

    from app.options_builder import model
    legs, spot = _strangle(), base._chain().underlying_ltp
    rows = []
    for label, move, iv, days in (("spot +150", 150.0, 0.0, 0), ("spot -150", -150.0, 0.0, 0), ("IV +2 points", 0.0, 0.02, 0),
                                  ("2 days later, spot unchanged", 0.0, 0.0, 2)):
        pnl = model.pnl_on(legs, [spot + move], base.NOW + timedelta(days=days), iv_shift=iv)[0]
        rows.append({"scenario": label, "spot": _r(spot + move), "pnl": _r(pnl)})
    return {"symbol": "NIFTY", "expiry": EXPIRY, "strategy": "short strangle (eval)", "scenarios": rows,
            "model": "Black-Scholes, lognormal price, one volatility (an estimate, not a forecast)"}


def _greeks() -> Dict[str, Any]:
    from datetime import date

    from app.option_chain.greeks import BSInputs, black_scholes, time_to_expiry_years
    from app.option_chain.models import OptionType
    t, spot, row = time_to_expiry_years(date.fromisoformat(EXPIRY), base.NOW), base._chain().underlying_ltp, _row(24850)
    out = []
    for kind, ot, iv in (("CE", OptionType.CALL, row.call_iv), ("PE", OptionType.PUT, row.put_iv)):
        g = black_scholes(BSInputs(spot, 24850.0, t, 0.065, iv / 100.0, ot))
        out.append({"strike": 24850, "type": kind, "iv_pct": _r(iv), "delta": _r(g.delta), "gamma": _r(g.gamma, 4), "theta": _r(g.theta),
                    "vega": _r(g.vega)})
    return {"symbol": "NIFTY", "expiry": EXPIRY, "spot": spot, "days_to_expiry": math.ceil(t * 365), "rows": out}


def _orders() -> Dict[str, Any]:
    return {"count": 3, "orders": [
        {"id": 901, "mode": "PAPER", "symbol": "NIFTY26OCT24950CE", "side": "SELL", "quantity": 75, "type": "LIMIT", "price": 41.0,
         "status": "OPEN", "placed_at": base.AS_OF},
        {"id": 902, "mode": "PAPER", "symbol": "SBIN", "side": "BUY", "quantity": 20, "type": "LIMIT", "price": 812.5, "status": "OPEN",
         "placed_at": base.AS_OF},
        {"id": 903, "mode": "PAPER", "symbol": "INFY", "side": "BUY", "quantity": 10, "type": "MARKET", "price": None, "status": "REJECTED",
         "reason": "the quantity is above your own max open positions setting", "placed_at": base.AS_OF}],
        "open": 2, "rejected": 1, "read_at": base.AS_OF}


def _alerts() -> Dict[str, Any]:
    return {"count": 2, "alerts": [
        {"id": 31, "symbol": "NIFTY", "condition": "price crosses 25,080 (the lower edge of the daily supply area)", "level": 25080.0,
         "status": "ACTIVE", "channel": "in-app"},
        {"id": 32, "symbol": "BANKNIFTY", "condition": "price closes below 51,200 on the daily chart", "level": 51200.0, "status": "ACTIVE",
         "channel": "telegram"}], "read_at": base.AS_OF}


def _settings() -> Dict[str, Any]:
    return {"language": "en", "default_timeframe": "1d", "watchlist": ["SBIN", "INFY", "TCS", "ITC"], "default_lots": 1,
            "product": "NRML", "mode": "PAPER", "notifications": {"telegram": True, "in_app": True}, "read_at": base.AS_OF}


def build() -> Dict[str, Any]:
    from evals import runner
    ctx = runner.copilot_fixtures()
    trader = dict(ctx["default"])
    trader.update({
        "get_strategy_payoff:NIFTY": base.ok(_payoff(), "options_builder"),
        "get_what_if:NIFTY": base.ok(_whatif(), "options_builder"),
        "get_option_greeks:NIFTY": base.ok(_greeks(), "option_chain_greeks"),
        "get_my_orders": base.ok(_orders(), "orders"),
        "get_my_alerts": base.ok(_alerts(), "alerts"),
        "get_my_settings": base.ok(_settings(), "settings"),
    })
    return {"_note": "Synthetic tool results for the M72 trader question set (backend/evals/build_trader_fixtures.py). Not market data.",
            "trader": trader, "no_data": ctx["no_data"]}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
