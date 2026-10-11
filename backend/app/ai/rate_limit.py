"""H-C1 b: rate limit on the AI endpoints (`/api/ai/*`, `/api/scanner/ai/*`), per user and per organisation.

Each call costs units: 1 by default, more for the heavy jobs (a strategist build or an interview plan walks
strategies bar by bar and may call the LLM several times). The units a user and an organisation may spend in one
window depend on the organisation's plan. Limits, weights and the window are config (`AI_RATE_LIMITS`,
`AI_RATE_WEIGHTS`, `AI_RATE_WINDOW_SECONDS`), never code.

Counting uses `app.core.rate_limit`: Redis when it is configured (shared by every API replica), else an in-process
window. The organisation is checked first, so a user who is refused by the organisation's limit does not also
spend their own units. A refusal is 429 with `Retry-After`.

Calls without a logged-in user are not counted here: the route's own auth answers them (401), and the only open
AI route is a static list.
"""
import re
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user_optional
from app.core import config
from app.core.rate_limit import allow
from app.db.models import Tenant, User
from app.db.session import get_session
from app.plans.registry import DEFAULT_PLAN_ID

NAME = "ai"


def limits_for(plan_id: Optional[str]) -> Dict[str, int]:
    """{"user": units, "tenant": units} per window for a plan; an unknown plan gets the default plan's limits."""
    table = config.AI_RATE_LIMITS
    row = table.get((plan_id or "").lower()) or table.get(DEFAULT_PLAN_ID) or {"user": 0, "tenant": 0}
    return {"user": int(row.get("user", 0)), "tenant": int(row.get("tenant", 0))}


def _route_key(request: Request) -> str:
    """The route template after the AI prefix ("strategist/build", "drafts/{draft_id}/backtest")."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    return re.sub(r"^/api/(v1/)?(scanner/)?ai/?", "", path)


def units_for(request: Request) -> int:
    weights = config.AI_RATE_WEIGHTS
    key = _route_key(request)
    return max(1, int(weights.get(f"{request.method} {key}", weights.get(key, 1))))


def _refused(scope: str) -> HTTPException:
    from app.observability.metrics import AI_RATE_LIMITED
    AI_RATE_LIMITED.labels(scope=scope).inc()
    who = "your organisation's" if scope == "tenant" else "your"
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                         detail=f"Too many AI requests: {who} limit for this minute is used up. Please try again shortly.",
                         headers={"Retry-After": str(int(config.AI_RATE_WINDOW_SECONDS))})


async def ai_rate_limit(request: Request, user: Optional[User] = Depends(get_current_user_optional),
                        session: AsyncSession = Depends(get_session)) -> None:
    if user is None:
        return
    tenant = await session.get(Tenant, user.tenant_id)
    limits = limits_for(tenant.plan if tenant else None)
    units, window = units_for(request), float(config.AI_RATE_WINDOW_SECONDS)
    if limits["tenant"] > 0 and not await allow(f"{NAME}_t", f"t{user.tenant_id}", limits["tenant"], window, units=units):
        raise _refused("tenant")
    if limits["user"] > 0 and not await allow(f"{NAME}_u", f"u{user.id}", limits["user"], window, units=units):
        raise _refused("user")

