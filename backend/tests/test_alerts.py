"""Phase B0: out-of-app alert delivery - channel config, outbox enqueue by severity, dispatcher
retries, Telegram/email senders (mocked), the worker drain and the API."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
from sqlalchemy import select

from app.alerts import dispatcher
from app.alerts.channels import decrypt_raw, severity_reaches
from app.alerts.dispatcher import dispatch_pending
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AlertChannelRecord, AlertDeliveryRecord, NotificationRecord, User
from app.notifications.service import notify
from tests.test_auth_api import _register, _session_factory, client

TELEGRAM = {"bot_token": "123456:ABC-DEF-ghi", "chat_id": "987654"}
EMAIL = {"smtp_host": "smtp.example.com", "smtp_port": 587, "username": "alerts", "password": "pw",
         "use_tls": True, "from_address": "alerts@example.com", "to_addresses": ["ops@example.com"]}


def _run(coro):
    return asyncio.run(coro)


def _upgrade_plan(tenant_id: int, plan: str = "business") -> None:
    """Free tenants are paper-only with one member (Phase B2); these tests exercise LIVE, teams
    and multiple channels, which are Pro/Business features."""
    from app.db.models import Tenant

    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            tenant.plan = plan
            await session.commit()
    asyncio.run(go())


def _auth(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "pro")
    return headers, me


def _put(headers, kind, config, **extra):
    return client.put(f"/api/alert-channels/{kind}", headers=headers, json={"config": config, **extra})


def _notify(tenant_id: int, severity: NotificationSeverity, title="Something happened") -> int:
    async def go():
        async with _session_factory() as session:
            record = await notify(session, tenant_id, NotificationType.SYSTEM_FAILURE, title=title, message="details", severity=severity)
            return record.id
    return _run(go())


def _deliveries(tenant_id: int) -> List[AlertDeliveryRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == tenant_id).order_by(AlertDeliveryRecord.id)))
    return _run(go())


def _dispatch(tenant_id: int, client_=None, now=None):
    """Tenant-scoped drain: the outbox is platform-wide and earlier tests leave PENDING rows."""
    async def go():
        async with _session_factory() as session:
            return await dispatch_pending(session, client=client_, now=now, tenant_id=tenant_id)
    return _run(go())


def _telegram_mock(responses: List[httpx.Response], seen: List[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0) if len(responses) > 1 else responses[0]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- config + API -------------------------------------------------------------------------------

def test_severity_floor():
    assert severity_reaches("CRITICAL", "WARNING") and severity_reaches("WARNING", "WARNING")
    assert not severity_reaches("INFO", "WARNING")
    assert severity_reaches("INFO", "INFO")


def test_alert_channels_require_auth():
    assert client.get("/api/alert-channels").status_code in (401, 403)
    assert client.put("/api/alert-channels/telegram", json={"config": TELEGRAM}).status_code in (401, 403)


def test_upsert_telegram_channel_masks_secret_and_audits():
    headers, me = _auth("alert-tg@example.com")
    created = _put(headers, "telegram", TELEGRAM)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["channel_type"] == "TELEGRAM" and body["enabled"] and body["min_severity"] == "WARNING"
    assert body["config"]["chat_id"] == "987654"
    assert "123456:ABC-DEF-ghi" not in created.text  # never returned
    assert body["config"]["bot_token_hint"].startswith("1234")

    listed = client.get("/api/alert-channels", headers=headers).json()
    assert [c["channel_type"] for c in listed] == ["TELEGRAM"]
    assert any(l["event"] == "alert_channel_created" for l in client.get("/api/audit-logs", headers=headers).json())


def test_update_keeps_secret_when_left_blank():
    headers, me = _auth("alert-keep@example.com")
    _put(headers, "telegram", TELEGRAM)
    updated = _put(headers, "telegram", {"bot_token": "", "chat_id": "111"}, min_severity="CRITICAL", enabled=False)
    assert updated.status_code == 200
    assert updated.json()["config"]["chat_id"] == "111" and updated.json()["min_severity"] == "CRITICAL"

    async def stored():
        async with _session_factory() as session:
            rec = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == me["tenant_id"]))
            return decrypt_raw(rec)
    assert _run(stored())["bot_token"] == "123456:ABC-DEF-ghi"


def test_invalid_configs_are_400_and_unknown_type_404():
    headers, _ = _auth("alert-bad@example.com")
    assert _put(headers, "telegram", {"bot_token": "no-colon-here", "chat_id": "1"}).status_code == 400
    assert _put(headers, "email", {"smtp_host": "h", "from_address": "not-an-email", "to_addresses": []}).status_code == 400
    assert _put(headers, "pigeon", {}).status_code == 404


def test_delete_channel():
    headers, _ = _auth("alert-del@example.com")
    _put(headers, "email", EMAIL)
    assert client.delete("/api/alert-channels/email", headers=headers).status_code == 204
    assert client.get("/api/alert-channels", headers=headers).json() == []


# --- enqueue ---------------------------------------------------------------------------------------

def test_notify_enqueues_only_channels_whose_floor_is_reached():
    headers, me = _auth("alert-enq@example.com")
    _put(headers, "telegram", TELEGRAM, min_severity="WARNING")
    _put(headers, "email", EMAIL, min_severity="CRITICAL")

    _notify(me["tenant_id"], NotificationSeverity.INFO)
    assert _deliveries(me["tenant_id"]) == []
    _notify(me["tenant_id"], NotificationSeverity.WARNING)
    assert len(_deliveries(me["tenant_id"])) == 1  # telegram only
    _notify(me["tenant_id"], NotificationSeverity.CRITICAL)
    assert len(_deliveries(me["tenant_id"])) == 3  # both
    assert all(d.status == "PENDING" for d in _deliveries(me["tenant_id"]))


def test_disabled_channel_gets_nothing():
    headers, me = _auth("alert-off@example.com")
    _put(headers, "telegram", TELEGRAM, enabled=False)
    _notify(me["tenant_id"], NotificationSeverity.CRITICAL)
    assert _deliveries(me["tenant_id"]) == []


# --- dispatch ---------------------------------------------------------------------------------------

def test_dispatch_sends_telegram_html_and_marks_sent():
    headers, me = _auth("alert-send@example.com")
    _put(headers, "telegram", TELEGRAM)
    _notify(me["tenant_id"], NotificationSeverity.CRITICAL, title="Broker <token> expired")
    seen: List[httpx.Request] = []
    mock = _telegram_mock([httpx.Response(200, json={"ok": True})], seen)

    sent, failed = _dispatch(me["tenant_id"], mock)

    assert (sent, failed) == (1, 0)
    assert len(seen) == 1 and seen[0].url.path == "/bot123456:ABC-DEF-ghi/sendMessage"
    payload = seen[0].read().decode()
    assert '"chat_id":"987654"' in payload and "Broker &lt;token&gt; expired" in payload and "[CRITICAL]" in payload
    delivery = _deliveries(me["tenant_id"])[0]
    assert delivery.status == "SENT" and delivery.sent_at is not None and delivery.attempts == 1
    channel = client.get("/api/alert-channels", headers=headers).json()[0]
    assert channel["last_delivered_at"] is not None and channel["last_error"] is None


def test_dispatch_retries_with_backoff_then_fails_for_good():
    headers, me = _auth("alert-retry@example.com")
    _put(headers, "telegram", TELEGRAM)
    _notify(me["tenant_id"], NotificationSeverity.CRITICAL)
    seen: List[httpx.Request] = []
    mock = _telegram_mock([httpx.Response(401, json={"ok": False, "description": "Unauthorized"})], seen)
    t0 = datetime.now(timezone.utc)

    assert _dispatch(me["tenant_id"], mock, now=t0) == (0, 1)
    d = _deliveries(me["tenant_id"])[0]
    assert d.status == "PENDING" and d.attempts == 1 and "Unauthorized" in d.last_error
    assert "123456:ABC" not in d.last_error
    next_at = d.next_attempt_at.replace(tzinfo=timezone.utc) if d.next_attempt_at.tzinfo is None else d.next_attempt_at
    assert next_at >= t0 + timedelta(seconds=dispatcher.BASE_BACKOFF_SECONDS)

    assert _dispatch(me["tenant_id"], mock, now=t0) == (0, 0)  # not due yet
    for i in range(1, dispatcher.MAX_ATTEMPTS):
        _dispatch(me["tenant_id"], mock, now=t0 + timedelta(hours=i))
    d = _deliveries(me["tenant_id"])[0]
    assert d.status == "FAILED" and d.attempts == dispatcher.MAX_ATTEMPTS
    assert len(seen) == dispatcher.MAX_ATTEMPTS
    assert client.get("/api/alert-channels", headers=headers).json()[0]["last_error"]


def test_dispatch_sends_email_via_smtp(monkeypatch):
    headers, me = _auth("alert-mail@example.com")
    _put(headers, "email", EMAIL, min_severity="WARNING")
    sent_messages = []
    monkeypatch.setattr(dispatcher, "_smtp_send", lambda config, message: sent_messages.append((config, message)))
    _notify(me["tenant_id"], NotificationSeverity.WARNING, title="Daily loss limit")

    assert _dispatch(me["tenant_id"]) == (1, 0)
    config, message = sent_messages[0]
    assert config.smtp_host == "smtp.example.com" and config.password == "pw"
    assert message["Subject"] == "[WARNING] Daily loss limit" and message["To"] == "ops@example.com"
    assert "details" in message.get_content()


def test_dispatch_email_failure_is_recorded_not_raised(monkeypatch):
    headers, me = _auth("alert-mailfail@example.com")
    _put(headers, "email", EMAIL)
    def boom(config, message):
        raise ConnectionRefusedError("smtp down")
    monkeypatch.setattr(dispatcher, "_smtp_send", boom)
    _notify(me["tenant_id"], NotificationSeverity.CRITICAL)
    assert _dispatch(me["tenant_id"]) == (0, 1)
    assert "smtp down" in _deliveries(me["tenant_id"])[0].last_error


def test_deliveries_endpoint_shows_outbox():
    headers, me = _auth("alert-outbox@example.com")
    _put(headers, "telegram", TELEGRAM)
    _notify(me["tenant_id"], NotificationSeverity.CRITICAL, title="Outbox check")
    rows = client.get("/api/alert-channels/deliveries", headers=headers).json()
    assert len(rows) == 1 and rows[0]["title"] == "Outbox check" and rows[0]["status"] == "PENDING"
    assert rows[0]["channel_type"] == "TELEGRAM"


def test_test_send_reports_failure_detail(monkeypatch):
    headers, _ = _auth("alert-test@example.com")
    _put(headers, "email", EMAIL)
    def boom(config, message):
        raise ConnectionRefusedError("connection refused")
    monkeypatch.setattr(dispatcher, "_smtp_send", boom)
    response = client.post("/api/alert-channels/email/test", headers=headers)
    assert response.status_code == 200 and response.json()["ok"] is False and "refused" in response.json()["detail"]

    monkeypatch.setattr(dispatcher, "_smtp_send", lambda config, message: None)
    assert client.post("/api/alert-channels/email/test", headers=headers).json()["ok"] is True
    assert client.post("/api/alert-channels/telegram/test", headers=headers).status_code == 404


def test_worker_cycle_drains_outbox(monkeypatch):
    from app.workers.trading_worker import TradingWorker
    from zoneinfo import ZoneInfo
    headers, me = _auth("alert-worker@example.com")
    _put(headers, "email", EMAIL)
    sent = []
    monkeypatch.setattr(dispatcher, "_smtp_send", lambda config, message: sent.append(message))
    _notify(me["tenant_id"], NotificationSeverity.CRITICAL, title="From the worker")

    worker = TradingWorker(_session_factory, cycle_seconds=60)
    report = _run(worker.run_cycle(now=datetime(2026, 9, 26, 10, 30, tzinfo=ZoneInfo("Asia/Kolkata"))))  # Saturday: market closed
    assert not report.market_open and not report.errors
    # The worker drains the whole platform's outbox, so earlier tests' leftovers ride along.
    assert "[CRITICAL] From the worker" in [m["Subject"] for m in sent]
    assert _deliveries(me["tenant_id"])[0].status == "SENT"
