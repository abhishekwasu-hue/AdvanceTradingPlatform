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
    AiCandidateRecord, MarketSnapshotRecord, NewsEventRecord,
    OIDayBaselineRecord, OISnapshotRecord, OptionChainSnapshotRecord, StrikeOISnapshotRecord,
    AlertDeliveryRecord, LlmCallRecord, LoginEventRecord, MfaBackupCodeRecord, NotificationRecord, PasswordResetRecord,
    TenantInviteRecord, User, UserSessionRecord,
)
from app.retention.policy import RetentionPolicy, load_policy

logger = logging.getLogger(__name__)

ERASED_EMAIL_DOMAIN = "erased.invalid"
SCRUB_MARKERS = ("[erased]", "[expired]")       # H-C1 e: llm_calls text replaced by erasure / by age


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
        # Phase W: recorded option-chain quotes - bounded history for option backtests.
        ("option_chain_snapshots", OptionChainSnapshotRecord, OptionChainSnapshotRecord.captured_at < _cutoff(now, policy.chain_snapshots_days)),
        # OI Banner O2: strikes first (their slot row is the parent), then the slots, then the day baselines.
        ("strike_oi_snapshots", StrikeOISnapshotRecord, StrikeOISnapshotRecord.snapshot_id.in_(
            select(OISnapshotRecord.id).where(OISnapshotRecord.captured_at < _cutoff(now, policy.oi_snapshots_days)))),
        ("oi_snapshots", OISnapshotRecord, OISnapshotRecord.captured_at < _cutoff(now, policy.oi_snapshots_days)),
        ("oi_day_baselines", OIDayBaselineRecord, OIDayBaselineRecord.first_seen_at < _cutoff(now, policy.oi_snapshots_days)),
        ("market_snapshots", MarketSnapshotRecord, MarketSnapshotRecord.captured_at < _cutoff(now, policy.market_snapshots_days)),
        # Phase BB: feed items age out; a person's cited MANUAL entry never does (it is their claim, kept).
        ("news_events_feed", NewsEventRecord, (NewsEventRecord.origin == "FEED") & (NewsEventRecord.created_at < _cutoff(now, policy.news_feed_days))),
        # P0.8 / A3: candidates nobody adopted age out after they expire; adopted and deployed ones stay as the record of
        # what the person chose from.
        ("ai_candidates_open", AiCandidateRecord, (AiCandidateRecord.status == "OPEN") & (AiCandidateRecord.expires_at < _cutoff(now, policy.ai_candidates_days))),
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
    if policy.llm_text_days > 0:                    # H-C1 e: scrub old LLM texts; the rows themselves are never deleted
        try:
            report.deleted["llm_calls_text_scrubbed"] = await scrub_llm_text(session, LlmCallRecord.created_at < _cutoff(now, policy.llm_text_days),
                                                                             "[expired]", limit=policy.batch_size)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Retention failed for llm_calls text")
            report.errors.append(f"llm_calls text: {exc}")
    if report.total or report.errors:
        detail = ", ".join(f"{k}={v}" for k, v in report.deleted.items() if v) or "nothing eligible"
        if report.errors:
            detail += "; errors: " + "; ".join(report.errors)
        await write_audit_log(session, None, None, "retention_run", detail)
    await session.commit()
    return report


async def scrub_llm_text(session: AsyncSession, predicate, marker: str, *, limit: Optional[int] = None) -> int:
    """H-C1 e: replace the stored prompt/question/answer text of matching `llm_calls` rows with `marker`. The rows, their
    SHA-256 hashes, model, prompt version, tokens and cost stay (the audit record that a call happened is kept)."""
    # A row already scrubbed keeps its first marker: an age scrub never turns an "[erased]" row into "[expired]".
    q = select(LlmCallRecord.id).where(predicate, LlmCallRecord.user_text.notin_(SCRUB_MARKERS)).order_by(LlmCallRecord.id)
    if limit:
        q = q.limit(limit)
    ids = list(await session.scalars(q))
    if ids:
        await session.execute(update(LlmCallRecord).where(LlmCallRecord.id.in_(ids)).values(system_text=marker, user_text=marker, response_text=marker))
    return len(ids)


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
    await scrub_llm_text(session, LlmCallRecord.user_id == user.id, "[erased]")       # H-C1 e: their AI conversations
    await write_audit_log(session, user.tenant_id, actor_id, "user_erased", f"user #{user.id}{(': ' + reason) if reason else ''}")
    return placeholder
