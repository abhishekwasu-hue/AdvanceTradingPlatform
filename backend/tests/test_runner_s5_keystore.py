"""Runner S5 (docs/design/USER_RUNNER.md, step S5): the local encrypted keystore and the Angel One daily login.

Made-up details only; every Angel One HTTP call is answered by an httpx.MockTransport (no real network)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import pyotp
import pytest

from app.brokers import angel_one
from app.runner import broker_login as BL
from app.runner import keys as K
from app.runner.keystore import KEYFILE, SCRYPT, Keystore, KeystoreError

API_KEY, CLIENT, PIN = "apikey-SECRET-7731", "Z12345", "4826"
TOTP = pyotp.random_base32()
JWT, REFRESH, FEED = "jwt-SECRET-token-9921", "refresh-SECRET-5512", "feed-SECRET-3381"
PASSPHRASE = "correct horse battery"
SECRETS = (API_KEY, PIN, TOTP, JWT, REFRESH, FEED, PASSPHRASE)
# A Monday, 08:40 IST (03:10 UTC): inside the login window (09:15 - 45 minutes = 08:30) on an NSE trading day.
MONDAY_0840 = datetime(2026, 11, 16, 3, 10, tzinfo=timezone.utc)
POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")


def _store(tmp_path, mode=KEYFILE) -> Keystore:
    store = Keystore(tmp_path, passphrase=PASSPHRASE if mode == SCRYPT else None)
    store.init(mode, PASSPHRASE if mode == SCRYPT else None)
    return store


def _with_angel(store: Keystore) -> Keystore:
    store.put_broker("angel_one", {"api_key": API_KEY, "client_id": CLIENT, "pin": PIN, "totp_secret": TOTP}, replace=True)
    return store


class FakeAngel:
    """Answers loginByPassword / getProfile like SmartAPI; `fail` = "auth" | "network" | None."""

    def __init__(self, fail: str | None = None) -> None:
        self.fail, self.calls = fail, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail == "network":
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == angel_one.LOGIN_PATH:
            if self.fail == "auth":
                return httpx.Response(401, json={"status": False, "message": f"Invalid totp for {CLIENT} {PIN}", "errorcode": "AB1050", "data": None})
            return httpx.Response(200, json={"status": True, "data": {"jwtToken": JWT, "refreshToken": REFRESH, "feedToken": FEED}})
        if request.url.path == angel_one.PROFILE_PATH:
            assert request.headers["Authorization"] == f"Bearer {JWT}"
            return httpx.Response(200, json={"status": True, "data": {"clientcode": CLIENT, "name": "Test User"}})
        return httpx.Response(404, json={"status": False, "message": "unexpected"})

    def factory(self, broker: str) -> httpx.AsyncClient:
        real = BL.broker_client(broker)
        assert real.trust_env is False                       # straight from this machine, no proxy from the environment
        return httpx.AsyncClient(base_url=real.base_url, transport=httpx.MockTransport(self))


def _daily(tmp_path, store, fake) -> BL.DailyLogin:
    return BL.DailyLogin(tmp_path, store, "angel_one", client_factory=fake.factory)


# --- keystore ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", [KEYFILE, SCRYPT])
def test_keystore_round_trip_and_nothing_readable_on_disk(tmp_path, mode):
    _with_angel(_store(tmp_path, mode))
    again = Keystore(tmp_path, passphrase=PASSPHRASE if mode == SCRYPT else None)
    assert again.mode() == mode
    assert again.broker_fields("angel_one") == {"api_key": API_KEY, "client_id": CLIENT, "pin": PIN, "totp_secret": TOTP}
    assert again.brokers() == {"angel_one": ["api_key", "client_id", "pin", "totp_secret"]}
    raw = (tmp_path / "keystore.enc").read_bytes()
    assert not any(s.encode() in raw for s in SECRETS + (CLIENT,))
    assert (tmp_path / "keystore.key").exists() is (mode == KEYFILE)
    assert repr(again) == f"Keystore({str(tmp_path)!r})"


def test_a_wrong_passphrase_or_a_changed_file_does_not_open(tmp_path):
    _with_angel(_store(tmp_path, SCRYPT))
    with pytest.raises(KeystoreError, match="Wrong passphrase"):
        Keystore(tmp_path, passphrase="not the passphrase").brokers()
    with pytest.raises(KeystoreError, match="locked with a passphrase"):
        Keystore(tmp_path).brokers()
    path = tmp_path / "keystore.enc"
    doc = json.loads(path.read_text())
    doc["kdf"]["r"] = 4                                       # the header is authenticated too
    path.write_text(json.dumps(doc))
    os.chmod(path, 0o600)
    with pytest.raises(KeystoreError):
        Keystore(tmp_path, passphrase=PASSPHRASE).brokers()


def test_a_short_passphrase_and_a_second_init_are_refused(tmp_path):
    with pytest.raises(KeystoreError, match="at least"):
        Keystore(tmp_path).init(SCRYPT, "short")
    _store(tmp_path)
    with pytest.raises(KeystoreError, match="already exists"):
        Keystore(tmp_path).init(KEYFILE)


@POSIX
def test_files_are_owner_only_and_a_readable_file_is_refused(tmp_path):
    home = tmp_path / "runner"
    _with_angel(_store(home))
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    for name in ("keystore.enc", "keystore.key"):
        assert stat.S_IMODE((home / name).stat().st_mode) == 0o600, name
    for name in ("keystore.enc", "keystore.key"):
        os.chmod(home / name, 0o644)
        with pytest.raises(KeystoreError, match="readable by you only"):
            Keystore(home).brokers()
        os.chmod(home / name, 0o600)
    assert Keystore(home).brokers()


def test_rekey_moves_to_a_passphrase_and_the_old_key_file_is_gone(tmp_path):
    store = _with_angel(_store(tmp_path))
    store.rekey(SCRYPT, PASSPHRASE)
    assert not (tmp_path / "keystore.key").exists()
    assert Keystore(tmp_path, passphrase=PASSPHRASE).broker_fields("angel_one")["pin"] == PIN
    Keystore(tmp_path, passphrase=PASSPHRASE).rekey(KEYFILE)
    assert Keystore(tmp_path).broker_fields("angel_one")["totp_secret"] == TOTP


def test_details_are_validated_and_a_change_ends_the_stored_session(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(KeystoreError, match="needs: pin, totp_secret"):
        store.put_broker("angel_one", {"api_key": API_KEY, "client_id": CLIENT}, replace=True)
    with pytest.raises(KeystoreError, match="Unknown field"):
        store.put_broker("angel_one", {"password": "x"}, replace=True)
    with pytest.raises(KeystoreError, match="keeps details for"):
        store.put_broker("zerodha", {}, replace=True)
    _with_angel(store)
    store.put_session("angel_one", "2026-11-16", {"access_token": JWT}, "now")
    assert store.session("angel_one", "2026-11-16")["access_token"] == JWT
    assert store.session("angel_one", "2026-11-17") is None             # a session ends at its daily expiry
    store.put_broker("angel_one", {"pin": "9999"}, replace=False)       # rotate: only the PIN
    assert store.broker_fields("angel_one")["pin"] == "9999" and store.broker_fields("angel_one")["api_key"] == API_KEY
    assert store.session("angel_one", "2026-11-16") is None
    assert store.remove_broker("angel_one") and store.brokers() == {}


# --- CLI -----------------------------------------------------------------------------------------------------------
def test_the_cli_asks_secrets_without_echo_and_never_prints_them(tmp_path):
    asked_hidden, asked_plain, printed = [], [], []

    def hidden(label: str) -> str:
        asked_hidden.append(label)
        return {"SmartAPI": API_KEY, "Trading": PIN, "TOTP": TOTP}[label.split()[0]]

    def plain(label: str) -> str:
        asked_plain.append(label)
        return CLIENT if label.startswith("Client") else ""

    run = lambda *argv: K.main([*argv, "--home", str(tmp_path)], secret_prompt=hidden, plain_prompt=plain, out=printed.append)
    assert run("init") == 0
    assert run("add", "angel_one") == 0
    assert [l.split()[0] for l in asked_hidden] == ["SmartAPI", "Trading", "TOTP"]          # api key, PIN, TOTP: hidden
    assert [l.split()[0] for l in asked_plain] == ["Client", "This"]                        # client code, static IP
    assert run("list") == 0
    assert printed[-1] == "angel_one: api_key, client_id, pin, totp_secret"
    asked_hidden.clear()
    assert K.main(["rotate", "angel_one", "--field", "pin", "--home", str(tmp_path)], secret_prompt=lambda _l: "1357",
                  plain_prompt=plain, out=printed.append) == 0
    assert Keystore(tmp_path).broker_fields("angel_one")["pin"] == "1357"
    assert run("remove", "angel_one") == 0 and Keystore(tmp_path).brokers() == {}
    assert not any(s in line for line in printed for s in SECRETS + ("1357",))
    assert run("add", "angel_two") == 1 and "keeps details for" in printed[-1]


# --- daily login ---------------------------------------------------------------------------------------------------
def test_login_success_stores_todays_session_and_reports_a_valid_session(tmp_path):
    store = _with_angel(_store(tmp_path))
    fake = FakeAngel()
    daily = _daily(tmp_path, store, fake)
    assert daily.status_line(MONDAY_0840) == "broker not logged in today"
    assert asyncio.run(daily.run_due(MONDAY_0840)) is True
    login = json.loads(fake.calls[0].content)
    assert login["clientcode"] == CLIENT and login["password"] == PIN and login["totp"].isdigit() and len(login["totp"]) == 6
    assert fake.calls[0].headers["X-PrivateKey"] == API_KEY
    assert {urlparse(str(c.url)).hostname for c in fake.calls} <= BL.BROKER_HOSTS
    assert store.session("angel_one", "2026-11-16") == {"day": "2026-11-16", "at": MONDAY_0840.isoformat(),
                                                        "access_token": JWT, "refresh_token": REFRESH, "feed_token": FEED}
    assert daily.status_line(MONDAY_0840) == "broker session valid"
    assert asyncio.run(daily.run_due(MONDAY_0840)) is None                   # idempotent: today's session is kept
    assert len(fake.calls) == 2
    adapter = BL.build_adapter(store, "angel_one", MONDAY_0840)           # the loop trades on today's session
    assert adapter.credentials.access_token == JWT
    assert BL.build_adapter(store, "angel_one", datetime(2026, 11, 17, 3, 10, tzinfo=timezone.utc)).credentials.access_token is None


def test_a_refused_login_is_retried_with_backoff_then_given_up_with_a_plain_reason(tmp_path):
    store = _with_angel(_store(tmp_path))
    (tmp_path / "runner.json").write_text(json.dumps({"broker_login_retries": 2, "broker_login_retry_minutes": 5}))
    daily = _daily(tmp_path, store, FakeAngel(fail="auth"))
    assert daily.settings.max_attempts == 3
    assert asyncio.run(daily.run_due(MONDAY_0840)) is False
    state = daily.status()
    assert state["state"] == BL.RETRYING and state["next_try"] == "2026-11-16T03:15:00+00:00"           # +5 min
    assert daily.status_line(MONDAY_0840).startswith("login failed: Angel One refused the login")
    assert asyncio.run(daily.run_due(datetime(2026, 11, 16, 3, 14, tzinfo=timezone.utc))) is None       # not yet
    assert asyncio.run(daily.run_due(datetime(2026, 11, 16, 3, 15, tzinfo=timezone.utc))) is False
    assert daily.status()["next_try"] == "2026-11-16T03:25:00+00:00"                                    # then doubling
    assert asyncio.run(daily.run_due(datetime(2026, 11, 16, 3, 25, tzinfo=timezone.utc))) is False
    assert daily.status()["state"] == BL.FAILED and daily.status()["attempts"] == 3
    assert asyncio.run(daily.run_due(datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc))) is None        # given up today
    assert daily.status_line(MONDAY_0840) == ("login failed: Angel One refused the login - check the client code, PIN "
                                              "and TOTP secret (python -m app.runner.keys rotate angel_one)")
    log = (tmp_path / "broker_login.log").read_text().splitlines()
    assert len(log) == 3 and "attempt 3 failed" in log[-1]
    daily.client_factory = FakeAngel().factory                               # the user fixes it and logs in by hand
    assert asyncio.run(daily.login(datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc), manual=True)) is True
    assert daily.status_line(MONDAY_0840) == "broker session valid"


def test_network_trouble_and_a_missing_or_locked_keystore_give_plain_reasons(tmp_path):
    store = _with_angel(_store(tmp_path / "a"))
    daily = _daily(tmp_path / "a", store, FakeAngel(fail="network"))
    assert asyncio.run(daily.run_due(MONDAY_0840)) is False
    assert daily.status()["reason"] == "Angel One could not be reached from this machine (network)"
    locked = Keystore(tmp_path / "b")
    locked.init(SCRYPT, PASSPHRASE)
    daily = BL.DailyLogin(tmp_path / "b", Keystore(tmp_path / "b"), client_factory=FakeAngel().factory)
    assert asyncio.run(daily.run_due(MONDAY_0840)) is False
    assert daily.status()["state"] == BL.NOT_SET_UP and "locked with a passphrase" in daily.status_line(MONDAY_0840)
    empty = _store(tmp_path / "c")
    daily = BL.DailyLogin(tmp_path / "c", empty, client_factory=FakeAngel().factory)
    assert asyncio.run(daily.run_due(MONDAY_0840)) is False
    assert daily.status_line(MONDAY_0840) == ("login failed: No angel_one details in the keystore yet: run "
                                              "`python -m app.runner.keys add angel_one`")


def test_the_login_window_and_its_setting(tmp_path):
    settings = BL.LoginSettings.load(tmp_path)
    assert (settings.minutes_before_open, settings.retries, settings.retry_minutes) == (45, 1, 10)   # the rule set
    assert not settings.in_window(datetime(2026, 11, 16, 2, 59, tzinfo=timezone.utc))     # 08:29 IST
    assert settings.in_window(datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc))          # 08:30 IST
    assert not settings.in_window(datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc))     # 15:30 IST: closed
    assert not settings.in_window(datetime(2026, 11, 15, 3, 10, tzinfo=timezone.utc))     # a Sunday
    (tmp_path / "runner.json").write_text(json.dumps({"cycle_seconds": 60, "broker_login_minutes_before_open": 20}))
    assert BL.LoginSettings.load(tmp_path).login_time(MONDAY_0840.date()).strftime("%H:%M") == "08:55"


def test_no_secret_ever_reaches_a_log_the_status_or_the_log_file(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    store = _with_angel(_store(tmp_path))
    for fake in (FakeAngel(fail="auth"), FakeAngel(fail="network"), FakeAngel()):
        asyncio.run(BL.DailyLogin(tmp_path, store, client_factory=fake.factory).login(MONDAY_0840, manual=True))
    texts = [caplog.text, (tmp_path / "broker_status.json").read_text(), (tmp_path / "broker_login.log").read_text(),
             BL.DailyLogin(tmp_path, store).status_line(MONDAY_0840)]
    for text in texts:
        assert not any(s in text for s in SECRETS), text
    assert "Invalid totp" not in "".join(texts)                             # never the broker's own text either


def test_every_angel_one_host_is_angel_ones_own():
    hosts = {urlparse(u).hostname for u in (angel_one.BASE_URL, angel_one.SCRIP_MASTER_URL)}
    assert hosts == set(BL.BROKER_HOSTS)
    assert all(h.endswith(("angelone.in", "angelbroking.com")) for h in hosts)
    assert urlparse(str(BL.broker_client("angel_one").base_url)).hostname == "apiconnect.angelone.in"
