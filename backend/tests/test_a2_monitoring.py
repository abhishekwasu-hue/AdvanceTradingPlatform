"""Production readiness A2: monitoring and alerts to the platform operator.

- the dedup: a WARN / FAIL is sent once it has held for two runs, never twice, a FAIL is repeated after the repeat
  hours, WARN <-> FAIL is sent, a recovery is sent once, UNKNOWN neither sends nor clears; undelivered = retried;
- the checks: error rate from the API's own counters (a restart = a new baseline), the worker heartbeat (FAIL only
  in market hours), the data feed (MarketDataStale), broker logins as counts only, backups / disk / certificate from
  the host facts (stale facts = unknown), the alert outbox, failed sign-ins;
- the message: counts and states only - no e-mail address, organisation name, chat id or token;
- the operator chat: a one-time code through the platform bot, private chats only, kept apart from organisations;
- the admin routes: SUPER_ADMIN with two-factor (tests/test_admin_console.py sweeps every /api/admin route), answers
  and never returns a chat id or an address;
- the incident public note on the status page; `atp.sh ops-check` hands the host facts to the backend.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.alerts import platform_bot
from app.core import config
from app.db.models import (
    AlertDeliveryRecord,
    Base,
    BrokerCredentialRecord,
    LoginEventRecord,
    NotificationLinkRecord,
    Tenant,
    User,
    WorkerHeartbeatRecord,
)
from app.notifications import mailer
from app.observability import ops_alerts
from app.observability.ops_alerts import FAIL, OK, UNKNOWN, WARN, Check
from app.telegram_inbound import service as tg

NOW = datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc)          # Wednesday 10:30 IST: NSE open
NIGHT = datetime(2026, 10, 21, 17, 0, tzinfo=timezone.utc)       # 22:30 IST: closed
TOKEN = "424242:PLATFORM-token-value"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    _run(setup())
    return async_sessionmaker(engine, expire_on_commit=False)


class _Bot:
    def __init__(self, ok=True):
        self.sent: list = []
        self.ok = ok

    def client(self):
        def handler(request: httpx.Request):
            body = json.loads(request.content or b"{}")
            method = request.url.path.rsplit("/", 1)[-1]
            if method == "getMe":
                return httpx.Response(200, json={"ok": True, "result": {"username": "AiAlgoDeskBot"}})
            self.sent.append({"method": method, **body})
            return httpx.Response(200 if self.ok else 400, json={"ok": self.ok, "result": {}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def bot(monkeypatch):
    async def never(*_a, **_k):
        return False
    monkeypatch.setattr(tg, "rate_limited", never)
    monkeypatch.setattr(config, "ATP_TELEGRAM_BOT_TOKEN", TOKEN, raising=False)
    monkeypatch.setattr(config, "ATP_TELEGRAM_WEBHOOK_SECRET", "", raising=False)
    monkeypatch.setattr(config, "ATP_TELEGRAM_BOT_USERNAME", "", raising=False)
    monkeypatch.setattr(platform_bot, "_username_cache", {})
    b = _Bot()
    monkeypatch.setattr(tg, "http_client", b.client)
    return b


def _c(key, state, detail="x"):
    return Check(key=key, label=ops_alerts.LABELS[key], state=state, detail=detail)


# --- dedup ------------------------------------------------------------------------------------------------------------

def _step(memory, state, now, key="disk", after=2, repeat=6.0):
    return ops_alerts.decide(memory, [_c(key, state)], now, after=after, repeat_hours=repeat)


def test_a_problem_is_sent_once_after_two_runs_and_never_twice():
    m, lines = _step({}, WARN, NOW)
    assert lines == [] and m["disk"]["streak"] == 1                  # one bad reading pages nobody
    m, lines = _step(m, WARN, NOW + timedelta(minutes=5))
    assert [line["kind"] for line in lines] == ["problem"] and m["disk"]["alerted"] == WARN
    for n in range(2, 50):                                           # a WARN that stays is never repeated
        m, lines = _step(m, WARN, NOW + timedelta(minutes=5 * n))
        assert lines == []


def test_a_fail_is_escalated_reminded_and_recovers_once():
    m, _ = _step({}, WARN, NOW)
    m, _ = _step(m, WARN, NOW + timedelta(minutes=5))
    m, lines = _step(m, FAIL, NOW + timedelta(minutes=10))
    assert lines == []                                               # the FAIL must hold too
    m, lines = _step(m, FAIL, NOW + timedelta(minutes=15))
    assert [line["kind"] for line in lines] == ["worse"]
    m, lines = _step(m, FAIL, NOW + timedelta(hours=3))
    assert lines == []
    m, lines = _step(m, FAIL, NOW + timedelta(hours=6, minutes=20))
    assert [line["kind"] for line in lines] == ["reminder"]
    m, lines = _step(m, UNKNOWN, NOW + timedelta(hours=7))           # a fact that could not be read: no news
    assert lines == [] and m["disk"]["alerted"] == FAIL
    m, lines = _step(m, OK, NOW + timedelta(hours=7, minutes=5))
    assert lines == []
    m, lines = _step(m, OK, NOW + timedelta(hours=7, minutes=10))
    assert [line["kind"] for line in lines] == ["recovered"] and m["disk"]["alerted"] is None
    m, lines = _step(m, OK, NOW + timedelta(hours=8))
    assert lines == []


def test_ok_and_unknown_never_send_and_after_is_a_setting():
    m = {}
    for n, state in enumerate([OK, UNKNOWN, OK, UNKNOWN]):
        m, lines = _step(m, state, NOW + timedelta(minutes=n))
        assert lines == []
    m, lines = _step({}, FAIL, NOW, after=1)
    assert [line["kind"] for line in lines] == ["problem"]


def test_the_message_holds_states_and_counts_only():
    lines = [{"kind": "problem", "key": "backup", "label": "Backups", "state": FAIL, "detail": "Local backup 30 h old."},
             {"kind": "recovered", "key": "disk", "label": "Disk space", "state": OK, "detail": "70% used."}]
    subject, body = ops_alerts.compose(lines)
    assert subject == "AiAlgoDesk server check: 1 problem, 1 back to normal"
    assert body.index("PROBLEM - Backups (FAIL)") < body.index("OK AGAIN - Disk space (OK)")
    assert "admin console" in body


# --- the checks -------------------------------------------------------------------------------------------------------

def _metrics(total_2xx, total_5xx):
    return ("# HELP atp_http_requests_total x\n# TYPE atp_http_requests_total counter\n"
            f'atp_http_requests_total{{method="GET",route="/api/x",status="2xx"}} {total_2xx}\n'
            f'atp_http_requests_total{{method="GET",route="/api/x",status="5xx"}} {total_5xx}\n')


def test_error_rate_compares_against_the_previous_sample_and_a_restart_is_a_new_baseline(monkeypatch):
    monkeypatch.setattr(config, "OPS_ERROR_RATE_MIN_REQUESTS", 50)
    check, sample = ops_alerts.error_rate(_metrics(100, 0), {}, NOW)
    assert check.state == UNKNOWN and sample["total"] == 100.0
    check, sample = ops_alerts.error_rate(_metrics(1000, 2), sample, NOW)
    assert check.state == OK and "0.2% of 902 requests" in check.detail
    check, sample = ops_alerts.error_rate(_metrics(1900, 20), sample, NOW)
    assert check.state == FAIL and "2.0% of 918 requests" in check.detail
    check, sample = ops_alerts.error_rate(_metrics(10, 0), sample, NOW)       # the API restarted
    assert check.state == UNKNOWN and sample["total"] == 10.0
    check, _ = ops_alerts.error_rate(_metrics(20, 5), sample, NOW)            # too few requests to judge
    assert check.state == OK
    assert ops_alerts.error_rate(None, sample, NOW)[0].state == UNKNOWN
    assert ops_alerts.error_rate("not prometheus {{{", sample, NOW)[0].state == UNKNOWN


def _seed(factory, *rows):
    async def go():
        async with factory() as session:
            for row in rows:
                session.add(row)
            await session.commit()
    _run(go())


def _eval(factory, now=NOW, state=None, api=None, worker=None):
    async def go():
        async with factory() as session:
            checks, samples = await ops_alerts.evaluate(session, state or {}, now=now, api_metrics=api, worker_metrics=worker)
            return {c.key: c for c in checks}, samples
    return _run(go())


def test_worker_heartbeat_fails_only_in_market_hours(factory):
    checks, _ = _eval(factory)
    assert checks["worker"].state == WARN and "never" in checks["worker"].detail
    _seed(factory, WorkerHeartbeatRecord(worker_name="trading_worker", last_seen_at=NOW - timedelta(minutes=10)))
    assert _eval(factory)[0]["worker"].state == FAIL
    assert _eval(factory, now=NIGHT)[0]["worker"].state == WARN
    assert _eval(factory, now=NOW - timedelta(minutes=9))[0]["worker"].state == OK


def test_data_feed_counts_refusals_on_old_data_since_the_last_check(factory):
    def worker(n):
        return f'# TYPE atp_market_data_stale_total counter\natp_market_data_stale_total{{kind="candles"}} {n}\n'
    checks, samples = _eval(factory, worker=worker(10))
    assert checks["data_feed"].state == UNKNOWN                   # first sample, no end-of-day data yet
    checks, samples = _eval(factory, state={"samples": samples}, worker=worker(12))
    assert checks["data_feed"].state == OK
    checks, _ = _eval(factory, state={"samples": samples}, worker=worker(30))
    assert checks["data_feed"].state == FAIL and "18 decisions refused" in checks["data_feed"].detail


def test_broker_logins_are_counts_only_and_long_lived_expiry_warns(factory):
    _seed(factory, Tenant(id=1, name="trader.one@example.com", plan="free"),
          User(id=1, tenant_id=1, email="trader.one@example.com", hashed_password="x", role="OWNER"))

    def cred(i, status, expires):
        return BrokerCredentialRecord(tenant_id=1, user_id=1, broker_name="upstox", account_label=f"a{i}", encrypted_payload="x",
                                      token_status=status, token_expires_at=expires)
    _seed(factory, cred(1, "VALID", NOW + timedelta(hours=17)),       # a daily login: never "expiring soon"
          cred(2, "EXPIRED", NOW - timedelta(hours=3)), cred(3, "VALID", NOW + timedelta(days=200)))
    check = _eval(factory)[0]["broker_tokens"]
    assert check.state == OK and check.detail == "3 broker connections: 2 working, 1 need a new login."
    _seed(factory, cred(4, "VALID", NOW + timedelta(days=5)))
    check = _eval(factory)[0]["broker_tokens"]
    assert check.state == WARN and "1 long-lived token expires within 7 days" in check.detail
    assert "example.com" not in check.detail and "upstox" not in check.detail


def test_outbox_and_sign_in_failures(factory, monkeypatch):
    monkeypatch.setattr(config, "OPS_OUTBOX_BACKLOG", 2)
    monkeypatch.setattr(config, "OPS_LOGIN_FAILURES_15M", 3)
    checks = _eval(factory)[0]
    assert checks["alert_outbox"].state == OK and checks["sign_in_failures"].state == OK
    _seed(factory, *[AlertDeliveryRecord(tenant_id=1, notification_id=1, channel_id=1, status="PENDING", next_attempt_at=NOW) for _ in range(3)],
          *[LoginEventRecord(email="x@example.com", success=False, reason="bad_password", created_at=NOW - timedelta(minutes=2)) for _ in range(4)])
    checks = _eval(factory)[0]
    assert checks["alert_outbox"].state == WARN and checks["sign_in_failures"].state == WARN
    assert "x@example.com" not in checks["sign_in_failures"].detail
    assert checks["db_size"].state == UNKNOWN                     # SQLite in tests: measured on Postgres only


BACKUP_OK = {"status": "ok", "problems": "", "local": {"age_hours": 3}, "offsite": {"configured": True, "age_hours": 3}}


def test_server_checks_read_the_host_facts_and_stale_facts_are_unknown(monkeypatch):
    facts = {"backup": BACKUP_OK, "disk_used_percent": 40, "cert_days_left": 60}
    got = {c.key: c for c in ops_alerts.server_checks({"facts": facts, "at": NOW.isoformat()}, NOW)}
    assert {k: c.state for k, c in got.items()} == {"backup": OK, "disk": OK, "certificate": OK}
    assert got["backup"].detail == "Last backup 3 h ago, off-site copy 3 h ago."
    facts = {"backup": {"status": "fail", "problems": "local backup 30 h old"}, "disk_used_percent": 96, "cert_days_left": 10}
    got = {c.key: c for c in ops_alerts.server_checks({"facts": facts, "at": NOW.isoformat()}, NOW)}
    assert {k: c.state for k, c in got.items()} == {"backup": FAIL, "disk": FAIL, "certificate": WARN}
    assert got["backup"].detail == "Local backup 30 h old."
    got = {c.key: c for c in ops_alerts.server_checks({"facts": {"cert_note": "ip_only", "disk_used_percent": 86}, "at": NOW.isoformat()}, NOW)}
    assert got["disk"].state == WARN and got["certificate"].state == UNKNOWN and got["backup"].state == UNKNOWN
    old = {"facts": {"disk_used_percent": 99}, "at": (NOW - timedelta(minutes=45)).isoformat()}
    assert {c.state for c in ops_alerts.server_checks(old, NOW)} == {UNKNOWN}
    assert {c.state for c in ops_alerts.server_checks(None, NOW)} == {UNKNOWN}


# --- one run: send, dedup, deliver ------------------------------------------------------------------------------------

def _go(factory, now, facts, **kw):
    async def go():
        async with factory() as session:
            return await ops_alerts.run(session, host_facts=facts, now=now, **kw)
    return _run(go())


def test_a_run_sends_one_message_to_the_operator_chat_and_e_mail_then_stays_quiet(factory, bot, monkeypatch):
    mails: list = []

    async def send(to, subject, body):
        mails.append((to, subject, body))
        return True
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", send)
    monkeypatch.setattr(config, "OPS_ALERT_TELEGRAM_CHAT_ID", "777000", raising=False)
    monkeypatch.setattr(config, "OPS_ALERT_EMAIL", "", raising=False)
    monkeypatch.setattr(config, "SUPER_ADMIN_EMAILS", {"owner@example.com"}, raising=False)
    _seed(factory, WorkerHeartbeatRecord(worker_name="trading_worker", last_seen_at=NOW + timedelta(hours=1)))   # fresh all along
    bad = {"backup": {"status": "fail", "problems": "local backup 30 h old"}, "disk_used_percent": 40, "cert_days_left": 60}
    first = _go(factory, NOW, bad)
    assert first["sent"] == [] and bot.sent == [] and mails == []
    second = _go(factory, NOW + timedelta(minutes=5), bad)
    assert [line["key"] for line in second["sent"]] == ["backup"] and second["delivery"] == {"telegram": True, "email": True}
    assert len(bot.sent) == 1 and bot.sent[0]["chat_id"] == "777000" and "PROBLEM - Backups (FAIL)" in bot.sent[0]["text"]
    assert mails[0][0] == "owner@example.com" and mails[0][2] == bot.sent[0]["text"]
    assert "owner@example.com" not in bot.sent[0]["text"] and TOKEN not in bot.sent[0]["text"]
    _go(factory, NOW + timedelta(minutes=10), bad)
    assert len(bot.sent) == 1                                          # dedup across runs (the row in platform_controls)
    good = {**bad, "backup": BACKUP_OK}
    _go(factory, NOW + timedelta(minutes=15), good)
    _go(factory, NOW + timedelta(minutes=20), good)
    assert len(bot.sent) == 2 and "OK AGAIN - Backups (OK)" in bot.sent[1]["text"]


def test_a_message_nobody_received_is_tried_again(factory, monkeypatch):
    monkeypatch.setattr(config, "OPS_ALERT_TELEGRAM_CHAT_ID", "", raising=False)
    monkeypatch.setattr(mailer, "configured", lambda: False)
    bad = {"disk_used_percent": 97}
    _go(factory, NOW, bad)
    out = _go(factory, NOW + timedelta(minutes=5), bad)
    assert "disk" in [line["key"] for line in out["sent"]] and out["delivery"] == {"telegram": None, "email": None}
    out = _go(factory, NOW + timedelta(minutes=10), bad)
    assert "disk" in [line["key"] for line in out["sent"]]             # still undelivered: tried again, not forgotten


def test_without_new_host_facts_the_stored_ones_are_used_while_fresh(factory, monkeypatch):
    monkeypatch.setattr(mailer, "configured", lambda: False)
    _go(factory, NOW, {"disk_used_percent": 50})

    async def admin_run(now):
        async with factory() as session:
            await ops_alerts.run(session, source="admin", now=now)
            return await ops_alerts.view(session, now=now)
    view = _run(admin_run(NOW + timedelta(minutes=10)))
    disk = next(c for c in view["checks"] if c["key"] == "disk")
    assert disk["state"] == OK and view["source"] == "admin" and view["schedule"]["late"] is False
    view = _run(admin_run(NOW + timedelta(minutes=50)))
    disk = next(c for c in view["checks"] if c["key"] == "disk")
    assert disk["state"] == UNKNOWN and view["schedule"]["late"] is True


# --- the operator chat ------------------------------------------------------------------------------------------------

def test_the_operator_chat_is_linked_by_a_one_time_code_in_a_private_chat(factory, bot):
    _seed(factory, Tenant(id=1, name="ops", plan="free"), User(id=1, tenant_id=1, email="ops@example.com", hashed_password="x", role="SUPER_ADMIN"))

    async def go():
        async with factory() as session:
            code, _ = await ops_alerts.new_link_code(session, 1, 1, now=NOW)
            await session.commit()
            assert await ops_alerts.link_pending(session, now=NOW)
            group = {"update_id": 1, "message": {"chat": {"id": -5}, "from": {"id": 9}, "text": f"/start {code}"}}
            assert (await platform_bot.handle_update(session, group, now=NOW))["result"] == "not_private"
            private = {"update_id": 2, "message": {"chat": {"id": 9}, "from": {"id": 9}, "text": f"/start {code}"}}
            assert (await platform_bot.handle_update(session, private, now=NOW))["result"] == "linked"
            assert (await platform_bot.handle_update(session, private, now=NOW))["result"] == "unknown_code"   # once only
            chat = await ops_alerts.telegram_chat(session)
            channels = await session.scalar(select(NotificationLinkRecord).where(NotificationLinkRecord.channel == "tg_platform"))
            view = await ops_alerts.view(session, now=NOW)
            await ops_alerts.unlink(session, 1)
            after = await ops_alerts.telegram_chat(session)
            return chat, channels, view, after
    chat, channels, view, after = _run(go())
    assert chat == ("9", "linked") and channels is None            # no organisation channel was touched
    assert view["channels"]["telegram"] == {"available": True, "connected": True, "from": "linked", "pending": False}
    assert "9" not in json.dumps(view["channels"]).replace("available", "")
    assert after == (None, "none")
    assert any(m.get("text") == ops_alerts.OPS_WELCOME for m in bot.sent)


def test_an_expired_operator_code_does_nothing(factory, bot):
    _seed(factory, Tenant(id=1, name="ops", plan="free"), User(id=1, tenant_id=1, email="ops@example.com", hashed_password="x", role="SUPER_ADMIN"))

    async def go():
        async with factory() as session:
            code, _ = await ops_alerts.new_link_code(session, 1, 1, now=NOW)
            await session.commit()
            update = {"update_id": 3, "message": {"chat": {"id": 9}, "from": {"id": 9}, "text": f"/start {code}"}}
            out = await platform_bot.handle_update(session, update, now=NOW + timedelta(hours=1))
            return out, await ops_alerts.telegram_chat(session)
    out, chat = _run(go())
    assert out["result"] == "expired" and chat == (None, "none")


# --- the admin routes -------------------------------------------------------------------------------------------------

def test_admin_routes_answer_a_super_admin_and_never_return_a_chat_id_or_address(bot, monkeypatch):
    from tests.test_admin_api import _admin
    from tests.test_auth_api import client
    monkeypatch.setattr(config, "OPS_ALERT_TELEGRAM_CHAT_ID", "123456789", raising=False)
    monkeypatch.setattr(mailer, "configured", lambda: False)
    headers, _ = _admin("a2-ops-admin@example.com")
    body = client.get("/api/admin/ops-alerts", headers=headers).json()
    assert body["channels"]["telegram"]["connected"] is True and body["channels"]["telegram"]["from"] == "env"
    assert "123456789" not in json.dumps(body)
    run = client.post("/api/admin/ops-alerts/run", headers=headers)
    assert run.status_code == 200 and [c["key"] for c in run.json()["checks"]] == [k for k, _, _ in ops_alerts.CHECKS]
    test = client.post("/api/admin/ops-alerts/test", headers=headers)
    assert test.status_code == 200 and test.json() == {"telegram": True, "email": None}
    link = client.post("/api/admin/ops-alerts/telegram", headers=headers)
    assert link.status_code == 200 and link.json()["url"].startswith("https://t.me/AiAlgoDeskBot?start=ops")
    assert client.delete("/api/admin/ops-alerts/telegram", headers=headers).status_code == 200
    monkeypatch.setattr(config, "OPS_ALERT_TELEGRAM_CHAT_ID", "", raising=False)
    assert client.post("/api/admin/ops-alerts/test", headers=headers).status_code == 409


def test_admin_routes_refuse_an_owner():
    from tests.test_auth_api import _register, client
    owner = {"Authorization": f"Bearer {_register('a2-ops-owner@example.com')}"}
    for method, path in (("GET", ""), ("POST", "/run"), ("POST", "/test"), ("POST", "/telegram"), ("DELETE", "/telegram")):
        assert client.request(method, f"/api/admin/ops-alerts{path}", headers=owner).status_code == 403
        assert client.request(method, f"/api/admin/ops-alerts{path}").status_code == 401


# --- the status page's incident note ----------------------------------------------------------------------------------

def test_an_incident_public_note_reaches_the_status_banner_and_nothing_else_does(factory):
    from app.incidents import service
    from app.observability import status_page

    async def go():
        async with factory() as session:
            rec = await service.open_incident(session, title="DB failover on host-7", severity="WARNING", summary="internal cause",
                                              public_note="  Charts load slowly;\n a fix is being rolled out.  ")
            banner = await status_page._banner(session)
            await service.update_incident(session, rec, User(id=1), public_note="")
            cleared = await status_page._banner(session)
            return banner, cleared
    banner, cleared = _run(go())
    assert banner["incidents"][0]["note"] == "Charts load slowly; a fix is being rolled out."
    assert "host-7" not in json.dumps(banner) and "internal cause" not in json.dumps(banner)
    assert "note" not in cleared["incidents"][0]


def test_incident_note_is_capped():
    from app.incidents import service
    assert len(service.clean_note("x" * 1000)) == service.PUBLIC_NOTE_MAX


# --- atp.sh ops-check -------------------------------------------------------------------------------------------------

def test_the_ops_check_script_prints_states_only(monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("ops_check", Path(__file__).resolve().parents[1] / "scripts" / "ops_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = {"checks": [{"key": "disk", "state": "fail"}, {"key": "backup", "state": "ok"}],
              "sent": [{"key": "disk"}], "delivery": {"telegram": True, "email": None}}
    assert mod.summary(result) == "ops-check: disk=fail backup=ok | 1 change(s) to report (telegram:sent)"
    seen = {}

    async def fake(host):
        seen["host"] = host
        return {"checks": [], "sent": [], "delivery": {"telegram": None, "email": None}}
    monkeypatch.setattr(mod, "run_once", fake)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"disk_used_percent": 12}'))
    assert mod.main(["--host-facts", "-"]) == 0 and seen["host"] == {"disk_used_percent": 12}
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
    assert mod.main(["--host-facts", "-"]) == 0 and seen["host"] == {}
    assert capsys.readouterr().out.count("ops-check:") == 2              # one line per run
