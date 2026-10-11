"""P1-c: the Options Strategy Builder API - what the builder page draws from (ADR-0025). Research only: every endpoint
computes; none places, stages or sizes an order (a built strategy reaches the order ticket as a basket in P1-d, through
the platform's execution and risk layers).

- `GET  /api/options-builder/catalog`   the template gallery (families, legs as offsets, descriptions).
- `POST /api/options-builder/template`  a template's legs at a given ATM / width / expiries / lots.
- `POST /api/options-builder/evaluate`  curves (expiry and any date, IV shift), exact extremes and intervals,
                                        breakevens, PoP, expected move, probability-weighted P&L, net and per-leg Greeks.
- `POST /api/options-builder/suggest`   a selector rule (P1-a, ported) on a broker chain through the adapter.

Instrument numbers (strike step, lot size, width) always come from the caller (the instrument master on the page);
nothing here defaults them.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers.models import OptionChain
from app.core.config import RISK_FREE_RATE
from app.db.models import User
from app.db.session import get_session
from app.options_builder import catalog, chain, model, selectors
from app.options_builder.greeks import leg_with_model_greeks
from app.options_builder.payoff import build_default_price_range, compute_strategy_payoff_curve
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/options-builder", tags=["options-builder"])
FLAG = "options_builder"
MAX_LEGS = 12
MAX_POINTS = 401
DISCLAIMER = "Model estimates (Black-Scholes, one volatility) for research; not a forecast or a recommendation."


class LegIn(BaseModel):
    direction: Literal["BUY", "SELL"]
    option_type: Literal["CE", "PE", "FUT"]
    strike: float = Field(gt=0)
    premium: float = Field(ge=0, description="entry price per unit (the futures price for FUT)")
    lots: int = Field(ge=1, le=1000)
    lot_size: int = Field(ge=1, le=100_000)
    expiry: date
    iv: Optional[float] = Field(default=None, gt=0, lt=5, description="annualised, as a fraction; solved from the premium when absent")


class EvaluateBody(BaseModel):
    legs: List[LegIn] = Field(min_length=1, max_length=MAX_LEGS)
    spot: float = Field(gt=0)
    as_of: Optional[Union[datetime, date]] = Field(default=None, description="an aware datetime (exact, intraday) or a date; default now")
    days_forward: float = Field(default=0, ge=0, le=400)
    iv_shift: float = Field(default=0.0, ge=-0.5, le=1.0)
    range_pct: float = Field(default=10.0, gt=0, le=60)
    points: int = Field(default=201, ge=21, le=MAX_POINTS)
    rate: float = Field(default=RISK_FREE_RATE, ge=-0.05, le=0.3)

    @field_validator("as_of", mode="before")
    @classmethod
    def _when(cls, v: Any) -> Any:
        return _parse_when(v)


class TemplateBody(BaseModel):
    name: str
    atm_strike: float = Field(gt=0)
    width: float = Field(gt=0)
    near_expiry: date
    next_expiry: Optional[date] = None
    lots: int = Field(default=1, ge=1, le=1000)


def _parse_when(v: Any) -> Any:
    """A plain YYYY-MM-DD stays a date (that day's close); a datetime must carry its time zone (no guessing)."""
    if isinstance(v, str):
        if len(v) == 10:
            return date.fromisoformat(v)
        parsed = datetime.fromisoformat(v)
        if parsed.tzinfo is None:
            raise ValueError("give as_of with a time zone (e.g. +05:30), or a plain date")
        return parsed
    if isinstance(v, datetime) and v.tzinfo is None:
        raise ValueError("give as_of with a time zone (e.g. +05:30), or a plain date")
    return v


class SuggestBody(BaseModel):
    chain: OptionChain
    rule: Literal["iron_condor", "iron_butterfly", "credit_spread", "credit_spread_fixed", "credit_spread_itm", "naked_itm"]
    step: float = Field(gt=0, description="the strike step, from the instrument master")
    hedge_width_points: float = Field(gt=0)
    atm_strike: Optional[float] = Field(default=None, gt=0)
    direction: Optional[Literal["BULLISH", "BEARISH"]] = None
    pop_threshold_pct: float = Field(default=70.0, ge=0, le=100)
    strikes_otm: int = Field(default=2, ge=0, le=50)
    itm_depth_points: float = Field(default=0.0, ge=0)
    hedge_enabled: bool = False
    as_of: Optional[Union[datetime, date]] = None

    @field_validator("as_of", mode="before")
    @classmethod
    def _when(cls, v: Any) -> Any:
        return _parse_when(v)


def _finite(x: float) -> Optional[float]:
    """JSON has no infinity: an unbounded value is null with its flag beside it."""
    return None if x is None or math.isinf(x) or math.isnan(x) else float(x)


def _legs(body: EvaluateBody, as_of: Union[date, datetime]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for leg in body.legs:
        d = leg.model_dump()
        d["expiry"] = leg.expiry.isoformat()
        if leg.option_type != "FUT" and d["iv"] is None:
            if leg.premium <= 0:
                raise HTTPException(status_code=422, detail=f"{leg.option_type} {leg.strike:g}: give an IV or a premium to solve it from")
            try:
                d = leg_with_model_greeks(d, underlying_price=body.spot, expiry=leg.expiry, as_of=as_of)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"{leg.option_type} {leg.strike:g}: {exc}") from exc
            d["iv_source"] = "solved from the premium"
        elif leg.option_type != "FUT":
            d["iv_source"] = "given"
        out.append(d)
    return out


@router.get("/catalog")
async def get_catalog(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    return {"families": catalog.families(),
            "templates": {name: {"family": t["family"], "what": t["what"], "two_expiries": any(leg[4] == 1 for leg in t["legs"]),
                                 "legs": [{"direction": d, "option_type": k, "offset": o, "lots": m, "expiry_slot": e} for d, k, o, m, e in t["legs"]]}
                          for name, t in catalog.CATALOG.items()}}


@router.post("/template")
async def build_template(body: TemplateBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    if body.name not in catalog.CATALOG:
        raise HTTPException(status_code=404, detail=f"no template named {body.name!r}")
    legs = catalog.build_template(body.name, body.atm_strike, body.width, body.near_expiry.isoformat(),
                                  body.next_expiry.isoformat() if body.next_expiry else None, lots=body.lots)
    if legs is None:
        raise HTTPException(status_code=422, detail=f"{body.name} needs the next expiry as well")
    return {"name": body.name, "legs": legs}


@router.post("/evaluate")
async def evaluate(body: EvaluateBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    as_of: Union[date, datetime] = body.as_of or datetime.now().astimezone()
    legs = _legs(body, as_of)
    prices = build_default_price_range(body.spot, num_points=body.points, range_pct=body.range_pct)
    single = len(model.expiries(legs)) == 1
    try:
        summary = model.summary(legs, body.spot, as_of=as_of, r=body.rate)
        curve_on = model.value_curve(legs, prices, as_of=as_of, days_forward=body.days_forward, iv_shift=body.iv_shift, r=body.rate)
        today = model.value_curve(legs, prices, as_of=as_of, iv_shift=body.iv_shift, r=body.rate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    out: Dict[str, Any] = {
        "prices": prices, "today": today, "on_date": curve_on, "days_forward": body.days_forward, "iv_shift": body.iv_shift,
        "summary": summary,
        "legs": [{**{k: leg.get(k) for k in ("direction", "option_type", "strike", "premium", "lots", "lot_size", "expiry", "iv", "iv_source")},
                  "greeks": model.net_greeks([leg], body.spot, as_of, iv_shift=body.iv_shift, r=body.rate)} for leg in legs],
        "single_expiry": single, "disclaimer": DISCLAIMER,
    }
    if single:
        ext = model.payoff_extremes(legs)
        intervals = model.profitable_intervals(legs)
        out["at_expiry"] = compute_strategy_payoff_curve(legs, prices)
        out["extremes"] = {"max_profit": _finite(ext["max_profit"]), "max_loss": _finite(ext["max_loss"]),
                           "unbounded_profit": ext["unbounded_profit"], "unbounded_loss": ext["unbounded_loss"],
                           "max_profit_at": ext["max_profit_at"], "max_loss_at": ext["max_loss_at"]}
        out["profitable"] = [[_finite(a), _finite(b)] for a, b in intervals]
        out["breakevens"] = sorted({round(x, 6) for a, b in intervals for x in (a, b) if 0 < x < math.inf})
    else:
        out["at_expiry"] = None                     # a calendar has no single expiry payoff; `on_date` at the near expiry draws it
        out["extremes"] = None
        out["profitable"] = None
        out["breakevens"] = None
    return out


@router.post("/suggest")
async def suggest(body: SuggestBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """A ported selector rule on the chain; the rule and its numbers come back with the result so the page can show
    them. None when the chain cannot give a complete structure - never a partial one."""
    await require_flag(session, FLAG, user.tenant_id)
    raw = chain.raw_chain(body.chain, as_of=body.as_of or datetime.now().astimezone())
    spot = body.chain.underlying_ltp
    atm = body.atm_strike or (min((r.strike for r in body.chain.rows), key=lambda k: abs(k - spot)) if spot and body.chain.rows else None)
    needs_direction = body.rule in ("credit_spread", "credit_spread_fixed", "credit_spread_itm", "naked_itm")
    if needs_direction and body.direction is None:
        raise HTTPException(status_code=422, detail=f"{body.rule} needs a direction (BULLISH or BEARISH)")
    direction = body.direction or ""
    result: Optional[Dict[str, Any]]
    if body.rule == "credit_spread":
        result = selectors.select_credit_spread(raw, direction, body.hedge_width_points, body.pop_threshold_pct)
    else:
        if atm is None:
            raise HTTPException(status_code=422, detail="give atm_strike, or a chain with its underlying price")
        at: float = atm
        if body.rule == "iron_condor":
            result = selectors.select_iron_condor(raw, at, body.step, body.hedge_width_points, body.pop_threshold_pct)
        elif body.rule == "iron_butterfly":
            result = selectors.select_iron_butterfly(raw, at, body.hedge_width_points, body.pop_threshold_pct)
        elif body.rule == "credit_spread_fixed":
            result = selectors.select_credit_spread_fixed_strikes(raw, direction, at, hedge_width_points=body.hedge_width_points,
                                                                  step=body.step, strikes_otm=body.strikes_otm)
        elif body.rule == "credit_spread_itm":
            result = selectors.select_credit_spread_itm(raw, direction, at, itm_depth_points=body.itm_depth_points,
                                                        hedge_width_points=body.hedge_width_points, step=body.step)
        else:
            result = selectors.select_naked_option_itm(raw, direction, at, body.itm_depth_points, hedge_width_points=body.hedge_width_points,
                                                       step=body.step, hedge_enabled=body.hedge_enabled)
    return {"rule": body.rule, "atm_strike": atm, "pop_source": chain.POP_SOURCE, "result": result,
            "found": result is not None, "disclaimer": DISCLAIMER}
