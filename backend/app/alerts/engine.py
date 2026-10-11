"""S3a (ADR-0022): the Notification Service's rule side - events in, grouped notifications out, through the existing
outbox (`notify()` -> `alert_deliveries` -> dispatcher). Nothing here sends anything itself, and nothing places or
changes an order.

`record_event` (called by the S4 bar-close / live engines, or any rule source):
* dedupe - the idempotency key (rule, symbol, condition hash, bar time) is unique in `alert_events`, so re-running the
  same bar never fires twice;
* cooldown - a symbol that fired for this rule within `cooldown_minutes` is stored as suppressed (reason "cooldown").

`flush` (the worker, every cycle):
* burst grouping - pending events of one rule and one bar become ONE notification listing every symbol, once the
  bar's group window has passed;
* quiet hours (the organisation's timezone) hold normal/low events until the window ends; critical always goes;
* the hourly cap holds the overflow for the next digest (reason "rate_cap"), critical excepted;
* digest rules collect their events and send one notification per rule when the hourly / end-of-day bucket closes.
Every event ends sent, suppressed or held with a reason code; deliveries carry priority, group id and digest bucket.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AlertDeliveryRecord, AlertEventRecord, AlertRuleRecord, NotificationPolicyRecord, NotificationRecord

PRIORITIES = ("critical", "normal", "low")
SEVERITY = {"critical": NotificationSeverity.CRITICAL, "normal": NotificationSeverity.WARNING, "low": NotificationSeverity.INFO}
MAX_SYMBOLS_IN_MESSAGE = 25


@dataclass
class Policy:
    timezone: str = "Asia/Kolkata"
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    max_per_hour: int = 30
    group_window_seconds: int = 10
    eod_digest_time: str = "15:45"

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def in_quiet_hours(policy: Policy, now: datetime) -> bool:
    """Quiet hours in the organisation's timezone; a window may cross midnight (22:00-07:00)."""
    if not policy.quiet_start or not policy.quiet_end:
        return False
    local = _utc(now).astimezone(policy.tz()).time()
    start, end = _hhmm(policy.quiet_start), _hhmm(policy.quiet_end)
    if start == end:
        return False
    return start <= local < end if start < end else (local >= start or local < end)


def digest_bucket(rule: AlertRuleRecord, policy: Policy, now: datetime) -> Tuple[str, datetime]:
    """(bucket id, when it closes) for a digest rule: the current local hour, or the trading day's EOD digest time."""
    local = _utc(now).astimezone(policy.tz())
    if rule.digest_every == "eod":
        due = datetime.combine(local.date(), _hhmm(policy.eod_digest_time), local.tzinfo)
        if local >= due:
            due = due + timedelta(days=1)
        return f"eod:{due.date().isoformat()}", due.astimezone(timezone.utc)
    start = local.replace(minute=0, second=0, microsecond=0)
    return f"hour:{start.strftime('%Y-%m-%dT%H')}", (start + timedelta(hours=1)).astimezone(timezone.utc)


def idem_key(rule_id: int, symbol: str, condition_hash: str, bar_time: datetime) -> str:
    raw = f"{rule_id}|{symbol.upper()}|{condition_hash}|{_utc(bar_time).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def condition_hash(rule: AlertRuleRecord) -> str:
    body = rule.condition_text or f"screen:{rule.screen_id}"
    return hashlib.sha256(f"{rule.kind}|{body}|{rule.base_tf}".encode()).hexdigest()


async def policy_for(session: AsyncSession, tenant_id: int) -> Policy:
    row = await session.scalar(select(NotificationPolicyRecord).where(NotificationPolicyRecord.tenant_id == tenant_id))
    if row is None:
        return Policy()
    return Policy(row.timezone, row.quiet_start, row.quiet_end, row.max_per_hour, row.group_window_seconds, row.eod_digest_time)


async def record_event(session: AsyncSession, rule: AlertRuleRecord, symbol: str, bar_time: datetime, values: Dict[str, Any],
                       now: Optional[datetime] = None) -> Optional[AlertEventRecord]:
    """One firing. Returns None for a duplicate (same rule, symbol, condition and bar), the event otherwise."""
    now = _utc(now or datetime.now(timezone.utc))
    if rule.status != "active" or (rule.expires_at is not None and _utc(rule.expires_at) <= now):
        return None
    chash = condition_hash(rule)
    key = idem_key(rule.id, symbol, chash, bar_time)
    if await session.scalar(select(AlertEventRecord.id).where(AlertEventRecord.idem_key == key)) is not None:
        return None
    since = now - timedelta(minutes=max(0, rule.cooldown_minutes))
    recent = await session.scalar(select(func.count()).select_from(AlertEventRecord).where(
        AlertEventRecord.rule_id == rule.id, AlertEventRecord.symbol == symbol.upper(), AlertEventRecord.created_at > since,
        AlertEventRecord.status.in_(("pending", "sent", "held"))))
    event = AlertEventRecord(tenant_id=rule.tenant_id, rule_id=rule.id, symbol=symbol.upper(), condition_hash=chash, bar_time=_utc(bar_time),
                             idem_key=key, priority=rule.priority, values_json=json.dumps(values, default=str), created_at=now,
                             status="suppressed" if recent else "pending", reason_code="cooldown" if recent else None)
    try:
        async with session.begin_nested():                         # a concurrent writer of the same key loses quietly
            session.add(event)
            await session.flush()
    except IntegrityError:
        return None
    return event


def _is_intrabar(event: AlertEventRecord) -> bool:
    try:
        return bool(json.loads(event.values_json or "{}").get("intrabar"))
    except (ValueError, TypeError, AttributeError):
        return False


def _message(rule: AlertRuleRecord, events: Sequence[AlertEventRecord], bucket: Optional[str]) -> Tuple[str, str]:
    symbols = sorted({e.symbol for e in events})
    shown = ", ".join(symbols[:MAX_SYMBOLS_IN_MESSAGE]) + (f" and {len(symbols) - MAX_SYMBOLS_IN_MESSAGE} more" if len(symbols) > MAX_SYMBOLS_IN_MESSAGE else "")
    bars = sorted({_utc(e.bar_time) for e in events})
    when = bars[-1].strftime("%Y-%m-%d %H:%M UTC")
    if bucket:
        title = f"{rule.name}: {len(symbols)} match(es) in this digest"
    else:
        title = f"{rule.name}: {len(symbols)} symbol(s) matched"
    on = f"the bar closing {when}"
    if any(_is_intrabar(e) for e in events):
        on = f"the bar still forming at {when} (intrabar: it may not hold at the close)"
    body = f"Matched on {on}: {shown}. Matches passed the rule's filters; they are not recommendations."
    return title[:200], body


async def _sent_last_hour(session: AsyncSession, tenant_id: int, now: datetime) -> int:
    return int(await session.scalar(select(func.count(func.distinct(AlertEventRecord.notification_id))).where(
        AlertEventRecord.tenant_id == tenant_id, AlertEventRecord.status == "sent", AlertEventRecord.sent_at > now - timedelta(hours=1))) or 0)


async def _send(session: AsyncSession, rule: AlertRuleRecord, events: List[AlertEventRecord], now: datetime, group_id: str,
                bucket: Optional[str]) -> NotificationRecord:
    from app.notifications.service import notify
    title, body = _message(rule, events, bucket)
    note = await notify(session, rule.tenant_id, NotificationType.SCREEN_ALERT, title=title, message=body, severity=SEVERITY[rule.priority])
    await session.execute(update(AlertDeliveryRecord).where(AlertDeliveryRecord.notification_id == note.id)
                          .values(priority=rule.priority, group_id=group_id, digest_bucket=bucket).execution_options(synchronize_session=False))
    from app.alerts.links import add_linked_delivery                    # S3b-2: the rule creator's own linked Telegram chat
    await add_linked_delivery(session, rule.tenant_id, note.id, rule.created_by, priority=rule.priority, group_id=group_id, digest_bucket=bucket)
    for e in events:
        e.status, e.reason_code, e.group_id, e.notification_id, e.sent_at = "sent", None, group_id, note.id, now
    return note


async def flush(session: AsyncSession, now: Optional[datetime] = None, tenant_id: Optional[int] = None) -> Dict[str, int]:
    """Turn pending / held events into grouped notifications under each organisation's policy. Returns counters."""
    now = _utc(now or datetime.now(timezone.utc))
    out = {"sent": 0, "held": 0}
    q = select(AlertEventRecord).where(AlertEventRecord.status.in_(("pending", "held")))
    if tenant_id is not None:
        q = q.where(AlertEventRecord.tenant_id == tenant_id)
    events = list(await session.scalars(q.order_by(AlertEventRecord.id)))
    if not events:
        return out
    rules = {r.id: r for r in await session.scalars(select(AlertRuleRecord).where(AlertRuleRecord.id.in_({e.rule_id for e in events})))}
    policies: Dict[int, Policy] = {}
    groups: Dict[Tuple[int, str], List[AlertEventRecord]] = {}
    for e in events:
        rule = rules.get(e.rule_id)
        if rule is None:
            continue
        policy = policies.get(e.tenant_id) or await policy_for(session, e.tenant_id)
        policies[e.tenant_id] = policy
        if rule.mode == "digest":
            bucket, due = digest_bucket(rule, policy, _utc(e.created_at))
            if now < due:
                e.status, e.reason_code = "held", "digest"
                continue
            groups.setdefault((rule.id, f"digest|{bucket}"), []).append(e)
            continue
        if e.status == "pending" and now - _utc(e.created_at) < timedelta(seconds=policy.group_window_seconds):
            continue                                                   # the bar's burst may still be arriving
        groups.setdefault((rule.id, f"bar|{_utc(e.bar_time).isoformat()}"), []).append(e)
    for (rule_id, key), members in groups.items():
        rule = rules[rule_id]
        policy = policies[rule.tenant_id]
        digest_id: Optional[str] = key.split("|", 1)[1] if key.startswith("digest|") else None
        if rule.priority != "critical":
            if in_quiet_hours(policy, now):
                for e in members:
                    e.status, e.reason_code = "held", "quiet_hours"
                out["held"] += len(members)
                continue
            if await _sent_last_hour(session, rule.tenant_id, now) >= policy.max_per_hour:
                for e in members:
                    e.status, e.reason_code = "held", "rate_cap"
                out["held"] += len(members)
                continue
        group_id = hashlib.sha256(f"{rule_id}|{key}".encode()).hexdigest()[:32]
        await _send(session, rule, members, now, group_id, digest_id)
        out["sent"] += len(members)
    await session.commit()
    return out


__all__ = ["record_event", "flush", "Policy", "policy_for", "in_quiet_hours", "digest_bucket", "idem_key", "condition_hash", "PRIORITIES"]
