"""MO3 addition (a): the admin console's read-only sections - System, Users & business, Support and the Diagnostics
drawer (docs/owner-orders/ATP_HANDOVER_PACK.md section 8a, docs/design/UX_AUDIT_FULL.md section 6).

Every endpoint here is a GET, SUPER_ADMIN only, and needs a two-factor-verified session (the router's dependencies), so
nothing tenant-facing can reach it. It changes nothing: Controls keep using the existing write endpoints
(`/api/admin/controls/*`, the kill switch), and LIVE stays locked - nothing here reads or writes `live_trading`.

What is shown is what an operator needs and no more: e-mail addresses are partly masked in lists, IP addresses are cut
to their network part, error texts are cut short, and no secret, key, token, prompt or AI answer text is ever returned.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_mfa_session, require_role
from app.core.config import ACTIVATION_FIRST_SESSION_HOURS, WORKER_CYCLE_SECONDS
from app.db.models import (
    ActivationEventRecord,
    AlertDeliveryRecord,
    BrokerCredentialRecord,
    DataQualityEventRecord,
    InstrumentRecord,
    JournalEntryRecord,
    LlmCallRecord,
    LoginEventRecord,
    MarketSnapshotRecord,
    StrategyDeploymentRecord,
    Tenant,
    User,
    UserSessionRecord,
    WorkerHeartbeatRecord,
)
from app.db.session import get_session
from app.growth import activation as activation_steps
from app.plans.registry import get_plan

router = APIRouter(prefix="/api/admin/console", tags=["admin"],
                   dependencies=[Depends(require_role()), Depends(require_mfa_session)])

# A worker other than the trading worker beats on its own rhythm; quiet for longer than this is worth a look.
QUIET_AFTER_SECONDS = 15 * 60
ERROR_TEXT_MAX = 160


def mask_email(email: Optional[str]) -> Optional[str]:
    """`abhi.wasu@example.com` -> `ab*******@example.com`: enough to tell two people apart, not to copy an address."""
    if not email:
        return email
    local, at, domain = email.partition("@")
    if not at:
        return f"{email[:2]}***"
    return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"


def org_name(name: Optional[str]) -> Optional[str]:
    """An organisation is named after its owner's e-mail at sign-up; such a name is masked like the address."""
    return mask_email(name) if name and "@" in name else name


def mask_ip(ip: Optional[str]) -> Optional[str]:
    """The network part only: `203.0.113.7` -> `203.0.113.x`, an IPv6 address -> its first three groups."""
    if not ip:
        return ip
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + ":x"
    parts = ip.split(".")
    return ".".join(parts[:3] + ["x"]) if len(parts) == 4 else "x"


def _short(text: Optional[str], limit: int = ERROR_TEXT_MAX) -> Optional[str]:
    if not text:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    aware = _aware(value)
    return aware.isoformat() if aware else None


def _device(user_agent: Optional[str]) -> Optional[str]:
    """A plain device label from a user agent, never the raw string."""
    if not user_agent:
        return None
    ua = user_agent.lower()
    system = next((name for needle, name in (("android", "Android"), ("iphone", "iPhone"), ("ipad", "iPad"), ("windows", "Windows"),
                                             ("mac os", "Mac"), ("linux", "Linux")) if needle in ua), "Other")
    browser = next((name for needle, name in (("edg/", "Edge"), ("chrome/", "Chrome"), ("firefox/", "Firefox"), ("safari/", "Safari"))
                    if needle in ua), "browser")
    return f"{browser} on {system}"


# --- System ----------------------------------------------------------------------------------------------------------

async def _workers(session: AsyncSession, now: datetime) -> List[Dict[str, Any]]:
    out = []
    for w in await session.scalars(select(WorkerHeartbeatRecord).order_by(WorkerHeartbeatRecord.worker_name)):
        seen = _aware(w.last_seen_at)
        age = int((now - seen).total_seconds()) if seen else None
        limit = WORKER_CYCLE_SECONDS * 3 if w.worker_name == "trading_worker" else QUIET_AFTER_SECONDS
        state = "never_seen" if age is None else ("ok" if age <= limit else "stale")
        out.append({"name": w.worker_name, "state": state, "seconds_since_heartbeat": age, "last_seen_at": _iso(seen),
                    "started_at": _iso(w.started_at), "cycles": w.cycle_count, "last_cycle_ms": w.last_cycle_ms,
                    "last_error": _short(w.last_error)})
    return out


async def _data_feed(session: AsyncSession, now: datetime) -> Dict[str, Any]:
    from app.brokers.circuit_breaker import all_breakers
    from app.market_data.calendar import market_session_status

    market = await market_session_status(session, now)
    last_memory = _aware(await session.scalar(select(func.max(MarketSnapshotRecord.captured_at))))
    memory_last_hour = await session.scalar(select(func.count()).select_from(MarketSnapshotRecord)
                                            .where(MarketSnapshotRecord.captured_at >= now - timedelta(hours=1))) or 0
    instruments = await session.scalar(select(func.count()).select_from(InstrumentRecord)) or 0
    instruments_synced = _aware(await session.scalar(select(func.max(InstrumentRecord.synced_at))))
    quality = dict((await session.execute(
        select(DataQualityEventRecord.kind, func.count())
        .where(DataQualityEventRecord.tenant_id.is_(None), DataQualityEventRecord.detected_at >= now - timedelta(hours=24))
        .group_by(DataQualityEventRecord.kind))).all())
    return {
        "market_open": market.is_open, "market_reason": market.reason, "next_open": _iso(market.next_open),
        "market_memory": {"last_captured_at": _iso(last_memory),
                          "minutes_since": int((now - last_memory).total_seconds() // 60) if last_memory else None,
                          "snapshots_last_hour": int(memory_last_hour)},
        "instrument_master": {"instruments": int(instruments), "last_synced_at": _iso(instruments_synced)},
        "data_quality_24h": {str(k): int(v) for k, v in quality.items()},
        "broker_circuits": [{"broker": b["broker"], "state": b["state"], "failures_in_window": b["failures_in_window"],
                             "calls_in_window": b["calls_in_window"]}
                            for b in (br.snapshot() for br in all_breakers().values())],
    }


@router.get("/system")
async def system(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Health (database, migrations, cache, the Phase 0 posture), every worker's heartbeat, the data feed and (M66 EN-1)
    the private package's signal engine: installed or not, name / version / tag / interface, or why not and what the
    owner does. Admin-only (this router); never on the public /api/system/status."""
    from app.engine_provider import loader as trade_engine
    from app.observability.routes import deep_health

    now = datetime.now(timezone.utc)
    health = await deep_health(Response(), session)
    return {"checked_at": now.isoformat(), "health": health, "workers": await _workers(session, now),
            "data_feed": await _data_feed(session, now), "trade_engine": trade_engine.describe(admin=True),
            "broker_calls": await broker_calls_today()}


async def broker_calls_today() -> Dict[str, Any]:
    """M71 PL-2: today's broker REST calls by broker / pool / endpoint kind (HTTP sent and budgeted) - read-only."""
    from app.brokers.broker_limits import calls_today
    try:
        return await calls_today()
    except Exception:  # noqa: BLE001 - the counters never fail the admin page
        return {}


# --- Users & business ------------------------------------------------------------------------------------------------

@router.get("/users")
async def users(q: Optional[str] = None, limit: int = Query(200, ge=1, le=500),
                session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Every person on the platform, newest first: masked e-mail, role, organisation and its plan, sign-up date and
    when they were last seen (their latest session use). Read-only."""
    last_seen = (select(UserSessionRecord.user_id, func.max(UserSessionRecord.last_used_at).label("seen"))
                 .group_by(UserSessionRecord.user_id).subquery())
    query = (select(User, Tenant, last_seen.c.seen).join(Tenant, Tenant.id == User.tenant_id)
             .join(last_seen, last_seen.c.user_id == User.id, isouter=True))
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(func.lower(User.email).like(pattern) | func.lower(Tenant.name).like(pattern))
    rows = (await session.execute(query.order_by(User.id.desc()).limit(limit))).all()
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    since = datetime.now(timezone.utc) - timedelta(days=7)
    active_7d = await session.scalar(select(func.count(func.distinct(UserSessionRecord.user_id)))
                                     .where(UserSessionRecord.last_used_at >= since)) or 0
    signups_7d = await session.scalar(select(func.count()).select_from(User).where(User.created_at >= since)) or 0
    return {
        "totals": {"users": int(total), "active_7d": int(active_7d), "signups_7d": int(signups_7d)},
        "users": [{
            "id": u.id, "email": mask_email(u.email), "role": u.role, "active": u.is_active, "mfa": u.mfa_enabled,
            "email_verified": u.email_verified_at is not None, "tenant_id": t.id, "tenant": org_name(t.name),
            "plan": get_plan(t.plan).name, "tenant_status": t.status, "signed_up_at": _iso(u.created_at), "last_seen_at": _iso(seen),
        } for u, t, seen in rows],
    }


# MO4 slice 3 (flag `growth_v2`): the activation events, from rows that already exist - no tracker, no new table.
ACTIVATION_STEPS = (
    ("broker", "Connected a broker"),
    ("paper_bot", "Started a first trading bot"),
    ("journal", "First journal entry"),
)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


@router.get("/activation")
async def activation(days: int = Query(90, ge=1, le=365), limit: int = Query(200, ge=1, le=500),
                     admin: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Who reached which first step after signing up: a broker connected (the first broker credential they added), a
    first trading bot (the first PAPER deployment they created) and a first journal entry (their first auto-captured
    journal row). "Activated" = all three. C13 adds `growth_funnel` (signed up -> broker -> trading bot -> active in week 1)
    and each person's `active_week1` (null while the first week is still running). AUD-G2.4 adds `value_funnel` (the five
    activation steps with the median hours to each, and the first-session share). Read-only; masked e-mail addresses;
    behind `growth_v2`."""
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, "growth_v2", admin.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": "growth_v2"})
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    broker = (select(BrokerCredentialRecord.user_id.label("uid"), func.min(BrokerCredentialRecord.created_at).label("at"))
              .group_by(BrokerCredentialRecord.user_id).subquery())
    bot = (select(StrategyDeploymentRecord.created_by.label("uid"), func.min(StrategyDeploymentRecord.created_at).label("at"))
           .where(StrategyDeploymentRecord.mode == "PAPER", StrategyDeploymentRecord.created_by.is_not(None))
           .group_by(StrategyDeploymentRecord.created_by).subquery())
    journal = (select(JournalEntryRecord.user_id.label("uid"), func.min(JournalEntryRecord.captured_at).label("at"))
               .group_by(JournalEntryRecord.user_id).subquery())
    rows = (await session.execute(
        select(User, Tenant, broker.c.at, bot.c.at, journal.c.at).join(Tenant, Tenant.id == User.tenant_id)
        .join(broker, broker.c.uid == User.id, isouter=True).join(bot, bot.c.uid == User.id, isouter=True)
        .join(journal, journal.c.uid == User.id, isouter=True)
        .where(User.created_at >= since, User.role != "SUPER_ADMIN").order_by(User.id.desc()))).all()
    returned = await _came_back(session, [(u.id, _aware(u.created_at)) for u, *_ in rows])
    reached_at = await _activation_steps(session, [u.id for u, *_ in rows])
    reached = {key: 0 for key, _ in ACTIVATION_STEPS}
    hours: List[float] = []
    people: List[Dict[str, Any]] = []
    growth = {key: 0 for key, _ in GROWTH_FUNNEL}
    week1_open = 0
    for u, t, broker_at, bot_at, journal_at in rows:
        steps = {"broker": _aware(broker_at), "paper_bot": _aware(bot_at), "journal": _aware(journal_at)}
        for key, at in steps.items():
            reached[key] += at is not None
        signed_up = _aware(u.created_at)
        present = [at for at in steps.values() if at is not None]
        activated_at = max(present) if len(present) == len(steps) else None
        if activated_at is not None and signed_up is not None:
            hours.append(max(0.0, (activated_at - signed_up).total_seconds() / 3600))
        week1_done = signed_up is not None and now - signed_up >= WEEK_ONE
        active_week1 = (u.id in returned) if week1_done else None
        if week1_done:
            # C13: the growth funnel, in order - each step counts the people who also took every step before it.
            chain = [True, steps["broker"] is not None, steps["paper_bot"] is not None, bool(active_week1)]
            for (key, _), ok in zip(GROWTH_FUNNEL, _prefix_all(chain)):
                growth[key] += ok
        else:
            week1_open += 1
        if len(people) < limit:
            people.append({"id": u.id, "email": mask_email(u.email), "tenant": org_name(t.name), "plan": get_plan(t.plan).name,
                           "signed_up_at": _iso(signed_up), **{f"{k}_at": _iso(v) for k, v in steps.items()},
                           "activated_at": _iso(activated_at), "active_week1": active_week1})
    total = len(rows)
    median = _median(hours)
    return {
        "days": days, "since": since.isoformat(),
        "funnel": [{"key": "signed_up", "label": "Signed up", "count": total}]
                  + [{"key": key, "label": label, "count": reached[key]} for key, label in ACTIVATION_STEPS]
                  + [{"key": "activated", "label": "All three (activated)", "count": len(hours)}],
        "median_hours_to_activation": round(median, 1) if median is not None else None,
        "growth_funnel": {"steps": [{"key": key, "label": label, "count": growth[key]} for key, label in GROWTH_FUNNEL],
                          "week1_open": week1_open},
        # AUD-G2.4: signup -> broker_connect -> first_chart -> first_paper_trade -> first_journal_review, from the
        # activation_events rows (the steps atp_activation_total{step} counts), with the median hours from signing up to
        # each step and the share whose first paper trade came within their first session.
        "value_funnel": activation_steps.value_funnel(
            [(_aware(u.created_at), reached_at.get(u.id, {})) for u, *_ in rows], now=now,
            first_session_hours=ACTIVATION_FIRST_SESSION_HOURS),
        "people": people,
    }


async def _activation_steps(session: AsyncSession, user_ids: List[int]) -> Dict[int, Dict[str, datetime]]:
    """AUD-G2.4: {user id: {step: first reached at}} for these people."""
    out: Dict[int, Dict[str, datetime]] = {}
    for start in range(0, len(user_ids), 500):
        chunk = user_ids[start:start + 500]
        for uid, step, at in (await session.execute(
                select(ActivationEventRecord.user_id, ActivationEventRecord.step, ActivationEventRecord.reached_at)
                .where(ActivationEventRecord.user_id.in_(chunk)))).all():
            out.setdefault(uid, {})[step] = at
    return out


# Readiness C13: the growth funnel - signed up -> broker -> trading bot -> active in week 1. Only people whose first week is
# over are counted (the others are `week1_open`). "Active in week 1" = signed in or used the app again on a later day:
# a successful sign-in, or a session started or used, between one day and seven days after signing up.
GROWTH_FUNNEL = (
    ("signed_up", "Signed up"),
    ("broker", "Connected a broker"),
    ("paper_bot", "Started a trading bot"),
    ("active_week1", "Active in week 1"),
)
WEEK_ONE = timedelta(days=7)
CAME_BACK_AFTER = timedelta(days=1)


def _prefix_all(flags: List[bool]) -> List[bool]:
    """[a, b, c] -> [a, a and b, a and b and c]."""
    out: List[bool] = []
    ok = True
    for flag in flags:
        ok = ok and flag
        out.append(ok)
    return out


async def _came_back(session: AsyncSession, people: List[tuple]) -> set:
    """The ids of the people who signed in or used the app between one and seven days after signing up."""
    signed = {uid: at for uid, at in people if at is not None}
    if not signed:
        return set()
    ids = list(signed)
    seen: List[tuple] = []
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        seen += (await session.execute(select(LoginEventRecord.user_id, LoginEventRecord.created_at)
                                       .where(LoginEventRecord.user_id.in_(chunk), LoginEventRecord.success.is_(True)))).all()
        for uid, created, used in (await session.execute(select(UserSessionRecord.user_id, UserSessionRecord.created_at, UserSessionRecord.last_used_at)
                                                        .where(UserSessionRecord.user_id.in_(chunk)))).all():
            seen += [(uid, created), (uid, used)]
    out = set()
    for uid, at in seen:
        start, when = signed.get(uid), _aware(at)
        if start is not None and when is not None and start + CAME_BACK_AFTER <= when <= start + WEEK_ONE:
            out.add(uid)
    return out


def _tier(feature: str) -> str:
    from app.ai.providers import TASK_TIERS
    return TASK_TIERS.get(feature, "-")


def _price(provider: str, model: str) -> Optional[Dict[str, Any]]:
    from app.ai import pricing
    price, estimated = pricing.price_for(provider, model)
    if price is None:
        return None
    return {"input_per_m_usd": price[0], "output_per_m_usd": price[1], "estimated": bool(estimated)}


@router.get("/ai-cost")
async def ai_cost(days: int = Query(30, ge=1, le=365), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """AI spend from the call log (`llm_calls`): per organisation, and per task with its tier, provider, model, tokens,
    the list price and the cost. Calls on a user's own key are counted apart (the user pays the provider)."""
    from app.ai import pricing

    since = datetime.now(timezone.utc) - timedelta(days=days)
    own_key_cost = func.sum(case((LlmCallRecord.billed_to_user_key.is_(True), LlmCallRecord.cost_usd), else_=0.0))
    failed = func.sum(case((LlmCallRecord.status != "ok", 1), else_=0))
    base = [func.count(), func.sum(LlmCallRecord.input_tokens), func.sum(LlmCallRecord.output_tokens), func.sum(LlmCallRecord.cost_usd),
            own_key_cost, failed]
    per_tenant = (await session.execute(
        select(LlmCallRecord.tenant_id, Tenant.name, Tenant.plan, *base).join(Tenant, Tenant.id == LlmCallRecord.tenant_id)
        .where(LlmCallRecord.created_at >= since).group_by(LlmCallRecord.tenant_id, Tenant.name, Tenant.plan)
        .order_by(func.sum(LlmCallRecord.cost_usd).desc()))).all()
    per_task = (await session.execute(
        select(LlmCallRecord.feature, LlmCallRecord.provider, LlmCallRecord.model, *base)
        .where(LlmCallRecord.created_at >= since)
        .group_by(LlmCallRecord.feature, LlmCallRecord.provider, LlmCallRecord.model)
        .order_by(func.sum(LlmCallRecord.cost_usd).desc()))).all()

    def nums(calls, tin, tout, cost, own, bad) -> Dict[str, Any]:
        usd = float(cost or 0.0)
        return {"calls": int(calls or 0), "input_tokens": int(tin or 0), "output_tokens": int(tout or 0), "cost_usd": round(usd, 4),
                "cost_inr": round(pricing.usd_to_inr(usd), 2), "own_key_cost_usd": round(float(own or 0.0), 4), "failed": int(bad or 0)}

    tenants = [{"tenant_id": tid, "tenant": org_name(name), "plan": get_plan(plan).name, **nums(*rest)} for tid, name, plan, *rest in per_tenant]
    tasks = [{"task": feature, "tier": _tier(feature), "provider": provider, "model": model, "price": _price(provider, model), **nums(*rest)}
             for feature, provider, model, *rest in per_task]
    total_usd = sum(t["cost_usd"] for t in tenants)
    return {"days": days, "since": since.isoformat(), "usd_inr": pricing.usd_inr_rate(),
            "total": {"calls": sum(t["calls"] for t in tenants), "cost_usd": round(total_usd, 4),
                      "cost_inr": round(pricing.usd_to_inr(total_usd), 2)},
            "tenants": tenants, "tasks": tasks}


# --- Support ---------------------------------------------------------------------------------------------------------

@router.get("/support/users/{user_id}")
async def support_user(user_id: int, session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """One person's status for a support question: their account, the organisation's broker connections (status
    only), recent sign-ins (masked network, a device label), bots with an error and failed AI calls. Never a secret."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown user")
    tenant = await session.get(Tenant, user.tenant_id)
    now = datetime.now(timezone.utc)
    seen = _aware(await session.scalar(select(func.max(UserSessionRecord.last_used_at)).where(UserSessionRecord.user_id == user.id)))
    brokers = list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
                                         .order_by(BrokerCredentialRecord.broker_name, BrokerCredentialRecord.account_label)))
    logins = list(await session.scalars(select(LoginEventRecord).where(LoginEventRecord.user_id == user.id)
                                        .order_by(LoginEventRecord.id.desc()).limit(10)))
    bots = list(await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == user.tenant_id, StrategyDeploymentRecord.last_error.is_not(None))
        .order_by(StrategyDeploymentRecord.id.desc()).limit(10)))
    ai_failed = list(await session.scalars(select(LlmCallRecord).where(
        LlmCallRecord.user_id == user.id, LlmCallRecord.status != "ok", LlmCallRecord.created_at >= now - timedelta(days=7))
        .order_by(LlmCallRecord.id.desc()).limit(10)))
    dead = await session.scalar(select(func.count()).select_from(AlertDeliveryRecord).where(
        AlertDeliveryRecord.tenant_id == user.tenant_id, AlertDeliveryRecord.status.in_(("DEAD", "FAILED")))) or 0
    return {
        "user": {"id": user.id, "email": mask_email(user.email), "role": user.role, "active": user.is_active, "mfa": user.mfa_enabled,
                 "email_verified": user.email_verified_at is not None, "trading_paused": bool(user.trading_disabled_reason),
                 "signed_up_at": _iso(user.created_at), "last_seen_at": _iso(seen)},
        "tenant": {"id": tenant.id, "name": org_name(tenant.name), "plan": get_plan(tenant.plan).name, "status": tenant.status} if tenant else None,
        "brokers": [{"broker": b.broker_name, "account": b.account_label, "status": b.token_status,
                     "expires_at": _iso(b.token_expires_at), "last_verified_at": _iso(b.last_verified_at)} for b in brokers],
        "sign_ins": [{"at": _iso(e.created_at), "success": e.success, "reason": e.reason or None, "network": mask_ip(e.ip_address),
                      "device": _device(e.user_agent)} for e in logins],
        "bots_with_errors": [{"id": d.id, "strategy": d.strategy_id, "symbol": d.symbol, "mode": d.mode, "status": d.status,
                              "error": _short(d.last_error), "failures_in_a_row": d.consecutive_failures} for d in bots],
        "ai_failures_7d": [{"at": _iso(c.created_at), "task": c.feature, "provider": c.provider, "status": _short(c.status, 120)}
                           for c in ai_failed],
        "alert_deliveries_failed": int(dead),
    }


# --- Diagnostics drawer ----------------------------------------------------------------------------------------------

@router.get("/diagnostics")
async def diagnostics(limit: int = Query(20, ge=1, le=100), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """What already went wrong, in one place: the last server errors with their request ids (this process, in memory),
    worker errors, bots with an error, failed AI calls, failed alert deliveries, data-quality events and failed
    sign-ins. Each row is cut short; the logs hold the rest (search them by the request id)."""
    from app.observability import recent_errors

    now = datetime.now(timezone.utc)
    bots = list(await session.scalars(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.last_error.is_not(None))
                                      .order_by(StrategyDeploymentRecord.id.desc()).limit(limit)))
    ai_failed = list(await session.scalars(select(LlmCallRecord).where(LlmCallRecord.status != "ok")
                                           .order_by(LlmCallRecord.id.desc()).limit(limit)))
    deliveries = dict((await session.execute(select(AlertDeliveryRecord.status, func.count())
                                             .where(AlertDeliveryRecord.status.in_(("DEAD", "FAILED")))
                                             .group_by(AlertDeliveryRecord.status))).all())
    quality = list(await session.scalars(select(DataQualityEventRecord).where(DataQualityEventRecord.tenant_id.is_(None))
                                         .order_by(DataQualityEventRecord.id.desc()).limit(limit)))
    failed_sign_ins = await session.scalar(select(func.count()).select_from(LoginEventRecord).where(
        LoginEventRecord.success.is_(False), LoginEventRecord.created_at >= now - timedelta(hours=24))) or 0
    return {
        "checked_at": now.isoformat(),
        "server_errors": recent_errors.recent(limit),
        "worker_errors": [w for w in await _workers(session, now) if w["last_error"]],
        "bots_with_errors": [{"id": d.id, "tenant_id": d.tenant_id, "strategy": d.strategy_id, "symbol": d.symbol, "mode": d.mode,
                              "status": d.status, "error": _short(d.last_error), "failures_in_a_row": d.consecutive_failures,
                              "last_evaluated_at": _iso(d.last_evaluated_at)} for d in bots],
        "ai_failures": [{"at": _iso(c.created_at), "tenant_id": c.tenant_id, "task": c.feature, "provider": c.provider,
                         "status": _short(c.status, 120)} for c in ai_failed],
        "alert_deliveries_failed": {str(k): int(v) for k, v in deliveries.items()},
        "data_quality": [{"at": _iso(e.detected_at), "kind": e.kind, "instrument": e.instrument_key, "timeframe": e.timeframe,
                          "source": e.source} for e in quality],
        "failed_sign_ins_24h": int(failed_sign_ins),
    }
