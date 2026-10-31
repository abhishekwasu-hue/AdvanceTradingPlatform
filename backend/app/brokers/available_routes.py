"""`GET /api/broker/available`: the broker ids the abstraction layer can adapt to.

M55 Q1: moved from app/main.py (which now only includes routers) as it was - only the annotations are in built-in
generics; included at the end of the router list in the same order as before, so every path, method, dependency
and the match order are unchanged (tests/test_m55_q1_route_table.py).
"""

from fastapi import APIRouter

from app.brokers.registry import available_brokers

router = APIRouter()


@router.get("/api/broker/available")
def list_available_brokers() -> dict[str, list[str]]:
    """Broker ids the abstraction layer can adapt to. Storing credentials and authenticating
    against one requires a logged-in user - see /api/broker/{name}/credentials and
    /api/broker/{name}/authenticate - credentials are encrypted at rest, never in plaintext.
    """
    return {"brokers": available_brokers()}
