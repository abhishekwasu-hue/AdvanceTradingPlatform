"""M72 PR-4 (CP-6, findings F12 / M68): the Copilot's options-seller toolkit - read and calculation tools only.

* `find_strikes`  - the strikes of one side of the chain that meet the trader's OWN rule: what he states in this message
                    (a distance in points, a number of strikes from ATM, a premium band) or, when he states none, his
                    saved Trade settings (`options.strike_rule` + `options.strike_offset`). Nothing saved and nothing
                    stated = an error that tells the model to ask him; never a pre-chosen distance, delta or premium.
                    The rows are listed by distance from the price - matches, never a recommendation.
* `calc_option_payoff` - the expiry payoff of legs the trader states (or a tool read): net premium, the best and worst
                    P&L (unbounded said as such), the break-even prices, the payoff at given prices.
* `calc_option_what_if` - the legs' P&L if the price, the IV or the days change, by the platform's Black-Scholes, with
                    the trader's own risk-free rate (`options.risk_free_rate_pct`); Not set = ask (F12: no fixed 7 %).

Every number a model passes to these tools must come from the trader's message or a tool result of this request
(`agent.GROUNDED_ARG_TOOLS`); the results are code arithmetic, so the model never computes a figure itself. Each tool is
flagged `copilot_agent`, kind "read": nothing here places, blocks or changes an order (ADR-0006).
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.ai.tools import Tool, ToolContext, ToolResult, register

FLAG = "copilot_agent"
NOT_SET_RULE = ("his strike rule is Not set in Trade settings (Options) and his message states none - ask him which strike, "
                "distance or premium he uses; never assume one")
MAX_ROWS = 12
PRICE = Field(gt=0, lt=10_000_000, allow_inf_nan=False)


def _r(v: Any, nd: int = 2) -> Any:
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), nd)


async def _options_settings(ctx: ToolContext) -> dict[str, Any]:
    """His saved Options values (only the ones he saved; a missing key is Not set)."""
    from app.me import prefs, trade_settings
    value, _version = await prefs.read_one(trade_settings.NAMESPACE, ctx.user, ctx.session)
    values = trade_settings.clean(value.get("values"))
    return {k.removeprefix("options."): v for k, v in values.items() if k.startswith("options.")}


# --- find_strikes -----------------------------------------------------------------------------------------------------------

class _FindStrikes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlying: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9&\- ]+$")
    expiry: date | None = Field(default=None, description="YYYY-MM-DD; omit for the nearest expiry")
    right: Literal["CE", "PE"] = Field(description="The side of the chain the trader asks about")
    distance_points: float | None = Field(default=None, gt=0, lt=100_000, description="Only when the trader states a distance in points")
    strikes_away: int | None = Field(default=None, ge=0, le=50, description="Only when the trader states a number of strikes from ATM")
    premium_min: float | None = Field(default=None, ge=0, lt=100_000, description="Only when the trader states a premium band")
    premium_max: float | None = Field(default=None, gt=0, lt=100_000, description="Only when the trader states a premium band")


def _rows(chain: Any, right: str) -> list[dict[str, Any]]:
    key = "call" if right == "CE" else "put"
    out = []
    for r in sorted(chain.rows, key=lambda x: x.strike):
        out.append({"strike": r.strike, "ltp": getattr(r, f"{key}_ltp"), "iv": getattr(r, f"{key}_iv"), "oi": getattr(r, f"{key}_oi"),
                    "oi_change": getattr(r, f"{key}_change_oi")})
    return out


def strikes_matching(rows: list[dict[str, Any]], spot: float, right: str, *, distance_points: float | None, strikes_away: int | None,
                     premium_min: float | None, premium_max: float | None) -> tuple[list[dict[str, Any]], str]:
    """The rows on the out-of-the-money side that meet every stated criterion, nearest first, and the rule in words. Pure."""
    strikes = [r["strike"] for r in rows]
    if not strikes:
        return [], "no strikes"
    atm = min(strikes, key=lambda k: abs(k - spot))
    i_atm = strikes.index(atm)
    otm = [r for r in rows if (r["strike"] >= atm if right == "CE" else r["strike"] <= atm)]
    otm.sort(key=lambda r: abs(r["strike"] - spot))
    words = []
    picked = otm
    if strikes_away is not None:
        j = i_atm + strikes_away if right == "CE" else i_atm - strikes_away
        picked = [r for r in picked if 0 <= j < len(strikes) and r["strike"] == strikes[j]]
        words.append(f"{strikes_away} strikes from ATM")
    if distance_points is not None:
        picked = [r for r in picked if abs(r["strike"] - spot) >= distance_points]
        words.append(f"at least {distance_points:g} points from the price")
    if premium_min is not None or premium_max is not None:
        lo, hi = premium_min if premium_min is not None else 0.0, premium_max if premium_max is not None else math.inf
        picked = [r for r in picked if r["ltp"] is not None and lo <= r["ltp"] <= hi]
        words.append("premium " + (f"{lo:g} to {hi:g}" if math.isfinite(hi) else f"at least {lo:g}"))
    out = [{**r, "distance_points": _r(abs(r["strike"] - spot)), "distance_pct": _r(abs(r["strike"] - spot) / spot * 100 if spot else None)}
           for r in picked[:MAX_ROWS]]
    return out, ", ".join(words)


async def find_strikes(ctx: ToolContext, args: _FindStrikes) -> ToolResult:
    """The strikes of one side that meet the trader's OWN rule (stated in his message, else his saved Trade settings), nearest
    first, with premium, IV, OI and the distance from the price. Lists matches only - never picks one for him."""
    from app.ai.tools.market import _failed, _iso, read_chain
    stated = any(v is not None for v in (args.distance_points, args.strikes_away, args.premium_min, args.premium_max))
    rule_from = "his message"
    strikes_away = args.strikes_away
    if not stated:
        saved = await _options_settings(ctx)
        rule = saved.get("strike_rule")
        if rule is None:
            return ToolResult(False, None, None, "strike_finder", error=NOT_SET_RULE)
        if rule == "ATM":
            strikes_away = 0
        elif rule == "OTM":
            if saved.get("strike_offset") is None:
                return ToolResult(False, None, None, "strike_finder", error=NOT_SET_RULE)
            strikes_away = int(saved["strike_offset"])
        else:
            return ToolResult(False, None, None, "strike_finder", error="his saved strike rule is in the money; this lists out-of-the-money strikes - ask him")
        rule_from = "his Trade settings"
    try:
        chain, source = await read_chain(ctx, args.underlying, args.expiry)
    except HTTPException as exc:
        return _failed("strike_finder", exc)
    if not chain.rows or not chain.underlying_ltp:
        return ToolResult(False, None, None, source, error=f"the chain for {args.underlying} is empty or has no price")
    spot = float(chain.underlying_ltp)
    rows, words = strikes_matching(_rows(chain, args.right), spot, args.right, distance_points=args.distance_points, strikes_away=strikes_away,
                                   premium_min=args.premium_min, premium_max=args.premium_max)
    walls = sorted((r for r in _rows(chain, args.right) if r["oi"]), key=lambda r: -r["oi"])[:3]
    data = {"underlying": args.underlying.upper(), "expiry": str(chain.expiry), "right": args.right, "price": _r(spot), "rule": words or "none",
            "rule_from": rule_from, "matches": rows, "highest_oi_strikes": [{"strike": w["strike"], "oi": w["oi"]} for w in walls],
            "note": "Strikes that meet his own rule, nearest first - information, not a recommendation."}
    return ToolResult(True, data, _iso(ctx.now), source, {"read_at": _iso(ctx.now), "expiry": str(chain.expiry)})


# --- payoff and what-if -------------------------------------------------------------------------------------------------------

class _Leg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    right: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike: float = PRICE
    premium: float = Field(ge=0, lt=10_000_000, allow_inf_nan=False)
    quantity: float = Field(gt=0, lt=10_000_000, allow_inf_nan=False, description="Units (lots x lot size), as the trader states or a tool read")


class _Payoff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legs: list[_Leg] = Field(min_length=1, max_length=8)
    at_prices: list[float] = Field(default_factory=list, max_length=10, description="Prices the trader asks about (optional)")


def _builder_legs(legs: list[_Leg]) -> list[dict[str, Any]]:
    return [{"direction": leg.side, "option_type": leg.right, "strike": leg.strike, "premium": leg.premium, "lots": leg.quantity, "lot_size": 1.0,
             "expiry": None} for leg in legs]


def payoff_numbers(legs: list[_Leg], at_prices: list[float]) -> dict[str, Any]:
    """Code arithmetic over the stated legs (one expiry): net premium, extremes, break-evens, payoff at given prices."""
    from app.options_builder import model
    built = [{**leg, "expiry": "2000-01-01"} for leg in _builder_legs(legs)]       # one shared expiry: the extremes need it
    ext = model.payoff_extremes(built)
    intervals = model.profitable_intervals(built)
    breakevens = sorted({_r(x) for a, b in intervals for x in (a, b) if x not in (0.0,) and math.isfinite(x)})
    net = sum((leg.premium if leg.side == "SELL" else -leg.premium) * leg.quantity for leg in legs)
    return {"net_premium": _r(net), "net_premium_means": "received" if net > 0 else "paid" if net < 0 else "none",
            "max_profit": "unlimited" if ext["unbounded_profit"] else _r(ext["max_profit"]),
            "max_loss": "unlimited" if ext["unbounded_loss"] else _r(ext["max_loss"]),
            "breakevens": breakevens, "at_prices": [{"price": p, "pnl": _r(model._payoff(built, p))} for p in at_prices]}


async def calc_option_payoff(ctx: ToolContext, args: _Payoff) -> ToolResult:
    """The expiry payoff of the legs the trader states (or a tool read): net premium, best and worst P&L (unlimited said so),
    break-even prices and the P&L at prices he asks about. Code arithmetic; same-expiry legs."""
    from app.ai.tools.market import _iso
    data = {"legs": [leg.model_dump() for leg in args.legs], **payoff_numbers(args.legs, args.at_prices),
            "note": "At expiry, before charges; a model, not a forecast."}
    return ToolResult(True, data, _iso(ctx.now), "calc", {"computed_at": _iso(ctx.now)})


class _WhatIfLeg(_Leg):
    iv_pct: float = Field(gt=0, lt=500, allow_inf_nan=False, description="The leg's IV in percent, from the chain or the trader")
    expiry: date


class _WhatIf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legs: list[_WhatIfLeg] = Field(min_length=1, max_length=8)
    price: float = Field(gt=0, lt=10_000_000, allow_inf_nan=False, description="The price now, from a tool result")
    price_change: float = Field(default=0.0, gt=-10_000_000, lt=10_000_000, allow_inf_nan=False, description="Points, only as the trader states")
    iv_change_pct: float = Field(default=0.0, gt=-500, lt=500, allow_inf_nan=False, description="IV points, only as the trader states")
    days_forward: float = Field(default=0.0, ge=0, le=400, allow_inf_nan=False, description="Days ahead, only as the trader states")


async def calc_option_what_if(ctx: ToolContext, args: _WhatIf) -> ToolResult:
    """The legs' P&L now and if the price, the IV or the days change (the trader's own scenario), by the platform's
    Black-Scholes with HIS risk-free rate from Trade settings (Not set: ask him). A model, never a forecast."""
    from app.ai.tools.market import _iso
    from app.option_chain.greeks import today_ist
    from app.options_builder import model
    rate = (await _options_settings(ctx)).get("risk_free_rate_pct")
    if rate is None:
        return ToolResult(False, None, None, "calc", error="his risk-free rate is Not set in Trade settings (Options) - ask him for it; never assume one")
    r = float(rate) / 100.0
    legs = [{"direction": leg.side, "option_type": leg.right, "strike": leg.strike, "premium": leg.premium, "lots": leg.quantity, "lot_size": 1.0,
             "expiry": leg.expiry.isoformat(), "iv": leg.iv_pct / 100.0} for leg in args.legs]
    today = today_ist()
    moved = args.price + args.price_change
    when = model._shift(today, args.days_forward)
    try:
        now_pnl = model.pnl_on(legs, [args.price], today, r=r)[0]
        then_pnl = model.pnl_on(legs, [moved], when, iv_shift=args.iv_change_pct / 100.0, r=r)[0]
        greeks = model.net_greeks(legs, args.price, today, r=r)
    except ValueError as exc:
        return ToolResult(False, None, None, "calc", error=str(exc)[:200])
    shown_now, shown_then = _r(now_pnl), _r(then_pnl)
    # the change is the difference of the two SHOWN figures, so the three numbers on the card always add up
    change = _r(shown_then - shown_now) if shown_now is not None and shown_then is not None else None
    data = {"price_now": args.price, "pnl_now": shown_now, "scenario": {"price": _r(moved), "iv_change_pct": args.iv_change_pct,
            "days_forward": args.days_forward}, "pnl_in_scenario": shown_then, "pnl_change": change,
            "net_greeks_now": {k: _r(v, 4) for k, v in greeks.items()}, "risk_free_rate_pct": float(rate),
            "note": "Black-Scholes estimate with his own rate; a model, not a forecast."}
    return ToolResult(True, data, _iso(ctx.now), "calc", {"computed_at": _iso(ctx.now)})


OPTIONS_TOOLS = ("find_strikes", "calc_option_payoff", "calc_option_what_if")

for _tool in (
    Tool("find_strikes", find_strikes.__doc__ or "", _FindStrikes, find_strikes, cost_units=3, timeout_seconds=20, flag=FLAG),
    Tool("calc_option_payoff", calc_option_payoff.__doc__ or "", _Payoff, calc_option_payoff, cost_units=1, timeout_seconds=5, flag=FLAG),
    Tool("calc_option_what_if", calc_option_what_if.__doc__ or "", _WhatIf, calc_option_what_if, cost_units=1, timeout_seconds=5, flag=FLAG),
):
    register(_tool)

__all__ = ["OPTIONS_TOOLS", "find_strikes", "calc_option_payoff", "calc_option_what_if", "strikes_matching", "payoff_numbers", "NOT_SET_RULE"]
