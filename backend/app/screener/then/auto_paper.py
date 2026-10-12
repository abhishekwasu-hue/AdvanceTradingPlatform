"""N1 X2: Auto PAPER from scans - when a scan intent may take the core's PAPER route, and the limits on it.

N1-3 is the owner's decision. Three switches must all be on, or an AUTO_PAPER setup stays a proposal exactly as in X1:

1. the platform flag `scan_auto_paper` (operator only; in DEFAULT_OFF_FLAGS);
2. approval-only mode off for the organisation (`approval_only_entries`; N1-6 - asked again right before placing);
3. the template's own owner armed it (`templates.arm_of`: a valid key in `gate_settings_json`; missing, malformed,
   another user's or another mode's arming is "not armed").

Limits (X2-3): at most `SCAN_AUTO_PAPER_MAX_PER_DAY` Auto PAPER entries per template per IST day and a
`SCAN_AUTO_PAPER_COOLDOWN_MINUTES` cool-down per symbol for the same person, counted from `execution_intents` (route
PAPER, DONE). Over a limit the setup becomes a proposal. Nothing here places an order: the dispatcher's `_paper` does,
through the PAPER pipeline without a broker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.db.models import AlertRuleRecord, ExecutionIntentRecord, ScanThenTemplateRecord
from app.market_data.calendar import IST
from app.screener.then import templates as T

FLAG = "scan_auto_paper"
SOURCE = "scan"
_REF = re.compile(r"^rule:(\d+):symbol:(.+)$")

WHY_FLAG_OFF = "Auto PAPER is not offered on this platform (the operator's scan_auto_paper switch is off): a proposal instead"
WHY_APPROVAL = "Approval-only mode is on: Auto PAPER becomes a proposal a person approves"
WHY_NOT_ARMED = "This template is not armed for Auto PAPER: a proposal instead"
WHY_NOT_AUTO = "This template's mode is not Auto PAPER"


@dataclass(frozen=True)
class Availability:
    available: bool                  # the platform switch is on and approval-only mode is off
    armed: bool                      # the template's owner armed it (valid arming)
    why: str                         # why it is not placing (empty when both)

    @property
    def places(self) -> bool:
        return self.available and self.armed


def rule_and_symbol(source_ref: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    m = _REF.match(source_ref or "")
    return (int(m.group(1)), m.group(2)) if m else (None, None)


async def template_for(session: AsyncSession, tenant_id: int, rule_id: Optional[int]) -> Optional[ScanThenTemplateRecord]:
    if rule_id is None:
        return None
    return await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.tenant_id == tenant_id,
                                                                     ScanThenTemplateRecord.rule_id == rule_id))


async def availability(session: AsyncSession, tenant_id: int, row: Optional[ScanThenTemplateRecord]) -> Availability:
    """The three switches for one template (the dispatcher and the template's API read the same answer)."""
    from app.execution import approval
    from app.platform.controls import flag_enabled
    armed = T.is_armed(row)
    if not await flag_enabled(session, FLAG, tenant_id):
        return Availability(False, armed, WHY_FLAG_OFF)
    if await approval.required(session, tenant_id):
        return Availability(False, armed, WHY_APPROVAL)
    if row is None or row.mode != "AUTO_PAPER":
        return Availability(True, False, WHY_NOT_AUTO)
    return Availability(True, armed, "" if armed else WHY_NOT_ARMED)


async def intent_places(session: AsyncSession, intent: ExecutionIntentRecord) -> bool:
    """Whether this intent may take the PAPER route: a scan intent whose template (same organisation, same owner,
    enabled) has all three switches on. Anything else is False (a proposal)."""
    if intent.source != SOURCE:
        return False
    rule_id, _symbol = rule_and_symbol(intent.source_ref)
    row = await template_for(session, intent.tenant_id, rule_id)
    if row is None or not row.enabled or row.user_id != intent.user_id:
        return False
    rule = await session.get(AlertRuleRecord, rule_id)
    if rule is None or rule.tenant_id != intent.tenant_id:
        return False
    return (await availability(session, intent.tenant_id, row)).places


def day_start(now: datetime) -> datetime:
    local = now.astimezone(IST).date()
    return datetime.combine(local, dtime(0, 0), IST).astimezone(timezone.utc)


async def entries_today(session: AsyncSession, tenant_id: int, rule_id: int, now: datetime) -> int:
    """Auto PAPER entries placed from this template's rule since the IST day began."""
    return int(await session.scalar(select(func.count()).select_from(ExecutionIntentRecord).where(
        ExecutionIntentRecord.tenant_id == tenant_id, ExecutionIntentRecord.source == SOURCE,
        ExecutionIntentRecord.source_ref.like(f"rule:{rule_id}:symbol:%"), ExecutionIntentRecord.route == "PAPER",
        ExecutionIntentRecord.status == "DONE", ExecutionIntentRecord.finished_at >= day_start(now))) or 0)


async def limit_reason(session: AsyncSession, intent: ExecutionIntentRecord, now: datetime) -> Optional[str]:
    """Why this intent goes to a proposal instead (a limit reached), or None."""
    rule_id, symbol = rule_and_symbol(intent.source_ref)
    if rule_id is None:
        return "Not a scan template's intent"
    cap = int(app_config.SCAN_AUTO_PAPER_MAX_PER_DAY)
    if await entries_today(session, intent.tenant_id, rule_id, now) >= cap:
        return f"Auto PAPER limit reached: {cap} entries from this template today - a proposal instead"
    minutes = int(app_config.SCAN_AUTO_PAPER_COOLDOWN_MINUTES)
    if minutes > 0:
        since = now - timedelta(minutes=minutes)
        recent = await session.scalar(select(ExecutionIntentRecord.id).where(
            ExecutionIntentRecord.tenant_id == intent.tenant_id, ExecutionIntentRecord.user_id == intent.user_id,
            ExecutionIntentRecord.source == SOURCE, ExecutionIntentRecord.symbol == intent.symbol,
            ExecutionIntentRecord.route == "PAPER", ExecutionIntentRecord.status == "DONE",
            ExecutionIntentRecord.id != intent.id, ExecutionIntentRecord.finished_at >= since).limit(1))
        if recent is not None:
            return f"Auto PAPER cool-down: {intent.symbol} had an Auto PAPER entry in the last {minutes} minutes - a proposal instead"
    return None
