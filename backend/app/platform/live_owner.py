"""M55 S1 (owner order): LIVE can be switched on only by the owner.

Two changes open the way to LIVE: turning the `live_trading` flag on (globally or for any organisation) and turning
the `phase0_paper_only` lock off (any state that is not "on for everyone"). Each is allowed only when:
1. the caller is the owner identity, the account `LIVE_OWNER_USER_ID` names (app/core/config.py, default 1);
2. this session passed a two-factor check recently (the same check the admin routes use, `MFA_STEP_UP_MINUTES`);
3. the request carries the typed confirmation `CONFIRM_TEXT`.
Any other SUPER_ADMIN gets a 403 with a plain reason. Every attempt, allowed or refused, writes one audit row (committed
before a refusal is raised). The restrictive direction (`live_trading` off, the lock on) is never held up here.
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
    """True when this flag change opens a way to LIVE: `live_trading` on for anyone, or the lock not on for everyone."""
    if name == LIVE_TRADING:
        return bool(on or tenants)
    if name == PAPER_ONLY:
        return not on
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
    if not is_owner(user):
        await _audit(session, user, REFUSED_EVENT, f"{what} refused - not the owner")
        raise LiveOwnerRefused(NOT_OWNER)
    from fastapi import HTTPException

    from app.auth.dependencies import ensure_mfa_session
    from app.core.config import MFA_STEP_UP_MINUTES
    try:
        await ensure_mfa_session(session, user, session_id, "Switching LIVE on", within_minutes=MFA_STEP_UP_MINUTES)
    except HTTPException:
        await _audit(session, user, REFUSED_EVENT, f"{what} refused - no recent two-factor check")
        raise
    if (confirm or "").strip() != CONFIRM_TEXT:
        await _audit(session, user, REFUSED_EVENT, f"{what} refused - typed confirmation missing")
        raise LiveOwnerRefused(NO_CONFIRM, status_code=400)
    await _audit(session, user, ALLOWED_EVENT, f"{what} allowed - owner, two-factor checked, confirmed")
