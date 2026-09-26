"""Phase D3: retention jobs and personal-data erasure.

`run_retention` deletes rows past their policy age, one bounded batch per table per run (so a
first run against years of backlog never holds a long transaction - the worker calls it daily
and catches up over a few days), and records what it did as one `retention_run` audit row.
`preview_retention` reports the same counts without deleting, for the admin console.

`erase_user` is the DPDP-style account erasure: it does not delete the `users` row (every trade,
order and audit row must stay attributed to a stable user id for the regulatory period) but
replaces the personal data on it - email, password, MFA secret - with inert values, ends every
session, and rewrites the email on that user's login attempts. The audit trail keeps the old
email only inside earlier rows' hash-chained `detail` text, which cannot be edited without
breaking the chain; the erasure row itself records the user id, not the email.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.sessions import revoke_all_sessions
from app.db.models import (
    AlertDeliveryRecord, LoginEventRecord, MfaBackupCodeRecord, NotificationRecord, PasswordResetRecord,
    TenantInviteRecord, User, UserSessionRecord,
)
from app.retention.policy import RetentionPolicy, load_policy

logger = logging.getLogger(__name__)

ERASED_EMAIL_DOMAIN = "erased.invalid"


@dataclass
class RetentionReport:
    started_at: datetime
    dry_run: bool
    deleted: Dict[str, int] = field(default_factory=dict)
    errors: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.deleted.values())


def _cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _rules(now: datetime, policy: RetentionPolicy):
    """(table, model, predicate) for every table the policy covers. Predicates only ever select
    rows that are finished: sent/failed deliveries, expired or revoked sessions, spent tokens."""
    return [
        ("login_events", LoginEventRecord, LoginEventRecord.created_at < _cutoff(now, policy.login_events_days)),
        ("alert_deliveries", AlertDeliveryRecord, (AlertDeliveryRecord.status != "PENDING")
         & (AlertDeliveryRecord.created_at < _cutoff(now, policy.alert_deliveries_days))),
        ("notifications", NotificationRecord, NotificationRecord.created_at < _cutoff(now, policy.notifications_days)),
        ("user_sessions", UserSessionRecord, or_(
            UserSessionRecord.expires_at < _cutoff(now, policy.sessions_days),
            UserSessionRecord.revoked_at < _cutoff(now, policy.sessions_days),
        )),
        ("password_resets", PasswordResetRecord, or_(
            PasswordResetRecord.expires_at < _cutoff(now, policy.password_resets_days),
            PasswordResetRecord.used_at < _cutoff(now, policy.password_resets_days),
        )),
        ("tenant_invites", TenantInviteRecord, or_(
            TenantInviteRecord.expires_at < _cutoff(now, policy.invites_days),
            TenantInviteRecord.accepted_at < _cutoff(now, policy.invites_days),
        )),
    ]


async def preview_retention(session: AsyncSession, now: Optional[datetime] = None, policy: Optional[RetentionPolicy] = None) -> RetentionReport:
    now = now or datetime.now(timezone.utc)
    policy = policy or load_policy()
    report = RetentionReport(started_at=now, dry_run=True)
    for table, model, predicate in _rules(now, policy):
        report.deleted[table] = int(await session.scalar(select(func.count()).select_from(model).where(predicate)) or 0)
    return report


async def run_retention(session: AsyncSession, now: Optional[datetime] = None, policy: Optional[RetentionPolicy] = None) -> RetentionReport:
    """Deletes up to `policy.batch_size` eligible rows per table, commits, and audits the run.
    Returns the report even when the policy is disabled (with nothing deleted)."""
    now = now or datetime.now(timezone.utc)
    policy = policy or load_policy()
    report = RetentionReport(started_at=now, dry_run=False)
    if not policy.enabled:
        return report
    for table, model, predicate in _rules(now, policy):
        try:
            ids = list(await session.scalars(select(model.id).where(predicate).order_by(model.id).limit(policy.batch_size)))
            if ids:
                await session.execute(delete(model).where(model.id.in_(ids)))
            report.deleted[table] = len(ids)
        except Exception as exc:  # noqa: BLE001 - one table's problem must not stop the others
            logger.exception("Retention failed for %s", table)
            report.errors.append(f"{table}: {exc}")
            report.deleted[table] = 0
    if report.total or report.errors:
        detail = ", ".join(f"{k}={v}" for k, v in report.deleted.items() if v) or "nothing eligible"
        if report.errors:
            detail += "; errors: " + "; ".join(report.errors)
        await write_audit_log(session, None, None, "retention_run", detail)
    await session.commit()
    return report


async def erase_user(session: AsyncSession, user: User, *, actor_id: Optional[int], reason: str = "") -> str:
    """Replaces the personal data on a deactivated user with inert values (see module docstring).
    Returns the new placeholder email. Does not commit."""
    placeholder = f"erased-{user.id}@{ERASED_EMAIL_DOMAIN}"
    old_email = user.email
    user.email = placeholder
    user.hashed_password = "!erased"  # never a valid bcrypt hash - login is impossible
    user.is_active = False
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_enabled_at = None
    await revoke_all_sessions(session, user.id, "account erased")
    await session.execute(delete(MfaBackupCodeRecord).where(MfaBackupCodeRecord.user_id == user.id))
    await session.execute(delete(PasswordResetRecord).where(PasswordResetRecord.user_id == user.id))
    await session.execute(update(LoginEventRecord).where(LoginEventRecord.email == old_email.lower()).values(email=placeholder))
    await write_audit_log(session, user.tenant_id, actor_id, "user_erased", f"user #{user.id}{(': ' + reason) if reason else ''}")
    return placeholder
