"""M89: the stored shared daily report (M88-R) on Telegram (app/daily_report/telegram.py).

One send per user per report (never twice), ON by default for a user with Telegram connected, his opt-out (Settings >
Alerts and the signed Stop link) respected, nothing while the flag is off, the message inside Telegram's 4096-character
limit with the information line and a link into ATP, the PDF attached, paced (and a 429's retry_after obeyed), a chat
that blocked the bot marked and never retried, and the admin's sent / failed counts. A mock Telegram only.
"""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import delete, select

from app.alerts.channels import TelegramConfig, encrypt_config
from app.core import config as app_config
from app.daily_report import telegram
from app.db.models import AlertChannelRecord, DailyReportSendRecord, NotificationLinkRecord, SharedDailyReportRecord, UserPrefsRecord
from app.secrets_store.envelope import ensure_tenant_key
from tests.test_admin_api import _admin
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _me
from tests.test_settings_a_connect_ai import _flags

REPORT = {"trade_date": "2026-11-06", "info_line": "Last close 6 Nov 2026 · AI-generated · information only, not advice",
          "index": [{"symbol": "NIFTY 50", "kind": "INDEX", "missing": False, "close": 24180.4, "change_pct": 0.62}],
          "stocks": [], "breadth": {"advances": 30, "declines": 20}, "sectors": [], "leaders": [], "laggards": [], "missing": [],
          "sections": {"index_read": "The index closed higher.", "breadth_read": "Thirty stocks rose.", "sectors_read": "Banks led.",
                       "leaders_laggards": "-", "notable_structures": "-", "conclusion": "A firm close."}}


def _run(coro):
    return asyncio.run(coro)


class Telegram:
    """A mock Telegram: records each call; `refuse` maps a chat id to (status, description[, retry_after])."""

    def __init__(self, refuse=None):
        self.calls: list[tuple[str, dict]] = []
        self.refuse = dict(refuse or {})

    def client(self):
        def handler(request: httpx.Request):
            method = request.url.path.rsplit("/", 1)[-1]
            if method == "sendMessage":
                body = json.loads(request.content)
            else:
                text = request.content.decode("latin-1")
                body = {"chat_id": text.split('name="chat_id"\r\n\r\n', 1)[1].split("\r\n", 1)[0], "document": "application/pdf" in text}
            self.calls.append((method, body))
            refused = self.refuse.get(str(body["chat_id"]))
            if refused:
                if refused[0] == 429 and len(refused) > 2:
                    self.refuse.pop(str(body["chat_id"]))               # the flood limit passes after retry_after
                    return httpx.Response(429, json={"ok": False, "description": refused[1], "parameters": {"retry_after": refused[2]}})
                return httpx.Response(refused[0], json={"ok": False, "description": refused[1]})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


CONNECTED: list[int] = []


def _who(email, chat=None):
    headers = _auth(email)
    me = _me(headers)
    who = {"headers": headers, "user_id": me["id"], "tenant_id": me["tenant_id"]}
    if chat:
        CONNECTED.append(who["tenant_id"])
        async def go():
            async with _session_factory() as session:
                await ensure_tenant_key(session, who["tenant_id"])
                session.add(AlertChannelRecord(tenant_id=who["tenant_id"], channel_type="TELEGRAM", created_by=who["user_id"],
                                               encrypted_config=encrypt_config(TelegramConfig(chat_id=chat, platform_bot=True), who["tenant_id"]),
                                               enabled=True, min_severity="WARNING"))
                session.add(NotificationLinkRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], channel="tg_platform", status="used",
                                                   chat_id=chat, created_at=datetime.now(timezone.utc)))
                await session.commit()
        _run(go())
    return who


def _report(day="2026-11-06") -> int:
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(DailyReportSendRecord))
            await session.execute(delete(SharedDailyReportRecord))
            row = SharedDailyReportRecord(trade_date=datetime.fromisoformat(day).date(), status="ok", attempts=1, content_json=json.dumps(REPORT))
            session.add(row)
            await session.commit()
            return int(row.id)
    return _run(go())


def _deliver(report_id, tg, sleeps=None):
    async def sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    async def go():
        async with _session_factory() as session:
            async with tg.client() as c:
                return await telegram.deliver(session, report_id, client=c, sleep=sleep, pdf=b"%PDF-1.4 test")
    return _run(go())


def _rows(report_id):
    async def go():
        async with _session_factory() as session:
            return {(r.user_id): (r.status, r.attempts) for r in await session.scalars(
                select(DailyReportSendRecord).where(DailyReportSendRecord.report_id == report_id))}
    return _run(go())


@pytest.fixture(autouse=True)
def bot(monkeypatch):
    monkeypatch.setattr(app_config, "ATP_TELEGRAM_BOT_TOKEN", "424242:PLATFORM-token-value", raising=False)
    monkeypatch.setattr(app_config, "PUBLIC_BASE_URL", "https://app.example.test", raising=False)

    async def others(enabled: bool, ids: list[int] | None = None) -> list[int]:
        # The shared test database keeps other tests' Telegram channels; the report goes to every connected user, so
        # those are switched off while this test runs (only its own chats count) and switched back on after.
        async with _session_factory() as session:
            if ids is None:
                ids = list((await session.scalars(select(AlertChannelRecord.id).where(
                    AlertChannelRecord.channel_type == "TELEGRAM", AlertChannelRecord.enabled.is_(True)))).all())
            for row in await session.scalars(select(AlertChannelRecord).where(AlertChannelRecord.id.in_(ids))):
                row.enabled = enabled
            await session.commit()
            return ids
    parked = _run(others(False))
    yield
    _run(others(True, parked))
    _flags(ui_v2=False, shared_daily_report=False)

    async def disconnect():                                              # each test's chats are its own
        async with _session_factory() as session:
            await session.execute(delete(AlertChannelRecord).where(AlertChannelRecord.tenant_id.in_(CONNECTED)))
            await session.execute(delete(NotificationLinkRecord).where(NotificationLinkRecord.tenant_id.in_(CONNECTED)))
            await session.commit()
    _run(disconnect())
    CONNECTED.clear()


def _mine(tg, chat, method="sendMessage"):
    return [b for m, b in tg.calls if m == method and str(b["chat_id"]) == chat]


def test_sent_once_per_user_per_report_with_the_pdf_and_nothing_while_the_flag_is_off():
    a, b = _who("m89-a@example.com", chat="889001"), _who("m89-b@example.com", chat="889002")
    _who("m89-none@example.com")                                         # no Telegram: never queued
    report_id = _report()
    tg = Telegram()
    assert _deliver(report_id, tg)["sent"] == 0 and _mine(tg, "889001") == []      # flag OFF: nothing queued or sent
    _flags(ui_v2=True, shared_daily_report=True)
    _deliver(report_id, tg)
    assert len(_mine(tg, "889001")) == 1 and len(_mine(tg, "889002")) == 1
    assert len(_mine(tg, "889001", "sendDocument")) == 1 and _mine(tg, "889001", "sendDocument")[0]["document"] is True
    _deliver(report_id, tg)                                              # a second pass: never twice
    assert len(_mine(tg, "889001")) == 1 and len(_mine(tg, "889002")) == 1
    rows = _rows(report_id)
    assert rows[a["user_id"]] == ("sent", 1) and rows[b["user_id"]] == ("sent", 1) and len(rows) == 2


def test_the_message_fits_telegram_and_carries_the_line_the_link_and_stop():
    msg = telegram.message_html(REPORT)
    assert len(msg) <= 4096 and "Last close 6 Nov 2026 · AI-generated · information only, not advice" in msg and "NIFTY 50" in msg
    long = dict(REPORT, sections={**REPORT["sections"], "index_read": "word " * 3000})
    assert len(telegram.message_html(long)) <= 4096
    keys = telegram.buttons("https://app.example.test", 3, 9)["inline_keyboard"]
    assert keys[0][0]["url"] == "https://app.example.test/copilot/read?report=open"
    assert telegram.read_stop_token(keys[1][0]["url"].split("t=", 1)[1]) == (3, 9)
    assert telegram.read_stop_token("bad.token") is None


def test_opt_out_in_settings_and_the_stop_link_are_respected():
    _flags(ui_v2=True, shared_daily_report=True)
    a, b = _who("m89-out-a@example.com", chat="889101"), _who("m89-out-b@example.com", chat="889102")
    got = client.get("/api/daily-report/telegram", headers=a["headers"]).json()
    assert got == {"telegram": True, "connected": True}                  # ON by default with Telegram connected
    assert client.put("/api/daily-report/telegram", headers=a["headers"], json={"telegram": False}).json()["telegram"] is False
    token = telegram.stop_token(b["tenant_id"], b["user_id"])
    page = client.get(f"/api/daily-report/telegram/stop?t={token}")
    assert page.status_code == 200 and "<form method='post'" in page.text
    assert client.post(f"/api/daily-report/telegram/stop?t={token}").status_code == 200
    tg = Telegram()
    _deliver(_report(), tg)
    assert _mine(tg, "889101") == [] and _mine(tg, "889102") == []


def test_paced_a_429_waits_and_a_blocked_chat_is_skipped_for_good():
    _flags(ui_v2=True, shared_daily_report=True)
    users = [_who(f"m89-pace-{i}@example.com", chat=f"88920{i}") for i in range(3)]
    report_id = _report()
    tg = Telegram(refuse={"889201": (403, "Forbidden: bot was blocked by the user"), "889202": (429, "Too Many Requests", 3)})
    sleeps: list[float] = []
    result = _deliver(report_id, tg, sleeps)
    pace = app_config.DAILY_REPORT_TELEGRAM_PACE_MS / 1000.0
    assert sleeps.count(pace) == 2 and 3.0 in sleeps                    # paced between users; the 429's retry_after obeyed
    assert result == {"sent": 2, "failed": 0, "blocked": 1, "skipped": 0}
    rows = _rows(report_id)
    assert rows[users[1]["user_id"]] == ("blocked", 1)
    _deliver(report_id, tg)                                              # a blocked chat is never tried again
    assert len(_mine(tg, "889201")) == 1


def test_a_failed_send_is_retried_and_the_admin_sees_the_counts():
    _flags(ui_v2=True, shared_daily_report=True)
    who = _who("m89-fail@example.com", chat="889301")
    report_id = _report()
    tg = Telegram(refuse={"889301": (500, "Internal Server Error")})
    assert _deliver(report_id, tg)["failed"] == 1
    tg.refuse.clear()
    assert _deliver(report_id, tg)["sent"] == 1
    assert _rows(report_id)[who["user_id"]] == ("sent", 2)
    headers, _ = _admin("m89-admin@example.com")
    runs = client.get("/api/admin/daily-report", headers=headers).json()["runs"]
    assert runs[0]["telegram"]["sent"] == 1 and runs[0]["telegram"]["failed"] == 0


def test_prefs_namespace_default_on():
    from app.me.prefs import NAMESPACES
    assert NAMESPACES["daily_report"]().model_dump() == {"telegram": True}

    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.namespace == "daily_report").limit(1))
    _run(go())
