"""M55 S1 (owner order): LIVE can be switched on only by the owner.

LIVE-1 (owner order, binding): `live_trading` is the ONE live switch (app/platform/live_switch.py). Turning it on
(globally) is the only change that opens the way to LIVE; the former PAPER-only lock `phase0_paper_only` follows it and is
not set on its own (controls.set_flag refuses it). Turning `live_trading` on is allowed only when:
1. the caller is the owner identity, the account `LIVE_OWNER_USER_ID` names (app/core/config.py, default 1);
2. this session passed a two-factor check recently (the same check the admin routes use, `MFA_STEP_UP_MINUTES`);
3. the request carries the typed confirmation `CONFIRM_TEXT`.
Any other SUPER_ADMIN gets a 403 with a plain reason. Every attempt, allowed or refused, writes one audit row (committed
before a refusal is raised). The restrictive direction (`live_trading` off) is never held up here.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.db.models import User

LIVE_TRADING = "live_trading"
PAPER_ONLY = "phase0_paper_only"
OWNER_ONLY_FLAGS = (LIVE_TRADING, PAPER_ONLY)
CONFIRM_TEXT = "ENABLE LIVE"

ALLOWED_EVENT = "live_owner_change_allowed"
REFUSED_EVENT = "live_owner_change_refused"

NOT_OWNER = "Only the owner can switch LIVE on."
NO_CONFIRM = f'Type "{CONFIRM_TEXT}" to confirm switching LIVE on.'


class LiveOwnerRefused(Exception):
    """A refused LIVE-opening change; `status_code` is what the route answers."""

    def __init__(self, detail: str, status_code: int = 403) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def owner_user_id() -> int:
    from app.core import config
    return int(config.LIVE_OWNER_USER_ID)


def is_owner(user: User | None) -> bool:
    """The configured owner account, active and a platform administrator (the only role that reaches Admin -> Controls)."""
    from app.core.enums import UserRole
    return (user is not None and user.id is not None and user.id == owner_user_id()
            and user.role == UserRole.SUPER_ADMIN.value and user.is_active is not False)


def opens_live(name: str, *, on: bool, tenants: list[int] | None) -> bool:
    """True when this flag change opens a way to LIVE: `live_trading` on (or an organisation list, kept owner-only as
    before). LIVE-1: `phase0_paper_only` opens nothing any more - it follows `live_trading` and set_flag refuses it."""
    if name == LIVE_TRADING:
        return bool(on or tenants)
    return False


async def _audit(session: AsyncSession, user: User, event: str, what: str) -> None:
    await write_audit_log(session, None, user.id, event, what)
    await session.commit()


async def ensure_may_open_live(session: AsyncSession, user: User, name: str, *, on: bool, tenants: list[int] | None,
                               session_id: int | None, confirm: str | None) -> None:
    """Raises LiveOwnerRefused (or the MFA step-up refusal) unless the owner may make this change; audits either way.
    A change that does not open LIVE returns at once."""
    if not opens_live(name, on=on, tenants=tenants):
        return
    what = f"{name}: on={on} tenants={sorted(int(t) for t in tenants or []) or '-'}"
    await _ensure_owner(session, user, what, session_id=session_id, confirm=confirm, why="Switching LIVE on",
                        not_owner=NOT_OWNER, no_confirm=NO_CONFIRM)


async def _ensure_owner(session: AsyncSession, user: User, what: str, *, session_id: int | None, confirm: str | None,
                        why: str, not_owner: str, no_confirm: str) -> None:
    """The owner identity, a recent two-factor check and the typed confirmation - or an audited refusal."""
    if not is_owner(user):
        await _audit(session, user, REFUSED_EVENT, f"{what} refused - not the owner")
        raise LiveOwnerRefused(not_owner)
    from fastapi import HTTPException

    from app.auth.dependencies import ensure_mfa_session
    from app.core.config import MFA_STEP_UP_MINUTES
    try:
        await ensure_mfa_session(session, user, session_id, why, within_minutes=MFA_STEP_UP_MINUTES)
    except HTTPException:
        await _audit(session, user, REFUSED_EVENT, f"{what} refused - no recent two-factor check")
        raise
    if (confirm or "").strip() != CONFIRM_TEXT:
        await _audit(session, user, REFUSED_EVENT, f"{what} refused - typed confirmation missing")
        raise LiveOwnerRefused(no_confirm, status_code=400)
    await _audit(session, user, ALLOWED_EVENT, f"{what} allowed - owner, two-factor checked, confirmed")


# --- M55 S1b (owner decision with #744): the per-organisation LIVE grant ------------------------------------------------
# LIVE-1 (owner order: ONE live switch): the grant no longer decides anything. B1 (owner 14 Nov): `live_trading` ON is
# LIVE through ATP's server for the OWNER'S organisation only; OFF is PAPER only for everyone. The organisation list of
# the flag is kept as stored (the owner-only, audited set_org_grant below still writes it, nothing is deleted), but it
# does NOT open server LIVE for any other organisation: `org_live_open` reads only the switch and the owner's
# organisation (app/platform/live_switch.py).
GRANTED_EVENT = "live_org_granted"
REVOKED_EVENT = "live_org_revoked"
NOT_OWNER_GRANT = "Only the owner can grant or revoke LIVE for an organisation."
NO_CONFIRM_GRANT = f'Type "{CONFIRM_TEXT}" to confirm the LIVE change for this organisation.'
GRANT_VIA_LIST = 'Grant LIVE to an organisation under "LIVE for organisations", not with the switch.'


async def granted_orgs(session: AsyncSession) -> list[int]:
    """The organisations the owner granted LIVE (sorted ids; empty by default)."""
    from app.platform.controls import feature_flags
    return list((await feature_flags(session)).get(LIVE_TRADING, {}).get("tenants") or [])


async def org_live_open(session: AsyncSession, tenant_id: int | None) -> bool:
    """LIVE-1 / B1: True when the one live switch (`live_trading`) is ON and `tenant_id` is the owner's organisation -
    `live_switch.live_allowed`, read fresh (it also refreshes the lock state the broker chokepoint reads). The grant list
    is not read. The name stays for its callers (the F&O ticket, the plan check)."""
    if tenant_id is None:
        return False
    from app.platform import live_switch
    return await live_switch.live_allowed(session, int(tenant_id))


async def set_org_grant(session: AsyncSession, user: User, tenant_id: int, *, granted: bool, session_id: int | None,
                        confirm: str | None) -> list[int]:
    """Grant (or revoke) LIVE for one organisation - the owner only, two-factor checked, typed confirmation; audited
    either way. Raises LiveOwnerRefused (or the step-up refusal), or ValueError for an unknown organisation. Returns the
    granted list. Only the list changes: the global switch keeps its stored (or default) value. B1: the list is stored and
    shown only - it does not open LIVE through ATP's server for the organisation (only the owner's organisation has it)."""
    from app.db.models import Tenant
    from app.platform import controls
    tid = int(tenant_id)
    what = f"{LIVE_TRADING}: {'grant' if granted else 'revoke'} organisation {tid}"
    await _ensure_owner(session, user, what, session_id=session_id, confirm=confirm,
                        why="Changing LIVE for an organisation", not_owner=NOT_OWNER_GRANT, no_confirm=NO_CONFIRM_GRANT)
    if granted and await session.get(Tenant, tid) is None:
        await _audit(session, user, REFUSED_EVENT, f"{what} refused - unknown organisation")
        raise ValueError(f"Unknown organisation {tid}")
    resolved = (await controls.feature_flags(session)).get(LIVE_TRADING) or {}
    current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
    flags = dict(current.get("flags") or {})
    tenants = {int(t) for t in resolved.get("tenants") or []}
    tenants = tenants | {tid} if granted else tenants - {tid}
    flags[LIVE_TRADING] = {"on": bool(resolved.get("on", False)), "tenants": sorted(tenants)}
    await controls._set(session, controls.KEY_FEATURE_FLAGS, {**current, "flags": flags}, user)
    await write_audit_log(session, None, user.id, GRANTED_EVENT if granted else REVOKED_EVENT,
                          f"organisation {tid}; granted now: {sorted(tenants) or '-'}")
    await session.commit()
    return sorted(tenants)
