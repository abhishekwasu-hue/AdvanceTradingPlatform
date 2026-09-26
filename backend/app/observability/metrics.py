"""Phase E1: Prometheus metrics.

One registry for the process (API or worker), exposed at `GET /metrics` on the API (optionally
token-protected, see `METRICS_TOKEN`) and on a small HTTP server in the worker
(`WORKER_METRICS_PORT`). Two kinds of series:

* **Counters/histograms incremented where things happen** - HTTP requests (route template, not
  raw path, so ids do not explode the label space), orders by mode and final status, login
  attempts, worker cycles and their duration, alert deliveries. These are process-local: the API
  and the worker each report what *they* did.
* **Gauges refreshed from the database at scrape time** (`refresh_db_gauges`) - active
  deployments, open positions, pending alert outbox, worker heartbeat age, login failures in the
  last 15 minutes. These describe the platform's state and are the ones to alert on (a heartbeat
  age above three cycles on a trading day is the "engine is down" signal).

`prometheus_client` metrics are registered once per process; the module guards re-import (tests
reload modules) by reusing existing collectors from the default registry.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertDeliveryRecord, LoginEventRecord, StrategyDeploymentRecord, TradeRecord, WorkerHeartbeatRecord

NAMESPACE = "atp"


def _existing(name: str):
    return REGISTRY._names_to_collectors.get(name)  # noqa: SLF001 - documented-enough internal


def _counter(name: str, doc: str, labels=()):
    return _existing(f"{NAMESPACE}_{name}_total") or _existing(f"{NAMESPACE}_{name}") or Counter(name, doc, labels, namespace=NAMESPACE)


def _gauge(name: str, doc: str, labels=()):
    return _existing(f"{NAMESPACE}_{name}") or Gauge(name, doc, labels, namespace=NAMESPACE)


def _histogram(name: str, doc: str, labels=(), buckets=Histogram.DEFAULT_BUCKETS):
    return _existing(f"{NAMESPACE}_{name}") or Histogram(name, doc, labels, namespace=NAMESPACE, buckets=buckets)


# --- API ---------------------------------------------------------------------------------------
HTTP_REQUESTS = _counter("http_requests", "HTTP requests by method, route template and status class", ("method", "route", "status"))
HTTP_LATENCY = _histogram("http_request_duration_seconds", "HTTP request latency by route template", ("method", "route"),
                          buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))

# --- trading -----------------------------------------------------------------------------------
ORDERS = _counter("orders", "Order attempts by mode and final status", ("mode", "status"))
LOGIN_ATTEMPTS = _counter("login_attempts", "Login attempts by outcome", ("success", "reason"))
ALERT_DELIVERIES = _counter("alert_deliveries", "Out-of-app alert deliveries by channel type and outcome", ("channel", "status"))

# --- worker ------------------------------------------------------------------------------------
WORKER_CYCLES = _counter("worker_cycles", "Trading worker cycles by market state", ("market_open",))
WORKER_CYCLE_SECONDS = _histogram("worker_cycle_duration_seconds", "Trading worker cycle wall time",
                                  buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60))
WORKER_SIGNALS = _counter("worker_signals_executed", "Signals the worker executed")
WORKER_CLOSES = _counter("worker_positions_closed", "Positions the worker closed")
WORKER_ERRORS = _counter("worker_errors", "Errors recorded on worker cycle reports")
WORKER_LAST_CYCLE = _gauge("worker_last_cycle_timestamp_seconds", "Unix time of the worker's last completed cycle (this process)")
RETENTION_DELETED = _counter("retention_rows_deleted", "Rows deleted by the retention job", ("table",))

# --- platform state (DB-derived, refreshed per scrape) -----------------------------------------
ACTIVE_DEPLOYMENTS = _gauge("active_deployments", "Deployments in ACTIVE status", ("mode",))
OPEN_POSITIONS = _gauge("open_positions", "Open trades", ("mode",))
ALERT_OUTBOX_PENDING = _gauge("alert_outbox_pending", "Alert deliveries waiting to be sent")
WORKER_HEARTBEAT_AGE = _gauge("worker_heartbeat_age_seconds", "Seconds since the trading worker's last heartbeat row (-1 = never)")
LOGIN_FAILURES_15M = _gauge("login_failures_15m", "Failed login attempts in the last 15 minutes")


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


def observe_http(method: str, route: str, status_code: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=status_class(status_code)).inc()
    HTTP_LATENCY.labels(method=method, route=route).observe(seconds)


def observe_cycle(*, market_open: bool, seconds: float, signals: int, closes: int, errors: int) -> None:
    WORKER_CYCLES.labels(market_open=str(market_open).lower()).inc()
    WORKER_CYCLE_SECONDS.observe(seconds)
    if signals:
        WORKER_SIGNALS.inc(signals)
    if closes:
        WORKER_CLOSES.inc(closes)
    if errors:
        WORKER_ERRORS.inc(errors)
    WORKER_LAST_CYCLE.set(datetime.now(timezone.utc).timestamp())


async def refresh_db_gauges(session: AsyncSession, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    rows = await session.execute(
        select(StrategyDeploymentRecord.mode, func.count()).where(StrategyDeploymentRecord.status == "ACTIVE")
        .group_by(StrategyDeploymentRecord.mode)
    )
    counts = dict(rows.all())
    for mode in ("PAPER", "LIVE"):
        ACTIVE_DEPLOYMENTS.labels(mode=mode).set(counts.get(mode, 0))

    rows = await session.execute(
        select(TradeRecord.mode, func.count()).where(TradeRecord.exit_time.is_(None)).group_by(TradeRecord.mode)
    )
    counts = dict(rows.all())
    for mode in ("PAPER", "LIVE"):
        OPEN_POSITIONS.labels(mode=mode).set(counts.get(mode, 0))

    pending = await session.scalar(select(func.count()).select_from(AlertDeliveryRecord).where(AlertDeliveryRecord.status == "PENDING"))
    ALERT_OUTBOX_PENDING.set(pending or 0)

    heartbeat = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
    if heartbeat is None or heartbeat.last_seen_at is None:
        WORKER_HEARTBEAT_AGE.set(-1)
    else:
        seen = heartbeat.last_seen_at if heartbeat.last_seen_at.tzinfo else heartbeat.last_seen_at.replace(tzinfo=timezone.utc)
        WORKER_HEARTBEAT_AGE.set(max(0.0, (now - seen).total_seconds()))

    failures = await session.scalar(
        select(func.count()).select_from(LoginEventRecord)
        .where(LoginEventRecord.success.is_(False), LoginEventRecord.created_at >= now - timedelta(minutes=15))
    )
    LOGIN_FAILURES_15M.set(failures or 0)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
