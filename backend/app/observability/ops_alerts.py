"""Production readiness A2: the server checks that message the platform operator (Abhi), with dedup and a recovery note.

Who evaluates: the server's scheduled check, `deploy/atp.sh ops-check`, every 5 minutes from the crontab block that
every `deploy` installs (`atp.sh schedule`). It collects the host facts the app cannot see (`atp.sh backup-status`, the
disk, the HTTPS certificate's days left) and hands them to `backend/scripts/ops_check.py` inside the backend container,
which runs `run()` here. It is deliberately not the trading worker: a worker that has stopped cannot report that it has
stopped. Admin > System > Alerts shows the same checks (`ops_routes.py`) and can run them once by hand.

The checks (thresholds are settings in app/core/config.py, the Prometheus rules' own numbers where a rule exists in
scripts/monitoring/prometheus-alerts.yml - nothing evaluates those rules on the 8 GB host, so they are evaluated here):

* error rate - the API's 5xx share since the previous check, from its own `/metrics` counters (ApiErrorBudgetBurn);
* trading worker - its heartbeat age (TradingWorkerStalled): FAIL during market hours, WARN otherwise;
* data feed - decisions refused on old market data since the previous check, from the worker's `/metrics`
  (MarketDataStale), and the end-of-day file not covering the last session (WARN);
* broker logins - counts only across all organisations: working, need a new login, and long-lived tokens (the Upstox
  Analytics Token kind) that expire within OPS_TOKEN_EXPIRY_WARN_DAYS;
* backups - `atp.sh backup-status` (the A1 hook) as it is; disk space; database size; certificate days left;
* alert delivery - the outbox backlog and dead letters (AlertOutboxBacklog); failed sign-ins in 15 minutes
  (CredentialStuffing).

Dedup: one row in `platform_controls` (`ops_alert_state`, no migration) keeps each check's last verdict, how many runs
in a row it has held and what was last sent. A WARN or FAIL is sent once it has held for OPS_ALERT_AFTER_CHECKS runs; a
FAIL still there is repeated every OPS_ALERT_REPEAT_HOURS; a change WARN <-> FAIL is sent; a return to OK after a sent
problem is sent as "OK again". UNKNOWN (a fact that could not be read) never sends and never clears a sent problem.
All changes of one run go out as one message. A message that reached no channel is tried again next run.

Where it goes: the operator's Telegram chat through the platform bot (registered once from the admin console with a
one-time code, or OPS_ALERT_TELEGRAM_CHAT_ID), and e-mail through PLATFORM_SMTP_* to OPS_ALERT_EMAIL (empty =
SUPER_ADMIN_EMAILS). Never a user's alert channel. Messages carry counts and plain states only: no e-mail address, no
organisation or user name, no token, no host name.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import (
    AlertDeliveryRecord, BrokerCredentialRecord, LoginEventRecord, NotificationLinkRecord, PlatformControlRecord, WorkerHeartbeatRecord,
)

logger = logging.getLogger(__name__)

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
STATE_KEY = "ops_alert_state"
CHANNEL_KEY = "ops_alert_channel"
LINK_CHANNEL = "tg_ops"                                      # NotificationLinkRecord.channel (String(16))
METRICS_TIMEOUT_SECONDS = 5.0

CHECKS: Tuple[Tuple[str, str, str], ...] = (
    ("error_rate", "API error rate", "app"),
    ("worker", "Trading worker", "app"),
    ("data_feed", "Market data feed", "app"),
    ("broker_tokens", "Broker logins", "app"),
    ("backup", "Backups", "server"),
    ("disk", "Disk space", "server"),
    ("db_size", "Database size", "app"),
    ("certificate", "HTTPS certificate", "server"),
    ("alert_outbox", "Alert delivery", "app"),
    ("sign_in_failures", "Failed sign-ins", "app"),
)
LABELS = {key: label for key, label, _ in CHECKS}

OPS_WELCOME = ("Connected. Server alerts for the AiAlgoDesk platform operator will come to this chat: backups, disk, "
               "the trading worker, the data feed and errors. Change it in the admin console, System, Alerts.")


@dataclass
class Check:
    key: str
    label: str
    state: str
    detail: str
    source: str = "app"


def _check(key: str, state: str, detail: str) -> Check:
    source = next((s for k, _, s in CHECKS if k == key), "app")
    return Check(key=key, label=LABELS.get(key, key), state=state, detail=detail, source=source)


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def worst(states: List[str]) -> str:
    rank = {UNKNOWN: 0, OK: 1, WARN: 2, FAIL: 3}
    known = [s for s in states if s != UNKNOWN]
    return max(known, key=lambda s: rank[s]) if known else UNKNOWN


# --- the stored rows (platform_controls) ----------------------------------------------------------------------------

async def _get(session: AsyncSession, key: str) -> Dict[str, Any]:
    row = await session.get(PlatformControlRecord, key)
    if row is None:
        return {}
    try:
        value = json.loads(row.value_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def _put(session: AsyncSession, key: str, value: Dict[str, Any], user_id: Optional[int] = None) -> None:
    row = await session.get(PlatformControlRecord, key)
    if row is None:
        row = PlatformControlRecord(key=key)
        session.add(row)
    row.value_json = json.dumps(value, sort_keys=True)
    row.updated_by = user_id


# --- Prometheus series ----------------------------------------------------------------------------------------------

def counter_sum(metrics_text: str, sample_name: str, **labels: str) -> Optional[float]:
    """The sum of one counter's samples whose labels match (a missing series = 0.0); None when the text is not readable."""
    from prometheus_client.parser import text_string_to_metric_families
    total = 0.0
    try:
        for family in text_string_to_metric_families(metrics_text):
            for sample in family.samples:
                if sample.name == sample_name and all(sample.labels.get(k) == v for k, v in labels.items()):
                    total += float(sample.value)
    except Exception:  # noqa: BLE001 - a malformed scrape reads as "not known"
        return None
    return total


async def fetch_metrics(url: str, *, client: Optional[httpx.AsyncClient] = None) -> Optional[str]:
    """One scrape inside the compose network (the API's own token when it has one). None when it does not answer."""
    if not url:
        return None
    headers = {"Authorization": f"Bearer {config.METRICS_TOKEN}"} if config.METRICS_TOKEN else {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=METRICS_TIMEOUT_SECONDS)
    try:
        response = await client.get(url, headers=headers)
        return response.text if response.status_code == 200 else None
    except httpx.HTTPError:
        return None
    finally:
        if own:
            await client.aclose()


def local_api_metrics() -> Optional[str]:
    """This API process's view (every API process's when PROMETHEUS_MULTIPROC_DIR is set) - for the admin "Check now"."""
    try:
        from app.observability.metrics import render
        return render()[0].decode()
    except Exception:  # noqa: BLE001
        return None


# --- the checks -----------------------------------------------------------------------------------------------------

def error_rate(metrics_text: Optional[str], previous: Dict[str, Any], now: datetime) -> Tuple[Check, Dict[str, Any]]:
    """The 5xx share of the API's requests since the previous sample (a counter that went down = a restart: new
    baseline). Returns the verdict and the sample to keep."""
    total = counter_sum(metrics_text, "atp_http_requests_total") if metrics_text else None
    errors = counter_sum(metrics_text, "atp_http_requests_total", status="5xx") if metrics_text else None
    if total is None or errors is None:
        return _check("error_rate", UNKNOWN, "The API's request counts could not be read."), previous
    sample = {"total": total, "errors": errors, "at": now.isoformat()}
    before_total, before_errors = previous.get("total"), previous.get("errors")
    if not isinstance(before_total, (int, float)) or not isinstance(before_errors, (int, float)) or total < before_total:
        return _check("error_rate", UNKNOWN, "Measuring: the next check compares against this one."), sample
    requests = int(total - before_total)
    failed = max(0, int(errors - before_errors))
    if requests < config.OPS_ERROR_RATE_MIN_REQUESTS:
        return _check("error_rate", OK, f"{_plural(requests, 'request')} since the last check, {failed} with a server error."), sample
    share = failed / requests if requests else 0.0
    state = FAIL if share > config.OPS_ERROR_RATE_MAX else OK
    return _check("error_rate", state, f"{share:.1%} of {_plural(requests, 'request')} since the last check failed with a server error."), sample


async def worker(session: AsyncSession, now: datetime) -> Check:
    from app.market_data.calendar import market_session_status
    record = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == "trading_worker"))
    seen = _utc(record.last_seen_at) if record is not None else None
    if seen is None:
        return _check("worker", WARN, "The trading worker has never checked in.")
    age = int((now - seen).total_seconds())
    minutes = max(1, age // 60)
    if age <= config.OPS_HEARTBEAT_MAX_SECONDS:
        return _check("worker", OK, "Checking in every cycle.")
    market_open = (await market_session_status(session, now)).is_open
    if market_open:
        return _check("worker", FAIL, f"No heartbeat for {_plural(minutes, 'minute')} during market hours: strategies, scans and alerts are paused.")
    return _check("worker", WARN, f"No heartbeat for {_plural(minutes, 'minute')} (market closed).")


async def data_feed(session: AsyncSession, now: datetime, worker_metrics: Optional[str], previous: Dict[str, Any]) -> Tuple[Check, Dict[str, Any]]:
    from app.market_data.calendar import load_holidays
    from app.market_data.entitlement import eod_as_of, eod_staleness
    parts: List[Tuple[str, str]] = []
    keep: Dict[str, Any] = dict(previous)
    refused = counter_sum(worker_metrics, "atp_market_data_stale_total") if worker_metrics else None
    if refused is not None:
        before = previous.get("refused")
        keep = {"refused": refused, "at": now.isoformat()}
        if isinstance(before, (int, float)) and refused >= before:
            delta = int(refused - before)
            if delta > config.OPS_FEED_STALE_REFUSALS:
                parts.append((FAIL, f"{_plural(delta, 'decision')} refused on old market data since the last check."))
            else:
                parts.append((OK, "Live decisions are getting fresh data."))
    eod_date = await eod_as_of(session)
    if eod_date is not None:
        stale = eod_staleness(eod_date, now, await load_holidays(session))
        parts.append((WARN, f"End-of-day data is from {eod_date.isoformat()}; the latest session's file is not loaded yet.") if stale
                     else (OK, f"End-of-day data is up to date ({eod_date.isoformat()})."))
    if not parts:
        return _check("data_feed", UNKNOWN, "No feed reading yet."), keep
    state = worst([s for s, _ in parts])
    detail = " ".join(d for s, d in parts if s == state) if state != OK else " ".join(d for _, d in parts)
    return _check("data_feed", state, detail), keep


async def broker_tokens(session: AsyncSession, now: datetime) -> Check:
    """Counts across every organisation. A daily login dies every night by design, so only long-lived tokens (expiry
    more than a day away) count as "expiring soon"."""
    total = int(await session.scalar(select(func.count()).select_from(BrokerCredentialRecord)) or 0)
    if total == 0:
        return _check("broker_tokens", OK, "No broker connection yet.")
    working = int(await session.scalar(select(func.count()).select_from(BrokerCredentialRecord).where(
        BrokerCredentialRecord.token_status == "VALID",
        (BrokerCredentialRecord.token_expires_at.is_(None)) | (BrokerCredentialRecord.token_expires_at > now))) or 0)
    soon = int(await session.scalar(select(func.count()).select_from(BrokerCredentialRecord).where(
        BrokerCredentialRecord.token_expires_at > now + timedelta(days=1),
        BrokerCredentialRecord.token_expires_at <= now + timedelta(days=config.OPS_TOKEN_EXPIRY_WARN_DAYS))) or 0)
    detail = f"{_plural(total, 'broker connection')}: {working:,} working, {total - working:,} need a new login"
    if soon:
        verb = "expires" if soon == 1 else "expire"
        return _check("broker_tokens", WARN, f"{detail}; {_plural(soon, 'long-lived token')} {verb} within {config.OPS_TOKEN_EXPIRY_WARN_DAYS} days.")
    return _check("broker_tokens", OK, detail + ".")


async def db_size(session: AsyncSession) -> Check:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return _check("db_size", UNKNOWN, "Measured on the production database only.")
    try:
        size = await session.scalar(text("SELECT pg_database_size(current_database())"))
    except Exception:  # noqa: BLE001
        await session.rollback()
        return _check("db_size", UNKNOWN, "The database size could not be read.")
    gb = float(size or 0) / 1024 ** 3
    state = FAIL if gb >= config.OPS_DB_SIZE_FAIL_GB else WARN if gb >= config.OPS_DB_SIZE_WARN_GB else OK
    return _check("db_size", state, f"{gb:.1f} GB.")


async def alert_outbox(session: AsyncSession) -> Check:
    pending = int(await session.scalar(select(func.count()).select_from(AlertDeliveryRecord).where(AlertDeliveryRecord.status == "PENDING")) or 0)
    dead = int(await session.scalar(select(func.count()).select_from(AlertDeliveryRecord).where(AlertDeliveryRecord.status == "DEAD")) or 0)
    if pending > config.OPS_OUTBOX_BACKLOG:
        return _check("alert_outbox", WARN, f"{_plural(pending, 'user alert')} waiting to be delivered.")
    if dead:
        return _check("alert_outbox", WARN, f"{_plural(dead, 'user alert')} failed after every retry (Admin, Controls, dead letters).")
    return _check("alert_outbox", OK, f"{pending:,} waiting.")


async def sign_in_failures(session: AsyncSession, now: datetime) -> Check:
    failures = int(await session.scalar(select(func.count()).select_from(LoginEventRecord).where(
        LoginEventRecord.success.is_(False), LoginEventRecord.created_at >= now - timedelta(minutes=15))) or 0)
    if failures > config.OPS_LOGIN_FAILURES_15M:
        return _check("sign_in_failures", WARN, f"{_plural(failures, 'failed sign-in')} in 15 minutes (a password-guessing run?).")
    return _check("sign_in_failures", OK, f"{failures:,} in 15 minutes.")


def server_checks(host: Optional[Dict[str, Any]], now: datetime) -> List[Check]:
    """Backups, disk and certificate from the host facts `atp.sh ops-check` collected."""
    host_row: Dict[str, Any] = host if isinstance(host, dict) else {}
    facts = host_row.get("facts")
    at = _parse(host_row.get("at"))
    if not isinstance(facts, dict) or at is None or now - at > timedelta(minutes=config.OPS_HOST_FACTS_MAX_MINUTES):
        why = ("The server's scheduled check has not reported yet." if at is None
               else f"The server's scheduled check last reported {int((now - at).total_seconds() // 60)} minutes ago.")
        return [_check(key, UNKNOWN, why) for key in ("backup", "disk", "certificate")]
    out: List[Check] = []

    backup = facts.get("backup")
    if isinstance(backup, dict) and backup.get("status") in (OK, WARN, FAIL):
        local = (backup.get("local") or {}).get("age_hours")
        offsite = backup.get("offsite") or {}
        if backup.get("problems"):
            detail = str(backup["problems"])[:300] + "."
        else:
            detail = f"Last backup {local} h ago" if isinstance(local, int) else "Backups recorded"
            detail += f", off-site copy {offsite.get('age_hours')} h ago." if isinstance(offsite.get("age_hours"), int) else "."
        out.append(_check("backup", str(backup["status"]), detail[0].upper() + detail[1:]))
    else:
        out.append(_check("backup", UNKNOWN, "The backup status could not be read."))

    used = facts.get("disk_used_percent")
    if isinstance(used, int):
        state = FAIL if used >= config.OPS_DISK_FAIL_PERCENT else WARN if used >= config.OPS_DISK_WARN_PERCENT else OK
        out.append(_check("disk", state, f"{used}% used."))
    else:
        out.append(_check("disk", UNKNOWN, "Disk use could not be read."))

    days = facts.get("cert_days_left")
    if isinstance(days, int):
        state = FAIL if days <= config.OPS_CERT_FAIL_DAYS else WARN if days <= config.OPS_CERT_WARN_DAYS else OK
        tail = " Renewal is failing: see the runbook, the certificate section." if state != OK else ""
        out.append(_check("certificate", state, f"Valid for {_plural(days, 'more day')}.{tail}"))
    elif facts.get("cert_note") == "ip_only":
        out.append(_check("certificate", UNKNOWN, "No public certificate: the server answers on an IP address."))
    else:
        out.append(_check("certificate", UNKNOWN, "The certificate could not be read."))
    return out


async def evaluate(session: AsyncSession, state: Dict[str, Any], *, now: datetime, api_metrics: Optional[str],
                   worker_metrics: Optional[str]) -> Tuple[List[Check], Dict[str, Any]]:
    """Every check, in CHECKS order, and the samples to keep for the next run's deltas."""
    samples = _dict(state.get("samples"))
    rate, rate_sample = error_rate(api_metrics, samples.get("http") or {}, now)
    feed, feed_sample = await data_feed(session, now, worker_metrics, samples.get("feed") or {})
    found = {c.key: c for c in [
        rate, await worker(session, now), feed, await broker_tokens(session, now), await db_size(session),
        await alert_outbox(session), await sign_in_failures(session, now), *server_checks(state.get("host"), now),
    ]}
    return [found[key] for key, _, _ in CHECKS], {"http": rate_sample, "feed": feed_sample}


# --- dedup ----------------------------------------------------------------------------------------------------------

def decide(previous: Dict[str, Dict[str, Any]], checks: List[Check], now: datetime, *, after: int, repeat_hours: float
           ) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    """Pure: the new per-check memory and the lines to send (kind problem | worse | better | reminder | recovered)."""
    memory: Dict[str, Dict[str, Any]] = {}
    lines: List[Dict[str, str]] = []
    for c in checks:
        before = previous.get(c.key) or {}
        same = before.get("state") == c.state
        entry: Dict[str, Any] = {"state": c.state, "streak": int(before.get("streak") or 0) + 1 if same else 1,
                 "since": before.get("since") if same and before.get("since") else now.isoformat(),
                 "alerted": before.get("alerted"), "alerted_at": before.get("alerted_at")}
        alerted = entry["alerted"]
        held = entry["streak"] >= after
        kind: Optional[str] = None
        if c.state in (WARN, FAIL) and held and alerted != c.state:
            kind = "problem" if alerted not in (WARN, FAIL) else ("worse" if c.state == FAIL else "better")
        elif c.state == FAIL and alerted == FAIL:
            last = _parse(entry["alerted_at"])
            if last is None or now - last >= timedelta(hours=repeat_hours):
                kind = "reminder"
        elif c.state == OK and alerted in (WARN, FAIL) and held:
            kind = "recovered"
        if kind is not None:
            entry["alerted"] = None if kind == "recovered" else c.state
            entry["alerted_at"] = now.isoformat()
            lines.append({"kind": kind, "key": c.key, "label": c.label, "state": c.state, "detail": c.detail})
        memory[c.key] = entry
    return memory, lines


_KIND_WORD = {"problem": "PROBLEM", "worse": "WORSE", "better": "STILL WRONG", "reminder": "STILL FAILING", "recovered": "OK AGAIN"}


def compose(lines: List[Dict[str, str]]) -> Tuple[str, str]:
    """The subject and the plain text of one message for all of a run's changes."""
    problems = sum(1 for line in lines if line["kind"] != "recovered")
    recovered = len(lines) - problems
    head = []
    if problems:
        head.append(_plural(problems, "problem"))
    if recovered:
        head.append(f"{recovered:,} back to normal")
    subject = f"AiAlgoDesk server check: {', '.join(head)}"
    order = sorted(lines, key=lambda line: (line["kind"] == "recovered", line["state"] != FAIL))
    body = [subject, ""]
    for line in order:
        state = {"fail": "FAIL", "warn": "WARNING", "ok": "OK"}.get(line["state"], line["state"].upper())
        body.append(f"{_KIND_WORD[line['kind']]} - {line['label']} ({state}): {line['detail']}")
    body += ["", "Details: admin console, System, Alerts."]
    return subject, "\n".join(body)


# --- where it goes --------------------------------------------------------------------------------------------------

def email_recipients() -> List[str]:
    configured = [e.strip() for e in config.OPS_ALERT_EMAIL.split(",") if e.strip()]
    return configured or sorted(config.SUPER_ADMIN_EMAILS)


async def telegram_chat(session: AsyncSession) -> Tuple[Optional[str], str]:
    """(chat id, where from: linked | env | none)."""
    linked = str((await _get(session, CHANNEL_KEY)).get("telegram_chat_id") or "")
    if linked:
        return linked, "linked"
    if config.OPS_ALERT_TELEGRAM_CHAT_ID:
        return config.OPS_ALERT_TELEGRAM_CHAT_ID, "env"
    return None, "none"


async def deliver(session: AsyncSession, subject: str, body: str, *, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Optional[bool]]:
    """Every operator channel. None = not set up; True / False = sent / failed."""
    from app.alerts import platform_bot
    from app.notifications import mailer
    out: Dict[str, Optional[bool]] = {"telegram": None, "email": None}
    chat, _ = await telegram_chat(session)
    if chat and platform_bot.configured():
        answer = await platform_bot.send_text(chat, body, client=client)
        out["telegram"] = bool(answer.get("ok"))
    recipients = email_recipients()
    if recipients and mailer.configured():
        results = [await mailer.send(to, subject, body) for to in recipients]
        out["email"] = any(results)
    return out


def delivered(result: Dict[str, Optional[bool]]) -> bool:
    return any(v is True for v in result.values())


# --- one run --------------------------------------------------------------------------------------------------------

async def run(session: AsyncSession, *, host_facts: Optional[Dict[str, Any]] = None, source: str = "server",
              now: Optional[datetime] = None, api_metrics: Optional[str] = None, worker_metrics: Optional[str] = None,
              send: bool = True, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """Evaluate, decide, send, store. `host_facts` (from `atp.sh ops-check`) replaces the stored ones; without them the
    stored ones are used while fresh. Commits."""
    now = _utc(now) or datetime.now(timezone.utc)
    state = await _get(session, STATE_KEY)
    if host_facts is not None:
        state["host"] = {"facts": host_facts, "at": now.isoformat()}
    checks, samples = await evaluate(session, state, now=now, api_metrics=api_metrics, worker_metrics=worker_metrics)
    previous = _dict(state.get("checks"))
    memory, lines = decide(previous, checks, now, after=config.OPS_ALERT_AFTER_CHECKS, repeat_hours=config.OPS_ALERT_REPEAT_HOURS)
    result: Dict[str, Optional[bool]] = {"telegram": None, "email": None}
    if lines and send:
        subject, body = compose(lines)
        result = await deliver(session, subject, body, client=client)
        if not delivered(result):
            for line in lines:                                   # nothing reached anyone: try these again next run
                memory[line["key"]]["alerted"] = (previous.get(line["key"]) or {}).get("alerted")
                memory[line["key"]]["alerted_at"] = (previous.get(line["key"]) or {}).get("alerted_at")
            logger.warning("Operator alert not delivered (no channel answered): %s", subject)
    state.update({"checks": memory, "samples": samples, "last_run": {"at": now.isoformat(), "source": source},
                  "results": [asdict(c) for c in checks]})
    if lines:
        state["last_message"] = {"at": now.isoformat(), "lines": len(lines), "delivered": delivered(result) if send else False}
    await _put(session, STATE_KEY, state)
    await session.commit()
    return {"checks": [asdict(c) for c in checks], "sent": lines if send else [], "delivery": result}


async def view(session: AsyncSession, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The admin console's card: the last run's checks, the channels and whether the schedule is running."""
    from app.alerts import platform_bot
    from app.notifications import mailer
    from app.observability import error_tracker
    now = _utc(now) or datetime.now(timezone.utc)
    state = await _get(session, STATE_KEY)
    last = _dict(state.get("last_run"))
    last_at = _parse(last.get("at"))
    host_at = _parse(_dict(state.get("host")).get("at"))
    memory = _dict(state.get("checks"))
    results = [r for r in (state.get("results") or []) if isinstance(r, dict)]
    for r in results:
        m = memory.get(str(r.get("key"))) or {}
        r["since"], r["alerted"] = m.get("since"), m.get("alerted")
    chat, chat_from = await telegram_chat(session)
    recipients = email_recipients()
    return {
        "checked_at": last_at.isoformat() if last_at else None,
        "source": last.get("source"),
        "checks": results,
        "schedule": {"server_reported_at": host_at.isoformat() if host_at else None,
                     "late": host_at is None or now - host_at > timedelta(minutes=config.OPS_HOST_FACTS_MAX_MINUTES)},
        "last_message": state.get("last_message"),
        "channels": {
            "telegram": {"available": platform_bot.configured(), "connected": bool(chat), "from": chat_from,
                         "pending": await link_pending(session, now)},
            "email": {"available": mailer.configured(), "recipients": len(recipients)},
        },
        "error_tracker": {"on": error_tracker.enabled()},
    }


# --- the operator chat (one-time code with the platform bot) ---------------------------------------------------------

def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def new_link_code(session: AsyncSession, tenant_id: int, user_id: int, now: Optional[datetime] = None) -> Tuple[str, datetime]:
    from app.alerts.links import LINK_CODE_TTL
    now = _utc(now) or datetime.now(timezone.utc)
    await session.execute(update(NotificationLinkRecord).where(
        NotificationLinkRecord.channel == LINK_CHANNEL, NotificationLinkRecord.status == "pending",
    ).values(status="revoked", code_hash=None).execution_options(synchronize_session=False))
    code = "ops" + secrets.token_urlsafe(12)
    expires = now + LINK_CODE_TTL
    session.add(NotificationLinkRecord(tenant_id=tenant_id, user_id=user_id, channel=LINK_CHANNEL, status="pending", code_hash=_hash(code),
                                       code_expires_at=expires, created_at=now))
    await session.flush()
    return code, expires


async def link_pending(session: AsyncSession, now: Optional[datetime] = None) -> bool:
    now = _utc(now) or datetime.now(timezone.utc)
    row = await session.scalar(select(NotificationLinkRecord).where(
        NotificationLinkRecord.channel == LINK_CHANNEL, NotificationLinkRecord.status == "pending",
    ).order_by(NotificationLinkRecord.id.desc()).limit(1))
    return row is not None and row.code_expires_at is not None and (_utc(row.code_expires_at) or now) > now


async def consume_link(session: AsyncSession, code: str, chat_id: str, from_id: str, now: Optional[datetime] = None) -> Optional[str]:
    """`/start <code>` for the operator chat. None = not an operator code (the trader flow handles it); otherwise
    "linked" or why not. The caller commits."""
    from app.audit.log import write_audit_log
    now = _utc(now) or datetime.now(timezone.utc)
    row = await session.scalar(select(NotificationLinkRecord).where(NotificationLinkRecord.code_hash == _hash(code or ""),
                                                                    NotificationLinkRecord.channel == LINK_CHANNEL))
    if row is None:
        return None
    if row.status != "pending":
        return "unknown_code"
    if row.code_expires_at is None or (_utc(row.code_expires_at) or now) <= now:
        row.status, row.code_hash = "revoked", None
        return "expired"
    if not chat_id or chat_id != from_id:
        return "not_private"
    await _put(session, CHANNEL_KEY, {"telegram_chat_id": chat_id, "linked_at": now.isoformat()}, row.user_id)
    row.status, row.code_hash, row.chat_id, row.linked_at = "used", None, chat_id, now
    await session.flush()
    await write_audit_log(session, None, row.user_id, "ops_alert_telegram_linked", "operator alert chat linked through the platform bot")
    return "linked"


async def unlink(session: AsyncSession, user_id: int) -> None:
    from app.audit.log import write_audit_log
    await _put(session, CHANNEL_KEY, {}, user_id)
    await write_audit_log(session, None, user_id, "ops_alert_telegram_unlinked", "operator alert chat removed")
    await session.commit()


__all__ = ["OK", "WARN", "FAIL", "UNKNOWN", "CHECKS", "Check", "counter_sum", "fetch_metrics", "local_api_metrics", "error_rate", "evaluate",
           "server_checks", "decide", "compose", "deliver", "run", "view", "new_link_code", "consume_link", "link_pending", "unlink"]
