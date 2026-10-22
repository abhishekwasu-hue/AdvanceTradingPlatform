"""UX2 fix 5: "Connect Telegram" with the platform's bot - a one-time code in a t.me link, Start in a private chat links
the organisation's Telegram channel (no token or chat id typed), the platform token is never stored per organisation,
the chat cannot be moved by the alert-channel API, and the shared bot gets no command or approval rights."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.alerts import dispatcher, platform_bot
from app.alerts.channels import decrypt_config, decrypt_raw
from app.core import config as app_config
from app.db.models import AlertChannelRecord, NotificationLinkRecord
from app.telegram_inbound import service as tg
from tests.test_auth_api import _session_factory, client
from tests.test_phase_be_telegram_inbound import _audit_events, _flag
from tests.test_trading_worker import _tenant

TOKEN = "424242:PLATFORM-token-value"


def _run(coro):
    return asyncio.run(coro)


class _Bot:
    """The platform bot's Telegram API: getMe, sendMessage, getUpdates (queued updates), setWebhook."""
    def __init__(self, send_ok=True):
        self.calls: list = []
        self.updates: list = []
        self.send_ok = send_ok

    def client(self):
        def handler(request: httpx.Request):
            method = request.url.path.rsplit("/", 1)[-1]
            body = json.loads(request.content or b"{}")
            self.calls.append({"method": method, "path": request.url.path, **body})
            if method == "getMe":
                return httpx.Response(200, json={"ok": True, "result": {"username": "AiAlgoDeskBot"}})
            if method == "getUpdates":
                offset = body.get("offset", 0)
                return httpx.Response(200, json={"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset]})
            if method == "sendMessage" and not self.send_ok:
                return httpx.Response(400, json={"ok": False, "description": "Forbidden: bot was blocked by the user"})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _platform(monkeypatch):
    async def never(*_a, **_k):
        return False
    monkeypatch.setattr(tg, "rate_limited", never)
    monkeypatch.setattr(app_config, "ATP_TELEGRAM_BOT_TOKEN", TOKEN, raising=False)
    monkeypatch.setattr(app_config, "ATP_TELEGRAM_BOT_USERNAME", "", raising=False)
    monkeypatch.setattr(app_config, "ATP_TELEGRAM_WEBHOOK_SECRET", "", raising=False)
    monkeypatch.setattr(platform_bot, "_username_cache", {})
    monkeypatch.setattr(platform_bot, "_offset", {})
    bot = _Bot()
    monkeypatch.setattr(tg, "http_client", bot.client)
    return bot


def _start_update(update_id, chat_id, code, from_id=None):
    return {"update_id": update_id, "message": {"message_id": update_id, "chat": {"id": chat_id, "type": "private" if from_id is None else "group"},
                                                "from": {"id": from_id if from_id is not None else chat_id}, "text": f"/start {code}"}}


def _code_from(url):
    assert url.startswith("https://t.me/AiAlgoDeskBot?start=")
    return url.split("=", 1)[1]


def _channel(tenant_id):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == tenant_id,
                                                                          AlertChannelRecord.channel_type == "TELEGRAM"))
    return _run(go())


def test_connect_links_the_chat_that_pressed_start_and_stores_no_token(_platform):
    bot = _platform
    t = _tenant("ux2-tg-connect@example.com")
    state = client.get("/api/alerts/telegram/connect", headers=t["headers"]).json()
    assert state == {"available": True, "connected": False, "own_bot": False, "working": False}

    made = client.post("/api/alerts/telegram/connect", headers=t["headers"])
    assert made.status_code == 200, made.text
    code = _code_from(made.json()["url"])
    assert "telegram_connect_started" in _audit_events(t["tenant_id"])

    bot.updates = [_start_update(10, -100555, code, from_id=777), _start_update(11, 4242, "AAAAAAAAAAAAAAAAAAAA"), _start_update(12, 5150, code)]
    state = client.get("/api/alerts/telegram/connect", headers=t["headers"]).json()      # the Connect screen's check reads the messages
    assert state["connected"] is True and state["working"] is True
    replies = {c["chat_id"]: c["text"] for c in bot.calls if c["method"] == "sendMessage"}
    assert "private chat" in replies["-100555"] and "did not work" in replies["4242"] and replies["5150"].startswith("Connected.")
    assert "telegram_connected" in _audit_events(t["tenant_id"])

    record = _channel(t["tenant_id"])
    raw = decrypt_raw(record)
    assert raw["chat_id"] == "5150" and raw["platform_bot"] is True and raw["bot_token"] == ""     # the platform token is never stored
    assert TOKEN not in json.dumps(raw)
    assert decrypt_config(record).bot_token == TOKEN                                             # filled in from the server's setting
    listed = client.get("/api/alert-channels", headers=t["headers"]).json()
    assert listed[0]["config"] == {"chat_id": "5150", "platform_bot": True, "bot_token_hint": "ATP bot"}

    bot.updates.append(_start_update(13, 6160, code))                                           # a code works once
    client.post("/api/alerts/telegram/connect", headers=t["headers"])                            # (a pending code makes the check poll)
    client.get("/api/alerts/telegram/connect", headers=t["headers"])
    assert decrypt_raw(_channel(t["tenant_id"]))["chat_id"] == "5150"


def test_send_a_test_goes_through_the_platform_bot_and_a_missing_token_says_so(_platform, monkeypatch):
    bot = _platform
    t = _tenant("ux2-tg-test@example.com")
    code = _code_from(client.post("/api/alerts/telegram/connect", headers=t["headers"]).json()["url"])
    bot.updates = [_start_update(20, 7070, code)]
    client.get("/api/alerts/telegram/connect", headers=t["headers"])
    sent = []

    async def fake_send(config, html_text, client_=None, chat_id=None, reply_markup=None):
        if not config.bot_token:
            raise RuntimeError("Telegram alerts are not set up on this server yet")
        sent.append((config.bot_token, config.chat_id))
    monkeypatch.setattr(dispatcher, "send_telegram", fake_send)
    ok = client.post("/api/alert-channels/telegram/test", headers=t["headers"]).json()
    assert ok["ok"] is True and sent == [(TOKEN, "7070")]

    monkeypatch.setattr(app_config, "ATP_TELEGRAM_BOT_TOKEN", "", raising=False)
    assert client.get("/api/alerts/telegram/connect", headers=t["headers"]).json()["available"] is False
    refused = client.post("/api/alerts/telegram/connect", headers=t["headers"])
    assert refused.status_code == 409 and "not set up on this server" in refused.json()["detail"]
    failed = client.post("/api/alert-channels/telegram/test", headers=t["headers"]).json()
    assert failed["ok"] is False and "not set up on this server" in failed["detail"]


def test_the_real_send_refuses_without_a_token():
    from app.alerts.channels import TelegramConfig
    with pytest.raises(RuntimeError, match="not set up on this server"):
        _run(dispatcher.send_telegram(TelegramConfig(chat_id="1", platform_bot=True), "hi"))


def test_the_alert_channel_api_cannot_move_the_platform_bot_to_another_chat(_platform):
    bot = _platform
    t = _tenant("ux2-tg-hijack@example.com")
    # A fresh channel can never claim the platform bot through the alert-channel API.
    claimed = client.put("/api/alert-channels/telegram", headers=t["headers"], json={"config": {"chat_id": "999", "platform_bot": True}})
    assert claimed.status_code == 400 and _channel(t["tenant_id"]) is None

    code = _code_from(client.post("/api/alerts/telegram/connect", headers=t["headers"]).json()["url"])
    bot.updates = [_start_update(30, 8080, code)]
    client.get("/api/alerts/telegram/connect", headers=t["headers"])
    moved = client.put("/api/alert-channels/telegram", headers=t["headers"], json={"enabled": False, "min_severity": "INFO", "config": {"chat_id": "999"}})
    assert moved.status_code == 200
    raw = decrypt_raw(_channel(t["tenant_id"]))
    assert raw["chat_id"] == "8080" and raw["platform_bot"] is True and moved.json()["enabled"] is False   # the floor / on-off still change

    own = client.put("/api/alert-channels/telegram", headers=t["headers"], json={"config": {"bot_token": "123456:OWN-bot-token", "chat_id": "999"}})
    assert own.status_code == 200                                                    # an own bot replaces the platform one
    raw = decrypt_raw(_channel(t["tenant_id"]))
    assert raw["platform_bot"] is False and raw["chat_id"] == "999" and raw["bot_token"] == "123456:OWN-bot-token"
    state = client.get("/api/alerts/telegram/connect", headers=t["headers"]).json()
    assert state["own_bot"] is True and state["connected"] is False
    refused = client.post("/api/alerts/telegram/connect", headers=t["headers"])
    assert refused.status_code == 409 and "its own bot" in refused.json()["detail"]


def test_a_code_never_replaces_an_organisations_own_bot_and_expires(_platform):
    bot = _platform
    t = _tenant("ux2-tg-ownbot@example.com")
    code = _code_from(client.post("/api/alerts/telegram/connect", headers=t["headers"]).json()["url"])
    assert client.put("/api/alert-channels/telegram", headers=t["headers"], json={"config": {"bot_token": "123456:OWN-bot-token", "chat_id": "1"}}).status_code == 200

    async def consume(code_, chat, now=None):
        async with _session_factory() as session:
            record, why = await platform_bot.consume(session, code_, chat, chat, now=now)
            await session.commit()
            return why
    assert _run(consume(code, "9090")) == "own_bot"
    assert decrypt_raw(_channel(t["tenant_id"]))["bot_token"] == "123456:OWN-bot-token"

    t2 = _tenant("ux2-tg-expired@example.com")
    code2 = _code_from(client.post("/api/alerts/telegram/connect", headers=t2["headers"]).json()["url"])
    assert _run(consume(code2, "9191", now=datetime.now(timezone.utc) + timedelta(hours=1))) == "expired"
    assert _channel(t2["tenant_id"]) is None
    code3 = _code_from(client.post("/api/alerts/telegram/connect", headers=t2["headers"]).json()["url"])
    code4 = _code_from(client.post("/api/alerts/telegram/connect", headers=t2["headers"]).json()["url"])
    assert _run(consume(code3, "9292")) == "unknown_code"                            # a new code revokes the older one
    assert _run(consume(code4, "9292")) == "linked"

    async def links_of(tenant_id):
        async with _session_factory() as session:
            return [(r.channel, r.status) for r in await session.scalars(select(NotificationLinkRecord).where(NotificationLinkRecord.tenant_id == tenant_id))]
    assert ("tg_platform", "used") in _run(links_of(t2["tenant_id"]))                # never "linked": no second per-user copy of each alert
    assert bot.calls == [c for c in bot.calls if c["method"] in ("getMe",)]           # consume itself sends nothing


def test_the_shared_bot_gets_no_two_way_commands(_platform):
    bot = _platform
    _flag(True)
    t = _tenant("ux2-tg-inbound@example.com")
    code = _code_from(client.post("/api/alerts/telegram/connect", headers=t["headers"]).json()["url"])
    bot.updates = [_start_update(40, 1212, code)]
    client.get("/api/alerts/telegram/connect", headers=t["headers"])
    refused = client.put("/api/telegram/inbound", headers=t["headers"], json={"enabled": True, "allowed_chat_ids": []})
    assert refused.status_code == 400 and "own bot" in refused.json()["detail"]


def test_webhook_needs_the_secret_and_links_by_post(_platform, monkeypatch):
    monkeypatch.setattr(app_config, "ATP_TELEGRAM_WEBHOOK_SECRET", "hook-secret-value", raising=False)
    t = _tenant("ux2-tg-webhook@example.com")
    code = _code_from(client.post("/api/alerts/telegram/connect", headers=t["headers"]).json()["url"])
    path = platform_bot.WEBHOOK_PATH
    assert client.post(path, json=_start_update(50, 3131, code)).status_code == 401
    assert client.post(path, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}, json=_start_update(50, 3131, code)).status_code == 401
    ok = client.post(path, headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret-value"}, json=_start_update(50, 3131, code))
    assert ok.status_code == 200 and ok.json()["result"] == "linked"
    assert client.get("/api/alerts/telegram/connect", headers=t["headers"]).json()["connected"] is True
    assert not [c for c in _platform.calls if c["method"] == "getUpdates"]              # webhook mode never polls
    hello = client.post(path, headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret-value"},
                        json={"update_id": 51, "message": {"chat": {"id": 3131}, "from": {"id": 3131}, "text": "hi"}})
    assert hello.json()["handled"] == "hello"


def test_admin_routes_are_super_admin_only(_platform):
    t = _tenant("ux2-tg-admin@example.com")
    assert client.get("/api/admin/telegram/platform", headers=t["headers"]).status_code == 403
    assert client.post("/api/admin/telegram/platform/register", headers=t["headers"]).status_code == 403

