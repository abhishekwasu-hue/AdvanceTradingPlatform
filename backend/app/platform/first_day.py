"""Phase AX: the first-PAPER-day check - one read-only pass over everything the first real session
needs, run from a shell on the host (`scripts/first_paper_day_check.py`) before 09:15 IST.

It reuses the two go-live checklists (`platform/readiness.py`) and adds the probes a checklist
cannot do from a web request: the read-only broker smoke test on every usable stored session
(`brokers/smoke.py` - profile, funds, instruments, a NIFTY quote, one option contract, positions,
orders; never an order), today's exchange session, whether the worker actually evaluated the
active deployments recently (market-data freshness as the deployments see it) and, only when
asked, one test message through each configured alert channel. Everything else is a read.

Statuses: `ok`, `warn` (allowed, unwise), `fail` (do not start the day on this), `skip`
(not applicable / not requested). `ready` is "no fail".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import send_via_channel
from app.brokers.smoke import run_smoke
from app.brokers.token_lifecycle import build_adapter, token_is_usable
from app.core.config import WORKER_CYCLE_SECONDS
from app.core.enums import DeploymentStatus, NotificationSeverity, NotificationType
from app.db.models import AlertChannelRecord, BrokerCredentialRecord, NotificationRecord, StrategyDeploymentRecord, Tenant, User
from app.market_data.calendar import IST, market_session_status
from app.platform.readiness import platform_checklist, tenant_checklist

PLATFORM_KEYS = ("environment", "secrets_key", "database", "migrations", "redis", "worker", "instrument_master", "holidays", "global_kill_switch")
STATUS_MAP = {"ok": "ok", "todo": "fail", "warn": "warn", "info": "skip"}
ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}
STALE_CYCLES = 3


@dataclass
class Check:
    key: str
    title: str
    status: str            # ok / warn / fail / skip
    detail: str
    fix: str = ""
    section: str = "tenant"


@dataclass
class FirstDayReport:
    tenant_id: int
    tenant_name: str
    checked_at: str
    checks: List[Check] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    @property
    def counts(self) -> dict:
        out = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {"tenant_id": self.tenant_id, "tenant_name": self.tenant_name, "checked_at": self.checked_at, "ready": self.ready,
                "counts": self.counts, "checks": [asdict(c) for c in self.checks], "read_only": True}

    def render(self, lang: str = "mr") -> str:
        mr = lang == "mr"
        head = (f"पहिला PAPER दिवस - तपासणी · {self.tenant_name} (tenant {self.tenant_id}) · {self.checked_at}" if mr
                else f"First PAPER day check · {self.tenant_name} (tenant {self.tenant_id}) · {self.checked_at}")
        lines = [head, "=" * len(head)]
        section = None
        for c in self.checks:
            if c.section != section:
                section = c.section
                lines.append("")
                lines.append({"platform": "Platform", "tenant": "Organisation", "probe": "Live probes"}.get(section, section))
            lines.append(f"{ICON[c.status]} {c.title}: {c.detail}")
            if c.fix and c.status in ("fail", "warn"):
                lines.append(f"     → {c.fix}")
        n = self.counts
        lines.append("")
        if mr:
            lines.append(f"निकाल: {n['ok']} ठीक · {n['warn']} इशारे · {n['fail']} अडथळे · {n['skip']} वगळले")
            lines.append("✅ आज PAPER दिवस सुरू करू शकता. काहीही बदललेले नाही." if self.ready else "❌ आधी वरचे अडथळे दूर करा, मग पुन्हा चालवा. काहीही बदललेले नाही.")
        else:
            lines.append(f"Result: {n['ok']} ok · {n['warn']} warnings · {n['fail']} blockers · {n['skip']} skipped")
            lines.append("✅ Ready for today's PAPER session. Nothing was changed." if self.ready else "❌ Clear the blockers above and run again. Nothing was changed.")
        return "\n".join(lines)


async def pick_tenant(session: AsyncSession, selector: Optional[str] = None) -> Tuple[Optional[Tenant], Optional[User]]:
    """The organisation to check: by id or by a member's email; without a selector the one with
    an active deployment, else the only one. The acting user is its owner (or first active member)."""
    tenant: Optional[Tenant] = None
    if selector:
        if selector.isdigit():
            tenant = await session.get(Tenant, int(selector))
        else:
            user = await session.scalar(select(User).where(User.email == selector.strip().lower()))
            tenant = await session.get(Tenant, user.tenant_id) if user else None
    else:
        active = list(await session.scalars(select(StrategyDeploymentRecord.tenant_id).where(
            StrategyDeploymentRecord.status == DeploymentStatus.ACTIVE.value).distinct()))
        if len(active) == 1:
            tenant = await session.get(Tenant, active[0])
        elif not active:
            tenants = list(await session.scalars(select(Tenant).order_by(Tenant.id).limit(2)))
            tenant = tenants[0] if len(tenants) == 1 else None
    if tenant is None:
        return None, None
    user = await session.scalar(select(User).where(User.tenant_id == tenant.id, User.is_active.is_(True), User.role == "OWNER").order_by(User.id).limit(1))
    if user is None:
        user = await session.scalar(select(User).where(User.tenant_id == tenant.id, User.is_active.is_(True)).order_by(User.id).limit(1))
    return tenant, user


async def run(
    session: AsyncSession, tenant: Tenant, user: User, *, now: Optional[datetime] = None, smoke: bool = True, send_test_alert: bool = False,
    adapter_factory: Callable = build_adapter, smoke_runner: Callable[..., Awaitable] = run_smoke, sender: Callable[..., Awaitable] = send_via_channel,
) -> FirstDayReport:
    now = now or datetime.now(timezone.utc)
    report = FirstDayReport(tenant_id=tenant.id, tenant_name=tenant.name, checked_at=now.astimezone(IST).strftime("%Y-%m-%d %H:%M IST"))

    # 1. Platform and organisation checklists (Phase AB), narrowed to what a PAPER day needs.
    platform = await platform_checklist(session)
    for item in platform.items:
        if item.key in PLATFORM_KEYS:
            report.checks.append(Check(f"platform.{item.key}", item.title, STATUS_MAP[item.status], item.detail, item.fix, "platform"))
    org = await tenant_checklist(session, user, tenant, "PAPER")
    for item in org.items:
        if item.key in ("worker", "instrument_master", "holidays"):
            continue        # already on the platform list
        status = STATUS_MAP[item.status]
        if item.scope == "LIVE" and status == "skip":
            continue        # account hygiene is a LIVE item; keep the output to today's question
        report.checks.append(Check(f"tenant.{item.key}", item.title, status, item.detail, item.fix, "tenant"))

    # 2. Today's exchange session.
    nse = await market_session_status(session, now)
    ist = now.astimezone(IST)
    if nse.is_open:
        report.checks.append(Check("session", "NSE session today", "ok", f"open now ({ist:%H:%M} IST)", section="probe"))
    elif nse.next_open is not None and nse.next_open.date() == ist.date():
        report.checks.append(Check("session", "NSE session today", "ok", f"trading day; opens {nse.next_open:%H:%M} IST", section="probe"))
    else:
        report.checks.append(Check("session", "NSE session today", "warn", nse.reason + (f"; next open {nse.next_open:%Y-%m-%d %H:%M} IST" if nse.next_open else ""),
                                   "Not a trading day - the worker idles; run this again on the morning of the session.", "probe"))

    # 3. Read-only broker smoke test on every usable stored session.
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant.id).order_by(BrokerCredentialRecord.id)))
    usable = [r for r in records if token_is_usable(r, now)]
    if not smoke:
        report.checks.append(Check("broker_smoke", "Broker read-only smoke test", "skip", "not requested (--no-smoke)", section="probe"))
    elif not usable:
        report.checks.append(Check("broker_smoke", "Broker read-only smoke test", "fail" if records else "skip",
                                   "no stored broker session has a VALID token" if records else "no broker credentials stored",
                                   "Log in to the broker under Settings > Brokers (Upstox: Login button; Fyers/Kite: Open login -> paste the code), then run again.", "probe"))
    for record in usable:
        label = f"{record.broker_name} ({record.account_label or 'primary'})"
        try:
            smoke_report = await smoke_runner(adapter_factory(record), account_label=record.account_label or "primary")
        except Exception as exc:  # noqa: BLE001 - the probe itself must never abort the check
            report.checks.append(Check(f"broker_smoke.{record.id}", f"Broker read-only smoke test - {label}", "fail", f"could not run: {exc}"[:300],
                                       "Check the broker session and the network from the host; see OPERATIONS 1.6aa.", "probe"))
            continue
        failed = [s for s in smoke_report.steps if s.status == "fail"]
        detail = smoke_report.summary + ("; " + "; ".join(f"{s.name}: {s.detail}"[:120] for s in failed[:3]) if failed else "")
        report.checks.append(Check(f"broker_smoke.{record.id}", f"Broker read-only smoke test - {label}", "ok" if smoke_report.ok else "fail", detail,
                                   "" if smoke_report.ok else "A red profile means the token; a red derivatives/contract_quote means the scrip master. Fix and log in again (OPERATIONS 1.6aa).", "probe"))

    # 4. Are the active deployments being evaluated (the freshness the deployments actually see)?
    active = list(await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == tenant.id, StrategyDeploymentRecord.status == DeploymentStatus.ACTIVE.value).order_by(StrategyDeploymentRecord.id)))
    paper = [d for d in active if d.mode == "PAPER"]
    if not paper:
        report.checks.append(Check("paper_deployment", "An ACTIVE PAPER deployment", "fail", f"{len(active)} active deployment(s), none in PAPER",
                                   "Create a PAPER deployment on the Autopilot page and start it; the first real day is PAPER only.", "probe"))
    else:
        report.checks.append(Check("paper_deployment", "An ACTIVE PAPER deployment", "ok", ", ".join(f"{d.strategy_id} on {d.symbol} [{d.timeframe}]" for d in paper[:5]), section="probe"))
    limit = WORKER_CYCLE_SECONDS * STALE_CYCLES
    for d in active:
        if d.last_error:
            report.checks.append(Check(f"deployment.{d.id}.error", f"Deployment {d.strategy_id} on {d.symbol} - last error", "warn", d.last_error[:200],
                                       "Open the deployment on the Autopilot page; an unknown symbol or an expired token is the usual cause.", "probe"))
        if nse.is_open:
            seen = d.last_evaluated_at
            if seen is not None and seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            age = None if seen is None else int((now - seen).total_seconds())
            fresh = age is not None and age <= limit
            report.checks.append(Check(f"deployment.{d.id}.fresh", f"Deployment {d.strategy_id} on {d.symbol} - evaluated recently", "ok" if fresh else "fail",
                                       "never evaluated" if age is None else f"last evaluated {age}s ago (limit {limit}s)",
                                       "" if fresh else "The worker is not evaluating: check its heartbeat and logs (OPERATIONS 1.7); candles may be stale (G1 gate).", "probe"))

    # 5. Alert channels: a real message only when asked.
    channels = list(await session.scalars(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant.id, AlertChannelRecord.enabled.is_(True))))
    if not channels:
        report.checks.append(Check("alert_test", "Alert channel test message", "fail", "no enabled out-of-app channel",
                                   "Add Telegram or email under Settings > Alerts; a stopped worker must reach a phone.", "probe"))
    elif not send_test_alert:
        report.checks.append(Check("alert_test", "Alert channel test message", "skip", f"{len(channels)} channel(s) configured; pass --send-test-alert to send one", section="probe"))
    else:
        for channel in channels:
            probe = NotificationRecord(tenant_id=tenant.id, user_id=user.id, event_type=NotificationType.SYSTEM_FAILURE.value, severity=NotificationSeverity.INFO.value,
                                       title="First PAPER day check", message=f"Test message from first_paper_day_check.py for {tenant.name}. If you can read this, {channel.channel_type.lower()} alerts work.",
                                       created_at=now)
            try:
                await sender(channel, probe)
                report.checks.append(Check(f"alert_test.{channel.channel_type}", f"Alert channel test message - {channel.channel_type}", "ok", "sent", section="probe"))
            except Exception as exc:  # noqa: BLE001 - surfacing the error is the point
                report.checks.append(Check(f"alert_test.{channel.channel_type}", f"Alert channel test message - {channel.channel_type}", "fail", str(exc)[:300],
                                           "Fix the channel under Settings > Alerts and press Send test there.", "probe"))
    return report
