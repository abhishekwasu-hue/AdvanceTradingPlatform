"""B8 (readiness): phone notifications as a new alerts channel, and the Web Push key rule.

* Web Push: in production a throwaway VAPID key never takes a subscription (it would die at the next restart).
* The FCM channel: the four kinds (signals, approvals, exits, renew reminders) each open their screen; one outbox row
  per phone; a member's own notification reaches only that member's phones; per-phone kinds, EMERGENCY always; quiet
  hours deliver without sound; a gone token removes the phone; the flag off queues nothing; the payload carries only a
  fixed sentence and opaque ids (MA-7).
* The device routes: flag-gated, the caller's own phones only, no token in any answer.
* The real FCM sender against a mocked Google: the signed assertion, the access token, the v1 message, a gone token.
"""
import asyncio
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import select

from app.alerts import dispatcher, fcm, push_kinds, webpush
from app.alerts.channels import decrypt_raw
from app.core import config
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AiActionRecord, AlertChannelRecord, AlertDeliveryRecord, NotificationPolicyRecord
from app.notifications.service import notify
from app.platform import controls
from tests.test_alerts import _auth, _put
from tests.test_auth_api import _session_factory, client


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"b8-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _token() -> str:
    return "fcm-test-" + "-".join(uuid.uuid4().hex for _ in range(3))     # built at runtime: not a real token


def _set_flag(tenant_id: int, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get("mobile_push") or {"on": False, "tenants": []}
            tenants = set(entry.get("tenants") or [])
            (tenants.add if on else tenants.discard)(tenant_id)
            flags["mobile_push"] = {"on": False, "tenants": sorted(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _member(owner_headers) -> tuple:
    inv = client.post("/api/team/invites", headers=owner_headers, json={"email": _email("member"), "role": "USER"}).json()
    token = parse_qs(urlparse(inv["invite_url"]).query)["invite"][0]
    member = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()
    headers = {"Authorization": f"Bearer {member['access_token']}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


@pytest.fixture
def sent():
    """The fake FCM sender: every v1 message the server would POST, in order."""
    messages: List[Dict[str, Any]] = []

    async def fake(message: Dict[str, Any]) -> None:
        messages.append(message)

    fcm.set_sender(fake)
    yield messages
    fcm.set_sender(None)


def _register(headers, token: str, **extra):
    return client.post("/api/mobile/devices", headers=headers, json={"token": token, "label": "My phone", "app_version": "1.0 (7)", **extra})


def _action(tenant_id: int, action: str) -> int:
    async def go():
        async with _session_factory() as session:
            row = AiActionRecord(tenant_id=tenant_id, action=action, rule="b8-test", reason="test",
                                 expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _notify(tenant_id: int, event: NotificationType, severity=NotificationSeverity.WARNING, **kw) -> int:
    async def go():
        async with _session_factory() as session:
            return (await notify(session, tenant_id, event, title="NIFTY 23,050 reached", message="P&L -1,200 on RELIANCE",
                                 severity=severity, **kw)).id
    return _run(go())


def _rows(tenant_id: int, notification_id: int) -> List[AlertDeliveryRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == tenant_id,
                                                                                AlertDeliveryRecord.notification_id == notification_id)))
    return _run(go())


def _dispatch(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return await dispatcher.dispatch_pending(session, tenant_id=tenant_id)
    return _run(go())


def _fcm_channel(tenant_id: int) -> AlertChannelRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                         AlertChannelRecord.channel_type == "FCM"))
    return _run(go())


# --- the kinds, their screens and their text --------------------------------------------------------------------------

def _n(**kw):
    base = dict(id=9, event_type=NotificationType.SYSTEM_FAILURE.value, severity="WARNING", ai_action_id=None, related_trade_id=None, user_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_four_kinds_and_where_each_opens():
    signal = _n(event_type="AI_PROPOSAL", ai_action_id=12)
    assert push_kinds.phone_kind(signal, "ENTRY") == "signals"
    assert push_kinds.phone_path("signals", signal) == "/signals?proposal=12"
    assert push_kinds.phone_kind(signal, "EXIT_POSITION") == "approvals"
    assert push_kinds.phone_kind(signal, None) == "approvals"
    assert push_kinds.phone_path("approvals", signal) == "/copilot/watchtower?action=12"
    closed = _n(event_type="EXIT", related_trade_id=33)
    assert push_kinds.phone_kind(closed) == "exits" and push_kinds.phone_path("exits", closed) == "/positions?trade=33"
    assert push_kinds.phone_kind(_n(event_type="EMERGENCY_EXIT")) == "exits"
    assert push_kinds.phone_path("exits", _n(event_type="EXIT")) == "/positions"
    for event in ("TOKEN_EXPIRED", "BROKER_DISCONNECT"):
        assert push_kinds.phone_kind(_n(event_type=event)) == "renew"
        assert push_kinds.phone_path("renew", _n(event_type=event)) == "/broker"
    # Other kinds stay off phones, unless they are an emergency.
    for event in ("ENTRY", "EOD_SUMMARY", "SCREEN_ALERT", "NEWS_ALERT", "SECURITY"):
        assert push_kinds.phone_kind(_n(event_type=event)) is None
    assert push_kinds.phone_kind(_n(severity="EMERGENCY")) == "exits"


def test_phone_text_is_fixed_and_has_no_trading_call_words():
    banned = ("buy", "sell", "target", "stop-loss", "stop loss", "₹", "p&l", "profit")
    for kind in push_kinds.PHONE_KINDS:
        title, body = push_kinds.phone_text(kind, _n())
        text = f"{title} {body}".lower()
        assert not any(word in text for word in banned), (kind, text)
        assert not any(ch.isdigit() for ch in text), (kind, text)
    assert push_kinds.phone_text("renew", _n(severity="EMERGENCY")) == push_kinds.EMERGENCY_TEXT


def test_per_phone_choices_and_addressing():
    assert push_kinds.device_wants(None, "exits", _n())
    assert not push_kinds.device_wants(["signals"], "exits", _n())
    assert push_kinds.device_wants([], "exits", _n(severity="EMERGENCY"))
    assert push_kinds.addressed_to(5, _n(user_id=None)) and push_kinds.addressed_to(5, _n(user_id=5))
    assert not push_kinds.addressed_to(6, _n(user_id=5))
    assert push_kinds.device_id("abc") == push_kinds.device_id(" abc ") and len(push_kinds.device_id("abc")) == 24


# --- Web Push: a throwaway key never takes a subscription in production --------------------------------------------------

def _subscription() -> dict:
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,
                                                                            serialization.PublicFormat.UncompressedPoint)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    return {"endpoint": "https://fcm.googleapis.com/fcm/send/b8", "p256dh": b64(key), "auth": b64(bytes(range(16))), "label": "laptop"}


def test_web_push_refuses_subscriptions_while_the_key_is_ephemeral_in_production(monkeypatch):
    headers, _ = _auth(_email("vapid"))
    monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", None)
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    key = client.get("/api/alert-channels/push/public-key", headers=headers).json()
    assert key["accepting"] is False and "not set up" in key["message"]
    r = _put(headers, "push", {"subscription": _subscription()})
    assert r.status_code == 409 and "not set up on this server" in r.json()["detail"]
    assert "throwaway key" in webpush.status_line()

    monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", "set-in-env")          # configured() only looks at presence
    assert client.get("/api/alert-channels/push/public-key", headers=headers).json()["accepting"] is True
    assert webpush.accepting_subscriptions() and "is set" in webpush.status_line()


def test_web_push_keeps_working_with_the_ephemeral_key_in_development(monkeypatch):
    monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", None)
    monkeypatch.setattr(config, "ENVIRONMENT", "development")
    assert webpush.accepting_subscriptions()
    headers, _ = _auth(_email("vapid-dev"))
    assert _put(headers, "push", {"subscription": _subscription()}).status_code == 200


def test_a_browser_push_of_an_entry_signal_opens_its_signals_item(monkeypatch):
    payloads: List[dict] = []

    async def fake_send(endpoint, p256dh, auth, payload, client=None):
        payloads.append(payload)

    monkeypatch.setattr(webpush, "send_push", fake_send)
    headers, me = _auth(_email("web-signal"))
    assert _put(headers, "push", {"subscription": _subscription()}, min_severity="INFO").status_code == 200
    entry = _action(me["tenant_id"], "ENTRY")
    _notify(me["tenant_id"], NotificationType.AI_PROPOSAL, ai_action_id=entry)
    _dispatch(me["tenant_id"])
    assert payloads and payloads[-1]["url"] == f"/signals?proposal={entry}"


# --- the device routes ------------------------------------------------------------------------------------------------

def test_device_routes_are_off_until_the_flag_is_on(sent):
    headers, _ = _auth(_email("off"))
    r = client.get("/api/mobile/devices", headers=headers)
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "mobile_push"
    assert _register(headers, _token()).status_code == 404


def test_registering_needs_the_server_key(monkeypatch):
    headers, me = _auth(_email("nokey"))
    _set_flag(me["tenant_id"])
    monkeypatch.setattr(config, "FCM_SERVICE_ACCOUNT_JSON_BASE64", "")
    monkeypatch.setattr(config, "FCM_SERVICE_ACCOUNT_FILE", "")
    assert client.get("/api/mobile/devices", headers=headers).json()["configured"] is False
    r = _register(headers, _token())
    assert r.status_code == 409 and r.json()["detail"] == fcm.NOT_SET_UP


def test_each_member_sees_and_changes_only_their_own_phones(sent):
    owner_headers, owner = _auth(_email("own"))
    _set_flag(owner["tenant_id"])
    member_headers, member = _member(owner_headers)
    token = _token()
    r = _register(owner_headers, token)
    assert r.status_code == 200, r.text
    view = r.json()
    assert token not in json.dumps(view) and view["kinds"] == list(push_kinds.PHONE_KINDS)
    assert _register(owner_headers, token).json()["id"] == view["id"]          # a refresh, not a second phone
    listed = client.get("/api/mobile/devices", headers=owner_headers).json()
    assert [d["id"] for d in listed["devices"]] == [view["id"]] and token not in json.dumps(listed)
    assert client.get("/api/mobile/devices", headers=member_headers).json()["devices"] == []
    # The organisation-wide channel list shows only a count.
    channels = client.get("/api/alert-channels", headers=owner_headers).json()
    assert next(c for c in channels if c["channel_type"] == "FCM")["config"] == {"count": 1}
    assert token not in json.dumps(channels)
    # Another member cannot touch it.
    assert client.patch(f"/api/mobile/devices/{view['id']}", headers=member_headers, json={"label": "x"}).status_code == 404
    assert client.delete(f"/api/mobile/devices/{view['id']}", headers=member_headers).status_code == 404
    assert client.post(f"/api/mobile/devices/{view['id']}/test", headers=member_headers).status_code == 404
    # The owner can rename it and choose its kinds; nonsense kinds are refused.
    r = client.patch(f"/api/mobile/devices/{view['id']}", headers=owner_headers, json={"label": "Pixel", "kinds": ["exits", "signals"]})
    assert r.status_code == 200 and r.json()["label"] == "Pixel" and r.json()["kinds"] == ["exits", "signals"]
    assert client.patch(f"/api/mobile/devices/{view['id']}", headers=owner_headers, json={"kinds": ["everything"]}).status_code == 400
    # The generic channel PUT never takes phones.
    assert _put(owner_headers, "fcm", {"devices": []}).status_code == 400
    # Test send goes to this phone only, with the test sentence.
    r = client.post(f"/api/mobile/devices/{view['id']}/test", headers=owner_headers)
    assert r.json()["ok"] is True and sent[-1]["message"]["token"] == token
    # A phone that changes hands moves to its new member.
    assert _register(member_headers, token).status_code == 200
    assert client.get("/api/mobile/devices", headers=owner_headers).json()["devices"] == []
    assert len(client.get("/api/mobile/devices", headers=member_headers).json()["devices"]) == 1
    mine = client.get("/api/mobile/devices", headers=member_headers).json()["devices"][0]["id"]
    assert client.delete(f"/api/mobile/devices/{mine}", headers=member_headers).status_code == 204
    assert client.get("/api/mobile/devices", headers=member_headers).json()["devices"] == []


def test_a_phone_moving_to_another_organisation_leaves_the_first(sent):
    a_headers, a = _auth(_email("org-a"))
    b_headers, b = _auth(_email("org-b"))
    _set_flag(a["tenant_id"])
    _set_flag(b["tenant_id"])
    token = _token()
    assert _register(a_headers, token).status_code == 200
    assert _register(b_headers, token).status_code == 200
    assert client.get("/api/mobile/devices", headers=a_headers).json()["devices"] == []
    assert len(client.get("/api/mobile/devices", headers=b_headers).json()["devices"]) == 1


# --- routing through the outbox -----------------------------------------------------------------------------------------

def test_signals_approvals_exits_and_renew_reach_the_right_phones_with_the_right_screen(sent):
    owner_headers, owner = _auth(_email("route"))
    tenant = owner["tenant_id"]
    _set_flag(tenant)
    member_headers, member = _member(owner_headers)
    owner_token, member_token = _token(), _token()
    _register(owner_headers, owner_token)
    member_device = _register(member_headers, member_token).json()["id"]

    # A signal addressed to the owner: only the owner's phone, opening its Signals item.
    entry = _action(tenant, "ENTRY")
    signal = _notify(tenant, NotificationType.AI_PROPOSAL, ai_action_id=entry, user_id=owner["id"])
    assert [r.address for r in _rows(tenant, signal)] == [push_kinds.device_id(owner_token)]
    # An organisation-wide approval and exit: both phones, one row each.
    question = _action(tenant, "EXIT_POSITION")
    approval = _notify(tenant, NotificationType.AI_PROPOSAL, ai_action_id=question)
    closed = _notify(tenant, NotificationType.EXIT, severity=NotificationSeverity.INFO, related_trade_id=None)
    renew = _notify(tenant, NotificationType.TOKEN_EXPIRED)
    for n in (approval, closed, renew):
        assert len(_rows(tenant, n)) == 2
    # A kind no phone gets queues nothing for phones.
    report = _notify(tenant, NotificationType.EOD_SUMMARY, severity=NotificationSeverity.INFO)
    assert _rows(tenant, report) == []

    sent_before = len(sent)
    _dispatch(tenant)
    messages = [m["message"] for m in sent[sent_before:]]
    by_path: Dict[str, List[str]] = {}
    for m in messages:
        by_path.setdefault(m["data"]["path"], []).append(m["token"])
    assert by_path[f"/signals?proposal={entry}"] == [owner_token]
    assert sorted(by_path[f"/copilot/watchtower?action={question}"]) == sorted([owner_token, member_token])
    assert sorted(by_path["/positions"]) == sorted([owner_token, member_token])
    assert sorted(by_path["/broker"]) == sorted([owner_token, member_token])
    # MA-7: nothing from the notification itself (symbol, price, P&L) is in any payload.
    for m in messages:
        blob = json.dumps(m)
        assert "NIFTY" not in blob and "RELIANCE" not in blob and "1,200" not in blob and "23,050" not in blob
        assert m["notification"]["title"] in {t for t, _ in push_kinds.PHONE_TEXT.values()}
        assert set(m["data"]) == {"path", "kind", "notification_id"}
        assert m["android"]["priority"] == "high"

    # The member turns exits off on their phone: the next exit reaches the owner only; an emergency still reaches both.
    client.patch(f"/api/mobile/devices/{member_device}", headers=member_headers, json={"kinds": ["signals", "approvals", "renew"]})
    closed_again = _notify(tenant, NotificationType.EXIT, severity=NotificationSeverity.INFO)
    assert [r.address for r in _rows(tenant, closed_again)] == [push_kinds.device_id(owner_token)]
    kill = _notify(tenant, NotificationType.SYSTEM_FAILURE, severity=NotificationSeverity.EMERGENCY)
    assert len(_rows(tenant, kill)) == 2


def test_a_deactivated_member_s_phone_gets_nothing(sent):
    from app.db.models import User
    owner_headers, owner = _auth(_email("gone-member"))
    tenant = owner["tenant_id"]
    _set_flag(tenant)
    member_headers, member = _member(owner_headers)
    _register(owner_headers, _token())
    _register(member_headers, _token())

    async def deactivate():
        async with _session_factory() as session:
            (await session.get(User, member["id"])).is_active = False
            await session.commit()
    _run(deactivate())
    assert len(_rows(tenant, _notify(tenant, NotificationType.TOKEN_EXPIRED))) == 1


def test_flag_off_queues_nothing_and_turning_it_off_later_suppresses(sent):
    headers, me = _auth(_email("flagoff"))
    tenant = me["tenant_id"]
    _set_flag(tenant)
    _register(headers, _token())
    queued = _notify(tenant, NotificationType.TOKEN_EXPIRED)
    assert len(_rows(tenant, queued)) == 1
    _set_flag(tenant, on=False)
    assert _rows(tenant, _notify(tenant, NotificationType.TOKEN_EXPIRED)) == []
    before = len(sent)
    _dispatch(tenant)
    assert len(sent) == before
    row = _rows(tenant, queued)[0]
    assert row.status == "SUPPRESSED" and row.reason_code == "flag_off"


def test_a_gone_token_removes_the_phone_and_is_not_retried():
    headers, me = _auth(_email("gone"))
    tenant = me["tenant_id"]
    _set_flag(tenant)

    async def gone(message):
        raise fcm.TokenGone("gone")

    fcm.set_sender(gone)
    try:
        _register(headers, _token())
        n = _notify(tenant, NotificationType.EXIT, severity=NotificationSeverity.INFO)
        _dispatch(tenant)
    finally:
        fcm.set_sender(None)
    row = _rows(tenant, n)[0]
    assert row.status == "SUPPRESSED" and row.reason_code == "device_gone"
    assert decrypt_raw(_fcm_channel(tenant))["devices"] == []


def test_no_server_key_records_not_configured(monkeypatch, sent):
    headers, me = _auth(_email("lostkey"))
    tenant = me["tenant_id"]
    _set_flag(tenant)
    _register(headers, _token())
    n = _notify(tenant, NotificationType.EXIT, severity=NotificationSeverity.INFO)
    fcm.set_sender(None)                              # the key goes away before the send
    monkeypatch.setattr(config, "FCM_SERVICE_ACCOUNT_JSON_BASE64", "")
    monkeypatch.setattr(config, "FCM_SERVICE_ACCOUNT_FILE", "")
    _dispatch(tenant)
    row = _rows(tenant, n)[0]
    assert row.status == "SUPPRESSED" and row.reason_code == "not_configured"


def test_quiet_hours_deliver_without_sound_but_critical_rings(sent):
    headers, me = _auth(_email("quiet"))
    tenant = me["tenant_id"]
    _set_flag(tenant)

    async def policy():
        async with _session_factory() as session:
            # A window around the clock now (it may cross midnight), so the test never runs in the 23:59 gap.
            now = datetime.now(timezone.utc)
            start, end = (now - timedelta(hours=2)).strftime("%H:%M"), (now + timedelta(hours=2)).strftime("%H:%M")
            session.add(NotificationPolicyRecord(tenant_id=tenant, timezone="UTC", quiet_start=start, quiet_end=end))
            await session.commit()
    _run(policy())
    _register(headers, _token())
    _notify(tenant, NotificationType.EXIT, severity=NotificationSeverity.INFO)
    before = len(sent)
    _dispatch(tenant)
    quiet = sent[before]["message"]["android"]
    assert quiet["priority"] == "normal" and quiet["notification"]["channel_id"] == "quiet" and quiet["notification"]["default_sound"] is False
    _notify(tenant, NotificationType.TOKEN_EXPIRED, severity=NotificationSeverity.CRITICAL)
    before = len(sent)
    _dispatch(tenant)
    loud = sent[before]["message"]["android"]
    assert loud["priority"] == "high" and loud["notification"]["channel_id"] == "renew"


def test_the_phone_channel_does_not_count_against_the_plan_limit(sent):
    from app.plans.limits import count_alert_channels
    headers, me = _auth(_email("limit"))
    _set_flag(me["tenant_id"])
    _register(headers, _token())

    async def count():
        async with _session_factory() as session:
            return await count_alert_channels(session, me["tenant_id"])
    assert _run(count()) == 0


# --- the real sender against a mocked Google ------------------------------------------------------------------------

def _service_account() -> Dict[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return {"type": "service_account", "project_id": "atp-b8-test", "client_email": "b8@atp-b8-test.iam.example", "private_key": pem}


def test_real_sender_signs_the_assertion_and_posts_the_v1_message(monkeypatch):
    account = _service_account()
    monkeypatch.setattr(config, "FCM_SERVICE_ACCOUNT_JSON_BASE64", base64.b64encode(json.dumps(account).encode()).decode())
    monkeypatch.setattr(config, "FCM_PROJECT_ID", "")
    fcm._access.update(token=None, expires=0.0)
    assert fcm.configured() and fcm.project_id() == "atp-b8-test"
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "minted-in-test", "expires_in": 3600})
        if b"gone-phone" in request.content:
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}})
        return httpx.Response(200, json={"name": "projects/atp-b8-test/messages/1"})

    message = fcm.build_message("phone-token-from-test", title="t", body="b", path="/broker", kind="renew", notification_id=3, quiet=False)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await fcm.send(message, http)
            with pytest.raises(fcm.TokenGone):
                await fcm.send(fcm.build_message("gone-phone", title="t", body="b", path="/", kind="exits", notification_id=None,
                                                 quiet=True), http)
    _run(go())
    token_request, send_request = seen[0], seen[1]
    assert str(send_request.url) == "https://fcm.googleapis.com/v1/projects/atp-b8-test/messages:send"
    assert send_request.headers["Authorization"] == "Bearer minted-in-test"
    assert json.loads(send_request.content) == message
    assert len([r for r in seen if r.url.host == "oauth2.googleapis.com"]) == 1        # the access token is cached
    # The assertion is an RS256 JWT for the messaging scope, signed with the account's key.
    form = parse_qs(token_request.content.decode())
    header, payload, signature = form["assertion"][0].split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    claims = json.loads(base64.urlsafe_b64decode(pad(payload)))
    assert claims["scope"] == fcm.SCOPE and claims["iss"] == account["client_email"] and claims["aud"] == fcm.TOKEN_URL
    public = serialization.load_pem_private_key(account["private_key"].encode(), password=None).public_key()
    public.verify(base64.urlsafe_b64decode(pad(signature)), f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr, call-arg]
    fcm._access.update(token=None, expires=0.0)


def test_an_unreadable_key_reads_as_not_configured(monkeypatch):
    monkeypatch.setattr(config, "FCM_SERVICE_ACCOUNT_JSON_BASE64", "not base64 json")
    assert fcm.service_account() is None and not fcm.configured()
    assert "not set" in fcm.status_line()
    with pytest.raises(fcm.FcmError):
        _run(fcm.send({"message": {}}))

