"""S3b-2 (ADR-0022): per-user Telegram linking by one-time `/start <code>` (private chats only, single use, expiry,
no command rights) feeding the rule creator's own outbox row; email unsubscribe (signed link, page + POST, one mail
per recipient with List-Unsubscribe, screen alerts only, trader can re-subscribe)."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.alerts import dispatcher, engine, links
from app.core import config as app_config
from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import AlertDeliveryRecord, AlertRuleRecord, NotificationLinkRecord, NotificationPolicyRecord
from tests.test_auth_api import _session_factory, client
from tests.test_phase_be_telegram_inbound import _audit_events, _message, _no_shared_rate_limit, _post, _setup, _Telegram  # noqa: F401
from tests.test_trading_worker import _tenant

BAR = datetime(2026, 3, 10, 4, 0, tzinfo=timezone.utc)
EMAIL = {"smtp_host": "smtp.example.com", "smtp_port": 587, "username": "alerts", "password": "pw", "use_tls": True,
         "from_address": "alerts@example.com", "to_addresses": ["a@example.com", "b@example.com"]}


def _run(coro):
    return asyncio.run(coro)


def _rule(tenant_id, user_id, name="Breakout"):
    async def go():
        async with _session_factory() as session:
            if await session.scalar(select(NotificationPolicyRecord).where(NotificationPolicyRecord.tenant_id == tenant_id)) is None:
                session.add(NotificationPolicyRecord(tenant_id=tenant_id, timezone="Asia/Kolkata", max_per_hour=100, group_window_seconds=0,
                                                     eod_digest_time="15:45", updated_at=BAR))
            row = AlertRuleRecord(tenant_id=tenant_id, created_by=user_id, name=name, kind="instrument", symbol="X", condition_text="close > 1",
                                  base_tf="5m", priority="normal", cooldown_minutes=0, mode="instant", digest_every="hourly", status="active",
                                  created_at=BAR, updated_at=BAR)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _fire(tenant_id, rule_id, symbol, bar):
    async def go():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rule_id)
            await engine.record_event(session, rule, symbol, bar, {"close": 101.5, "as_of": bar.isoformat()}, now=bar)
            await session.commit()
            await engine.flush(session, bar + timedelta(seconds=1), tenant_id=tenant_id)
    _run(go())


def _dispatch(tenant_id, http=None):
    async def go():
        async with _session_factory() as session:
            return await dispatcher.dispatch_pending(session, client=http, tenant_id=tenant_id)
    return _run(go())


def _addresses(tenant_id):
    async def go():
        async with _session_factory() as session:
            return [d.address for d in await session.scalars(select(AlertDeliveryRecord).where(AlertDeliveryRecord.tenant_id == tenant_id)
                                                              .order_by(AlertDeliveryRecord.id))]
    return _run(go())


def test_telegram_link_by_one_time_code_feeds_the_creators_own_chat(monkeypatch):
    telegram = _Telegram()
    t = _setup("s3b2-link@example.com", monkeypatch, telegram)
    made = client.post("/api/alerts/telegram/link-code", headers=t["headers"]).json()
    code = made["code"]
    assert made["command"] == f"/start {code}" and links.START_CODE.fullmatch(code)

    group = {"update_id": 2, "message": {"message_id": 2, "chat": {"id": -100123}, "from": {"id": 777}, "text": f"/start {code}"}}
    assert _post(t, group).json()["result"] == "not_private"                       # a group would see one user's alerts
    assert _post(t, _message("777", "/start AAAAAAAAAAAAAAAAAAAA")).json()["result"] == "unknown_code"
    assert _post(t, _message("777", f"/start {code}")).json()["result"] == "linked"
    assert telegram.calls[-1]["chat_id"] == "777" and "Linked" in telegram.calls[-1]["text"]
    assert "telegram_linked" in _audit_events(t["tenant_id"])
    assert _post(t, _message("888", f"/start {code}")).json()["result"] == "unknown_code"   # works once
    assert client.get("/api/alerts/telegram/link", headers=t["headers"]).json()["linked"] is True
    assert _post(t, _message("777", "/brief")).json()["handled"] == "ignored"      # linking grants no command rights

    rid = _rule(t["tenant_id"], t["user_id"])
    _fire(t["tenant_id"], rid, "TCS", BAR)
    assert sorted(map(str, _addresses(t["tenant_id"]))) == ["777", "None"]
    telegram.calls.clear()
    assert _dispatch(t["tenant_id"], telegram.client()) == (2, 0)
    assert sorted(c["chat_id"] for c in telegram.calls if c["method"] == "sendMessage") == ["777", "987654"]

    assert client.delete("/api/alerts/telegram/link", headers=t["headers"]).json() == {"linked": False}
    _fire(t["tenant_id"], rid, "INFY", BAR + timedelta(minutes=5))
    assert _addresses(t["tenant_id"])[-1] is None and _addresses(t["tenant_id"]).count("777") == 1


def test_link_code_expires_and_needs_inbound(monkeypatch):
    t = _tenant("s3b2-noinbound@example.com")
    assert client.post("/api/alerts/telegram/link-code", headers=t["headers"]).status_code == 409

    async def go():
        async with _session_factory() as session:
            code, expires = await links.new_link_code(session, t["tenant_id"], t["user_id"], now=BAR)
            await session.commit()
            late = await links.consume_start(session, t["tenant_id"], code, "42", "42", now=expires + timedelta(seconds=1))
            again, _ = await links.new_link_code(session, t["tenant_id"], t["user_id"], now=BAR)
            newer, _ = await links.new_link_code(session, t["tenant_id"], t["user_id"], now=BAR)
            stale = await links.consume_start(session, t["tenant_id"], again, "42", "42", now=BAR)          # replaced by a newer code
            foreign = await links.consume_start(session, t["tenant_id"] + 999, newer, "42", "42", now=BAR)
            ok = await links.consume_start(session, t["tenant_id"], newer, "42", "42", now=BAR)
            stored = list(await session.scalars(select(NotificationLinkRecord.code_hash).where(NotificationLinkRecord.user_id == t["user_id"])))
            return late[1], stale[1], foreign[1], ok[1], stored, newer
    late, stale, foreign, ok, stored, newer = _run(go())
    assert (late, stale, foreign, ok) == ("expired", "unknown_code", "unknown_code", "linked")
    assert newer not in stored and all(h is None for h in stored)                  # only hashes, cleared once used


def test_email_unsubscribe_link_stops_screen_alerts_for_that_address_only(monkeypatch):
    t = _tenant("s3b2-mail@example.com")
    assert client.put("/api/alert-channels/email", headers=t["headers"], json={"config": EMAIL, "min_severity": "INFO"}).status_code == 200
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "https://atp.example.com")
    sent = []
    monkeypatch.setattr(dispatcher, "_smtp_send", lambda config, message: sent.append(message))
    rid = _rule(t["tenant_id"], t["user_id"])
    _fire(t["tenant_id"], rid, "TCS", BAR)
    assert _dispatch(t["tenant_id"]) == (1, 0)
    assert [m["To"] for m in sent] == ["a@example.com", "b@example.com"]           # one mail per recipient, own link each
    header = sent[0]["List-Unsubscribe"]
    assert header.startswith("<https://atp.example.com/api/alerts/unsubscribe?t=") and sent[0]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert header[1:-1] in sent[0].get_content() and header != sent[1]["List-Unsubscribe"]
    token = header[1:-1].split("t=", 1)[1]

    page = client.get(f"/api/alerts/unsubscribe?t={token}")
    assert page.status_code == 200 and "<form method='post'" in page.text and "a@example.com" in page.text
    assert client.get("/api/alerts/email-opt-outs", headers=t["headers"]).json() == []    # opening the link changes nothing
    assert client.post(f"/api/alerts/unsubscribe?t={token[:-1]}0").status_code == 400
    assert "not valid" in client.get("/api/alerts/unsubscribe?t=x.y").text
    assert client.post(f"/api/alerts/unsubscribe?t={token}").status_code == 200
    outs = client.get("/api/alerts/email-opt-outs", headers=t["headers"]).json()
    assert [o["address"] for o in outs] == ["a@example.com"] and "email_unsubscribed" in _audit_events(t["tenant_id"])

    sent.clear()
    _fire(t["tenant_id"], rid, "INFY", BAR + timedelta(minutes=5))
    _dispatch(t["tenant_id"])
    assert [m["To"] for m in sent] == ["b@example.com"]

    sent.clear()                                                                    # risk/system mail still reaches everyone
    async def risk():
        async with _session_factory() as session:
            from app.notifications.service import notify
            await notify(session, t["tenant_id"], NotificationType.SYSTEM_FAILURE, title="Daily loss limit", severity=NotificationSeverity.CRITICAL)
    _run(risk())
    _dispatch(t["tenant_id"])
    assert len(sent) == 1 and sent[0]["To"] == "a@example.com, b@example.com" and sent[0]["List-Unsubscribe"] is None

    other = _tenant("s3b2-mail-other@example.com")
    assert client.delete(f"/api/alerts/email-opt-outs/{outs[0]['id']}", headers=other["headers"]).status_code == 404
    assert client.delete(f"/api/alerts/email-opt-outs/{outs[0]['id']}", headers=t["headers"]).status_code == 200
    sent.clear()
    _fire(t["tenant_id"], rid, "WIPRO", BAR + timedelta(minutes=10))
    _dispatch(t["tenant_id"])
    assert [m["To"] for m in sent] == ["a@example.com", "b@example.com"]


def test_no_public_url_means_no_link_and_tokens_are_tenant_bound(monkeypatch):
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "")
    assert links.unsubscribe_url(1, "a@example.com") is None
    tok = links.unsubscribe_token(7, "A@Example.com")
    assert links.read_unsubscribe_token(tok) == (7, "a@example.com")
    mac = tok.split(".")[1]
    forged = links.unsubscribe_token(8, "a@example.com").split(".")[0] + "." + mac
    assert links.read_unsubscribe_token(forged) is None
