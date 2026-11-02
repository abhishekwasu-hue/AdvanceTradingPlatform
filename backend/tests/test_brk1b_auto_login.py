"""BRK-1 B (owner order BRK-1 B): the easiest official login for every broker.

- TOTP brokers (Angel One, Shoonya, Kotak Neo, Groww, Dhan): ATP logs each active connection in by itself before the open
  (app/brokers/auto_login.py) - NSE trading days only, idempotent, one attempt plus a bounded retry; a failure that uses
  them up marks the connection "Not working" with one plain sentence and sends ONE reminder; Renew stays the fallback;
- Dhan: the official TOTP token API (client ID + PIN + TOTP) is the default way; the pasted 24-hour token the fallback;
- Zerodha / Fyers: one Log in (the broker returns to ATP's callback); the pasted address only as the hidden fallback;
- every broker connection: one reminder before its session ends (login_reminder.send_connection_reminders);
- the smoke test's quote step has no per-broker gate; the PIN and the TOTP code never reach a log line.

Every broker call goes to a fake transport (test_p2a_broker_connect's, plus Dhan's auth host): nothing leaves the process.
All values are fake and built at run time.
"""
import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.brokers import auto_login as al
from app.brokers import catalog as cat
from app.brokers import connect as cn
from app.brokers import login_reminder as lr
from app.brokers import upstox_analytics as ua
from app.brokers.models import BrokerCredentials
from app.brokers.token_lifecycle import load_credentials
from app.compliance import rules
from app.core.enums import NotificationType
from app.core.logging_config import redact_query
from app.db.models import BrokerCredentialRecord, NotificationRecord
from app.market_data.calendar import IST, MARKET_OPEN
from app.secrets_store.encryption import encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL
from tests import test_p2a_broker_connect as p2a
from tests.test_auth_api import _session_factory, client

DHAN_CLIENT = "1000001"
DHAN_PIN = "".join(str((i * 7 + 3) % 10) for i in range(6))
DHAN_SEED = p2a._sentinel("dhan-seed")
DHAN_TOTP_TOKEN = p2a._sentinel("dhan-totp-token")
CALLS: list = []


async def _transport(self, request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    CALLS.append((host, path))
    if host == "auth.dhan.co" and path == "/app/generateAccessToken":
        q = request.url.params
        if q.get("pin") == DHAN_PIN and len(q.get("totp", "")) == 6 and q.get("dhanClientId") == DHAN_CLIENT:
            return httpx.Response(200, json={"accessToken": DHAN_TOTP_TOKEN, "expiryTime": "2099-01-01T00:00:00"})
        # A broker that echoes what it was sent: neither the reply nor a log may keep the PIN.
        return httpx.Response(400, json={"status": "error", "message": f"Invalid pin {q.get('pin')}"})
    return await p2a._fake_transport(self, request)


class _FakeWs:
    async def close(self):
        return None


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    from app.core import rate_limit
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _transport)

    async def fake_ws(url):
        return _FakeWs()
    monkeypatch.setattr(ua, "_default_ws_connect", fake_ws)
    p2a.DOWN_HOSTS.clear()
    p2a.DHAN_GOOD.clear()
    p2a.DHAN_GOOD.add(DHAN_TOTP_TOKEN)
    CALLS.clear()
    rate_limit.reset("broker_connect")
    yield
    rate_limit.reset("broker_connect")


def _run(coro):
    return asyncio.run(coro)


def _past_monday() -> date:
    today = datetime.now(IST).date()
    return today - timedelta(days=today.weekday() + 7)


def _at(day: date, minutes_from_open: int) -> datetime:
    return datetime.combine(day, MARKET_OPEN, tzinfo=IST) + timedelta(minutes=minutes_from_open)


def _org(prefix: str):
    headers = p2a._org(f"brk1b-{prefix}")
    return headers, p2a._tenant(headers)


def _set(tenant_id: int, broker: str, **values) -> None:
    async def go():
        async with _session_factory() as session:
            row = await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker))
            creds = values.pop("creds", None)
            if creds is not None:
                payload = load_credentials(row).model_dump(exclude_none=True)
                payload.update(creds)
                row.encrypted_payload = encrypt_text(BrokerCredentials(**payload).model_dump_json(), tenant_id, PURPOSE_BROKER_CREDENTIAL)
            for key, value in values.items():
                setattr(row, key, value.astimezone(UTC) if isinstance(value, datetime) else value)
            await session.commit()
    _run(go())


def _ended_before(tenant_id: int, broker: str, day: date) -> None:
    """Yesterday's session: still marked VALID but it ended before the open (as at a broker's nightly expiry)."""
    _set(tenant_id, broker, token_status="VALID", token_expires_at=datetime.combine(day, datetime.min.time(), tzinfo=IST))


def _view(tenant_id: int, broker: str, now=None) -> dict:
    return cn.connection_view(p2a._row(tenant_id, broker), now)


def _reminders(tenant_id: int):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == tenant_id, NotificationRecord.title.startswith(lr.TITLE_PREFIX))
                .order_by(NotificationRecord.id)))
    return _run(go())


def _logins(host: str, path_end: str) -> int:
    return sum(1 for h, p in CALLS if h == host and p.endswith(path_end))


def _run_due(now):
    return _run(al.run_due(_session_factory, now, holidays=set()))


def _login_mine(tenant_id: int, broker: str, now):
    """One connection's automatic login, as run_due runs it for every row."""
    record_id = p2a._row(tenant_id, broker).id

    async def go():
        async with _session_factory() as session:
            return await al.login_one(session, record_id, now)
    return _run(go())


def _attempts(tenant_id: int, broker: str) -> int:
    return int((load_credentials(p2a._row(tenant_id, broker)).auto_login or {}).get("attempts") or 0)


def _remind(tenant_id: int, now, holidays=frozenset()) -> int:
    """One per-connection reminder pass (every organisation's rows, as the worker runs it); how many reached this one."""
    async def go():
        async with _session_factory() as session:
            await lr.send_connection_reminders(session, now, holidays=set(holidays))
    before = len([n for n in _reminders(tenant_id) if n.metadata_json])
    _run(go())
    return len([n for n in _reminders(tenant_id) if n.metadata_json]) - before


ANGEL = {"api_key": "smartkey", "client_id": "A1", "pin": p2a.ANGEL_PIN, "totp_secret": p2a.ANGEL_SEED}


# --- the brokers, the window, the rule set ------------------------------------------------------------------------------

def test_the_totp_brokers_and_the_window_come_from_the_catalog_and_the_rule_set():
    assert set(al.auto_brokers()) == {"angel_one", "shoonya", "kotak_neo", "groww", "dhan"}
    assert {lr.login_method(b) for b in al.auto_brokers()} == {"totp"}
    minutes = rules.load().param(lr.RULE, "auto_login_minutes_before_open")
    assert isinstance(minutes, int) and minutes > rules.load().param(lr.RULE, "reminder_minutes_before_open")
    assert al.max_attempts() == 1 + rules.load().param(lr.RULE, "auto_login_retries")
    day = _past_monday()
    assert al.login_time(day) == _at(day, -minutes)
    assert not al.in_window(_at(day, -minutes) - timedelta(minutes=1)) and al.in_window(_at(day, -minutes))
    assert al.in_window(_at(day, 60))                                    # a worker started mid-morning still logs in
    assert not al.in_window(_at(day, 375))                               # after the close


def test_no_run_outside_the_window_on_a_holiday_or_a_weekend():
    headers, tenant_id = _org("quiet")
    assert p2a._connect(headers, "angel_one", ANGEL)["status"] == "connected"
    day = _past_monday()
    _ended_before(tenant_id, "angel_one", day)
    before = _logins("apiconnect.angelone.in", "/loginByPassword")
    assert _run(al.run_due(_session_factory, _at(day, -50), holidays=set())) == 0          # before the window
    assert _run(al.run_due(_session_factory, _at(day, -40), holidays={day})) == 0          # an NSE holiday
    assert _run(al.run_due(_session_factory, _at(day + timedelta(days=5), -40), holidays=set())) == 0   # Saturday
    assert _logins("apiconnect.angelone.in", "/loginByPassword") == before
    assert p2a._row(tenant_id, "angel_one").token_status == "VALID" and _view(tenant_id, "angel_one")["status"] == "needs_login"


# --- the automatic login ----------------------------------------------------------------------------------------------

def test_an_active_totp_connection_is_logged_in_before_the_open_once(caplog):
    caplog.set_level(logging.DEBUG)
    headers, tenant_id = _org("auto")
    assert p2a._connect(headers, "angel_one", ANGEL)["status"] == "connected"
    day = _past_monday()
    _ended_before(tenant_id, "angel_one", day)
    view = _view(tenant_id, "angel_one")
    assert view["status"] == "needs_login" and "ATP logs in for you before the market opens" in view["message"]
    assert _run_due(_at(day, -40)) >= 1
    row = p2a._row(tenant_id, "angel_one")
    assert row.token_status == "VALID" and load_credentials(row).access_token == p2a.ANGEL_SESSION
    assert _view(tenant_id, "angel_one")["status"] == "connected"
    assert _attempts(tenant_id, "angel_one") == 1
    # Idempotent: the session lasts the day, so later runs (another cycle, a restarted worker) do nothing.
    before = _logins("apiconnect.angelone.in", "/loginByPassword")
    assert _login_mine(tenant_id, "angel_one", _at(day, -39)) is None
    _run_due(_at(day, 30))
    assert _attempts(tenant_id, "angel_one") == 1 and _logins("apiconnect.angelone.in", "/loginByPassword") == before
    assert not [n for n in _reminders(tenant_id) if n.metadata_json]                  # nothing to remind about
    p2a._assert_no_secret(caplog.text)


def test_a_failing_login_is_retried_once_then_not_working_with_one_reminder(caplog):
    caplog.set_level(logging.DEBUG)
    headers, tenant_id = _org("fail")
    assert p2a._connect(headers, "angel_one", ANGEL)["status"] == "connected"
    day = _past_monday()
    _ended_before(tenant_id, "angel_one", day)
    _set(tenant_id, "angel_one", creds={"pin": "0000"})                  # Angel One no longer accepts the details
    t0 = _at(day, -40)
    _run_due(t0)
    assert _attempts(tenant_id, "angel_one") == 1
    assert _view(tenant_id, "angel_one", t0)["status"] == "needs_login"      # still trying: not reported yet
    assert not [n for n in _reminders(tenant_id) if n.metadata_json]
    assert _login_mine(tenant_id, "angel_one", t0 + timedelta(minutes=5)) is None    # before the retry interval
    assert _attempts(tenant_id, "angel_one") == 1
    retry = rules.load().param(lr.RULE, "auto_login_retry_minutes")
    assert _login_mine(tenant_id, "angel_one", t0 + timedelta(minutes=retry)) is False   # the one retry
    assert _attempts(tenant_id, "angel_one") == 2
    _run_due(t0 + timedelta(minutes=3 * retry))                             # bounded: no third attempt today
    assert _attempts(tenant_id, "angel_one") == 2

    view = _view(tenant_id, "angel_one", t0)
    assert view["status"] == "not_working" and view["renew"] is True        # Renew stays as the fallback
    assert view["message"].startswith("This morning's automatic login did not work: Angel One did not accept these details")
    p2a._assert_plain(view["message"])
    [conn] = [c for c in p2a._connections(headers) if c["broker"] == "angel_one"]
    assert conn["status"] in ("not_working", "needs_login")               # the API reads it on today's real clock

    sent = [n for n in _reminders(tenant_id) if n.metadata_json]
    assert len(sent) == 1, [n.title for n in sent]
    meta = json.loads(sent[0].metadata_json)
    assert meta["kind"] == lr.CONNECTION_REMINDER and meta["broker"] == "angel_one" and meta["day"] == day.isoformat()
    assert sent[0].event_type == NotificationType.TOKEN_EXPIRED.value and sent[0].severity == "WARNING"
    assert sent[0].user_id is not None and "automatic login did not work" in sent[0].title
    assert "Renew" in sent[0].message
    # The per-connection reminder pass and the organisation's pre-open reminder do not repeat it.
    assert _remind(tenant_id, _at(day, -10)) == 0
    from tests.test_trading_worker import _deploy
    me = client.get("/api/auth/me", headers=headers).json()
    deployment = _deploy({"tenant_id": tenant_id, "user_id": me["id"]}, broker_name="angel_one")

    async def org_reminders():
        async with _session_factory() as session:
            assert "angel_one" in (await lr.due_reminders(session, _at(day, -10), holidays=())).get(tenant_id, [])
            await lr.send_reminders(session, _at(day, -10), holidays=())
            from app.db.models import StrategyDeploymentRecord
            (await session.get(StrategyDeploymentRecord, deployment)).status = "STOPPED"     # leave nothing running
            await session.commit()
    _run(org_reminders())
    assert len([n for n in _reminders(tenant_id) if "angel_one" in n.title or "Angel One" in n.title]) == 1
    p2a._assert_no_secret(caplog.text)

    # New details clear the state: the next connect is a fresh start.
    _set(tenant_id, "angel_one", creds={"pin": p2a.ANGEL_PIN})
    assert p2a._connect(headers, "angel_one", ANGEL)["status"] == "connected"
    assert load_credentials(p2a._row(tenant_id, "angel_one")).auto_login is None


def test_disconnected_unproven_and_pasted_token_rows_are_left_alone():
    day = _past_monday()
    headers, tenant_id = _org("left")
    assert p2a._connect(headers, "angel_one", ANGEL)["status"] == "connected"
    assert client.post("/api/broker/angel_one/disconnect", headers=headers).status_code == 204       # on purpose
    row = p2a._row(tenant_id, "angel_one")
    assert row.token_status == "EXPIRED" and row.token_expires_at is None and not lr.is_active_connection(row)
    unproven = p2a._row(tenant_id, "angel_one")
    unproven.last_verified_at = None
    assert not lr.is_active_connection(unproven)
    # Dhan connected the fallback way (a pasted token): nothing to log in with.
    assert p2a._connect(headers, "dhan", {"client_id": DHAN_CLIENT, "access_token": p2a.DHAN_A})["status"] == "connected"
    _ended_before(tenant_id, "dhan", day)
    _run_due(_at(day, -40))
    angel = p2a._row(tenant_id, "angel_one")
    assert angel.token_status == "EXPIRED" and angel.token_expires_at is None           # not logged back in
    assert load_credentials(angel).auto_login is None
    assert load_credentials(p2a._row(tenant_id, "dhan")).auto_login is None             # never attempted
    view = _view(tenant_id, "dhan")
    assert view["status"] == "needs_login" and "paste today's token" in view["message"]
    # Its reminder names the paste, not ATP's own login.
    assert _remind(tenant_id, _at(day, -30)) == 1
    [sent] = [n for n in _reminders(tenant_id) if n.metadata_json]
    assert lr.HOW["manual"] in sent.message and "Dhan" in sent.title


# --- Dhan: the TOTP login is the default way, the pasted token the fallback ------------------------------------------

def test_dhan_connects_with_client_id_pin_and_totp_and_renews_with_one_click(caplog):
    caplog.set_level(logging.DEBUG)
    headers, tenant_id = _org("dhan")
    entry = cat.entry("dhan")
    assert entry.method == cat.TOTP_AUTO and entry.field_keys() == ("client_id", "pin", "totp_secret")
    assert entry.renew_keys() == () and entry.fallback_keys() == ("client_id", "access_token")
    out = p2a._connect(headers, "dhan", {"client_id": DHAN_CLIENT, "pin": DHAN_PIN, "totp_secret": DHAN_SEED})
    assert out == {"status": "connected", "message": "Connected."}
    row = p2a._row(tenant_id, "dhan")
    assert row.token_status == "VALID" and load_credentials(row).access_token == DHAN_TOTP_TOKEN
    sent = [c for c in CALLS if c[0] == "auth.dhan.co"]
    assert sent == [("auth.dhan.co", "/app/generateAccessToken")]
    assert p2a._connect(headers, "dhan", {}, renew=True)["status"] == "connected"         # one click
    # A pasted token on renew is accepted too (the fallback), and the TOTP login still makes today's token.
    assert p2a._connect(headers, "dhan", {"access_token": p2a.DHAN_B}, renew=True)["status"] == "connected"
    # A wrong PIN: one plain sentence, and the PIN Dhan echoed back is in no log line.
    out = p2a._connect(headers, "dhan", {"client_id": DHAN_CLIENT, "pin": "111111", "totp_secret": DHAN_SEED})
    assert out["status"] == "not_working" and "TOTP secret" in out["message"]
    assert DHAN_PIN not in caplog.text and "111111" not in caplog.text
    p2a._assert_no_secret(caplog.text)


def test_dhan_fallback_paste_and_its_renew():
    headers, tenant_id = _org("dhanpaste")
    assert p2a._connect(headers, "dhan", {"client_id": DHAN_CLIENT, "access_token": p2a.DHAN_A})["status"] == "connected"
    assert not [c for c in CALLS if c[0] == "auth.dhan.co"]                # no TOTP login without a secret
    out = p2a._connect(headers, "dhan", {}, renew=True)
    assert out["status"] == "needs_login" and "Paste today's Dhan access token" in out["message"]
    assert p2a._connect(headers, "dhan", {"access_token": p2a.DHAN_B}, renew=True)["status"] == "connected"
    # Mixed ways are refused with a plain sentence: the fallback asks for its own fields only.
    out = p2a._connect(headers, "dhan", {"pin": DHAN_PIN, "access_token": p2a.DHAN_B})
    assert out["status"] == "not_working" and "Paste your" in out["message"]


def test_dhan_adapter_never_puts_the_pin_in_an_error():
    from app.brokers.dhan import DhanBroker
    from app.brokers.exceptions import BrokerAuthenticationError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": f"pin {request.url.params.get('pin')} rejected"})
    creds = BrokerCredentials(client_id=DHAN_CLIENT, pin=DHAN_PIN, totp_secret=DHAN_SEED)
    broker = DhanBroker(creds, client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=DhanBroker.BASE_URL))
    with pytest.raises(BrokerAuthenticationError) as exc:
        _run(broker.authenticate())
    assert DHAN_PIN not in str(exc.value) and DHAN_SEED not in str(exc.value)
    with pytest.raises(BrokerAuthenticationError):
        DhanBroker(BrokerCredentials(client_id=DHAN_CLIENT, pin=DHAN_PIN))            # a PIN without the TOTP secret
    # The PIN and the code ride in the query string: every HTTP client log line blanks them.
    line = redact_query(f"POST https://auth.dhan.co/app/generateAccessToken?dhanClientId={DHAN_CLIENT}&pin={DHAN_PIN}&totp=123456")
    assert DHAN_PIN not in line and "123456" not in line and f"dhanClientId={DHAN_CLIENT}" in line


# --- Zerodha / Fyers: one Log in, the paste only as the fallback -------------------------------------------------------

def test_zerodha_and_fyers_log_in_through_atps_callback_with_nothing_to_paste():
    headers, tenant_id = _org("kite")
    for broker in ("zerodha", "fyers"):
        entry = cat.entry(broker)
        assert entry.method == cat.OAUTH_REDIRECT and entry.renew_keys() == () and entry.fallback_renew_keys() == ("request_token",)
        assert "redirect_uri" not in entry.field_keys()
    out = p2a._connect(headers, "zerodha", {"api_key": p2a.KITE_APP, "api_secret": p2a.KITE_S})
    assert out["status"] == "needs_login" and "ATP finishes the login by itself" in out["message"]
    out = p2a._connect(headers, "zerodha", {}, renew=True)
    assert out["status"] == "needs_login" and "press Log in" in out["message"].replace("Press", "press")
    [conn] = p2a._connections(headers)
    assert conn["status"] == "needs_login" and conn["renew"] is True and "ATP finishes the login by itself" in conn["message"]
    # The Log in button's route: ATP's own callback is the redirect; the state names the tenant and the account.
    start = client.get("/api/broker/zerodha/oauth/start", headers=headers)
    assert start.status_code == 200, start.text
    assert start.json()["authorization_url"].startswith("https://kite.zerodha.com/connect/login?")
    rows = {r["broker"]: r for r in client.get("/api/broker/catalog", headers=headers).json()}
    assert rows["zerodha"]["redirect_url"].endswith("/api/broker/zerodha/oauth/callback")
    assert rows["fyers"]["fallback"]["fields"][-1]["key"] == "redirect_uri"
    # The fallback: the pasted address still logs in.
    good = f"https://example.com/cb?action=login&status=success&request_token={p2a.KITE_CODE}"
    assert p2a._connect(headers, "zerodha", {"request_token": good}, renew=True)["status"] == "connected"


def test_fyers_and_upstox_user_facing_text_follows_the_research():
    from app.brokers import field_specs as fs
    fyers = fs.BROKER_FIELD_SPECS["fyers"]
    assert fyers.console_url == "https://fyers.in/web/api-dashboard/user-apps" and fyers.get("api_key").example.endswith("-200")
    upstox = fs.BROKER_FIELD_SPECS["upstox"]
    assert upstox.login_method != fs.LOGIN_OAUTH and upstox.oauth_start_path is None and upstox.oauth_callback_path is None
    text = " ".join([upstox.tile_line, upstox.login_note] + [f.label + " " + f.help for f in upstox.fields if not f.advanced])
    assert "Analytics Token" in text and "OAuth" not in text and "Log in once" not in text
    assert cat.entry("upstox").method == cat.ANALYTICS_TOKEN and cat.entry("coindcx").method == cat.API_KEY_SECRET


# --- every connection: one reminder before its session ends -----------------------------------------------------------

def test_each_connection_is_reminded_once_before_its_session_ends():
    day = _past_monday()
    headers, tenant_id = _org("remind")
    p2a._connect(headers, "zerodha", {"api_key": p2a.KITE_APP, "api_secret": p2a.KITE_S})
    good = f"https://example.com/cb?action=login&status=success&request_token={p2a.KITE_CODE}"
    assert p2a._connect(headers, "zerodha", {"request_token": good}, renew=True)["status"] == "connected"
    _ended_before(tenant_id, "zerodha", day)
    lead = rules.load().param(lr.RULE, "reminder_minutes_before_open")
    assert _remind(tenant_id, _at(day, -lead - 1)) == 0     # not due yet
    assert _remind(tenant_id, _at(day, -lead), holidays={day}) == 0   # holiday
    assert _remind(tenant_id, _at(day, -lead)) == 1
    assert _remind(tenant_id, _at(day, 5)) == 0             # once a day
    [sent] = [n for n in _reminders(tenant_id) if n.metadata_json]
    assert sent.title == "Log in to Zerodha (Kite) before the session ends" and lr.HOW["oauth"] in sent.message

    # A session that ends during market hours: reminded that long before its end, not before the open.
    headers2, tenant2 = _org("remind-mid")
    p2a._connect(headers2, "zerodha", {"api_key": p2a.KITE_APP, "api_secret": p2a.KITE_S})
    assert p2a._connect(headers2, "zerodha", {"request_token": good}, renew=True)["status"] == "connected"
    ends = _at(day, 180)
    _set(tenant2, "zerodha", token_status="VALID", token_expires_at=ends)
    assert _remind(tenant2, _at(day, -lead)) == 0
    assert _remind(tenant2, ends - timedelta(minutes=lead)) == 1
    [mid] = [n for n in _reminders(tenant2) if n.metadata_json]
    assert "session ends at" in mid.message


def test_the_worker_runs_the_automatic_login_in_its_cycle(monkeypatch):
    from tests.test_trading_worker import _FakeBroker, _worker
    headers, tenant_id = _org("worker")
    assert p2a._connect(headers, "angel_one", ANGEL)["status"] == "connected"
    day = _past_monday()
    _ended_before(tenant_id, "angel_one", day)
    worker = _worker(monkeypatch, _FakeBroker())

    async def no_holidays(session, exchange="NSE", year=None):
        return set()
    monkeypatch.setattr(lr, "load_holidays", no_holidays)
    monkeypatch.setattr(al, "load_holidays", no_holidays)
    report = _run(worker.run_cycle(_at(day, -40)))
    assert report.auto_logins >= 1
    assert p2a._row(tenant_id, "angel_one").token_status == "VALID"


# --- the smoke test's quote step has no per-broker gate ----------------------------------------------------------------

def test_the_smoke_quote_step_is_the_same_for_every_broker():
    from pathlib import Path

    from app.brokers.smoke import run_smoke
    from tests.test_trading_worker import _FakeBroker
    source = Path(__file__).resolve().parents[1].joinpath("app/brokers/smoke.py").read_text(encoding="utf-8")
    assert "brokers.gates" not in source and "GATES" not in source and "kotak_neo" not in source and "groww" not in source
    for broker in ("kotak_neo", "groww", "dhan", "angel_one"):
        fake = _FakeBroker()
        fake.name = broker
        report = _run(run_smoke(fake, step_timeout=5))
        quote = next(s for s in report.steps if s.name == "quote")
        assert quote.status == "ok", (broker, quote.detail)
