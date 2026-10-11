"""S3b (ADR-0022): the webhook channel - the versioned atp.notification/1 body with an atp.alert/1 block for screen alerts
(symbols, trigger values with their data timestamps), the optional Chartink shape, the receiver's signature + replay
window check, dead letters with a reason code, and retry from the dead-letter list."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete

from app.alerts import dispatcher, engine
from app.alerts.dispatcher import REPLAY_WINDOW_SECONDS, sign_webhook, verify_webhook
from app.db.models import AlertDeliveryRecord, AlertEventRecord, AlertRuleRecord, NotificationPolicyRecord
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

BAR = datetime(2026, 3, 10, 4, 0, tzinfo=timezone.utc)
SECRET = "s3cr3t-webhook-secret-0123"


def _run(coro):
    return asyncio.run(coro)


def _capture(status=200):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"body": request.content, "headers": dict(request.headers)})
        return httpx.Response(status, json={})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def _setup(email, payload_format="atp"):
    headers, me = _owner(email)
    cfg = {"url": "https://hooks.example.com/x", "secret": SECRET, "payload_format": payload_format}
    assert client.put("/api/alert-channels/webhook", headers=headers, json={"config": cfg, "min_severity": "INFO"}).status_code in (200, 201)

    async def go():
        async with _session_factory() as session:
            for m in (AlertEventRecord, NotificationPolicyRecord):
                await session.execute(delete(m).where(m.tenant_id == me["tenant_id"]))
            session.add(NotificationPolicyRecord(tenant_id=me["tenant_id"], timezone="Asia/Kolkata", max_per_hour=30, group_window_seconds=0,
                                                 eod_digest_time="15:45", updated_at=BAR))
            rule = AlertRuleRecord(tenant_id=me["tenant_id"], name="Volume shocker", kind="instrument", symbol="X", condition_text="volume > 1",
                                   base_tf="5m", priority="normal", cooldown_minutes=0, mode="instant", digest_every="hourly", status="active",
                                   created_at=BAR, updated_at=BAR)
            session.add(rule)
            await session.commit()
            for sym, close in (("TCS", 3810.5), ("INFY", 1502.0)):
                await engine.record_event(session, rule, sym, BAR, {"close": close, "volume": 98000, "as_of": BAR.isoformat()}, now=BAR)
            await session.commit()
            await engine.flush(session, BAR + timedelta(seconds=1), tenant_id=me["tenant_id"])
    _run(go())
    return headers, me


def _dispatch(tenant_id, http):
    async def go():
        async with _session_factory() as session:
            return await dispatcher.dispatch_pending(session, client=http, tenant_id=tenant_id)            # the outbox stamps rows with the real clock
    return _run(go())


def test_screen_alert_webhook_carries_the_versioned_alert_block():
    _, me = _setup("s3b-atp@example.com")
    http, seen = _capture()
    assert _dispatch(me["tenant_id"], http) == (1, 0)
    body = json.loads(seen[0]["body"])
    assert body["schema"] == "atp.notification/1" and seen[0]["headers"]["x-atp-schema"] == "atp.notification/1"
    alert = body["alert"]
    assert alert["schema"] == "atp.alert/1" and alert["rule_name"] == "Volume shocker" and alert["symbols"] == ["INFY", "TCS"]
    assert alert["trigger_values"]["TCS"]["close"] == 3810.5 and alert["data_timestamps"]["INFY"] == BAR.isoformat()
    assert alert["bar_time"] == BAR.isoformat()
    ts, sig = seen[0]["headers"]["x-atp-timestamp"], seen[0]["headers"]["x-atp-signature"]
    assert verify_webhook(SECRET, seen[0]["body"], ts, sig, now=datetime.fromtimestamp(int(ts), timezone.utc))


def test_chartink_shape_on_request():
    _, me = _setup("s3b-chartink@example.com", payload_format="chartink")
    http, seen = _capture()
    _dispatch(me["tenant_id"], http)
    body = json.loads(seen[0]["body"])
    assert body == {"stocks": "INFY,TCS", "trigger_prices": "1502.0,3810.5", "triggered_at": BAR.isoformat(), "scan_name": "Volume shocker",
                    "alert_name": "Volume shocker", "scan_url": ""}
    assert seen[0]["headers"]["x-atp-schema"] == "chartink/1"


def test_receiver_check_refuses_replays_and_tampering():
    body, ts = b'{"a":1}', str(int(BAR.timestamp()))
    sig = sign_webhook(SECRET, body, ts)
    assert verify_webhook(SECRET, body, ts, sig, now=BAR + timedelta(seconds=REPLAY_WINDOW_SECONDS - 1))
    assert not verify_webhook(SECRET, body, ts, sig, now=BAR + timedelta(seconds=REPLAY_WINDOW_SECONDS + 1))   # replayed later
    assert not verify_webhook(SECRET, b'{"a":2}', ts, sig, now=BAR)                                              # body changed
    assert not verify_webhook("another-secret-0000000", body, ts, sig, now=BAR)
    assert not verify_webhook(SECRET, body, "yesterday", sig, now=BAR)


def test_dead_letters_are_listed_and_can_be_retried(monkeypatch):
    headers, me = _setup("s3b-dead@example.com")
    monkeypatch.setattr(dispatcher, "MAX_ATTEMPTS", 1)
    http, _ = _capture(status=500)
    assert _dispatch(me["tenant_id"], http) == (0, 1)
    dead = client.get("/api/alerts/dead-letters", headers=headers).json()
    assert len(dead) == 1 and dead[0]["reason"] == "dead_letter" and dead[0]["channel"] == "WEBHOOK" and "HTTP 500" in dead[0]["last_error"]
    other, _ = _owner("s3b-dead-other@example.com")
    assert client.post(f"/api/alerts/deliveries/{dead[0]['id']}/retry", headers=other).status_code == 404
    assert client.post(f"/api/alerts/deliveries/{dead[0]['id']}/retry", headers=headers).json()["status"] == "PENDING"
    assert client.post(f"/api/alerts/deliveries/{dead[0]['id']}/retry", headers=headers).status_code == 409
    ok, seen = _capture()
    assert _dispatch(me["tenant_id"], ok) == (1, 0) and json.loads(seen[0]["body"])["alert"]["symbols"] == ["INFY", "TCS"]

    async def row():
        async with _session_factory() as session:
            return await session.get(AlertDeliveryRecord, dead[0]["id"])
    r = _run(row())
    assert r.status == "SENT" and r.reason_code is None and r.group_id


def test_non_alert_notifications_keep_their_body():
    from app.db.models import NotificationRecord
    note = NotificationRecord(id=5, tenant_id=1, event_type="SYSTEM_FAILURE", severity="CRITICAL", title="t", message="m", created_at=BAR)
    body = dispatcher.webhook_payload(note)
    assert body["schema"] == "atp.notification/1" and "alert" not in body and body["event_type"] == "SYSTEM_FAILURE"

    async def ctx():
        async with _session_factory() as session:
            return await dispatcher.alert_context(session, note)
    assert _run(ctx()) is None
