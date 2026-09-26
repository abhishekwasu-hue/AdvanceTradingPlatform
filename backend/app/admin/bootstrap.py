"""How the first platform administrator comes to exist. SUPER_ADMIN is never granted through
any tenant-facing UI or API; it comes from `SUPER_ADMIN_EMAILS` (comma-separated) in the
environment. At startup, and whenever one of those emails registers, that user is promoted.
Removing an email from the list does not demote anyone - demotion is a deliberate DB change by
whoever operates the platform, not a side effect of editing an env var."""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import SUPER_ADMIN_EMAILS
from app.core.enums import UserRole
from app.db.models import User

logger = logging.getLogger(__name__)


def is_configured_super_admin(email: str) -> bool:
    return email.lower() in SUPER_ADMIN_EMAILS


async def promote_configured_super_admins(session: AsyncSession) -> int:
    """Promotes every existing user whose email is configured. Returns how many changed."""
    if not SUPER_ADMIN_EMAILS:
        return 0
    rows = await session.scalars(select(User).where(User.email.in_(sorted(SUPER_ADMIN_EMAILS))))
    changed = 0
    for user in rows:
        if user.role != UserRole.SUPER_ADMIN.value:
            user.role = UserRole.SUPER_ADMIN.value
            changed += 1
            logger.warning("Promoted %s to SUPER_ADMIN from SUPER_ADMIN_EMAILS", user.email)
    if changed:
        await session.commit()
    return changed
