"""Phase AB: go-live readiness checklists.

The V1 acceptance row of the gap analysis has read "blocked on operator" for a while: the code is
there, the steps that need a human (broker key in Settings, a broker login, a worker process, an
alert channel, a deployment) are scattered across pages and runbooks. This module turns them into
two explicit checklists computed from the platform's own state:

* the **tenant** checklist (`tenant_checklist`) - what this organisation still has to do before a
  PAPER run, and the extra items a LIVE run needs (MFA, SEBI algo id, a verified email, ...);
* the **platform** checklist (`platform_checklist`, SUPER_ADMIN) - what the operator still has to
  configure in the deployment: secrets, SMTP, push keys, payment gateway, worker, migrations, Redis,
  instrument master, holiday calendar, kill switch.

Every item says what it checked, what it found and where to fix it (a page id the UI links to, or
an environment variable). Statuses: `ok`, `todo` (blocks the target), `warn` (works, but you will
regret leaving it), `info` (optional). The checklist never changes anything.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import webpush
from app.brokers.token_lifecycle import token_is_usable
from app.core import config
from app.db.models import (AiProviderConfigRecord, AlertChannelRecord, BrokerCredentialRecord, KillSwitchRecord, MarketHolidayRecord,
                           RiskLimitRecord, RiskSettingsRecord, StrategyDeploymentRecord, Tenant, User)
from app.instruments.master import master_status
from app.notifications import mailer
from app.observability.routes import _check_migrations, _check_redis, _check_worker

logger = logging.getLogger(__name__)

TARGETS = ("PAPER", "LIVE")
MASTER_MAX_AGE_DAYS = 3


@dataclass
class Item:
    key: str
    title: str
    status: str            # ok / todo / warn / info
    detail: str
    fix: str = ""
    link: Optional[str] = None      # frontend page id or env var name
    scope: str = "PAPER"            # PAPER = needed for a paper run; LIVE = additionally for live


@dataclass
class Checklist:
    target: str
    items: List[Item] = field(default_factory=list)

    @property
    def summary(self) -> Dict[str, int]:
        out = {"ok": 0, "todo": 0, "warn": 0, "info": 0}
        for i in self.items:
            out[i.status] = out.get(i.status, 0) + 1
        return out

    @property
    def ready(self) -> bool:
        return not any(i.status == "todo" for i in self.items)

    def as_dict(self) -> dict:
        return {"target": self.target, "ready": self.ready, "summary": self.summary, "items": [asdict(i) for i in self.items],
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "note": "Computed from the platform's own state; nothing here changes anything. 'todo' blocks the target, 'warn' is "
                        "allowed but unwise, 'info' is optional."}


def _age_days(ts: Optional[datetime], now: datetime) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 86400.0


async def tenant_checklist(session: AsyncSession, user: User, tenant: Tenant, target: str = "PAPER") -> Checklist:
    target = target.upper() if target and target.upper() in TARGETS else "PAPER"
    now = datetime.now(timezone.utc)
    items: List[Item] = []
    live = target == "LIVE"

    # 1. Broker credentials and a usable session ------------------------------------------------
    records = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant.id)))
    if records:
        names = sorted({r.broker_name for r in records})
        items.append(Item("broker_credentials", "Broker API key stored", "ok", f"{len(records)} account(s): {', '.join(names)}", scope="PAPER", link="settings"))
        usable = [r for r in records if token_is_usable(r)]
        if usable:
            items.append(Item("broker_session", "Broker session token valid today", "ok", ", ".join(f"{r.broker_name} ({r.account_label})" for r in usable), scope="PAPER", link="settings"))
        else:
            items.append(Item("broker_session", "Broker session token valid today", "todo", "No stored broker has a VALID token",
                              "Log in to the broker under Settings > Brokers (Upstox: OAuth). Tokens expire every trading day.", "settings"))
    else:
        items.append(Item("broker_credentials", "Broker API key stored", "todo", "No broker credentials for this organisation",
                          "Add the broker's API key and secret under Settings > Brokers. They are encrypted at rest and never go in the environment or in chat.", "settings"))
        items.append(Item("broker_session", "Broker session token valid today", "todo", "Needs credentials first", "Log in to the broker after storing its key.", "settings"))

    # 2. Instrument master ------------------------------------------------------------------------
    master = await master_status(session)
    fresh = [m for m in master if (_age_days(m["synced_at"], now) or 99) <= MASTER_MAX_AGE_DAYS]
    if fresh:
        items.append(Item("instrument_master", "Instrument master synced", "ok", "; ".join(f"{m['broker']}/{m['exchange']}: {m['rows']} rows" for m in fresh), link="instruments"))
    elif master:
        items.append(Item("instrument_master", "Instrument master synced", "warn", "Present but older than three days",
                          "Run a sync on the Instruments page or wait for the worker's daily sync (08:00 IST).", "instruments"))
    else:
        items.append(Item("instrument_master", "Instrument master synced", "todo", "No instruments loaded - contracts cannot be resolved",
                          "Sync on the Instruments page (Upstox needs no login for its public master) or start the worker.", "instruments"))

    # 3. Worker -----------------------------------------------------------------------------------
    worker = await _check_worker(session, now)
    if worker["status"] == "ok":
        items.append(Item("worker", "Trading worker running", "ok", f"heartbeat {worker['seconds_since_heartbeat']}s ago", link="dashboard"))
    elif worker["status"] == "never_seen":
        items.append(Item("worker", "Trading worker running", "todo", "No heartbeat has ever been recorded",
                          "Start the worker service (docker compose up worker, see docs/OPERATIONS.md 1.7). Deployments are evaluated only by the worker.", "dashboard"))
    else:
        items.append(Item("worker", "Trading worker running", "warn" if worker["status"] == "stale" else "todo",
                          f"heartbeat {worker['seconds_since_heartbeat']}s ago ({worker['status']})", "Restart the worker service and read its last error on the Dashboard.", "dashboard"))

    # 4. Holiday calendar -------------------------------------------------------------------------
    year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    holidays = int(await session.scalar(select(func.count()).select_from(MarketHolidayRecord).where(MarketHolidayRecord.holiday_date >= year_start.date())) or 0)
    items.append(Item("holidays", "Exchange holiday calendar loaded", "ok" if holidays else "warn",
                      f"{holidays} holiday(s) from {now.year} onwards" if holidays else "No holidays recorded for this year",
                      "" if holidays else "Load the NSE holiday list (Settings > Market holidays, or POST /api/market-holidays) so the worker stays idle on closed days.", "settings"))

    # 5. Deployments ------------------------------------------------------------------------------
    deployments = list(await session.scalars(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.tenant_id == tenant.id)))
    active = [d for d in deployments if d.status == "ACTIVE"]
    by_mode = {m: sum(1 for d in active if d.mode == m) for m in ("PAPER", "LIVE")}
    if active:
        items.append(Item("deployment", "A deployment is active", "ok", f"{len(active)} active (PAPER {by_mode['PAPER']}, LIVE {by_mode['LIVE']})", link="autopilot"))
    else:
        items.append(Item("deployment", "A deployment is active", "todo", f"{len(deployments)} deployment(s), none active",
                          "Create a PAPER deployment on the Autopilot page and start it. Run paper for at least a few sessions before LIVE.", "autopilot"))
    failing = [d for d in active if (d.consecutive_failures or 0) >= 3]
    if failing:
        items.append(Item("deployment_errors", "Deployments evaluating without errors", "warn", f"{len(failing)} with 3+ consecutive failures: {failing[0].last_error or ''}"[:200],
                          "Open the deployment on Autopilot and read its last error.", "autopilot"))

    # 6. Risk configuration -----------------------------------------------------------------------
    settings = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant.id))
    limits = int(await session.scalar(select(func.count()).select_from(RiskLimitRecord).where(RiskLimitRecord.tenant_id == tenant.id, RiskLimitRecord.enabled.is_(True))) or 0)
    if settings or limits:
        detail = (f"capital {settings.capital:,.0f}, {settings.risk_per_trade_pct}% per trade, {settings.max_daily_loss_pct}% daily loss cap" if settings else "") + (f"; {limits} scoped limit(s)" if limits else "")
        items.append(Item("risk", "Risk limits set", "ok", detail.strip("; "), link="risk"))
    else:
        items.append(Item("risk", "Risk limits set", "warn" if not live else "todo", "Platform defaults only (0.5% per trade, 3% daily loss, 3 open positions)",
                          "Review capital, per-trade risk and the daily loss cap on the Risk page before trading real money.", "risk"))

    # 7. Alerts -----------------------------------------------------------------------------------
    channels = list(await session.scalars(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant.id, AlertChannelRecord.enabled.is_(True))))
    if channels:
        erroring = [c for c in channels if c.last_error]
        items.append(Item("alerts", "Out-of-app alert channel", "ok" if not erroring else "warn",
                          ", ".join(sorted({c.channel_type for c in channels})) + (f"; last error on {erroring[0].channel_type}: {erroring[0].last_error}"[:160] if erroring else ""),
                          "Fix the failing channel under Settings > Alerts." if erroring else "", "settings"))
    else:
        items.append(Item("alerts", "Out-of-app alert channel", "warn" if not live else "todo", "Only in-app notifications",
                          "Add Telegram, email, push or a webhook under Settings > Alerts so a stopped worker or a kill switch reaches your phone.", "settings"))

    # 8. Safety state -----------------------------------------------------------------------------
    switches = list(await session.scalars(select(KillSwitchRecord).where(KillSwitchRecord.engaged.is_(True),
                                                                          (KillSwitchRecord.tenant_id == tenant.id) | (KillSwitchRecord.scope == "GLOBAL"))))
    if switches:
        items.append(Item("kill_switch", "No kill switch engaged", "warn", "; ".join(f"{s.scope}: {s.reason or 'no reason'}" for s in switches)[:200],
                          "Disengage on the Risk page (tenant/strategy) once the cause is understood; GLOBAL is the operator's.", "risk"))
    else:
        items.append(Item("kill_switch", "No kill switch engaged", "ok", "none engaged", link="risk"))
    if tenant.broker_uncertain_since:
        items.append(Item("broker_uncertain", "Broker state reconciled", "warn", f"LIVE entries blocked since {tenant.broker_uncertain_since:%Y-%m-%d %H:%M} UTC: {tenant.broker_uncertain_reason or ''}"[:200],
                          "The worker re-reconciles each cycle; check the broker session and positions.", "positions"))
    else:
        items.append(Item("broker_uncertain", "Broker state reconciled", "ok", "no reconciliation block", link="positions"))

    # 9. Account hygiene (LIVE) -------------------------------------------------------------------
    items.append(Item("mfa", "MFA enabled on your account", "ok" if user.mfa_enabled else ("todo" if live else "info"),
                      "enabled" if user.mfa_enabled else "not enabled", "" if user.mfa_enabled else "Enable TOTP MFA under Settings > Security; LIVE deployments and broker credential changes require a step-up.", "settings", scope="LIVE"))
    items.append(Item("email_verified", "Email verified", "ok" if user.email_verified_at else ("todo" if live else "info"),
                      "verified" if user.email_verified_at else "not verified", "" if user.email_verified_at else "Verify your email from the link in your inbox (needs platform SMTP) so resets and alerts reach you.", "settings", scope="LIVE"))
    items.append(Item("algo_id", "SEBI algo id set for order tagging", "ok" if tenant.algo_id else ("todo" if live else "info"),
                      tenant.algo_id or "not set", "" if tenant.algo_id else "Set the exchange-registered algo id under Settings > Compliance; every LIVE order is tagged with it.", "settings", scope="LIVE"))

    # 10. Optional --------------------------------------------------------------------------------
    ai = await session.scalar(select(AiProviderConfigRecord).where(AiProviderConfigRecord.tenant_id == tenant.id))
    items.append(Item("ai_provider", "AI provider key (optional)", "ok" if ai and ai.encrypted_api_key else "info",
                      f"{ai.provider} {ai.model}".strip() if ai else "rule-based fallback in use", "" if ai else "Add a provider key under Settings > AI to enable the LLM features; everything works without it.", "settings", scope="OPTIONAL"))
    return Checklist(target=target, items=items)


async def platform_checklist(session: AsyncSession) -> Checklist:
    now = datetime.now(timezone.utc)
    items: List[Item] = []
    prod = config.ENVIRONMENT.lower() in ("production", "prod", "staging")
    items.append(Item("environment", "Environment declared", "ok" if prod else "warn", config.ENVIRONMENT,
                      "" if prod else "Set ENVIRONMENT=production (or staging) so production-only guards apply.", "ENVIRONMENT"))
    items.append(Item("jwt_secret", "JWT secret is not the insecure default", "ok" if config.JWT_SECRET_KEY != config._INSECURE_DEFAULT_JWT_SECRET else "todo",
                      "custom" if config.JWT_SECRET_KEY != config._INSECURE_DEFAULT_JWT_SECRET else "development default in use", "Set JWT_SECRET_KEY to a long random value.", "JWT_SECRET_KEY"))
    items.append(Item("secrets_key", "Secrets encryption key set", "ok" if config.SECRETS_ENCRYPTION_KEY else "todo",
                      "set" if config.SECRETS_ENCRYPTION_KEY else "missing - broker and AI keys cannot be stored", "Set SECRETS_ENCRYPTION_KEY (Fernet key) and back it up with the database.", "SECRETS_ENCRYPTION_KEY"))
    items.append(Item("database", "Database is Postgres", "ok" if config.DATABASE_URL.startswith("postgresql") else "warn", config.DATABASE_URL.split("://", 1)[0],
                      "" if config.DATABASE_URL.startswith("postgresql") else "Point DATABASE_URL at Postgres; SQLite is for tests.", "DATABASE_URL"))
    migrations = await _check_migrations(session)
    items.append(Item("migrations", "Migrations at head", "ok" if migrations.get("status") == "ok" else "todo", str(migrations.get("status")),
                      "" if migrations.get("status") == "ok" else "Run alembic upgrade head.", "OPERATIONS.md"))
    redis = await _check_redis()
    items.append(Item("redis", "Redis reachable", "ok" if redis.get("status") == "ok" else "warn", str(redis.get("status")),
                      "" if redis.get("status") == "ok" else "Start Redis or fix REDIS_URL; the platform degrades to no caching and no rate budgets.", "REDIS_URL"))
    worker = await _check_worker(session, now)
    items.append(Item("worker", "Trading worker running", "ok" if worker["status"] == "ok" else ("todo" if worker["status"] == "never_seen" else "warn"),
                      worker["status"] + (f", {worker['seconds_since_heartbeat']}s ago" if "seconds_since_heartbeat" in worker else ""),
                      "" if worker["status"] == "ok" else "Start or restart the worker service.", "dashboard"))
    cors_open = "*" in config.ALLOWED_ORIGINS
    items.append(Item("cors", "CORS restricted to the frontend origin", "ok" if not cors_open else ("todo" if prod else "warn"),
                      ", ".join(config.ALLOWED_ORIGINS), "" if not cors_open else "Set ALLOWED_ORIGINS to the frontend's https origin.", "ALLOWED_ORIGINS"))
    items.append(Item("frontend_url", "Public frontend URL set (OAuth redirects, links in emails)", "ok" if config.FRONTEND_URL not in ("", "/") else "warn", config.FRONTEND_URL,
                      "" if config.FRONTEND_URL not in ("", "/") else "Set FRONTEND_URL to the public https address; the Upstox OAuth redirect is derived from it.", "FRONTEND_URL"))
    items.append(Item("smtp", "Platform SMTP configured", "ok" if mailer.configured() else "warn", config.PLATFORM_SMTP_HOST or "not set",
                      "" if mailer.configured() else "Set PLATFORM_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM; without it email verification, password reset and email alerts are disabled.", "PLATFORM_SMTP_HOST"))
    items.append(Item("push", "Web push keys (optional)", "ok" if webpush.configured() else "info", "set" if webpush.configured() else "not set",
                      "" if webpush.configured() else "Generate VAPID keys (python -m app.alerts.webpush) and set VAPID_PRIVATE_KEY/VAPID_SUBJECT for browser push alerts.", "VAPID_PRIVATE_KEY"))
    razorpay = config.BILLING_PROVIDER == "razorpay"
    keys = bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET and config.RAZORPAY_WEBHOOK_SECRET)
    items.append(Item("billing", "Payment gateway", "ok" if (razorpay and keys) else ("todo" if razorpay else "info"),
                      f"{config.BILLING_PROVIDER}" + ("" if not razorpay else (" with keys" if keys else " without complete keys")),
                      "" if (razorpay and keys) or not razorpay else "Set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET.",
                      "BILLING_PROVIDER"))
    items.append(Item("metrics_token", "Metrics endpoint protected", "ok" if config.METRICS_TOKEN else ("warn" if prod else "info"), "token set" if config.METRICS_TOKEN else "open",
                      "" if config.METRICS_TOKEN else "Set METRICS_TOKEN and configure Prometheus with the bearer token.", "METRICS_TOKEN"))
    master = await master_status(session)
    fresh = [m for m in master if (_age_days(m["synced_at"], now) or 99) <= MASTER_MAX_AGE_DAYS]
    items.append(Item("instrument_master", "Instrument master synced", "ok" if fresh else ("warn" if master else "todo"),
                      "; ".join(f"{m['broker']}/{m['exchange']}: {m['rows']}" for m in (fresh or master)) or "empty",
                      "" if fresh else "Sync the instrument master (worker daily job or the Instruments page).", "instruments"))
    year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    holidays = int(await session.scalar(select(func.count()).select_from(MarketHolidayRecord).where(MarketHolidayRecord.holiday_date >= year_start.date())) or 0)
    items.append(Item("holidays", "Exchange holiday calendar loaded", "ok" if holidays else "warn", f"{holidays} from {now.year}",
                      "" if holidays else "Load the NSE holiday list (POST /api/market-holidays).", "settings"))
    global_switch = await session.scalar(select(KillSwitchRecord).where(KillSwitchRecord.scope == "GLOBAL", KillSwitchRecord.engaged.is_(True)))
    items.append(Item("global_kill_switch", "Global kill switch disengaged", "ok" if not global_switch else "warn", "disengaged" if not global_switch else (global_switch.reason or "engaged"),
                      "" if not global_switch else "Disengage from the Admin console once the cause is resolved.", "admin"))
    uncertain = int(await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.broker_uncertain_since.is_not(None))) or 0)
    items.append(Item("broker_uncertain", "No organisation blocked on reconciliation", "ok" if not uncertain else "warn", f"{uncertain} organisation(s) blocked",
                      "" if not uncertain else "See Admin > Tenants for the reasons.", "admin"))
    live_streams = config.STREAMING_QUOTES_ENABLED
    items.append(Item("streaming", "Streaming quotes (optional)", "ok" if live_streams else "info", "enabled" if live_streams else "REST polling",
                      "" if live_streams else "Set STREAMING_QUOTES_ENABLED=true after the first live confirmation of the tick decoders.", "STREAMING_QUOTES_ENABLED"))
    return Checklist(target="PLATFORM", items=items)
