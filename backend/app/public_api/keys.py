"""Phase K3: public API keys (master prompt V3.11-3.12, V3.14 rules 9-10).

A key is `atp_<prefix>_<secret>`: the prefix is stored to find the row, the whole key is stored
as a SHA-256 hash, and the plaintext is shown exactly once at creation. Keys carry scopes
(`read:*` / `write:signals`), a per-minute rate limit, an optional expiry, and can be revoked.
`api_key_auth(scope)` is the dependency the `/api/public/v1/*` routes use; it also meters one
`api_call` per request against the tenant's plan allowance (`max_api_calls_per_day`).
"""
import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.service import meter, usage_today
from app.db.models import ApiKeyRecord, Tenant, User
from app.db.session import get_session
from app.plans.limits import feature_allowed
from app.plans.registry import get_plan

SCOPES = {
    "read:account": "profile, plan and usage",
    "read:instruments": "instrument master search, expiries, strikes",
    "read:strategies": "inbuilt and custom strategies",
    "read:signals": "signal history",
    "read:orders": "orders and their events",
    "read:positions": "open positions and trade history",
    "read:backtests": "saved backtest runs",
    "read:risk": "risk limits and events",
    "write:signals": "submit a signal for paper/live execution (idempotent)",
}
KEY_PREFIX = "atp"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> Tuple[str, str, str]:
    """(plaintext key, prefix, hash)."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    key = f"{KEY_PREFIX}_{prefix}_{secret}"
    return key, prefix, _hash(key)


def parse_scopes(text: str) -> List[str]:
    return [s for s in (text or "").split(",") if s]


class _Limiter:
    def __init__(self) -> None:
        self._hits: Dict[int, Deque[float]] = defaultdict(deque)

    def allow(self, key_id: int, per_minute: int, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.monotonic()
        bucket = self._hits[key_id]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= per_minute:
            return False
        bucket.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


limiter = _Limiter()


class ApiPrincipal:
    def __init__(self, key: ApiKeyRecord, user: User, tenant: Tenant) -> None:
        self.key = key
        self.user = user
        self.tenant = tenant
        self.scopes = parse_scopes(key.scopes)


async def resolve_key(session: AsyncSession, raw: str) -> Optional[ApiKeyRecord]:
    parts = raw.split("_", 2)   # the secret part may itself contain "_" (urlsafe base64)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    record = await session.scalar(select(ApiKeyRecord).where(ApiKeyRecord.key_prefix == parts[1]))
    if record is None or record.key_hash != _hash(raw):
        return None
    return record


def api_key_auth(scope: str):
    """Dependency factory: `Depends(api_key_auth("read:positions"))`."""
    async def _check(request: Request, session: AsyncSession = Depends(get_session),
                     x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> ApiPrincipal:
        if not x_api_key:
            raise HTTPException(status_code=401, detail="X-API-Key header required")
        record = await resolve_key(session, x_api_key.strip())
        now = datetime.now(timezone.utc)
        if record is None or record.revoked_at is not None:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        if record.expires_at is not None and (record.expires_at if record.expires_at.tzinfo else record.expires_at.replace(tzinfo=timezone.utc)) <= now:
            raise HTTPException(status_code=401, detail="API key expired")
        if scope not in parse_scopes(record.scopes):
            raise HTTPException(status_code=403, detail=f"API key lacks scope '{scope}'")
        tenant = await session.get(Tenant, record.tenant_id)
        from app.platform.controls import require_flag
        await require_flag(session, "public_api", record.tenant_id)  # Phase N4 operator kill flag
        if not feature_allowed(tenant, "public_api"):
            raise HTTPException(status_code=402, detail="Public API access needs the Pro or Business plan (or the organisation is suspended)")
        user = await session.get(User, record.user_id) if record.user_id else None
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="API key owner is no longer active")
        if not limiter.allow(record.id, record.rate_limit_per_minute):
            raise HTTPException(status_code=429, detail=f"Rate limit: {record.rate_limit_per_minute} requests per minute for this key")
        plan = get_plan(tenant.plan)
        if plan.max_api_calls_per_day and await usage_today(session, tenant.id, "api_call") >= plan.max_api_calls_per_day:
            raise HTTPException(status_code=429, detail=f"Daily public API allowance ({plan.max_api_calls_per_day}) used up")
        record.last_used_at = now
        await meter(session, tenant.id, "api_call", 1, source="public_api", metadata={"path": request.url.path, "key": record.id})
        from app.secrets_store.envelope import ensure_tenant_key
        await ensure_tenant_key(session, tenant.id)  # Phase N1
        return ApiPrincipal(record, user, tenant)
    return _check
