"""Phase N2 / V4.12: fine-grained scopes layered over the role matrix.

Roles stay the coarse model everyone understands (OWNER, USER, STRATEGY_CREATOR, VIEWER, plus the
platform roles). Each role maps to a default set of scopes; a tenant OWNER can then *deny* a
member individual scopes (or *grant* one the role lacks, within what the owner's own role has) via
`users.scope_overrides`. Scopes are checked by `require_scope(...)`, and the two historic gates
(`require_trader`, `require_owner`) now also honour a denied `trading:write` / `team:manage`, so a
denial actually bites everywhere without every route changing.

Rules:
* SUPER_ADMIN has every scope and cannot be overridden (platform staff are managed outside tenants).
* Overrides never grant `admin:platform` - that scope is the SUPER_ADMIN role itself.
* Exits are never gated by a scope (safety: an exit must always be possible).
"""
import json
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.enums import UserRole
from app.db.models import User
from app.db.session import get_session

# scope -> human description (drives the Team page editor and /api/auth/scopes)
SCOPES: Dict[str, str] = {
    "trading:write": "Place, configure and stop PAPER/LIVE trading (deployments, signals, kill switches)",
    "trading:live": "Create or resume LIVE deployments (in addition to trading:write)",
    "strategies:write": "Create and edit custom strategies and their versions",
    "brokers:write": "Store, refresh or remove broker credentials and accounts",
    "alerts:write": "Manage alert channels and notification settings",
    "risk:write": "Edit risk limits at tenant/user/account/strategy scopes",
    "billing:manage": "Change the plan, pay invoices, see billing history",
    "team:manage": "Invite, remove and change roles of team members",
    "api_keys:manage": "Create and revoke public API keys",
    "marketplace:publish": "List strategies on the marketplace",
    "ai:manage": "Configure the AI provider and approve AI drafts/actions",
    "analytics:read": "Read positions, trades, analytics and reports",
    "admin:platform": "Platform administration (SUPER_ADMIN only)",
}

_ALL = frozenset(SCOPES)
_TENANT_ALL = frozenset(s for s in SCOPES if s != "admin:platform")

ROLE_SCOPES: Dict[str, FrozenSet[str]] = {
    UserRole.SUPER_ADMIN.value: _ALL,
    UserRole.OWNER.value: _TENANT_ALL,
    UserRole.USER.value: frozenset({"trading:write", "trading:live", "strategies:write", "brokers:write", "alerts:write",
                                    "risk:write", "api_keys:manage", "marketplace:publish", "ai:manage", "analytics:read"}),
    UserRole.STRATEGY_CREATOR.value: frozenset({"trading:write", "trading:live", "strategies:write", "alerts:write",
                                                "marketplace:publish", "ai:manage", "analytics:read"}),
    UserRole.VIEWER.value: frozenset({"analytics:read"}),
    UserRole.SUPPORT.value: frozenset({"analytics:read"}),
}


def parse_overrides(raw: Optional[str]) -> Dict[str, List[str]]:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return {"deny": sorted(set(data.get("deny") or []) & _TENANT_ALL), "grant": sorted(set(data.get("grant") or []) & _TENANT_ALL)}


def scopes_for(user: User) -> List[str]:
    base = ROLE_SCOPES.get(user.role, frozenset())
    if user.role == UserRole.SUPER_ADMIN.value:
        return sorted(base)
    overrides = parse_overrides(user.scope_overrides)
    return sorted((base | set(overrides["grant"])) - set(overrides["deny"]))


def has_scope(user: User, scope: str) -> bool:
    return scope in scopes_for(user)


def set_overrides(member: User, actor: User, *, deny: Iterable[str], grant: Iterable[str]) -> Dict[str, List[str]]:
    """Validates and stores overrides. Grants are capped at what the actor's role holds, so an
    OWNER cannot hand out more than an owner has; unknown scopes are rejected."""
    deny_set, grant_set = set(deny), set(grant)
    unknown = (deny_set | grant_set) - _TENANT_ALL
    if unknown:
        raise ValueError(f"Unknown or non-overridable scopes: {sorted(unknown)}")
    if deny_set & grant_set:
        raise ValueError("A scope cannot be both denied and granted")
    actor_scopes = set(scopes_for(actor))
    if not grant_set <= actor_scopes:
        raise ValueError(f"You cannot grant scopes you do not hold yourself: {sorted(grant_set - actor_scopes)}")
    overrides = {"deny": sorted(deny_set), "grant": sorted(grant_set)}
    member.scope_overrides = json.dumps(overrides) if (deny_set or grant_set) else None
    return overrides


def scope_denied(user: User, scope: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission '{scope}' - ask your tenant owner",
                         headers={"X-Missing-Scope": scope})


def require_scope(scope: str, active_tenant: bool = True) -> Callable[..., User]:
    """Dependency: `Depends(require_scope("brokers:write"))`. SUPER_ADMIN passes always."""
    if scope not in SCOPES:
        raise KeyError(f"Unknown scope {scope}")

    async def _check(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
        if not has_scope(user, scope):
            raise scope_denied(user, scope)
        if active_tenant and user.role != UserRole.SUPER_ADMIN.value:
            from app.plans.limits import ensure_tenant_active, load_tenant
            ensure_tenant_active(await load_tenant(session, user.tenant_id))
        return user

    return _check


def catalogue() -> List[Dict[str, object]]:
    return [{"scope": s, "description": d, "roles": sorted(r for r, scopes in ROLE_SCOPES.items() if s in scopes)}
            for s, d in SCOPES.items()]
