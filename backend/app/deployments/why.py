"""AUD-T3 (owner audit 28 Oct): the "Why no trade" timeline - for each closed bar a bot looked at, the reason it did or
did not trade, in plain words.

The trading worker notes one code per evaluation (`TradingWorker._why`) and stores it here after the evaluation is
done, in its own commit: the log is written by the worker and never read by it, so it changes no decision and no exit.
One row per (deployment, bar); a bar read again keeps its newest reason. A reason with no bar (the venue is closed, the
organisation is paused) is not repeated while the latest row carries the same code (its time is when it started). Rows beyond
`WHY_NO_TRADE_KEEP_BARS` per deployment are pruned on write.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import DeploymentBarReasonRecord, StrategyDeploymentRecord

# code -> the plain sentence a trader sees. Closed list: the frontend shows the label, the worker's detail under it.
CODES: dict[str, str] = {
    "entered": "A trade was placed",
    "proposed": "Sent to you for approval",
    "no_signal": "No setup on this bar",
    "same_signal": "Same setup as before - already handled",
    "position_open": "A position from this bot is already open",
    "exit_read": "The open trade closed on this bar's exit reading",
    "risk_setting": "Your risk settings held the entry back",
    "oi_gate": "Your OI checks held the entry back",
    "entry_cutoff": "Past your last time for new entries",
    "swing_short": "A short cannot be carried overnight in cash shares",
    "stale_data": "Prices were late - the bar was skipped",
    "not_enough_history": "Not enough bars yet to read the chart",
    "contract_missing": "No contract matched your option / future settings",
    "broker_unavailable": "The broker account was not ready",
    "outside_hours": "The market was closed",
    "not_evaluated": "The bot was not run on this bar",
}


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


async def record(session: AsyncSession, dep: StrategyDeploymentRecord, code: str, bar_time: Optional[datetime],
                 detail: Optional[str], now: datetime) -> None:
    """Store one reason (no commit). Unknown codes are a programming error and fail loudly in tests."""
    if code not in CODES:
        raise ValueError(f"unknown why-no-trade code {code!r}")
    bar_time, now = _utc(bar_time), _utc(now)
    detail = (detail or "")[:300] or None
    latest = await session.scalar(
        select(DeploymentBarReasonRecord).where(DeploymentBarReasonRecord.deployment_id == dep.id)
        .order_by(DeploymentBarReasonRecord.seen_at.desc(), DeploymentBarReasonRecord.id.desc()).limit(1))
    if latest is not None and bar_time is None and latest.bar_time is None and latest.code == code:
        return              # the same bar-less reason again (e.g. closed all night): one row, from when it started
    if latest is not None and bar_time is not None and _utc(latest.bar_time) == bar_time:
        latest.code, latest.detail, latest.seen_at = code, detail, now
        return
    session.add(DeploymentBarReasonRecord(tenant_id=dep.tenant_id, deployment_id=dep.id, bar_time=bar_time, code=code,
                                          detail=detail, seen_at=now))
    await session.flush()
    keep = max(1, config.WHY_NO_TRADE_KEEP_BARS)
    cutoff = await session.scalar(
        select(DeploymentBarReasonRecord.id).where(DeploymentBarReasonRecord.deployment_id == dep.id)
        .order_by(DeploymentBarReasonRecord.seen_at.desc(), DeploymentBarReasonRecord.id.desc()).offset(keep).limit(1))
    if cutoff is not None:
        await session.execute(delete(DeploymentBarReasonRecord).where(
            DeploymentBarReasonRecord.deployment_id == dep.id, DeploymentBarReasonRecord.id <= cutoff))


async def recent(session: AsyncSession, deployment_id: int, limit: int) -> list[dict]:
    rows = await session.scalars(
        select(DeploymentBarReasonRecord).where(DeploymentBarReasonRecord.deployment_id == deployment_id)
        .order_by(DeploymentBarReasonRecord.seen_at.desc(), DeploymentBarReasonRecord.id.desc()).limit(limit))
    return [{"bar_time": _utc(r.bar_time).isoformat() if r.bar_time else None, "seen_at": _utc(r.seen_at).isoformat(),
             "code": r.code, "label": CODES.get(r.code, r.code), "detail": r.detail} for r in rows]
