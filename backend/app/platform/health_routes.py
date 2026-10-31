"""`GET /api/system/health`: the API's liveness answer.

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/system/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
