"""Phase M / V4.13 endpoints: public status, admin controls, per-user trading disable."""
import copy
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth import scopes
from app.auth.dependencies import current_session_id, ensure_admin_write_step_up, get_current_user, require_mfa_session, require_owner, require_role
from app.core.enums import UserRole
from app.db.models import User
from app.db.session import get_session
from app.platform import controls

status_router = APIRouter(prefix="/api/system", tags=["system"])
admin_router = APIRouter(prefix="/api/admin/controls", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])
users_router = APIRouter(prefix="/api/team/members", tags=["team"])


class MaintenanceBody(BaseModel):
    on: bool
    message: Optional[str] = Field(default=None, max_length=300)


class BrokersBody(BaseModel):
    names: List[str] = Field(default_factory=list, max_length=20)


class FlagBody(BaseModel):
    on: bool
    tenants: List[int] = Field(default_factory=list, max_length=500)
    # Z10-f: `legal_pages` is refused while a legal document has an open [PLACEHOLDER]; true switches it on anyway (audited).
    override_open_placeholders: bool = False
    # M55 S1: switching LIVE on (`live_trading` on, the PAPER-only lock off) needs the owner to type "ENABLE LIVE".
    confirm: str | None = Field(default=None, max_length=40)


class ScopeOverridesBody(BaseModel):
    deny: List[str] = Field(default_factory=list, max_length=20)
    grant: List[str] = Field(default_factory=list, max_length=20)


class TradingDisableBody(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class RiskCeilingsBody(BaseModel):
    values: Dict[str, float] = Field(default_factory=dict, max_length=10)


# LD-7: every open page polls the public status (7 queries: two controls, five holiday lists), and the answer is the same
# for everyone. Each API process keeps it for a few seconds; an admin change made through this process drops it at once,
# the other processes follow within the TTL. Display only: the maintenance gate itself (entries) reads the control.
STATUS_CACHE_SECONDS = 5.0
_status_cache: Dict[str, Any] = {}


def forget_status() -> None:
    _status_cache.clear()


@status_router.get("/status")
async def system_status(session: AsyncSession = Depends(get_session)) -> dict:
    """Unauthenticated: the frontend banner reads it before login too."""
    hit = _status_cache.get("body")
    if hit is not None and time.monotonic() - _status_cache["at"] < STATUS_CACHE_SECONDS:
        return copy.deepcopy(hit)
    from app.market_data.calendar import all_session_statuses
    result = await controls.status(session)
    result["sessions"] = {name: {"is_open": st.is_open, "reason": st.reason, "next_open": st.next_open.isoformat() if st.next_open else None}
                          for name, st in (await all_session_statuses(session)).items()}
    _status_cache.update(at=time.monotonic(), body=copy.deepcopy(result))
    return result


@admin_router.get("")
async def get_controls(session: AsyncSession = Depends(get_session)) -> dict:
    return await controls.status(session)


@admin_router.put("/maintenance")
async def put_maintenance(body: MaintenanceBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    forget_status()
    try:
        return await controls.set_maintenance(session, user, on=body.on, message=body.message)
    finally:
        forget_status()


@admin_router.put("/brokers")
async def put_brokers(body: BrokersBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    forget_status()
    try:
        return await controls.set_disabled_brokers(session, user, body.names)
    finally:
        forget_status()


@admin_router.get("/risk-ceilings")
async def get_risk_ceilings(session: AsyncSession = Depends(get_session)) -> dict:
    """Phase V1: the hard ceilings every tenant's risk settings stay under."""
    return await controls.risk_ceilings(session)


@admin_router.put("/risk-ceilings")
async def put_risk_ceilings(body: RiskCeilingsBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await controls.set_risk_ceilings(session, user, body.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.get("/marketplace-terms")
async def get_marketplace_terms(session: AsyncSession = Depends(get_session)) -> dict:
    """Phase X: platform fee, minimum payout and price cap for paid marketplace listings."""
    return await controls.marketplace_terms(session)


@admin_router.put("/marketplace-terms")
async def put_marketplace_terms(body: RiskCeilingsBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await controls.set_marketplace_terms(session, user, body.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AiPlatformLimitsBody(BaseModel):
    """M72 PR-1b: the owner's platform-key limits in rupees. A plan's value null = clear it (Not set); `clear_cap` clears the
    per-user daily cap. Fields left out keep their value."""
    plan_budget_inr: dict[str, float | None] = Field(default_factory=dict, max_length=10)
    user_daily_cap_inr: float | None = None
    clear_cap: bool = False


async def _ai_platform_view(session: AsyncSession) -> dict:
    from app.ai import platform_key
    from app.plans.registry import PLANS
    values = await platform_key.limits(session)
    # The key itself never leaves the host: only whether one is there and for which provider.
    return {**values, "key_configured": platform_key.configured_provider() is not None, "provider": platform_key.configured_provider(),
            "plans": [{"id": plan.id, "name": plan.name} for plan in PLANS.values()]}


@admin_router.get("/ai-platform")
async def get_ai_platform(session: AsyncSession = Depends(get_session)) -> dict:
    """M72 PR-1b (M72Q-1): each plan's monthly platform AI budget and the per-user daily cap (None = Not set)."""
    return await _ai_platform_view(session)


@admin_router.put("/ai-platform")
async def put_ai_platform(body: AiPlatformLimitsBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    from app.ai import platform_key
    try:
        await platform_key.set_limits(session, user, plan_budget_inr=body.plan_budget_inr, user_daily_cap_inr=body.user_daily_cap_inr,
                                      clear_cap=body.clear_cap)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _ai_platform_view(session)


# --- Phase N4: feature flags -----------------------------------------------------------------------

async def _flag_listing_for(session: AsyncSession, user: User) -> dict:
    """The flag listing; the two LIVE switches also say they are owner-only and whether this admin is the owner (M55 S1)."""
    from app.platform import live_owner
    flags = await controls.flag_listing(session)
    owner = live_owner.is_owner(user)
    for name in live_owner.OWNER_ONLY_FLAGS:
        if name in flags:
            flags[name].update(owner_only=True, can_switch_live=owner, confirm_text=live_owner.CONFIRM_TEXT,
                               opens_live_when_on=name == live_owner.LIVE_TRADING)
    if live_owner.LIVE_TRADING in flags:
        # M55 S1b: "LIVE for organisations" - the owner's grant list (the flag's allow-list), with the names.
        from sqlalchemy import select

        from app.db.models import Tenant
        ids = list(flags[live_owner.LIVE_TRADING].get("tenants") or [])
        names = dict((await session.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(ids)))).all()) if ids else {}
        flags[live_owner.LIVE_TRADING]["live_orgs"] = [{"id": i, "name": names.get(i) or f"Organisation {i}"} for i in ids]
    return flags


@admin_router.get("/flags")
async def get_flags(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """Every flag's state, plus (UX1, audit section 7) its plain description, area, what turns on, what it needs,
    its default and status - for the admin Feature flags list (SUPER_ADMIN + two-factor, the router's dependencies)."""
    return await _flag_listing_for(session, user)


@admin_router.put("/flags/{name}")
async def put_flag(name: str, body: FlagBody, user: User = Depends(require_role()),
                   session_id: int | None = Depends(current_session_id), session: AsyncSession = Depends(get_session)) -> dict:
    from app.platform.live_owner import LiveOwnerRefused
    try:
        await controls.set_flag(session, user, name, on=body.on, tenants=body.tenants,
                                override_open_placeholders=body.override_open_placeholders,
                                session_id=session_id, confirm=body.confirm)
    except LiveOwnerRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if name == "public_status_page":                                     # Z10-e: the public page follows at once
        from app.observability.status_page import reset_cache
        reset_cache()
    if name in ("status_page", "public_status_page"):                    # AUD-T8: the sessions block follows at once
        from app.observability import status_history
        status_history.reset_cache()
    return await _flag_listing_for(session, user)                       # UX1: the same fields as the list


class LiveOrgBody(BaseModel):
    granted: bool
    confirm: str | None = Field(default=None, max_length=40)


@admin_router.put("/live-orgs/{tenant_id}")
async def put_live_org(tenant_id: int, body: LiveOrgBody, user: User = Depends(require_role()),
                       session_id: int | None = Depends(current_session_id), session: AsyncSession = Depends(get_session)) -> dict:
    """M55 S1b: grant (granted = true) or revoke LIVE for one organisation - the owner only, a recent two-factor check
    and the typed confirmation; audited either way. Answers the flag listing (the same fields as GET /flags)."""
    from app.platform.live_owner import LiveOwnerRefused, set_org_grant
    try:
        await set_org_grant(session, user, tenant_id, granted=body.granted, session_id=session_id, confirm=body.confirm)
    except LiveOwnerRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _flag_listing_for(session, user)


# --- Owner decision 23 Oct: "Enable trader features" (app/platform/trader_pack.py) -------------------------------------
# SUPER_ADMIN + a two-factor-verified session (the router's dependencies). The A4 admin write step-up
# (`ensure_admin_write_step_up`) is not on main yet; when it lands, the two POSTs below take it like every admin write.

@admin_router.get("/trader-pack")
async def get_trader_pack(session: AsyncSession = Depends(get_session)) -> dict:
    """Per pack flag: ON / OFF, the plain description and a cheap "working" check; whether Undo has something to restore."""
    from app.platform import trader_pack
    return await trader_pack.status(session)


@admin_router.post("/trader-pack")
async def enable_trader_pack(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """Turn every trader flag ON (audited, idempotent, never a LIVE / Phase 0 / owner-decision flag)."""
    from app.platform import trader_pack
    result = await trader_pack.enable(session, user)
    return {**result, **await trader_pack.status(session)}


@admin_router.post("/trader-pack/undo")
async def undo_trader_pack(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """Restore exactly the stored flag state from before the enable (nothing saved: changes nothing)."""
    from app.platform import trader_pack
    result = await trader_pack.undo(session, user)
    return {**result, **await trader_pack.status(session)}


@status_router.get("/features")
async def my_features(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Which optional features are on for the caller's tenant (the UI hides what is off)."""
    return await controls.features_for_tenant(session, user.tenant_id)


@status_router.get("/encryption", dependencies=[Depends(require_mfa_session)])   # A4 / R5 b
async def encryption_status(user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase N1 (admin): how many secrets still sit under the master key vs tenant data keys."""
    from app.secrets_store import envelope
    return await envelope.status(session)


# --- Phase N2: per-member scope overrides ---------------------------------------------------------

@users_router.get("/{member_id}/scopes")
async def get_member_scopes(member_id: int, actor: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    member = await _member(session, member_id, actor)
    return {"id": member.id, "role": member.role, "scopes": scopes.scopes_for(member), "overrides": scopes.parse_overrides(member.scope_overrides)}


@users_router.put("/{member_id}/scopes")
async def put_member_scopes(member_id: int, body: ScopeOverridesBody, actor: User = Depends(require_owner),
                            session: AsyncSession = Depends(get_session),
                            session_id: Optional[int] = Depends(current_session_id)) -> dict:
    member = await _member_for_write(session, member_id, actor, session_id, "Changing the permissions of another organisation's member")
    if member.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=403, detail="Platform administrators are managed outside the tenant")
    if member.id == actor.id and "team:manage" in body.deny:
        raise HTTPException(status_code=409, detail="You cannot remove your own team:manage permission")
    try:
        overrides = scopes.set_overrides(member, actor, deny=body.deny, grant=body.grant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit_log(session, member.tenant_id, actor.id, "member_scopes_changed", f"{member.email}: {overrides}")
    await session.commit()
    return {"id": member.id, "role": member.role, "scopes": scopes.scopes_for(member), "overrides": overrides}


async def _member(session: AsyncSession, member_id: int, actor: User) -> User:
    member = await session.get(User, member_id)
    if member is None or (member.tenant_id != actor.tenant_id and actor.role != UserRole.SUPER_ADMIN.value):
        raise HTTPException(status_code=404, detail="No such member")
    return member


async def _member_for_write(session: AsyncSession, member_id: int, actor: User, session_id: Optional[int], why: str) -> User:
    """A4 review: a SUPER_ADMIN changing a member of ANOTHER organisation is a platform-wide write, so it gets the admin
    step-up (two-factor, recent); an owner acting inside their own organisation is unchanged."""
    member = await _member(session, member_id, actor)
    if member.tenant_id != actor.tenant_id:
        await ensure_admin_write_step_up(session, actor, session_id, why)
    return member


@users_router.post("/{member_id}/trading-disable")
async def disable_trading(member_id: int, body: TradingDisableBody, actor: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
                          session_id: Optional[int] = Depends(current_session_id)) -> dict:
    member = await _member_for_write(session, member_id, actor, session_id, "Pausing trading for another organisation's member")
    member.trading_disabled_reason = body.reason.strip()
    await write_audit_log(session, member.tenant_id, actor.id, "user_trading_disabled", f"{member.email}: {body.reason}")
    await session.commit()
    return {"id": member.id, "email": member.email, "trading_disabled_reason": member.trading_disabled_reason}


@users_router.post("/{member_id}/trading-enable")
async def enable_trading(member_id: int, actor: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
                         session_id: Optional[int] = Depends(current_session_id)) -> dict:
    member = await _member_for_write(session, member_id, actor, session_id, "Turning trading back on for another organisation's member")
    member.trading_disabled_reason = None
    await write_audit_log(session, member.tenant_id, actor.id, "user_trading_enabled", member.email)
    await session.commit()
    return {"id": member.id, "email": member.email, "trading_disabled_reason": None}


@users_router.post("/{member_id}/verify-email", dependencies=[Depends(require_mfa_session)])   # A4 / R5 b
async def admin_verify_email(member_id: int, actor: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase N3 (platform admin): stamp an address verified when no mailer is configured yet."""
    from app.auth.verification import mark_verified
    member = await _member(session, member_id, actor)
    mark_verified(member)
    await write_audit_log(session, member.tenant_id, actor.id, "email_verified_by_admin", member.email)
    await session.commit()
    return {"id": member.id, "email": member.email, "email_verified": True}


@users_router.get("/me/trading-status")
async def my_trading_status(user: User = Depends(get_current_user)) -> dict:
    return {"trading_disabled_reason": user.trading_disabled_reason}
