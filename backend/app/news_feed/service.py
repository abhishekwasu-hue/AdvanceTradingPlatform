"""Phase BB: the news feed service - one shared ingest for every organisation, keyword
classification on the shared row, AI classification per organisation with its key, alerts and
monitoring-agent proposals.

Rules (master plan, Phase BB as approved):
* FEED rows are `verified=False`, carry the item's URL and are shown as "unverified feed".
* Severity >= 4 from the keyword rules -> a NEWS_ALERT notification to every interested
  organisation; **never** a proposal on keywords alone.
* A monitoring-agent proposal (REDUCE_RISK, or PAUSE_DEPLOYMENT at severity 5) needs either the
  organisation's own AI classification at severity >= 4, or two different sources reporting the
  same event (same type and overlapping scope, both severity >= 4, within `CONFIRM_HOURS`). At most
  one proposal per event per organisation, whatever the path (`rule = NEWS:<hash>`), and the human
  still approves it (ADR-0006); it never touches an exit (ADR-0004).
* Fetch cadence: every `NORMAL_SECONDS`, tightened to `EVENT_SECONDS` within `EVENT_WINDOW_MINUTES`
  of a global macro event on the Risk Guardian calendar (`market_events`, tenant_id NULL).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import monitor
from app.ai.providers import RuleBasedProvider
from app.audit.log import write_audit_log
from app.billing.service import meter
from app.core.enums import Bias, DeploymentStatus, NotificationSeverity, NotificationType
from app.db.models import (AiActionRecord, AiProviderConfigRecord, MarketEventRecord, NewsClassificationRecord, NewsEventRecord,
                           StrategyDeploymentRecord, Tenant, TraderProfileRecord, User)
from app.fundamentals.models import SourceCitation
from app.market_data.calendar import IST
from app.news_feed import classify as cl
from app.news_feed.sources import BY_ID, SOURCES, FeedItem, fetch_many
from app.notifications.service import notify
from app.platform import controls

logger = logging.getLogger(__name__)

FLAG = "news_feed"
KEY_SOURCES = "news_feed_sources"
NORMAL_SECONDS = 900
EVENT_SECONDS = 300
EVENT_WINDOW_MINUTES = 60
ALERT_SEVERITY = 4
CONFIRM_HOURS = 6
CLASSIFY_BATCH = 20
CLASSIFY_MIN_KEYWORD_SEVERITY = 3
CLASSIFY_LOOKBACK_HOURS = 72
RULE_PREFIX = "NEWS:"
METRIC = "ai_news_classify"
_last_run: Dict[str, object] = {}


# --- settings ------------------------------------------------------------------------------------
async def enabled(session: AsyncSession, tenant_id: Optional[int] = None) -> bool:
    return await controls.flag_enabled(session, FLAG, tenant_id)


async def source_settings(session: AsyncSession) -> Dict[str, bool]:
    stored = (await controls._get(session, KEY_SOURCES)).get("sources") or {}
    return {s.id: bool(stored.get(s.id, s.default_on)) for s in SOURCES}


async def set_source(session: AsyncSession, source_id: str, on: bool, user: Optional[User]) -> Dict[str, bool]:
    if source_id not in BY_ID:
        raise KeyError(source_id)
    current = await controls._get(session, KEY_SOURCES)
    sources = dict(current.get("sources") or {})
    sources[source_id] = bool(on)
    await controls._set(session, KEY_SOURCES, {"sources": sources}, user)
    if user is not None:
        await write_audit_log(session, None, user.id, "news_feed_source_toggled", f"{source_id}: {'on' if on else 'off'} (terms: {BY_ID[source_id].terms})")
    await session.commit()
    return await source_settings(session)


def source_rows(settings: Dict[str, bool]) -> List[dict]:
    return [{"id": s.id, "name": s.name, "publisher": s.publisher, "url": s.url, "category": s.category.value, "official": s.official,
             "default_on": s.default_on, "on": settings.get(s.id, s.default_on), "terms": s.terms} for s in SOURCES]


# --- cadence -------------------------------------------------------------------------------------
async def cadence_seconds(session: AsyncSession, now: datetime) -> int:
    """5 minutes within +/- EVENT_WINDOW_MINUTES of a global macro event today, 15 otherwise."""
    ist = now.astimezone(IST)
    events = list(await session.scalars(select(MarketEventRecord).where(MarketEventRecord.event_date == ist.date(), MarketEventRecord.tenant_id.is_(None))))
    window = timedelta(minutes=EVENT_WINDOW_MINUTES)
    for event in events:
        start = _hhmm(event.start_time, dtime(9, 0))
        end = _hhmm(event.end_time, dtime(15, 30) if not event.start_time else start)
        begin_dt = datetime.combine(ist.date(), start, tzinfo=IST) - window
        end_dt = datetime.combine(ist.date(), end, tzinfo=IST) + window
        if begin_dt <= ist <= end_dt:
            return EVENT_SECONDS
    return NORMAL_SECONDS


def _hhmm(raw: Optional[str], default: dtime) -> dtime:
    try:
        hh, mm = (raw or "").split(":")
        return dtime(int(hh), int(mm))
    except (ValueError, AttributeError):
        return default


def due(last: Optional[datetime], now: datetime, cadence: int) -> bool:
    return last is None or (now - last).total_seconds() >= cadence


# --- ingest --------------------------------------------------------------------------------------
def _row_from_item(item: FeedItem, now: datetime) -> NewsEventRecord:
    source = BY_ID[item.source_id]
    classification = cl.keyword_classify(item.title, item.summary)
    direction = classification["direction"]
    sentiment = Bias.BULLISH if direction == "BULLISH" else Bias.BEARISH if direction == "BEARISH" else Bias.NEUTRAL
    category = source.category.value if not classification["symbols"] else "CORPORATE"
    if classification["type"] == "RATE_DECISION" and source.id != "rbi_press":
        category = "GLOBAL_MACRO"
    published = item.published_at or now
    citation = SourceCitation(source=source.name, source_url=item.link or None, publication_date=published.astimezone(IST).date(),
                              confidence=60.0 if source.official else 40.0)
    return NewsEventRecord(
        category=category, headline=item.title[:500], description=None, event_date=published.astimezone(IST).date(),
        affected_symbols_json=json.dumps(classification["symbols"]), sentiment=sentiment.value, source_json=citation.model_dump_json(),
        created_by=None, origin="FEED", source_url=(item.link or None), verified=False, dedupe_hash=item.dedupe_hash, feed_id=source.id,
        published_at=published, classification_json=json.dumps(classification, ensure_ascii=False),
    )


async def interested_tenants(session: AsyncSession) -> List[int]:
    """Organisations that trade or use the Copilot: an active/paused deployment or a trader profile."""
    with_deployments = set(await session.scalars(select(StrategyDeploymentRecord.tenant_id).where(
        StrategyDeploymentRecord.status.in_([DeploymentStatus.ACTIVE.value, DeploymentStatus.PAUSED.value])).distinct()))
    with_profiles = set(await session.scalars(select(TraderProfileRecord.tenant_id).distinct()))
    return sorted(with_deployments | with_profiles)


def classification_of(row: NewsEventRecord) -> dict:
    try:
        return json.loads(row.classification_json or "{}")
    except ValueError:
        return {}


async def ingest(session: AsyncSession, now: Optional[datetime] = None, *, client: Optional[httpx.AsyncClient] = None) -> dict:
    """Fetch every enabled source once, store the new items as unverified FEED rows, alert on high
    severity, propose on confirmation. Returns the run's counts (also kept in `_last_run`)."""
    now = now or datetime.now(timezone.utc)
    settings = await source_settings(session)
    sources = [s for s in SOURCES if settings.get(s.id)]
    items, errors = await fetch_many(client, sources)
    hashes = [i.dedupe_hash for i in items]
    existing = set(await session.scalars(select(NewsEventRecord.dedupe_hash).where(NewsEventRecord.dedupe_hash.in_(hashes)))) if hashes else set()
    new_rows: List[NewsEventRecord] = []
    seen = set(existing)
    for item in items:
        if item.dedupe_hash in seen:
            continue
        seen.add(item.dedupe_hash)
        row = _row_from_item(item, now)
        session.add(row)
        new_rows.append(row)
    await session.commit()
    for row in new_rows:
        await session.refresh(row)
    alerts = proposals = 0
    if new_rows:
        tenants = await interested_tenants(session)
        for row in new_rows:
            c = classification_of(row)
            if int(c.get("severity", 1)) >= ALERT_SEVERITY:
                alerts += await alert_tenants(session, row, c, tenants)
                if await confirmed_by_second_source(session, row, c):
                    for tenant_id in tenants:
                        proposals += await propose(session, tenant_id, row, int(c["severity"]), "two sources", now)
    result = {"at": now.isoformat(), "sources": [s.id for s in sources], "fetched": len(items), "new": len(new_rows), "duplicates": len(items) - len(new_rows),
              "errors": errors, "alerts": alerts, "proposals": proposals}
    _last_run.clear()
    _last_run.update(result)
    return result


async def alert_tenants(session: AsyncSession, row: NewsEventRecord, c: dict, tenants: List[int]) -> int:
    severity = NotificationSeverity.CRITICAL if int(c.get("severity", 1)) >= 5 else NotificationSeverity.WARNING
    sent = 0
    for tenant_id in tenants:
        await notify(session, tenant_id, NotificationType.NEWS_ALERT,
                     title=f"News (unverified feed, severity {c.get('severity')}): {row.headline[:120]}",
                     message=f"{c.get('one_line_en', '')}\n{c.get('one_line_mr', '')}\nSource: {BY_ID.get(row.feed_id).name if row.feed_id in BY_ID else row.feed_id} - {row.source_url or ''}\n"
                             "Unverified feed item: read the source before acting. No position was changed.",
                     severity=severity)
        sent += 1
    return sent


async def confirmed_by_second_source(session: AsyncSession, row: NewsEventRecord, c: dict) -> bool:
    """Another source reported the same kind of event (same type, overlapping scope, severity >= 4)
    within CONFIRM_HOURS of this one."""
    return await same_event(session, row, c, other_source_only=True) is not None


async def same_event(session: AsyncSession, row: NewsEventRecord, c: dict, *, other_source_only: bool = False) -> Optional[NewsEventRecord]:
    """The earliest FEED row describing the same event as `row` (same type, overlapping scope,
    severity >= 4, within CONFIRM_HOURS), or None. Two headlines about one rate cut are one event:
    proposals key on the earliest row so an organisation gets at most one per event."""
    anchor = row.published_at or row.created_at
    anchor = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
    window = timedelta(hours=CONFIRM_HOURS)
    others = await session.scalars(select(NewsEventRecord).where(
        NewsEventRecord.origin == "FEED", NewsEventRecord.id != row.id,
        NewsEventRecord.created_at >= anchor - window - timedelta(hours=24)))
    scope = set(c.get("scope") or [])
    best: Optional[NewsEventRecord] = None
    for other in others:
        if other_source_only and other.feed_id == row.feed_id:
            continue
        when = other.published_at or other.created_at
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        if abs((when - anchor).total_seconds()) > window.total_seconds():
            continue
        oc = classification_of(other)
        # P0.8 / A7: both classifications must name an overlapping scope - an empty scope used to match everything, which
        # let two unrelated severity-4 items "confirm" each other.
        if oc.get("type") == c.get("type") and int(oc.get("severity", 1)) >= ALERT_SEVERITY and (scope & set(oc.get("scope") or [])):
            if best is None or other.id < best.id:
                best = other
    return best


INDEX_SYMBOLS = ("NIFTY", "BANKNIFTY", "BANK NIFTY", "FINNIFTY", "FIN NIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX")


def deployment_matches(dep: StrategyDeploymentRecord, c: dict) -> bool:
    """P0.8 / A7: the news names this deployment's instrument - the symbol itself, or an index deployment for INDEX-scope
    news (a rate decision, a market-wide halt). A stock deployment is never paused for news about another stock, and
    nothing is paused because it happened to be the first active row."""
    symbol = (dep.symbol or "").upper()
    compact = symbol.replace(" ", "")
    symbols = {str(s).upper() for s in (c.get("symbols") or [])}
    scope = {str(s).upper() for s in (c.get("scope") or [])}
    if symbol in symbols or compact in symbols:
        return True
    return "INDEX" in scope and any(compact.startswith(ix.replace(" ", "")) for ix in INDEX_SYMBOLS)


def _rule(row: NewsEventRecord) -> str:
    return (RULE_PREFIX + (row.dedupe_hash or str(row.id)))[:40]


async def propose(session: AsyncSession, tenant_id: int, row: NewsEventRecord, severity: int, basis: str, now: datetime,
                  classification: Optional[dict] = None) -> int:
    """At most one monitoring-agent proposal per event per organisation; nothing on exits. The event
    is the earliest feed row about it, so a second headline on the same decision adds nothing."""
    c = classification or classification_of(row)
    earliest = await same_event(session, row, c)
    anchor = earliest if earliest is not None and earliest.id < row.id else row
    rule = _rule(anchor)
    exists = await session.scalar(select(AiActionRecord.id).where(AiActionRecord.tenant_id == tenant_id, AiActionRecord.rule == rule).limit(1))
    if exists is not None:
        return 0
    active = list(await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.status == DeploymentStatus.ACTIVE.value).order_by(StrategyDeploymentRecord.id)))
    if not active:
        return 0
    target = next((d for d in active if deployment_matches(d, c)), None)
    if severity >= 5:
        if target is None:
            # P0.8 / A7: a pause names a deployment; without a symbol/scope match there is nothing to pause on evidence - the
            # organisation already has the alert, and a wrong deployment must never be paused "because it was first".
            return 0
        action, deployment_id = "PAUSE_DEPLOYMENT", target.id
        what = f"pause {target.strategy_id} on {target.symbol} (new entries only; open positions keep their exits)"
    else:
        action, deployment_id = "REDUCE_RISK", None
        what = "reduce risk per trade until the dust settles"
    reason = (f"News ({basis}, severity {severity}, unverified feed): {row.headline[:160]}. Suggests: {what}. "
              f"Source: {row.source_url or row.feed_id}.")
    evidence = {"news_event_id": row.id, "event_anchor_id": anchor.id, "severity": severity, "basis": basis, "classification": c, "source_url": row.source_url}
    created = await monitor.raise_proposals(session, tenant_id, [monitor.Proposal(deployment_id, None, action, rule, reason, evidence)], now)
    return len(created)


# --- per-organisation AI classification -----------------------------------------------------------
async def classify_for_tenant(session: AsyncSession, tenant: Tenant, provider, *, now: Optional[datetime] = None, limit: int = CLASSIFY_BATCH,
                              min_keyword_severity: int = CLASSIFY_MIN_KEYWORD_SEVERITY) -> dict:
    """One batched provider call for the recent feed items this organisation has not classified
    yet (keyword severity >= min), cached per (tenant, item); proposals on AI severity >= 4."""
    now = now or datetime.now(timezone.utc)
    if isinstance(provider, RuleBasedProvider):
        return {"classified": 0, "proposals": 0, "skipped": "rule_based provider - keyword classification applies"}
    done = set(await session.scalars(select(NewsClassificationRecord.news_event_id).where(NewsClassificationRecord.tenant_id == tenant.id)))
    rows = list(await session.scalars(select(NewsEventRecord).where(
        NewsEventRecord.origin == "FEED", NewsEventRecord.created_at >= now - timedelta(hours=CLASSIFY_LOOKBACK_HOURS)).order_by(NewsEventRecord.id.desc())))
    pending = [r for r in rows if r.id not in done and int(classification_of(r).get("severity", 1)) >= min_keyword_severity][:limit]
    if not pending:
        return {"classified": 0, "proposals": 0, "skipped": "nothing new to classify"}
    text = await provider.complete(cl.SYSTEM_PROMPT, cl.ai_prompt([{"id": r.id, "title": r.headline, "published_at": r.published_at} for r in pending]), max_tokens=4000)
    parsed = cl.parse_ai(text, {r.id for r in pending})
    proposals = 0
    for row in pending:
        c = parsed.get(row.id)
        if c is None:
            continue
        session.add(NewsClassificationRecord(tenant_id=tenant.id, news_event_id=row.id, provider=getattr(provider, "name", "ai"), model=getattr(provider, "model", ""),
                                             severity=int(c["severity"]), classification_json=json.dumps(c, ensure_ascii=False)))
    await meter(session, tenant.id, METRIC, 1, source="worker", metadata={"items": len(pending), "parsed": len(parsed), "provider": getattr(provider, "name", "ai")}, commit=False)
    await session.commit()
    for row in pending:
        c = parsed.get(row.id)
        if c is not None and int(c["severity"]) >= ALERT_SEVERITY:
            proposals += await propose(session, tenant.id, row, int(c["severity"]), "AI classification", now, classification=c)
    return {"classified": len(parsed), "sent": len(pending), "proposals": proposals, "provider": getattr(provider, "name", "ai")}


async def classify_all(session: AsyncSession, now: datetime, provider_for=None) -> int:
    """Worker path: every organisation with an enabled provider key classifies its own batch."""
    from app.ai import settings as ai_settings
    provider_for = provider_for or ai_settings.provider_for
    classified = 0
    configs = list(await session.scalars(select(AiProviderConfigRecord).where(AiProviderConfigRecord.enabled.is_(True), AiProviderConfigRecord.encrypted_api_key.is_not(None))))
    for cfg in configs:
        tenant = await session.get(Tenant, cfg.tenant_id)
        if tenant is None or not await enabled(session, tenant.id):
            continue
        try:
            provider = await provider_for(session, tenant, task="classification")
            result = await classify_for_tenant(session, tenant, provider, now=now)
            classified += int(result.get("classified", 0))
            await ai_settings.mark_used(session, tenant.id, error=None)
        except Exception as exc:  # noqa: BLE001 - one organisation's key must not stop the others
            logger.warning("News classification for tenant %s failed: %s", cfg.tenant_id, exc)
            await ai_settings.mark_used(session, tenant.id, error=str(exc)[:200])
    await session.commit()
    return classified


# --- reads ---------------------------------------------------------------------------------------
async def items(session: AsyncSession, tenant_id: int, *, hours: int = 24, min_severity: int = 1, now: Optional[datetime] = None) -> List[dict]:
    now = now or datetime.now(timezone.utc)
    rows = list(await session.scalars(select(NewsEventRecord).where(
        NewsEventRecord.origin == "FEED", NewsEventRecord.created_at >= now - timedelta(hours=hours)).order_by(NewsEventRecord.id.desc()).limit(300)))
    mine = {c.news_event_id: c for c in await session.scalars(select(NewsClassificationRecord).where(
        NewsClassificationRecord.tenant_id == tenant_id, NewsClassificationRecord.news_event_id.in_([r.id for r in rows]) if rows else False))}
    out = []
    for row in rows:
        keyword = classification_of(row)
        ai = json.loads(mine[row.id].classification_json) if row.id in mine else None
        best = ai or keyword
        if int(best.get("severity", 1)) < min_severity:
            continue
        source = BY_ID.get(row.feed_id)
        out.append({"id": row.id, "headline": row.headline, "source": source.name if source else row.feed_id, "source_url": row.source_url,
                    "published_at": (row.published_at or row.created_at).isoformat(), "category": row.category, "symbols": json.loads(row.affected_symbols_json or "[]"),
                    "verified": bool(row.verified), "origin": row.origin, "classification": best, "keyword": keyword, "ai": ai})
    return out


def last_run() -> dict:
    return dict(_last_run)
