"""Phase BE: Telegram inbound - secret + whitelist, commands and free text (read-only), one-time approve/reject
buttons through monitor.decide/execute for PAPER proposals only, replay/tamper/expiry/foreign-tenant refusals,
rate limit, metering, registration."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
from sqlalchemy import select

from app.ai import monitor
from app.alerts import dispatcher
from app.alerts.channels import decrypt_raw
from app.db.models import AiActionRecord, AlertChannelRecord, AuditLogRecord, StrategyDeploymentRecord, TelegramCallbackRecord
from app.platform import controls
from app.telegram_inbound import service as tg
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import _deploy, _tenant

UTC = timezone.utc
TELEGRAM = {"bot_token": "123456:ABC-DEF-ghi", "chat_id": "987654"}


def _run(coro):
    return asyncio.run(coro)


def _audit_events(tenant_id):
    async def go():
        async with _session_factory() as session:
            return [r.event for r in await session.scalars(select(AuditLogRecord).where(AuditLogRecord.tenant_id == tenant_id))]
    return _run(go())


def _flag(on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["telegram_inbound"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


class _Telegram:
    """Records every Telegram API call and answers ok."""
    def __init__(self):
        self.calls: List[dict] = []

    def client(self):
        def handler(request: httpx.Request):
            body = json.loads(request.content or b"{}")
            self.calls.append({"method": request.url.path.rsplit("/", 1)[-1], "path": request.url.path, **body})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _setup(email, monkeypatch, telegram: _Telegram, allowed=("555",)):
    _flag(True)
    t = _tenant(email)
    assert client.put("/api/alert-channels/telegram", headers=t["headers"], json={"config": TELEGRAM}).status_code == 200
    monkeypatch.setattr(tg, "http_client", telegram.client)
    status = client.put("/api/telegram/inbound", headers=t["headers"], json={"enabled": True, "allowed_chat_ids": list(allowed)}).json()
    assert status["inbound_enabled"] is True and status["has_secret"] is True and set(status["allowed_chat_ids"]) == {"987654", *allowed}

    async def secret_and_token():
        async with _session_factory() as session:
            rec = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == t["tenant_id"]))
            raw = decrypt_raw(rec)
            from app.db.models import Tenant
            tenant = await session.get(Tenant, t["tenant_id"])
            return raw["inbound_secret"], tenant.webhook_token_hash
    t["secret"], t["token"] = _run(secret_and_token())
    assert "inbound_secret" not in json.dumps(status)          # the secret is never returned
    return t


def _post(t, update, secret=None, token=None):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret if secret is not None else t["secret"]}
    return client.post(f"/api/telegram/webhook/{token or t['token']}", headers=headers, json=update)


def _message(chat_id, text, msg_id=1):
    return {"update_id": 1, "message": {"message_id": msg_id, "chat": {"id": int(chat_id)}, "from": {"id": int(chat_id)}, "text": text}}


def test_webhook_guards_token_secret_whitelist_and_answers_commands(monkeypatch):
    telegram = _Telegram()
    t = _setup("be-cmd@example.com", monkeypatch, telegram)
    assert _post(t, _message("987654", "/help"), token="nope").status_code == 401
    assert _post(t, _message("987654", "/help"), secret="wrong").status_code == 401
    ignored = _post(t, _message("4242", "/brief")).json()
    assert ignored["handled"] == "ignored" and "whitelist" in ignored["reason"]
    assert "telegram_inbound_ignored" in _audit_events(t["tenant_id"])
    assert telegram.calls == []                                                   # nothing was answered to a stranger

    help_ = _post(t, _message("987654", "/help")).json()
    assert help_["handled"] == "message" and "Commands" in help_["reply"] and telegram.calls[-1]["method"] == "sendMessage" and telegram.calls[-1]["chat_id"] == "987654"
    positions = _post(t, _message("555", "/positions")).json()
    assert "No open positions" in positions["reply"]
    risk = _post(t, _message("555", "/risk")).json()
    assert risk["reply"].startswith("Risk:")
    brief = _post(t, _message("555", "/brief")).json()
    assert brief["handled"] == "message" and len(brief["reply"]) > 20
    news = _post(t, _message("555", "/news")).json()
    assert "news feed" in news["reply"].lower() or "feed" in news["reply"].lower()
    why = _post(t, _message("555", "/why abc")).json()
    assert why["reply"].startswith("Usage")
    unknown = _post(t, _message("555", "/dance")).json()
    assert "Commands" in unknown["reply"]
    free = _post(t, _message("555", "आज माझ्या deployments काय करत आहेत?")).json()
    assert free["handled"] == "message" and free["reply"]
    usage = client.get("/api/billing/usage", headers=t["headers"]).json()
    assert usage["metrics"].get("telegram_inbound", 0) >= 6
    # Rate limit: the 21st message in a minute is refused with one short reply.
    tg._rate.clear()
    for _ in range(tg.RATE_LIMIT):
        assert tg.rate_limited(t["tenant_id"], "555") is False
    assert tg.rate_limited(t["tenant_id"], "555") is True
    assert _post(t, _message("555", "/help")).json()["handled"] == "rate_limited"
    tg._rate.clear()


def _propose(t, action="PAUSE_DEPLOYMENT", dep_id=None, now=None):
    async def go():
        async with _session_factory() as session:
            rows = await monitor.raise_proposals(session, t["tenant_id"], [monitor.Proposal(dep_id, None, action, f"TEST_{uuid.uuid4().hex[:8]}", "losing streak", {"k": 1})],
                                                 now or datetime.now(UTC))
            return rows[0].id
    return _run(go())


def _dispatch(t, telegram: _Telegram):
    async def go():
        async with _session_factory() as session, telegram.client() as c:
            return await dispatcher.dispatch_pending(session, client=c, tenant_id=t["tenant_id"])
    return _run(go())


def _action(action_id):
    async def go():
        async with _session_factory() as session:
            return await session.get(AiActionRecord, action_id)
    return _run(go())


def _callback(chat_id, data, msg_id=42):
    return {"update_id": 2, "callback_query": {"id": "cq1", "from": {"id": int(chat_id)}, "data": data,
                                              "message": {"message_id": msg_id, "chat": {"id": int(chat_id)}, "text": "AI proposes pause"}}}


def test_buttons_decide_paper_proposals_once_and_refuse_live_replay_tamper_expiry(monkeypatch):
    telegram = _Telegram()
    t = _setup("be-buttons@example.com", monkeypatch, telegram)
    dep_id = _deploy(t, symbol="NIFTY 50")
    action_id = _propose(t, dep_id=dep_id)
    sent, failed = _dispatch(t, telegram)
    assert (sent, failed) == (1, 0)
    msg = next(c for c in telegram.calls if c["method"] == "sendMessage" and "reply_markup" in c)
    buttons = msg["reply_markup"]["inline_keyboard"][0]
    assert [b["text"].split(" ")[-1] for b in buttons] == ["Approve", "Reject"] and all(len(b["callback_data"]) <= 64 for b in buttons)
    approve, reject = (b["callback_data"] for b in buttons)

    # A stranger's chat cannot press the button; the whitelisted chat can, once.
    assert _post(t, _callback("4242", approve)).json()["handled"] == "ignored"
    out = _post(t, _callback("987654", approve)).json()
    assert out["handled"] == "callback" and "EXECUTED" in out["reply"]
    row = _action(action_id)
    assert row.status == "EXECUTED" and "via Telegram chat 987654" in (row.decision_note or "")

    async def dep_status():
        async with _session_factory() as session:
            return (await session.get(StrategyDeploymentRecord, dep_id)).status
    assert _run(dep_status()) == "PAUSED"
    assert telegram.calls[-2]["method"] == "answerCallbackQuery" and telegram.calls[-1]["method"] == "editMessageText"
    assert "Already decided" in _post(t, _callback("987654", approve)).json()["reply"]                    # replay
    assert "Already decided" in _post(t, _callback("987654", reject)).json()["reply"]                    # the sibling button died with the claim
    assert "telegram_callback_replayed" in _audit_events(t["tenant_id"]) and "telegram_decision" in _audit_events(t["tenant_id"])

    # Tampered signature, expired row, foreign tenant, LIVE deployment.
    dep2 = _deploy(t, symbol="RELIANCE")
    action2 = _propose(t, dep_id=dep2)
    _dispatch(t, telegram)
    msg2 = [c for c in telegram.calls if c["method"] == "sendMessage" and "reply_markup" in c][-1]
    approve2, reject2 = (b["callback_data"] for b in msg2["reply_markup"]["inline_keyboard"][0])

    async def tamper():
        async with _session_factory() as session:
            row = await session.scalar(select(TelegramCallbackRecord).where(TelegramCallbackRecord.nonce == approve2[2:]))
            row.signature = "0" * 64
            await session.commit()
    _run(tamper())
    assert "failed verification" in _post(t, _callback("987654", approve2)).json()["reply"]

    async def expire():
        async with _session_factory() as session:
            row = await session.scalar(select(TelegramCallbackRecord).where(TelegramCallbackRecord.nonce == reject2[2:]))
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()
    _run(expire())
    assert "expired" in _post(t, _callback("987654", reject2)).json()["reply"]
    assert _action(action2).status == "PROPOSED"                                                          # nothing decided it

    other = _setup("be-other@example.com", monkeypatch, telegram)
    assert "not valid for this organisation" in _post(other, _callback("987654", approve2)).json()["reply"]

    live_dep = _deploy(t, symbol="NIFTY 50", mode="LIVE")
    live_action = _propose(t, dep_id=live_dep)
    before = len([c for c in telegram.calls if c["method"] == "sendMessage"])
    _dispatch(t, telegram)
    live_msgs = [c for c in telegram.calls if c["method"] == "sendMessage"][before:]
    assert live_msgs and all("reply_markup" not in c for c in live_msgs)                                  # no buttons for LIVE

    async def forged():
        async with _session_factory() as session:
            session.add(TelegramCallbackRecord(tenant_id=t["tenant_id"], action_id=live_action, nonce="forgedlive", decision="approve", chat_id="987654",
                                               signature=tg._signature(t["tenant_id"], live_action, "forgedlive", "approve"),
                                               expires_at=datetime.now(UTC) + timedelta(hours=1), created_at=datetime.now(UTC)))
            await session.commit()
    _run(forged())
    assert "LIVE" in _post(t, _callback("987654", "p:forgedlive")).json()["reply"] and _action(live_action).status == "PROPOSED"
    exit_action = _propose(t, action="EXIT_POSITION", dep_id=dep_id)
    _dispatch(t, telegram)
    assert "reply_markup" not in [c for c in telegram.calls if c["method"] == "sendMessage"][-1]        # exits: web only

    async def forged_exit():
        async with _session_factory() as session:
            session.add(TelegramCallbackRecord(tenant_id=t["tenant_id"], action_id=exit_action, nonce="forgedexit", decision="approve", chat_id="987654",
                                               signature=tg._signature(t["tenant_id"], exit_action, "forgedexit", "approve"),
                                               expires_at=datetime.now(UTC) + timedelta(hours=1), created_at=datetime.now(UTC)))
            await session.commit()
    _run(forged_exit())
    assert "web only" in _post(t, _callback("987654", "p:forgedexit")).json()["reply"] and _action(exit_action).status == "PROPOSED"   # press-time refusal


def test_alert_channel_put_cannot_touch_inbound_and_kill_flags_bind_telegram(monkeypatch):
    telegram = _Telegram()
    _flag(True)
    t = _tenant("be-bypass@example.com")
    monkeypatch.setattr(tg, "http_client", telegram.client)
    # The generic channel PUT drops the inbound keys: no whitelist, no secret, no inbound without the owner endpoint.
    sneaky = {**TELEGRAM, "inbound_enabled": True, "allowed_chat_ids": ["4242"], "inbound_secret": "attacker-chosen"}
    assert client.put("/api/alert-channels/telegram", headers=t["headers"], json={"config": sneaky}).status_code == 200
    status = client.get("/api/telegram/inbound/status", headers=t["headers"]).json()
    assert status["inbound_enabled"] is False and status["has_secret"] is False and status["allowed_chat_ids"] == ["987654"]

    async def token():
        async with _session_factory() as session:
            from app.db.models import Tenant
            return (await session.get(Tenant, t["tenant_id"])).webhook_token_hash
    tok = _run(token())
    assert client.post(f"/api/telegram/webhook/{tok}", headers={"X-Telegram-Bot-Api-Secret-Token": "attacker-chosen"}, json=_message("4242", "/help")).status_code == 403

    # Properly configured inbound survives a later channel re-save (chat id change) with its secret intact.
    client.put("/api/telegram/inbound", headers=t["headers"], json={"enabled": True, "allowed_chat_ids": ["555"]})
    before = client.get("/api/telegram/inbound/status", headers=t["headers"]).json()
    assert client.put("/api/alert-channels/telegram", headers=t["headers"], json={"config": {"bot_token": "", "chat_id": "987654", "inbound_enabled": False}}).status_code == 200
    after = client.get("/api/telegram/inbound/status", headers=t["headers"]).json()
    assert after["inbound_enabled"] is True and after["has_secret"] is True and after["allowed_chat_ids"] == before["allowed_chat_ids"]

    async def secret():
        async with _session_factory() as session:
            rec = await session.scalar(select(AlertChannelRecord).where(AlertChannelRecord.tenant_id == t["tenant_id"]))
            return decrypt_raw(rec)["inbound_secret"]
    sec = _run(secret())
    assert sec != "attacker-chosen"
    ok = client.post(f"/api/telegram/webhook/{tok}", headers={"X-Telegram-Bot-Api-Secret-Token": sec}, json=_message("987654", "/help")).json()
    assert ok["handled"] == "message"

    # The platform's telegram_inbound flag binds the webhook itself, not only the settings endpoints.
    _flag(False)
    assert client.post(f"/api/telegram/webhook/{tok}", headers={"X-Telegram-Bot-Api-Secret-Token": sec}, json=_message("987654", "/help")).status_code == 403
    _flag(True)

    # The operator's ai_copilot kill flag binds Telegram free text and /brief too.
    async def ai_flag(on):
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["ai_copilot"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(ai_flag(False))
    try:
        off = client.post(f"/api/telegram/webhook/{tok}", headers={"X-Telegram-Bot-Api-Secret-Token": sec}, json=_message("987654", "what is the market doing?")).json()
        assert "switched off" in off["reply"]
        assert "switched off" in client.post(f"/api/telegram/webhook/{tok}", headers={"X-Telegram-Bot-Api-Secret-Token": sec}, json=_message("987654", "/brief")).json()["reply"]
        assert "No open positions" in client.post(f"/api/telegram/webhook/{tok}", headers={"X-Telegram-Bot-Api-Secret-Token": sec}, json=_message("987654", "/positions")).json()["reply"]
    finally:
        _run(ai_flag(True))
    # Command replies are plain text (no parse_mode), so an ampersand in a symbol is never double-escaped.
    assert "parse_mode" not in telegram.calls[-1]


def test_register_webhook_and_flag_off(monkeypatch):
    telegram = _Telegram()
    t = _setup("be-register@example.com", monkeypatch, telegram)
    reg = client.post("/api/telegram/inbound/register", headers=t["headers"]).json()
    call = telegram.calls[-1]
    assert reg["ok"] is True and call["method"] == "setWebhook" and call["secret_token"] == t["secret"] and call["url"].endswith(f"/api/telegram/webhook/{t['token']}")
    assert "123456:ABC" in call["path"] and "123456:ABC" not in json.dumps(reg)
    status = client.get("/api/telegram/inbound/status", headers=t["headers"]).json()
    assert status["inbound_enabled"] is True and status["telegram_actions"] == list(tg.TELEGRAM_ACTIONS) and status["flag_enabled"] is True
    _flag(False)
    assert client.put("/api/telegram/inbound", headers=t["headers"], json={"enabled": True, "allowed_chat_ids": []}).status_code == 503
    assert client.post("/api/telegram/inbound/register", headers=t["headers"]).status_code == 503
    _flag(True)
    # Turning inbound off makes the webhook refuse (403) even with the right secret.
    client.put("/api/telegram/inbound", headers=t["headers"], json={"enabled": False, "allowed_chat_ids": []})
    assert _post(t, _message("987654", "/help")).status_code == 403
