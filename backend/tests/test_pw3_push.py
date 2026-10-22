"""PW3: web push on every platform - a click opens the exact screen (same-origin path), per-category preferences
decide which notifications reach the devices (EMERGENCY always does), the preference survives device add/remove and
the pruning of gone devices, and a filtered-out category never creates an outbox row."""
import asyncio
import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.alerts import dispatcher, webpush
from app.alerts.channels import PushConfig, decrypt_raw, merge_push
from app.alerts.deeplink import PUSH_CATEGORIES, app_path, category_of, push_wanted
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AlertChannelRecord, AlertDeliveryRecord
from app.notifications.service import notify
from tests.test_alerts import _auth, _put
from tests.test_auth_api import _session_factory


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _subscription(n: int = 1) -> dict:
    """A syntactically real subscription: a P-256 public key and a 16-byte auth secret, like a browser's."""
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/device-{n}", "p256dh": _b64(key), "auth": _b64(bytes(range(16))), "label": f"phone {n}"}


def _n(**kw):
    base = dict(id=42, event_type=NotificationType.SYSTEM_FAILURE.value, ai_action_id=None, related_trade_id=None, related_order_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


# --- deep links --------------------------------------------------------------------------------------------------

def test_a_click_opens_the_screen_the_event_is_about():
    assert app_path(_n(event_type=NotificationType.AI_PROPOSAL.value, ai_action_id=7)) == "/copilot/watchtower?action=7"
    assert app_path(_n(event_type=NotificationType.ENTRY.value, related_trade_id=11)) == "/positions?trade=11"
    assert app_path(_n(event_type=NotificationType.REJECTION.value, related_order_id=5)) == "/orders?order=5"
    assert app_path(_n(event_type=NotificationType.OI_BANNER.value)) == "/option-chain"
    assert app_path(_n(event_type=NotificationType.TOKEN_EXPIRED.value)) == "/broker"
    assert app_path(_n(event_type=NotificationType.SCREEN_ALERT.value, id=9)) == "/notifications?n=9"
    assert app_path(_n(id=None)) == "/notifications"


def test_paths_are_same_origin_only():
    for event in NotificationType:
        path = app_path(_n(event_type=event.value, ai_action_id=1 if event == NotificationType.AI_PROPOSAL else None))
        assert path.startswith("/") and not path.startswith("//"), path


# --- categories --------------------------------------------------------------------------------------------------

def test_every_notification_type_has_a_category():
    for event in NotificationType:
        assert category_of(event.value) in PUSH_CATEGORIES
    assert category_of("SOMETHING_NEW") == "account"


def test_preferences_filter_but_emergency_always_goes_through():
    assert push_wanted(None, NotificationType.EOD_SUMMARY.value, "INFO") is True
    assert push_wanted(["proposals"], NotificationType.AI_PROPOSAL.value, "WARNING") is True
    assert push_wanted(["proposals"], NotificationType.EOD_SUMMARY.value, "INFO") is False
    assert push_wanted([], NotificationType.EMERGENCY_EXIT.value, NotificationSeverity.EMERGENCY.value) is True


def test_unknown_category_is_refused():
    with pytest.raises(ValueError):
        PushConfig.model_validate({"subscriptions": [_subscription()], "categories": ["everything"]})


def test_preference_survives_adding_and_removing_devices():
    stored = {"subscriptions": [_subscription(1)], "categories": ["proposals", "risk"]}
    added = merge_push({"subscription": _subscription(2)}, stored)
    assert added["categories"] == ["proposals", "risk"] and len(added["subscriptions"]) == 2
    removed = merge_push({"remove_endpoint": _subscription(1)["endpoint"]}, added)
    assert removed["categories"] == ["proposals", "risk"] and len(removed["subscriptions"]) == 1
    changed = merge_push({"categories": ["alerts"]}, removed)
    assert changed["categories"] == ["alerts"] and len(changed["subscriptions"]) == 1


# --- API + outbox ------------------------------------------------------------------------------------------------

def _channel(tenant_id: int) -> AlertChannelRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                          AlertChannelRecord.channel_type == "PUSH"))
    return asyncio.run(go())


def _notify(tenant_id: int, event: NotificationType, severity: NotificationSeverity) -> int:
    async def go():
        async with _session_factory() as session:
            return (await notify(session, tenant_id, event, title="t", message="m", severity=severity)).id
    return asyncio.run(go())


def _rows(tenant_id: int) -> List[AlertDeliveryRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == tenant_id)))
    return asyncio.run(go())


def test_api_stores_categories_and_filters_the_outbox():
    headers, me = _auth("pw3-push@example.com")
    r = _put(headers, "push", {"subscription": _subscription(1), "categories": ["proposals", "trades"]}, min_severity="INFO")
    assert r.status_code == 200, r.text
    assert r.json()["config"]["categories"] == ["proposals", "trades"]
    assert _put(headers, "push", {"categories": ["nonsense"]}, min_severity="INFO").status_code == 400

    eod = _notify(me["tenant_id"], NotificationType.EOD_SUMMARY, NotificationSeverity.INFO)
    entry = _notify(me["tenant_id"], NotificationType.ENTRY, NotificationSeverity.INFO)
    kill = _notify(me["tenant_id"], NotificationType.SYSTEM_FAILURE, NotificationSeverity.EMERGENCY)
    queued = {d.notification_id for d in _rows(me["tenant_id"])}
    assert eod not in queued                                    # reports are off for push: no outbox row at all
    assert entry in queued and kill in queued

    # Another device keeps the preference; a categories update keeps the devices.
    assert _put(headers, "push", {"subscription": _subscription(2)}, min_severity="INFO").status_code == 200
    raw = decrypt_raw(_channel(me["tenant_id"]))
    assert raw["categories"] == ["proposals", "trades"] and len(raw["subscriptions"]) == 2


def test_push_payload_carries_the_deep_link_and_pruning_keeps_the_preference(monkeypatch):
    sent: List[dict] = []

    async def fake_send(endpoint, p256dh, auth, payload, client=None):
        if endpoint.endswith("device-2"):
            raise webpush.PushGone(endpoint)
        sent.append(payload)

    monkeypatch.setattr(webpush, "send_push", fake_send)
    headers, me = _auth("pw3-push-link@example.com")
    assert _put(headers, "push", {"subscriptions": [_subscription(1), _subscription(2)], "categories": ["proposals"]}, min_severity="INFO").status_code == 200
    channel = _channel(me["tenant_id"])
    notification = SimpleNamespace(id=5, tenant_id=me["tenant_id"], event_type=NotificationType.AI_PROPOSAL.value, severity="WARNING",
                                   title="Exit NIFTY?", message="The monitor proposes an exit", ai_action_id=31,
                                   related_trade_id=None, related_order_id=None, created_at=datetime.now(timezone.utc))
    asyncio.run(dispatcher.send_via_channel(channel, notification, client=httpx.AsyncClient()))
    assert sent and sent[0]["url"] == "/copilot/watchtower?action=31"
    pruned = json.loads(json.dumps(decrypt_raw(channel)))
    assert [s["label"] for s in pruned["subscriptions"]] == ["phone 1"]
    assert pruned["categories"] == ["proposals"]
