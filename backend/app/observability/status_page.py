"""Z10-e (G7): the public status page, `GET /api/status` (no login), read by the `/status` page of the app.

Built only from what the platform already measures:

* **API** - the database round-trip of `/api/system/health/deep` (and Redis: unreachable reads as degraded).
* **Worker** - the trading worker's heartbeat age against its cycle, the same rule as `/api/system/health/deep`.
* **Data feed** - the newest exchange EOD file ingested and whether it covers the last closed session (BT1
  `eod_as_of` / `eod_staleness`), whether NSE is open, and how many live sessions through users' own brokers (Tier A,
  `byob_data_tier`) the worker keeps open: a count, nothing about whose.
* **Alert delivery** - the outbox lag: how long the oldest alert that is due has been waiting (SLO-8 says 60 s).
* **Banner** - maintenance mode with the operator's message, and the platform-wide incidents that are not resolved
  (`app/incidents`), as a severity band, a state and a start time.

What it never says: error text, versions, commits, migration revisions, host names, latencies, component internals,
anything per tenant or per user (organisation-scoped incidents are left out, and an incident's title, summary and
cause stay in the admin console), counts of users, positions or orders.

The flag `public_status_page` is off by default: while it is off the endpoint answers 404. When the database does not
answer the flag cannot be read, so the endpoint answers 503 with an outage body and nothing else - that is the moment a
status page exists for. The body is computed at most once every `CACHE_SECONDS` per process, and each IP is rate-limited.
Nothing here writes, and nothing here touches an order.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AlertDeliveryStatus
from app.core.rate_limit import rate_limit
from app.db.models import AlertDeliveryRecord, IncidentRecord
from app.db.session import get_session

FLAG = "public_status_page"
CACHE_SECONDS = 15.0
# Alert outbox lag bands. The worker drains the outbox once per cycle (60 s), so a due row may wait about a cycle; three
# cycles is the same "stale" line the worker heartbeat uses. Fifteen minutes is an outage.
OUTBOX_LAG_OK_SECONDS = 180
OUTBOX_LAG_DEGRADED_SECONDS = 900

OPERATIONAL, DEGRADED, OUTAGE, UNKNOWN = "operational", "degraded", "outage", "unknown"
_RANK = {UNKNOWN: 0, OPERATIONAL: 1, DEGRADED: 2, OUTAGE: 3}

router = APIRouter(tags=["system"])
# Module-level so tests can target it through `app.dependency_overrides`.
status_rate_limit = rate_limit("public_status", limit=60, window_seconds=60)

_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def reset_cache() -> None:
    """Tests: forget the cached body."""
    _cache.clear()


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _minutes(seconds: float) -> str:
    minutes = int(seconds // 60)
    return "under a minute" if minutes < 1 else ("1 minute" if minutes == 1 else f"{minutes} minutes")


def worst(states: List[str]) -> str:
    known = [s for s in states if s != UNKNOWN]
    return max(known, key=lambda s: _RANK[s]) if known else UNKNOWN


def _api(redis: Dict[str, Any]) -> Dict[str, Any]:
    if redis.get("status") == "unreachable":
        return {"state": DEGRADED, "summary": "Answering requests. Some pages may be slower than usual."}
    return {"state": OPERATIONAL, "summary": "Answering requests."}


def _worker(check: Dict[str, Any]) -> Dict[str, Any]:
    status = check.get("status")
    age = check.get("seconds_since_heartbeat")
    out: Dict[str, Any] = {"seconds_since_heartbeat": age}
    if status == "ok":
        return {**out, "state": OPERATIONAL, "summary": "Strategies, scans and alerts are being processed."}
    if status == "stale_market_open":
        return {**out, "state": OUTAGE, "summary": f"The engine has not checked in for {_minutes(age or 0)} during market hours. "
                "Strategies, scans and alerts are paused until it is back."}
    if status == "stale":
        return {**out, "state": DEGRADED, "summary": f"The engine has not checked in for {_minutes(age or 0)}. The market is closed."}
    return {"seconds_since_heartbeat": None, "state": UNKNOWN, "summary": "The engine has not reported yet."}


async def _data_feed(session: AsyncSession, now: datetime, market_open: Optional[bool]) -> Dict[str, Any]:
    from app.market_data.calendar import load_holidays, market_session_status
    from app.market_data.entitlement import eod_as_of, eod_staleness
    from app.market_data.fanin import FLAG as BYOB_FLAG, eligible_sessions
    from app.platform.controls import feature_flags

    if market_open is None:
        market_open = (await market_session_status(session, now)).is_open
    eod_date = await eod_as_of(session)
    stale = eod_staleness(eod_date, now, await load_holidays(session)) if eod_date is not None else None
    if eod_date is None:
        eod = {"state": UNKNOWN, "date": None, "summary": "No end-of-day data has been loaded yet."}
    elif stale:
        eod = {"state": DEGRADED, "date": eod_date.isoformat(),
               "summary": f"End-of-day data is from {eod_date.isoformat()}; the latest session's file has not been loaded yet. "
                          "Alerts on stale data are held back."}
    else:
        eod = {"state": OPERATIONAL, "date": eod_date.isoformat(), "summary": f"End-of-day data is up to date ({eod_date.isoformat()})."}

    flag = (await feature_flags(session)).get(BYOB_FLAG) or {}
    enabled = bool(flag.get("on") or flag.get("tenants"))
    sessions = len(await eligible_sessions(session, now)) if enabled and market_open else 0
    if not enabled:
        tier_a_summary = "Live data through your own broker is not enabled."
    elif not market_open:
        tier_a_summary = "Live sessions through users' own brokers open with the market."
    else:
        tier_a_summary = f"{sessions} live session{'s' if sessions != 1 else ''} through users' own brokers."
    return {"state": eod["state"], "summary": eod["summary"], "market_open": market_open, "eod": eod,
            "tier_a": {"enabled": enabled, "sessions": sessions, "summary": tier_a_summary}}


async def _alerts(session: AsyncSession, now: datetime) -> Dict[str, Any]:
    oldest = await session.scalar(select(func.min(AlertDeliveryRecord.next_attempt_at)).where(
        AlertDeliveryRecord.status == AlertDeliveryStatus.PENDING.value, AlertDeliveryRecord.next_attempt_at <= now))
    if oldest is None:
        return {"state": OPERATIONAL, "lag_seconds": 0, "summary": "Alerts are being delivered."}
    lag = max(0, int((now - (oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc))).total_seconds()))
    if lag <= OUTBOX_LAG_OK_SECONDS:
        return {"state": OPERATIONAL, "lag_seconds": lag, "summary": "Alerts are being delivered."}
    state = DEGRADED if lag <= OUTBOX_LAG_DEGRADED_SECONDS else OUTAGE
    return {"state": state, "lag_seconds": lag, "summary": f"Telegram, email and push alerts are delayed by about {_minutes(lag)}."}


async def _banner(session: AsyncSession) -> Dict[str, Any]:
    from app.platform.controls import status as control_status

    controls = await control_status(session)
    rows = await session.scalars(select(IncidentRecord).where(
        IncidentRecord.tenant_id.is_(None), IncidentRecord.status.in_(("OPEN", "MITIGATED"))).order_by(IncidentRecord.started_at.desc()).limit(5))
    incidents = [{"impact": "major" if r.severity in ("CRITICAL", "EMERGENCY") else "minor",
                  "state": "monitoring" if r.status == "MITIGATED" else "investigating",
                  "started_at": _iso(r.started_at)} for r in rows]
    return {"maintenance": bool(controls.get("maintenance_mode")), "maintenance_message": controls.get("maintenance_message"),
            "incidents": incidents}


def _outage(now: datetime) -> Dict[str, Any]:
    unknown = {"state": UNKNOWN, "summary": "Not known while the platform is unavailable."}
    return {"status": OUTAGE, "checked_at": now.isoformat(),
            "components": {"api": {"state": OUTAGE, "summary": "The platform is not answering requests."},
                           "worker": {**unknown, "seconds_since_heartbeat": None},
                           "data_feed": {**unknown, "market_open": None, "eod": {**unknown, "date": None},
                                         "tier_a": {"enabled": None, "sessions": None, "summary": unknown["summary"]}},
                           "alerts": {**unknown, "lag_seconds": None}},
            "banner": {"maintenance": False, "maintenance_message": None, "incidents": []}}


async def build(session: AsyncSession, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The public body; None while the flag is off. Raises nothing for a component that fails - it reads as unknown."""
    from app.observability.routes import _check_db, _check_redis, _check_worker
    from app.platform.controls import flag_enabled

    now = now or datetime.now(timezone.utc)
    if (await _check_db(session))["status"] != "ok":
        return _outage(now)
    if not await flag_enabled(session, FLAG):
        return None
    unknown = {"state": UNKNOWN, "summary": "Not known right now."}
    try:
        worker_check = await _check_worker(session, now)
    except Exception:  # noqa: BLE001 - one failing probe reads as unknown, never as a 500 on the status page
        worker_check = {"status": "unknown"}
    components: Dict[str, Any] = {"api": _api(await _check_redis()), "worker": _worker(worker_check)}
    try:
        market_open = worker_check.get("market_open")
        components["data_feed"] = await _data_feed(session, now, market_open if isinstance(market_open, bool) else None)
    except Exception:  # noqa: BLE001
        components["data_feed"] = {**unknown, "market_open": None, "eod": {**unknown, "date": None},
                                   "tier_a": {"enabled": None, "sessions": None, "summary": unknown["summary"]}}
    try:
        components["alerts"] = await _alerts(session, now)
    except Exception:  # noqa: BLE001
        components["alerts"] = {**unknown, "lag_seconds": None}
    try:
        banner = await _banner(session)
    except Exception:  # noqa: BLE001
        banner = {"maintenance": False, "maintenance_message": None, "incidents": []}
    overall = worst([c["state"] for c in components.values()])
    if any(i["impact"] == "major" for i in banner["incidents"]):
        overall = OUTAGE
    elif banner["incidents"] and overall == OPERATIONAL:
        overall = DEGRADED
    return {"status": overall, "checked_at": now.isoformat(), "components": components, "banner": banner}


@router.get("/api/status", dependencies=[Depends(status_rate_limit)])
async def public_status(response: Response, session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Unauthenticated, read-only. 404 while the `public_status_page` flag is off; 503 with an outage body when the
    database does not answer."""
    cached = _cache.get("body")
    if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
        body = cached[1]
    else:
        body_or_none = await build(session)
        if body_or_none is None:
            _cache.pop("body", None)
            raise HTTPException(status_code=404, detail="The status page is not enabled.")
        body = body_or_none
        if body["components"]["api"]["state"] != OUTAGE:
            _cache["body"] = (time.monotonic(), body)
    response.headers["Cache-Control"] = f"public, max-age={int(CACHE_SECONDS)}"
    if body["components"]["api"]["state"] == OUTAGE:
        response.status_code = 503
    return body
