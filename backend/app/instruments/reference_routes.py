"""`GET /api/instruments` and `GET /api/instruments/{symbol}`: the reference contract specs (public, static metadata).

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""

from fastapi import APIRouter, HTTPException

from app.instruments.models import ContractSpec
from app.instruments.registry import get_contract_spec, list_contract_specs

router = APIRouter()


@router.get("/api/instruments", response_model=list[ContractSpec])
def list_instruments() -> list[ContractSpec]:
    """Reference contract specs for MCX commodity and crypto symbols (see
    app/instruments/registry.py) - plain NSE/BSE equity & index-option symbols aren't in here,
    since they already size correctly off the tenant's own risk settings. Public, no auth: this
    is static reference metadata, not a live quote feed.
    """
    return list_contract_specs()


@router.get("/api/instruments/{symbol}", response_model=ContractSpec)
def get_instrument(symbol: str) -> ContractSpec:
    spec = get_contract_spec(symbol)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"No contract spec registered for '{symbol.upper()}'")
    return spec
