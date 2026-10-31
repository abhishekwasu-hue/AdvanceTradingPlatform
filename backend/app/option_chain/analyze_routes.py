"""`POST /api/option-chain/analyze` (PCR, Max Pain, OI build-up, bias of a supplied chain) and
`POST /api/option-chain/greeks` (the legs' and the position's Greeks).

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.brokers.models import OptionChain
from app.cache.client import cache_get, cache_set
from app.db.models import User
from app.option_chain.analysis import analyze_option_chain
from app.option_chain.leg_greeks import compute_strategy_greeks
from app.option_chain.models import OptionChainAnalysis, OptionLegInput, StrategyGreeksResult

router = APIRouter()


def _cache_key(prefix: str, payload: str) -> str:
    return f"{prefix}:{hashlib.sha256(payload.encode()).hexdigest()}"


class OptionChainAnalyzeRequest(BaseModel):
    chain: OptionChain
    top_n: int = 3


@router.post("/api/option-chain/analyze", response_model=OptionChainAnalysis)
async def option_chain_analyze(request: OptionChainAnalyzeRequest, _: User = Depends(get_current_user)) -> OptionChainAnalysis:
    """Takes a raw OptionChain (e.g. from BrokerInterface.get_option_chain()) and returns PCR,
    Max Pain, ATM/ITM/OTM, OI buildup/unwinding, and a bias confirmed by more than PCR alone.
    Pure function of its input, so short-lived results are cached in Redis (fails open to a
    plain recompute if Redis isn't reachable) - useful once a real option chain is being polled
    repeatedly for the same underlying/expiry within the same few seconds.
    """
    key = _cache_key("option_chain_analysis", request.model_dump_json())
    cached = await cache_get(key)
    if cached is not None:
        return OptionChainAnalysis.model_validate_json(cached)

    result = analyze_option_chain(request.chain, top_n=request.top_n)
    await cache_set(key, result.model_dump_json(), ttl_seconds=5)
    return result


class GreeksRequest(BaseModel):
    legs: list[OptionLegInput]


@router.post("/api/option-chain/greeks", response_model=StrategyGreeksResult)
async def option_chain_greeks(request: GreeksRequest, _: User = Depends(get_current_user)) -> StrategyGreeksResult:
    """Black-Scholes Delta/Gamma/Theta/Vega for one or more option legs, and the net Greeks of
    the combined position (a spread/straddle/strangle nets a short leg's Greeks against a long
    leg's). Each leg supplies either a real quoted `option_ltp` (implied volatility is solved
    from it) or an `implied_volatility` directly - never a fabricated one. A 422 means a leg's
    price is outside what's solvable (a stale/crossed quote), not a server error.
    """
    try:
        return compute_strategy_greeks(request.legs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
