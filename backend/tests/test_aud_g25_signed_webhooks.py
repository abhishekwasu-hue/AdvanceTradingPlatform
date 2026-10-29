"""AUD-G2.5 (owner audit 28 Oct): outbound, user-defined, SIGNED webhooks (flag `alert_webhooks`, needs `ui_v2`).

- the flag is registered OFF by default and depends on `ui_v2`; with either off the routes answer 404 and nothing is queued;
- https only; private / loopback / link-local / metadata / local names refused at add time, also after DNS resolution;
- at send time the name is resolved again and the POST is pinned to the checked address - a DNS answer that turned
  private after the add (rebinding) is refused, nothing is sent, the outbox retries and then dead-letters;
- the secret is made by the server, shown once, stored only encrypted; the signature is the HMAC of the raw body and is
  verifiable with the shown secret; the body's timestamp equals X-ATP-Timestamp; the event id is the same on every retry;
- per-member cap (ALERT_WEBHOOKS_MAX_PER_USER); each member sees, tests and removes only his own; another organisation
  never; a member's notification never reaches another member's webhook;
- Send test posts one signed request and reports the receiver's answer.
"""
import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.alerts import dispatcher, signed_webhooks
from app.core import config
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AlertChannelRecord, AlertDeliveryRecord
from app.notifications.service import notify
from app.platform import controls, flag_catalog
from tests.test_alerts import _auth
from tests.test_auth_api import _session_factory, client
from tests.test_settings_a_connect_ai import _flags

PUBLIC_IP = "93.184.216.34"


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"g25-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _resolver(mapping):
    async def resolve(host):
        return list(mapping.get(host, []))
    return resolve


@pytest.fixture
def on(monkeypatch):
    _flags(ui_v2=True, alert_webhooks=True)
    monkeypatch.setattr(signed_webhooks, "RESOLVER", _resolver({"hooks.example.com": [PUBLIC_IP]}))
    yield
    _flags(ui_v2=False, alert_webhooks=False)


def _receiver(status=200):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "body": request.content, "headers": dict(request.headers),
                     "sni": request.extensions.get("sni_hostname")})
        return httpx.Response(status, json={"ok": True})
    return httpx.MockTransport(handler), seen


def _add(headers, url="https://hooks.example.com/atp?x=1", **extra):
    return client.post("/api/alert-webhooks", headers=headers, json={"url": url, **extra})


def _member(owner_headers):
    inv = client.post("/api/team/invites", headers=owner_headers, json={"email": _email("member"), "role": "USER"}).json()
    token = parse_qs(urlparse(inv["invite_url"]).query)["invite"][0]
    member = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()
    headers = {"Authorization": f"Bearer {member['access_token']}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def _notify(tenant_id, user_id=None, severity=NotificationSeverity.WARNING, event=NotificationType.SYSTEM_FAILURE):
    async def go():
        async with _session_factory() as session:
            return (await notify(session, tenant_id, event, title="Price came back into the demand area",
                                 message="Structure: higher low held", severity=severity, user_id=user_id)).id
    return _run(go())


def _rows(tenant_id):
    async def go():
        async with _session_factory() as session:
            channel = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                            AlertChannelRecord.channel_type == "SIGNED_WEBHOOK"))
            if channel is None:
                return []
            return list(await session.scalars(select(AlertDeliveryRecord).where(AlertDeliveryRecord.channel_id == channel.id)
                                              .order_by(AlertDeliveryRecord.id)))
    return _run(go())


def _dispatch(tenant_id, transport, now=None):
    async def go():
        async with httpx.AsyncClient(transport=transport) as http, _session_factory() as session:
            return await dispatcher.dispatch_pending(session, client=http, tenant_id=tenant_id, now=now)
    return _run(go())


# --- flag ----------------------------------------------------------------------------------------------------------------

def test_flag_is_registered_off_by_default_and_depends_on_ui_v2():
    assert "alert_webhooks" in controls.FEATURE_FLAGS and "alert_webhooks" in controls.DEFAULT_OFF_FLAGS
    assert flag_catalog.FLAG_INFO["alert_webhooks"].depends_on == ("ui_v2",)


def test_routes_404_and_nothing_queued_while_a_flag_is_off(monkeypatch):
    headers, me = _auth(_email("off"))
    _flags(ui_v2=True, alert_webhooks=False)
    try:
        r = client.get("/api/alert-webhooks", headers=headers)
        assert r.status_code == 404 and r.headers["X-Feature-Disabled"] == "alert_webhooks"
        assert _add(headers).status_code == 404
        assert client.post("/api/alert-webhooks/abcdef0123456789/test", headers=headers).status_code == 404
        assert client.delete("/api/alert-webhooks/abcdef0123456789", headers=headers).status_code == 404
    finally:
        _flags(ui_v2=False)
    assert client.get("/api/alert-webhooks", headers=headers).headers["X-Feature-Disabled"] == "ui_v2"


def test_flag_off_after_add_queues_nothing_and_a_queued_row_is_suppressed(on, monkeypatch):
    headers, me = _auth(_email("flagoff"))
    assert _add(headers).status_code == 201
    _notify(me["tenant_id"])
    assert len(_rows(me["tenant_id"])) == 1
    _flags(alert_webhooks=False)
    _notify(me["tenant_id"])
    assert len(_rows(me["tenant_id"])) == 1                                  # nothing new queued
    transport, seen = _receiver()
    _dispatch(me["tenant_id"], transport)
    row = _rows(me["tenant_id"])[0]
    assert row.status == "SUPPRESSED" and row.reason_code == "flag_off" and not seen


# --- adding: https only, SSRF ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://hooks.example.com/x",                       # not https
    "https://127.0.0.1/x", "https://10.0.0.5/x", "https://192.168.1.1/x", "https://[::1]/x",
    "https://169.254.169.254/latest/meta-data", "https://[fe80::1]/x", "https://100.64.0.1/x", "https://[::ffff:127.0.0.1]/x",
    "https://localhost/x", "https://metadata.google.internal/x", "https://router.local/x", "https://intranet/x",
    "https://user:pass@hooks.example.com/x", "ftp://hooks.example.com/x", "https://hooks.example.com:99999/x",
])
def test_add_refuses_non_https_and_private_destinations(on, url):
    headers, _ = _auth(_email("ssrf"))
    r = _add(headers, url=url)
    assert r.status_code in (400, 422), r.text


def test_add_refuses_a_name_that_resolves_to_a_private_address(on, monkeypatch):
    headers, _ = _auth(_email("dns-add"))
    monkeypatch.setattr(signed_webhooks, "RESOLVER", _resolver({"evil.example.com": ["93.184.216.34", "10.1.2.3"],
                                                                "gone.example.com": []}))
    r = _add(headers, url="https://evil.example.com/x")
    assert r.status_code == 400 and "private" in r.json()["detail"]
    assert _add(headers, url="https://gone.example.com/x").status_code == 400


def test_generic_channel_routes_never_take_a_signed_webhook(on):
    headers, _ = _auth(_email("generic"))
    r = client.put("/api/alert-channels/signed_webhook", headers=headers,
                   json={"config": {"endpoints": [{"id": "abcdef0123456789", "user_id": 1, "url": "https://hooks.example.com/x",
                                                   "secret": "x" * 40}]}})
    assert r.status_code == 400
    assert client.post("/api/alert-channels/signed_webhook/test", headers=headers).status_code == 400
    assert client.delete("/api/alert-channels/signed_webhook", headers=headers).status_code == 400


# --- the secret ------------------------------------------------------------------------------------------------------------

def test_secret_is_made_by_the_server_shown_once_and_stored_encrypted(on):
    headers, me = _auth(_email("secret"))
    r = _add(headers, label="My n8n", secret="m" * 30)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["secret"].startswith("whsec_") and len(created["secret"]) > 30 and created["secret"] != "m" * 30
    listing = client.get("/api/alert-webhooks", headers=headers).json()
    assert listing["webhooks"][0]["id"] == created["id"] and "secret" not in json.dumps(listing)
    assert created["secret"] not in json.dumps(client.get("/api/alert-channels", headers=headers).json())

    async def stored():
        async with _session_factory() as session:
            return await session.scalar(select(AlertChannelRecord.encrypted_config).where(
                AlertChannelRecord.tenant_id == me["tenant_id"], AlertChannelRecord.channel_type == "SIGNED_WEBHOOK"))
    raw = _run(stored())
    assert created["secret"] not in raw and "hooks.example.com" not in raw


# --- signing, pinning, idempotency ----------------------------------------------------------------------------------------

def test_delivery_is_signed_pinned_and_verifiable_with_the_shown_secret(on):
    headers, me = _auth(_email("sign"))
    secret = _add(headers).json()["secret"]
    note_id = _notify(me["tenant_id"])
    transport, seen = _receiver()
    assert _dispatch(me["tenant_id"], transport) == (1, 0)
    req = seen[0]
    body, h = req["body"], req["headers"]
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(h["x-atp-signature"], expected)
    payload = json.loads(body)
    assert str(payload["timestamp"]) == h["x-atp-timestamp"] and payload["event_id"] == h["x-atp-event-id"]
    assert payload["notification_id"] == note_id and payload["event_type"] == "SYSTEM_FAILURE" and payload["test"] is False
    assert payload["text"] == "Structure: higher low held" and "tenant_id" not in payload
    assert signed_webhooks.verify(secret, body, h["x-atp-signature"], h["x-atp-timestamp"])
    assert not signed_webhooks.verify(secret, body + b" ", h["x-atp-signature"], h["x-atp-timestamp"])     # tampered
    assert not signed_webhooks.verify("whsec_wrong", body, h["x-atp-signature"], h["x-atp-timestamp"])
    late = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert not signed_webhooks.verify(secret, body, h["x-atp-signature"], h["x-atp-timestamp"], now=late)  # replay
    # pinned: the connection goes to the checked address; the name is the Host and the TLS name
    assert req["url"] == f"https://{PUBLIC_IP}/atp?x=1" and h["host"] == "hooks.example.com" and req["sni"] == "hooks.example.com"


def test_retries_keep_the_event_id_then_dead_letter(on, monkeypatch):
    headers, me = _auth(_email("retry"))
    _add(headers)
    _notify(me["tenant_id"])
    monkeypatch.setattr(dispatcher, "MAX_ATTEMPTS", 3)
    transport, seen = _receiver(status=500)
    now = datetime.now(timezone.utc)
    for step in range(3):
        _dispatch(me["tenant_id"], transport, now=now + timedelta(hours=step))
    row = _rows(me["tenant_id"])[0]
    assert len(seen) == 3 and len({s["headers"]["x-atp-event-id"] for s in seen}) == 1
    assert row.status in ("FAILED", "DEAD") and row.reason_code == "dead_letter" and row.attempts == 3 and "HTTP 500" in row.last_error


def test_dns_rebinding_to_a_private_address_at_send_time_is_refused(on, monkeypatch):
    headers, me = _auth(_email("rebind"))
    assert _add(headers).status_code == 201                                    # public when added
    monkeypatch.setattr(signed_webhooks, "RESOLVER", _resolver({"hooks.example.com": ["169.254.169.254"]}))
    monkeypatch.setattr(dispatcher, "MAX_ATTEMPTS", 1)
    _notify(me["tenant_id"])
    transport, seen = _receiver()
    assert _dispatch(me["tenant_id"], transport) == (0, 1)
    row = _rows(me["tenant_id"])[0]
    assert not seen and row.reason_code == "dead_letter" and "private" in row.last_error


def test_redirects_are_not_followed(on):
    headers, me = _auth(_email("redirect"))
    _add(headers)
    _notify(me["tenant_id"])
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    _dispatch(me["tenant_id"], httpx.MockTransport(handler))
    assert len(seen) == 1 and "HTTP 302" in _rows(me["tenant_id"])[0].last_error


# --- cap, scoping, filters -------------------------------------------------------------------------------------------------

def test_cap_per_member(on, monkeypatch):
    monkeypatch.setattr(config, "ALERT_WEBHOOKS_MAX_PER_USER", 2)
    headers, me = _auth(_email("cap"))
    assert _add(headers).status_code == 201 and _add(headers).status_code == 201
    r = _add(headers)
    assert r.status_code == 409 and "up to 2" in r.json()["detail"]
    listing = client.get("/api/alert-webhooks", headers=headers).json()
    assert listing["max"] == 2 and listing["left"] == 0


def test_each_member_sees_tests_and_removes_only_his_own(on, monkeypatch):
    _flags(byob_data_tier=False)
    owner, me = _auth(_email("owner"))
    hook = _add(owner).json()
    member, member_me = _member(owner)
    assert client.get("/api/alert-webhooks", headers=member).json()["webhooks"] == []
    assert client.delete(f"/api/alert-webhooks/{hook['id']}", headers=member).status_code == 404
    assert client.post(f"/api/alert-webhooks/{hook['id']}/test", headers=member).status_code == 404
    stranger, _ = _auth(_email("stranger"))
    assert client.delete(f"/api/alert-webhooks/{hook['id']}", headers=stranger).status_code == 404
    assert client.get("/api/alert-webhooks", headers=stranger).json()["webhooks"] == []
    # a notification addressed to the member never reaches the owner's webhook; an organisation-wide one does
    _notify(me["tenant_id"], user_id=member_me["id"])
    assert _rows(me["tenant_id"]) == []
    _notify(me["tenant_id"])
    assert len(_rows(me["tenant_id"])) == 1
    assert client.delete(f"/api/alert-webhooks/{hook['id']}", headers=owner).status_code == 204
    assert client.get("/api/alert-webhooks", headers=owner).json()["webhooks"] == []
    transport, seen = _receiver()
    _dispatch(me["tenant_id"], transport)
    row = _rows(me["tenant_id"])[0]
    assert not seen and row.status == "SUPPRESSED" and row.reason_code == "webhook_removed"


def test_event_type_and_severity_filters(on):
    headers, me = _auth(_email("filter"))
    assert _add(headers, event_types=["exit"], min_severity="WARNING").status_code == 201
    assert _add(headers, event_types=["NOT_A_TYPE"]).status_code == 400
    _notify(me["tenant_id"])                                                   # SYSTEM_FAILURE: filtered out
    _notify(me["tenant_id"], event=NotificationType.EXIT, severity=NotificationSeverity.INFO)   # below the floor
    assert _rows(me["tenant_id"]) == []
    _notify(me["tenant_id"], event=NotificationType.EXIT, severity=NotificationSeverity.WARNING)
    assert len(_rows(me["tenant_id"])) == 1


# --- Send test --------------------------------------------------------------------------------------------------------------

def test_send_test_posts_one_signed_request_and_reports_the_answer(on, monkeypatch):
    headers, _ = _auth(_email("test"))
    created = _add(headers).json()
    transport, seen = _receiver()
    monkeypatch.setattr(signed_webhooks, "TRANSPORT", transport)
    r = client.post(f"/api/alert-webhooks/{created['id']}/test", headers=headers).json()
    assert r == {"ok": True, "detail": "Your receiver answered HTTP 200"}
    body = seen[0]["body"]
    assert json.loads(body)["test"] is True and json.loads(body)["event_id"].startswith("evt_test_")
    assert signed_webhooks.verify(created["secret"], body, seen[0]["headers"]["x-atp-signature"], seen[0]["headers"]["x-atp-timestamp"])
    bad, _ = _receiver(status=401)
    monkeypatch.setattr(signed_webhooks, "TRANSPORT", bad)
    assert client.post(f"/api/alert-webhooks/{created['id']}/test", headers=headers).json() == {
        "ok": False, "detail": "The receiver answered HTTP 401"}
