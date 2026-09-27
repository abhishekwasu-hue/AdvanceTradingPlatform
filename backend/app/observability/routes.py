"""Phase E1: `/metrics` (Prometheus) and the structured health checks.

* `GET /api/system/health`       - liveness: the process answers. Unchanged, unauthenticated.
* `GET /api/system/health/deep`  - readiness with detail: database round-trip, Redis (optional
  component - "disabled"/"unreachable" degrade but do not fail), migrations at head, trading
  worker heartbeat age against its cycle. 200 when the database answers, 503 otherwise, and the
  body says which component is why. Unauthenticated, no secrets in the body.
* `GET /metrics`                 - Prometheus text format. Outside `/api` so the nginx front does
  not expose it to browsers; when `METRICS_TOKEN` is set the scraper must send it as a bearer.
"""
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.config import WORKER_CYCLE_SECONDS
from app.brokers.circuit_breaker import all_breakers
from app.db.models import Tenant, WorkerHeartbeatRecord
from app.db.session import get_session
from app.market_data.calendar import market_session_status
from app.observability.metrics import refresh_db_gauges, render

router = APIRouter(tags=["system"])


async def _check_db(session: AsyncSession) -> Dict[str, object]:
    started = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "down", "error": type(exc).__name__}


async def _check_redis() -> Dict[str, object]:
    if not config.REDIS_URL:
        return {"status": "disabled"}
    try:
        from app.cache.client import _get_client
        started = time.perf_counter()
        pong = await _get_client().ping()
        return {"status": "ok" if pong else "unreachable", "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unreachable", "error": type(exc).__name__}


async def _check_migrations(session: AsyncSession) -> Dict[str, object]:
    try:
        current = await session.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:  # noqa: BLE001 - tests run on create_all without the version table
        return {"status": "unknown", "detail": "no alembic_version table"}
    try:
        from pathlib import Path
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        ini = Path(__file__).resolve().parents[2] / "alembic.ini"
        if not ini.exists():
            return {"status": "unknown", "current": current, "detail": "alembic.ini not shipped"}
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(ini.parent / "alembic"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "current": current, "error": type(exc).__name__}
    return {"status": "ok" if current == head else "behind", "current": current, "head": head}


async def _check_worker(session: AsyncSession, now: datetime) -> Dict[str, object]:
    record = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
    if record is None or record.last_seen_at is None:
        return {"status": "never_seen"}
    seen = record.last_seen_at if record.last_seen_at.tzinfo else record.last_seen_at.replace(tzinfo=timezone.utc)
    age = (now - seen).total_seconds()
    stale = age > WORKER_CYCLE_SECONDS * 3
    market = await market_session_status(session, now)
    status = "ok" if not stale else ("stale_market_open" if market.is_open else "stale")
    return {"status": status, "seconds_since_heartbeat": int(age), "cycle_seconds": WORKER_CYCLE_SECONDS,
            "market_open": market.is_open, "last_error": record.last_error}


@router.get("/api/system/health/deep")
async def deep_health(response: Response, session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    now = datetime.now(timezone.utc)
    db = await _check_db(session)
    checks = {"database": db}
    if db["status"] == "ok":
        checks["migrations"] = await _check_migrations(session)
        checks["worker"] = await _check_worker(session, now)
    checks["redis"] = await _check_redis()

    if db["status"] != "ok":
        overall = "down"
    elif checks["redis"]["status"] == "unreachable" or checks.get("worker", {}).get("status") == "stale_market_open" \
            or checks.get("migrations", {}).get("status") == "behind":
        overall = "degraded"
    else:
        overall = "ok"
    response.status_code = 503 if overall == "down" else 200
    return {"status": overall, "checked_at": now.isoformat(), "environment": config.ENVIRONMENT, "checks": checks}


@router.get("/api/system/ready")
@router.get("/api/system/health/ready")
async def ready(response: Response, session: AsyncSession = Depends(get_session)) -> Dict[str, str]:
    """Readiness for an orchestrator: only the database matters for serving requests.
    `/api/system/health/ready` is the master-prompt (V4.9) spelling of the same probe."""
    db = await _check_db(session)
    response.status_code = 200 if db["status"] == "ok" else 503
    return {"status": "ready" if db["status"] == "ok" else "not_ready"}


@router.get("/api/system/health/live")
async def live() -> Dict[str, str]:
    """Liveness (V4.9 spelling): the process answers. Same contract as `/api/system/health`."""
    return {"status": "ok"}


@router.get("/api/system/health/dependencies")
async def dependencies(response: Response, session: AsyncSession = Depends(get_session)) -> Dict[str, object]:
    """Every external dependency and safety gate in one view (V4.9): the deep health checks plus
    the per-broker circuit breakers (Phase G2) and how many organisations currently have LIVE
    entries blocked pending reconciliation (Phase G1). Same status code rules as /health/deep."""
    body = await deep_health(response, session)
    body["checks"]["broker_circuits"] = {name: b.snapshot() for name, b in all_breakers().items()}
    try:
        body["checks"]["broker_uncertain_tenants"] = int(
            await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.broker_uncertain_since.is_not(None))) or 0
        )
    except Exception:  # noqa: BLE001 - database down is already reported above
        body["checks"]["broker_uncertain_tenants"] = None
    if body["status"] == "ok" and any(b.state.value != "CLOSED" for b in all_breakers().values()):
        body["status"] = "degraded"
    return body


@router.get("/metrics", include_in_schema=False)
async def metrics(authorization: Optional[str] = Header(None), session: AsyncSession = Depends(get_session)) -> Response:
    token = config.METRICS_TOKEN
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="metrics token required")
    try:
        await refresh_db_gauges(session)
    except Exception:  # noqa: BLE001 - a DB outage must not hide the process metrics
        pass
    body, content_type = render()
    return Response(content=body, media_type=content_type)
