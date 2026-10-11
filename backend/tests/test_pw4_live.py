"""PW4: Telegram <-> app. Every Telegram alert and proposal carries an "Open in app" link to the exact screen (only
with an absolute app address - never a guessed one), and a live stream tells open apps within 2 s that a proposal was
decided or a notification arrived, for their own organisation only."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List

import httpx

from app.alerts import dispatcher
from app.alerts.deeplink import app_base_url, app_url, open_in_app_row
from app.core import config as app_config
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AiActionRecord
from app.live import routes as live
from app.notifications.service import notify
from tests.test_alerts import TELEGRAM, _auth, _put, _telegram_mock
from tests.test_auth_api import _session_factory, client

live.session_factory = _session_factory


def _n(**kw):
    base = dict(id=3, tenant_id=1, event_type=NotificationType.AI_PROPOSAL.value, ai_action_id=12, related_trade_id=None, related_order_id=None,
                severity="WARNING", title="Exit NIFTY?", message="The monitor proposes an exit", created_at=datetime.now(timezone.utc),
                metadata_json=None)
    base.update(kw)
    return SimpleNamespace(**base)


# --- Open in app -------------------------------------------------------------------------------------------------

def test_no_link_without_an_absolute_app_address(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "", raising=False)
    monkeypatch.setattr(app_config, "FRONTEND_URL", "/", raising=False)
    assert app_base_url() is None and app_url(_n()) is None and open_in_app_row(_n()) is None


def test_link_points_at_the_exact_screen(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "https://atp.example.in/", raising=False)
    assert app_url(_n()) == "https://atp.example.in/copilot/watchtower?action=12"
    assert open_in_app_row(_n(ai_action_id=None, event_type=NotificationType.ENTRY.value, related_trade_id=9)) == [
        {"text": "Open in app", "url": "https://atp.example.in/positions?trade=9"}]
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "", raising=False)
    monkeypatch.setattr(app_config, "FRONTEND_URL", "https://app.example.in", raising=False)
    assert app_url(_n()) == "https://app.example.in/copilot/watchtower?action=12"


def test_telegram_alert_carries_the_open_in_app_button(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "https://atp.example.in", raising=False)
    headers, me = _auth("pw4-telegram@example.com")
    assert _put(headers, "telegram", TELEGRAM, min_severity="INFO").status_code == 200

    async def channel():
        from sqlalchemy import select

        from app.db.models import AlertChannelRecord
        async with _session_factory() as session:
            return await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == me["tenant_id"]))
    record = asyncio.run(channel())
    seen: List[httpx.Request] = []
    note = _n(tenant_id=me["tenant_id"], ai_action_id=None, event_type=NotificationType.SCREEN_ALERT.value, id=77)
    asyncio.run(dispatcher.send_via_channel(record, note, client=_telegram_mock([httpx.Response(200, json={"ok": True})], seen)))
    body = json.loads(seen[0].content)
    assert body["reply_markup"] == {"inline_keyboard": [[{"text": "Open in app", "url": "https://atp.example.in/notifications?n=77"}]]}


def test_telegram_alert_without_app_address_is_unchanged(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "", raising=False)
    monkeypatch.setattr(app_config, "FRONTEND_URL", "/", raising=False)
    headers, me = _auth("pw4-telegram-plain@example.com")
    assert _put(headers, "telegram", TELEGRAM, min_severity="INFO").status_code == 200

    async def channel():
        from sqlalchemy import select

        from app.db.models import AlertChannelRecord
        async with _session_factory() as session:
            return await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == me["tenant_id"]))
    seen: List[httpx.Request] = []
    asyncio.run(dispatcher.send_via_channel(asyncio.run(channel()), _n(tenant_id=me["tenant_id"], ai_action_id=None),
                                            client=_telegram_mock([httpx.Response(200, json={"ok": True})], seen)))
    assert "reply_markup" not in json.loads(seen[0].content)


def test_proposal_buttons_keep_approve_reject_and_add_the_link(monkeypatch):
    from app.telegram_inbound import service as inbound
    sent = {}

    async def fake_keyboard(session, action, chat_id, now):
        return {"inline_keyboard": [[{"text": "Approve", "callback_data": "a"}, {"text": "Reject", "callback_data": "r"}]]}

    async def fake_reply(cfg, chat_id, text, client=None, reply_markup=None, parse_mode="HTML"):
        sent["markup"] = reply_markup
        return {"ok": True}

    monkeypatch.setattr(inbound, "keyboard_for", fake_keyboard)
    monkeypatch.setattr(inbound, "reply", fake_reply)
    monkeypatch.setattr(inbound, "decrypt_config", lambda channel: SimpleNamespace(inbound_enabled=True, chat_id="1"))
    row = [{"text": "Open in app", "url": "https://atp.example.in/copilot/watchtower?action=12"}]
    ok = asyncio.run(inbound.send_proposal_buttons(None, SimpleNamespace(), SimpleNamespace(), "<b>x</b>", extra_row=row))
    assert ok and sent["markup"]["inline_keyboard"][0][0]["text"] == "Approve" and sent["markup"]["inline_keyboard"][-1] == row


# --- live stream -------------------------------------------------------------------------------------------------

def _collect(tenant_id: int, during, seconds: float = 2.5) -> List[str]:
    """Runs the stream body for `seconds` while `during()` changes the database half-way; returns the event names."""
    async def go():
        names: List[str] = []

        async def never():
            return False

        async def consume():
            async for chunk in live.events(tenant_id, never, max_seconds=seconds, poll_seconds=0.2, heartbeat_seconds=0.5):
                text = chunk.decode()
                if text.startswith("event: "):
                    names.append(text.split("\n", 1)[0][len("event: "):])
                elif text.startswith(": ping"):
                    names.append("ping")

        async def act():
            await asyncio.sleep(0.6)
            await during()
        await asyncio.gather(consume(), act())
        return names
    return asyncio.run(go())


def test_a_decision_reaches_open_apps_of_that_organisation_only():
    _, a = _auth("pw4-live-a@example.com")
    _, b = _auth("pw4-live-b@example.com")
    now = datetime.now(timezone.utc)

    async def propose_for_a():
        async with _session_factory() as session:
            session.add(AiActionRecord(tenant_id=a["tenant_id"], action="REVIEW_STRATEGY", rule="WIN_RATE_DRIFT", reason="x",
                                       status="PROPOSED", expires_at=now + timedelta(hours=1)))
            await session.commit()

    names_a = _collect(a["tenant_id"], propose_for_a)
    assert names_a[0] == "ready" and "ai_actions" in names_a and names_a[-1] == "bye" and "ping" in names_a

    names_b = _collect(b["tenant_id"], propose_for_a)
    assert "ai_actions" not in names_b                                 # another organisation's proposal is not B's event


def test_a_new_notification_is_announced():
    _, me = _auth("pw4-live-notes@example.com")

    async def add_note():
        async with _session_factory() as session:
            await notify(session, me["tenant_id"], NotificationType.SYSTEM_FAILURE, title="t", message="m", severity=NotificationSeverity.INFO)

    assert "notifications" in _collect(me["tenant_id"], add_note)


def test_the_stream_needs_a_login():
    assert client.get("/api/live/stream").status_code == 401


# --- Telegram message follows the app (ported from the Trade bot's vision/tg.py) ------------------------------------

def _action_row(tenant_id: int, **kw) -> int:
    async def go():
        async with _session_factory() as session:
            row = AiActionRecord(tenant_id=tenant_id, action="REVIEW_STRATEGY", rule="WIN_RATE_DRIFT", reason="x", status="PROPOSED",
                                 expires_at=datetime.now(timezone.utc) + timedelta(hours=1), **kw)
            session.add(row)
            await session.commit()
            return row.id
    return asyncio.run(go())


def _telegram_calls(seen: List[httpx.Request]) -> List[tuple]:
    out = []
    for r in seen:
        method = r.url.path.rsplit("/", 1)[-1]
        try:
            body = json.loads(r.content)
        except ValueError:
            body = {}
        out.append((method, body))
    return out


def test_proposal_buttons_remember_the_telegram_message(monkeypatch):
    from app.telegram_inbound import service as inbound

    async def fake_keyboard(session, action, chat_id, now):
        return {"inline_keyboard": [[{"text": "Approve", "callback_data": "a"}]]}

    async def fake_reply(cfg, chat_id, text, client=None, reply_markup=None, parse_mode="HTML"):
        return {"ok": True, "result": {"message_id": 4242}}

    monkeypatch.setattr(inbound, "keyboard_for", fake_keyboard)
    monkeypatch.setattr(inbound, "reply", fake_reply)
    monkeypatch.setattr(inbound, "decrypt_config", lambda channel: SimpleNamespace(inbound_enabled=True, chat_id="-100777"))
    _, me = _auth("pw4-msgid@example.com")
    action_id = _action_row(me["tenant_id"])

    async def go():
        async with _session_factory() as session:
            action = await session.get(AiActionRecord, action_id)
            assert await inbound.send_proposal_buttons(session, SimpleNamespace(), action, "<b>x</b>")
            await session.commit()
            await session.refresh(action)
            return action.telegram_chat_id, action.telegram_message_id
    assert asyncio.run(go()) == ("-100777", 4242)


def test_an_app_decision_takes_the_buttons_off_and_says_so(monkeypatch):
    from app.telegram_inbound import service as inbound
    headers, me = _auth("pw4-sync@example.com")
    assert _put(headers, "telegram", TELEGRAM, min_severity="INFO").status_code == 200
    action_id = _action_row(me["tenant_id"], telegram_chat_id="987654", telegram_message_id=31)
    seen: List[httpx.Request] = []
    monkeypatch.setattr(inbound, "http_client", lambda: _telegram_mock([httpx.Response(200, json={"ok": True})], seen))

    r = client.post(f"/api/ai/actions/{action_id}/reject", headers=headers, json={"note": "not now"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    calls = _telegram_calls(seen)
    assert calls[0] == ("editMessageReplyMarkup", {"chat_id": "987654", "message_id": 31, "reply_markup": {"inline_keyboard": []}})
    assert calls[1][0] == "sendMessage" and calls[1][1]["reply_to_message_id"] == 31 and calls[1][1]["text"].startswith("❌ Rejected in the app")


def test_no_telegram_message_no_call_and_telegram_down_never_fails_the_decision(monkeypatch):
    from app.telegram_inbound import service as inbound
    headers, me = _auth("pw4-sync-down@example.com")
    assert _put(headers, "telegram", TELEGRAM, min_severity="INFO").status_code == 200
    seen: List[httpx.Request] = []
    monkeypatch.setattr(inbound, "http_client", lambda: _telegram_mock([httpx.Response(200, json={"ok": True})], seen))
    plain = _action_row(me["tenant_id"])
    assert client.post(f"/api/ai/actions/{plain}/reject", headers=headers, json={}).status_code == 200
    assert seen == []                                                   # never sent to Telegram: nothing to update

    def broken():
        def handler(request):
            raise httpx.ConnectError("down")
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(inbound, "http_client", broken)
    linked = _action_row(me["tenant_id"], telegram_chat_id="987654", telegram_message_id=32)
    assert client.post(f"/api/ai/actions/{linked}/reject", headers=headers, json={}).json()["status"] == "REJECTED"


def test_expiry_takes_the_buttons_off_too(monkeypatch):
    from app.ai import monitor
    from app.telegram_inbound import service as inbound
    synced = []

    async def fake_sync(session, action, client=None, now=None):
        synced.append((action.id, action.status))
        return True
    monkeypatch.setattr(inbound, "sync_proposal_message", fake_sync)
    _, me = _auth("pw4-expire@example.com")
    action_id = _action_row(me["tenant_id"], telegram_chat_id="1", telegram_message_id=9)

    async def go():
        async with _session_factory() as session:
            return await monitor.expire_stale(session, datetime.now(timezone.utc) + timedelta(hours=2))
    assert asyncio.run(go()) >= 1 and (action_id, "EXPIRED") in synced


def test_photo_falls_back_to_plain_text_when_telegram_cannot_parse_the_caption():
    from app.telegram_inbound import service as inbound
    seen: List[httpx.Request] = []
    replies = [httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"}),
               httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})]
    mock = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: (seen.append(req), replies.pop(0))[1]))
    cfg = SimpleNamespace(bot_token="123:abc")
    got = asyncio.run(inbound.send_photo(cfg, "42", b"\x89PNG fake", "<b>NIFTY</b> <i>broken", client=mock))
    assert got == 77 and len(seen) == 2 and seen[0].url.path.endswith("/sendPhoto")
    assert b'name="parse_mode"' in seen[0].content and b'name="parse_mode"' not in seen[1].content
    assert b"NIFTY broken" in seen[1].content


def test_photo_without_an_image_is_a_text_message():
    from app.telegram_inbound import service as inbound
    seen: List[httpx.Request] = []
    mock = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: (seen.append(req), httpx.Response(200, json={"ok": True, "result": {"message_id": 5}}))[1]))
    assert asyncio.run(inbound.send_photo(SimpleNamespace(bot_token="t"), "42", None, "hello", client=mock)) == 5
    assert seen[0].url.path.endswith("/sendMessage")
