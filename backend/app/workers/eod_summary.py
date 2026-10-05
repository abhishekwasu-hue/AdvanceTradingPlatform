"""Phase AX: the end-of-day summary - one notification per organisation after the NSE close.

The first PAPER days are judged on the ledger: did the worker stay up, did the deployments see
signals, were entries taken and exited, was anything left open after the square-off, did any
deployment pause itself? Until now the operator had to assemble that from four pages after 15:30.
`build()` reads the day's rows for one tenant and `send_all()` raises an `EOD_SUMMARY`
notification per organisation that had something to report (an active or paused deployment, or a
trade today). It never changes trading state and is raised once per IST day by the worker from
`EOD_AT` on (`eod_due`); the dispatcher then delivers it like any other notification to the
tenant's Telegram / email channels whose floor it reaches (INFO on a clean day, WARNING when
something needs a look).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DeploymentStatus, NotificationSeverity, NotificationType
from app.db.models import NotificationRecord, SignalHistoryRecord, StrategyDeploymentRecord, Tenant, TradeRecord, WorkerHeartbeatRecord
from app.market_data.calendar import IST
from app.notifications.service import notify

EOD_AT = dtime(15, 35)          # IST; after the 15:15 square-off and the 15:30 close


def eod_due(now: datetime) -> bool:
    """A weekday from 15:35 IST on. Holidays are not excluded on purpose: a worker that ran all day
    on a holiday has nothing to report (no deployments evaluated), and `send_all` then skips."""
    ist = now.astimezone(IST)
    return ist.weekday() < 5 and ist.time() >= EOD_AT


def day_window(now: datetime) -> tuple[date, datetime, datetime]:
    """(IST calendar day, UTC start, UTC end) of the trading day `now` falls in."""
    day = now.astimezone(IST).date()
    start = datetime.combine(day, dtime(0, 0), tzinfo=IST)
    return day, start.astimezone(now.tzinfo), (start + timedelta(days=1)).astimezone(now.tzinfo)


@dataclass
class DeploymentLine:
    strategy_id: str
    symbol: str
    mode: str
    status: str
    signals_today: int = 0
    last_error: Optional[str] = None
    pause_reason: Optional[str] = None


@dataclass
class Summary:
    tenant_id: int
    day: str
    signals: int = 0
    trades_opened: int = 0
    trades_closed: int = 0
    net_pnl: float = 0.0
    by_mode: Dict[str, int] = field(default_factory=dict)        # trades opened today per mode
    exits: Dict[str, int] = field(default_factory=dict)          # exit_reason -> count
    still_open: int = 0                                          # positions without an exit after the square-off
    deployments: List[DeploymentLine] = field(default_factory=list)
    paused: int = 0
    erroring: int = 0
    broker_uncertain: Optional[str] = None
    reconciled_today: bool = False
    worker_last_error: Optional[str] = None
    worker_cycles: int = 0

    @property
    def severity(self) -> NotificationSeverity:
        if self.still_open or self.paused or self.erroring or self.broker_uncertain:
            return NotificationSeverity.WARNING
        return NotificationSeverity.INFO

    @property
    def title(self) -> str:
        return f"EOD summary {self.day}: {self.signals} signal(s), {self.trades_opened} entry(ies), {self.trades_closed} exit(s), net {self.net_pnl:+,.0f}"

    def lines(self) -> List[str]:
        out = [f"Deployments: {len(self.deployments)} ({self.paused} paused, {self.erroring} with errors)"]
        for d in self.deployments:
            note = d.pause_reason or d.last_error or ""
            out.append(f"- {d.strategy_id} on {d.symbol} [{d.mode}] {d.status}: {d.signals_today} signal(s)" + (f" - {note[:120]}" if note else ""))
        modes = ", ".join(f"{m} {n}" for m, n in sorted(self.by_mode.items())) or "none"
        out.append(f"Entries today: {self.trades_opened} ({modes}); exits: {self.trades_closed}" + (f" ({', '.join(f'{k} {v}' for k, v in sorted(self.exits.items()))})" if self.exits else ""))
        out.append(f"Net P&L of today's exits: {self.net_pnl:+,.2f}")
        out.append("Open after square-off: " + (f"{self.still_open} position(s) - check Positions" if self.still_open else "none"))
        out.append("Reconciliation: " + ("clean today" if self.reconciled_today and not self.broker_uncertain else (f"BLOCKED - {self.broker_uncertain}" if self.broker_uncertain else "not run today")))
        out.append(f"Worker: {self.worker_cycles} cycle(s) so far" + (f"; last error: {self.worker_last_error[:160]}" if self.worker_last_error else "; no cycle errors"))
        return out

    def as_dict(self) -> dict:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["title"] = self.title
        return data


async def build(session: AsyncSession, tenant_id: int, now: datetime) -> Summary:
    day, start, end = day_window(now)
    summary = Summary(tenant_id=tenant_id, day=day.isoformat())
    deployments = list(await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == tenant_id,
        StrategyDeploymentRecord.status.in_([DeploymentStatus.ACTIVE.value, DeploymentStatus.PAUSED.value])).order_by(StrategyDeploymentRecord.id)))
    signals = list(await session.scalars(select(SignalHistoryRecord).where(
        SignalHistoryRecord.tenant_id == tenant_id, SignalHistoryRecord.signal_time >= start, SignalHistoryRecord.signal_time < end)))
    summary.signals = len(signals)
    per_key: Dict[tuple, int] = {}
    for s in signals:
        per_key[(s.strategy_id, s.symbol)] = per_key.get((s.strategy_id, s.symbol), 0) + 1
    for d in deployments:
        line = DeploymentLine(d.strategy_id, d.symbol, d.mode, d.status, per_key.get((d.strategy_id, d.symbol), 0), d.last_error, d.pause_reason)
        summary.deployments.append(line)
        summary.paused += d.status == DeploymentStatus.PAUSED.value
        summary.erroring += bool(d.last_error)
    opened = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.entry_time >= start, TradeRecord.entry_time < end)))
    summary.trades_opened = len(opened)
    for t in opened:
        summary.by_mode[t.mode] = summary.by_mode.get(t.mode, 0) + 1
    closed = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time >= start, TradeRecord.exit_time < end)))
    summary.trades_closed = len(closed)
    summary.net_pnl = round(sum(float(t.pnl or 0.0) for t in closed), 2)
    for t in closed:
        key = (t.exit_reason or "manual").split(":")[0].strip().upper()[:40]
        summary.exits[key] = summary.exits.get(key, 0) + 1
    summary.still_open = int(await session.scalar(select(func.count()).select_from(TradeRecord).where(
        TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))) or 0)
    tenant = await session.get(Tenant, tenant_id)
    if tenant is not None:
        summary.broker_uncertain = tenant.broker_uncertain_reason if tenant.broker_uncertain_since else None
        last = tenant.last_reconciled_at
        if last is not None:
            last = last if last.tzinfo else last.replace(tzinfo=start.tzinfo)
            summary.reconciled_today = start <= last < end
    heartbeat = await session.scalar(select(WorkerHeartbeatRecord).order_by(WorkerHeartbeatRecord.id).limit(1))
    if heartbeat is not None:
        summary.worker_cycles = int(heartbeat.cycle_count or 0)
        summary.worker_last_error = heartbeat.last_error
    return summary


async def tenants_to_summarise(session: AsyncSession, now: datetime) -> List[int]:
    """Organisations with an active/paused deployment, plus any that traded today."""
    _, start, end = day_window(now)
    with_deployments = set(await session.scalars(select(StrategyDeploymentRecord.tenant_id).where(
        StrategyDeploymentRecord.status.in_([DeploymentStatus.ACTIVE.value, DeploymentStatus.PAUSED.value])).distinct()))
    traded = set(await session.scalars(select(TradeRecord.tenant_id).where(TradeRecord.entry_time >= start, TradeRecord.entry_time < end).distinct()))
    return sorted(with_deployments | traded)


async def already_sent(session: AsyncSession, tenant_id: int, now: datetime) -> bool:
    """A worker restarted in the evening must not raise the day's summary a second time."""
    day, _, _ = day_window(now)
    existing = await session.scalar(select(NotificationRecord.id).where(
        NotificationRecord.tenant_id == tenant_id, NotificationRecord.event_type == NotificationType.EOD_SUMMARY.value,
        NotificationRecord.title.like(f"EOD summary {day.isoformat()}:%")).limit(1))
    return existing is not None


async def send_all(session: AsyncSession, now: datetime) -> int:
    """One EOD_SUMMARY notification per organisation per IST day; returns how many were raised."""
    sent = 0
    for tenant_id in await tenants_to_summarise(session, now):
        if await already_sent(session, tenant_id, now):
            continue
        summary = await build(session, tenant_id, now)
        await notify(session, tenant_id, NotificationType.EOD_SUMMARY, title=summary.title, message="\n".join(summary.lines()), severity=summary.severity)
        sent += 1
    return sent
