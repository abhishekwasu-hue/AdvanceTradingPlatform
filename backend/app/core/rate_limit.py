"""Per-IP request rate limiting (master prompt Section 48: "rate limiting/circuit breakers").
Applied first to the auth endpoints (`/api/auth/register`, `/api/auth/login`) - the two routes
brute-force/credential-stuffing/registration-spam attacks actually target, and the two that used
to accept unlimited attempts from a single caller.

v1, in-process, sliding-window limiter - correct for the current single-process deployment (see
docs/ARCHITECTURE.md's own note that a multi-worker/multi-instance deployment is future work), but
each process keeps its own independent counters, so a real multi-instance rollout needs a shared
store (Redis - already optional infra here, see app/cache/client.py) for a limit to hold across
instances rather than being trivially bypassable by hitting a different instance. This limitation
is deliberate, not hidden: it's the same honest v1-scoping this codebase applies everywhere else
(see the notification read-marker and hash-chain concurrency notes for the same pattern).

Trusts `request.client.host` directly rather than an `X-Forwarded-For` header, since trusting a
client-supplied header without first validating it came from a known, trusted reverse proxy is a
well-known way to make a rate limiter trivially bypassable (an attacker just sets the header
themselves). A real deployment behind a reverse proxy/load balancer should configure it so
`request.client.host` already reflects the real client (e.g. Uvicorn's `--proxy-headers` with an
explicit `--forwarded-allow-ips`), not add header-trusting logic here.
"""
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Tuple

from fastapi import HTTPException, Request, status

_WINDOWS: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def rate_limit(name: str, limit: int, window_seconds: float) -> Callable[[Request], None]:
    """Dependency factory: `Depends(rate_limit("auth_login", limit=10, window_seconds=60))`.
    Raises 429 once a caller IP has made `limit` calls to this named limiter within the trailing
    `window_seconds`. `name` distinguishes independent limiters (e.g. register vs. login) sharing
    the same module-level state.
    """

    def _check(request: Request) -> None:
        key = (name, request.client.host if request.client else "unknown")
        now = time.monotonic()
        bucket = _WINDOWS[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, please try again later.",
            )
        bucket.append(now)

    return _check
