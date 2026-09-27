"""Phase O: per-exchange sessions, least-privilege DB roles, Web Push + SMS channels, chaos
behaviour under dependency failure, AI/billing metrics, PITR script shape."""
import asyncio
import base64
import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import select

from app.alerts import dispatcher, webpush
from app.alerts.channels import SmsConfig, merge_push, parse_config
from app.core.config import DATABASE_URL
from app.db.models import AlertChannelRecord, StrategyDeploymentRecord, Tenant, TradeRecord
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, intraday_cutoffs, session_family, session_status
from app.notifications.service import notify
from app.core.enums import NotificationSeverity, NotificationType
from app.observability import metrics
from tests.test_alerts import _auth, _dispatch, _put
from tests.test_auth_api import _session_factory, client
from tests.test_trading_worker import (
    BAR_TS, OPEN_NOW, STRATEGY, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker,
)

FRIDAY_2000 = datetime(2026, 9, 25, 20, 0, tzinfo=IST)     # NSE closed, MCX open
FRIDAY_2320 = datetime(2026, 9, 25, 23, 20, tzinfo=IST)    # MCX square-off window
SUNDAY_0300 = datetime(2026, 9, 27, 3, 0, tzinfo=IST)


def _run(coro):
    return asyncio.run(coro)


# --- O2: per-exchange sessions --------------------------------------------------------------------

def test_session_family_and_per_exchange_clocks():
    assert session_family("NFO") == "NSE" and session_family("bfo") == "NSE" and session_family(None) == "NSE"
    assert session_family("MCX_FO") == "MCX" and session_family("BINANCE") == "CRYPTO" and session_family("XYZ") == "NSE"
    assert not session_status(FRIDAY_2000).is_open                               # default = NSE clock
    assert session_status(FRIDAY_2000, exchange="MCX").is_open
    assert session_status(SUNDAY_0300, exchange="CRYPTO").is_open
    assert not session_status(SUNDAY_0300, exchange="MCX").is_open
    assert "MCX" in session_status(SUNDAY_0300, exchange="MCX").reason
    # Holidays are per calendar: an NSE holiday date does not close MCX unless MCX declares it.
    holiday = date(2026, 9, 25)
    assert not session_status(FRIDAY_2000 - timedelta(hours=9), holidays=[holiday]).is_open
    assert session_status(FRIDAY_2000, holidays=[], exchange="MCX").is_open
    assert not session_status(FRIDAY_2000, holidays=[holiday], exchange="MCX").is_open
    assert intraday_cutoffs("NSE") == (EXCHANGE_SESSIONS["NSE"].no_new_entries_after, EXCHANGE_SESSIONS["NSE"].square_off_at)
    assert intraday_cutoffs("CRYPTO") == (None, None)


def _mcx_deploy(t, symbol="CRUDEOIL", status="ACTIVE") -> int:
    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(tenant_id=t["tenant_id"], strategy_id=STRATEGY, symbol=symbol, exchange="MCX", timeframe="1min",
                                           mode="PAPER", broker_name="upstox", status=status, created_by=t["user_id"])
            session.add(dep)
            await session.commit()
            return dep.id
    return _run(go())


def test_worker_trades_mcx_in_the_evening_and_leaves_nse_deployments_alone(monkeypatch):
    t = _tenant("o2-mcx@example.com")
    nse_id = _deploy(t)
    mcx_id = _mcx_deploy(t)
    _force_signal(monkeypatch, lambda: _signal(ts=FRIDAY_2000 - timedelta(minutes=1)))
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    report = _run(worker.run_cycle(now=FRIDAY_2000))
    assert report.market_open and report.open_exchanges == ["MCX"] and "NSE: closed" in report.session_reason
    assert report.deployments_evaluated == 1
    assert _get(StrategyDeploymentRecord, mcx_id).last_evaluated_at is not None
    assert _get(StrategyDeploymentRecord, nse_id).last_evaluated_at is None


def test_worker_with_only_nse_deployments_is_closed_in_the_evening(monkeypatch):
    t = _tenant("o2-nse-only@example.com")
    _deploy(t)
    worker = _worker(monkeypatch, _FakeBroker())
    report = _run(worker.run_cycle(now=FRIDAY_2000))
    assert not report.market_open and report.open_exchanges == [] and "closed for the day" in report.session_reason


def test_square_off_is_per_venue(monkeypatch):
    t = _tenant("o2-squareoff@example.com")
    _mcx_deploy(t)   # so the MCX clock is considered
    now = datetime.now(timezone.utc)

    async def seed():
        async with _session_factory() as session:
            for ex, sym in (("MCX", "CRUDEOIL"), ("NSE", "RELIANCE")):
                session.add(TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id=STRATEGY, symbol=sym, exchange=ex,
                                        direction="LONG", entry_price=100.0, quantity=1, stop_loss=95.0, target1=110.0, target2=120.0,
                                        entry_time=now - timedelta(hours=2)))
            await session.commit()
    _run(seed())
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    report = _run(worker.run_cycle(now=FRIDAY_2320))
    trades = {tr.symbol: tr for tr in _trades(t["tenant_id"])}
    assert trades["CRUDEOIL"].exit_time is not None and "square-off" in (trades["CRUDEOIL"].exit_reason or "").lower()
    assert trades["RELIANCE"].exit_time is None      # NSE is closed: nothing to square off there tonight
    assert report.positions_closed == 1


def test_system_status_lists_exchange_sessions():
    body = client.get("/api/system/status").json()
    assert set(body["sessions"]) == {"NSE", "MCX", "CRYPTO"}
    assert body["sessions"]["CRYPTO"]["is_open"] is True
    assert {"is_open", "reason", "next_open"} <= set(body["sessions"]["NSE"])


# --- O3: Web Push ----------------------------------------------------------------------------------

def _client_keys():
    priv = ec.generate_private_key(ec.SECP256R1())
    p256dh = webpush.b64url(priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    auth = webpush.b64url(os.urandom(16))
    return priv, p256dh, auth


def test_rfc8291_encryption_round_trips_and_hides_plaintext():
    priv, p256dh, auth = _client_keys()
    body = webpush.encrypt_payload(b'{"title":"hello"}', p256dh, auth)
    assert b"hello" not in body and len(body) > 86
    assert webpush.decrypt_payload(body, priv, auth) == b'{"title":"hello"}'
    with pytest.raises(ValueError):
        webpush.encrypt_payload(b"x", "short", auth)


def test_vapid_header_is_a_valid_es256_jwt_for_the_push_origin():
    header = webpush.vapid_authorization("https://fcm.googleapis.com/fcm/send/abc123", now=1_800_000_000)
    assert header.startswith("vapid t=") and ", k=" in header
    token, key_b64 = header[len("vapid t="):].split(", k=")
    h, p, s = token.split(".")
    claims = json.loads(webpush.b64url_decode(p))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["exp"] == 1_800_000_000 + 12 * 3600 and claims["sub"].startswith("mailto:")
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64url_decode(key_b64))
    sig = webpush.b64url_decode(s)
    pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")), f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
    pair = webpush.generate_keypair()
    assert set(pair) == {"VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY"} and len(webpush.b64url_decode(pair["VAPID_PUBLIC_KEY"])) == 65


def _push_mock(statuses: List[int], seen: List[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(statuses.pop(0) if len(statuses) > 1 else statuses[0])
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_push_channel_devices_add_remove_and_deliver_with_pruning(monkeypatch):
    headers, me = _auth("o3-push@example.com")
    assert client.get("/api/alert-channels/push/public-key", headers=headers).json()["public_key"]
    _, p1, a1 = _client_keys()
    _, p2, a2 = _client_keys()
    r = _put(headers, "push", {"subscription": {"endpoint": "https://push.example/one", "p256dh": p1, "auth": a1, "label": "laptop"}})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["count"] == 1 and "p256dh" not in json.dumps(r.json()["config"])
    r = _put(headers, "push", {"subscription": {"endpoint": "https://push.example/two", "p256dh": p2, "auth": a2, "label": "phone"}})
    assert r.json()["config"]["count"] == 2 and [d["label"] for d in r.json()["config"]["devices"]] == ["laptop", "phone"]
    # Re-registering the same endpoint refreshes rather than duplicates
    r = _put(headers, "push", {"subscription": {"endpoint": "https://push.example/two", "p256dh": p2, "auth": a2, "label": "phone-2"}})
    assert r.json()["config"]["count"] == 2

    seen: List[httpx.Request] = []
    _run(notify(_run_session(), me["tenant_id"], NotificationType.SYSTEM_FAILURE, title="Boom", message="x", severity=NotificationSeverity.CRITICAL)) if False else None
    async def raise_alert():
        async with _session_factory() as session:
            await notify(session, me["tenant_id"], NotificationType.SYSTEM_FAILURE, title="Broker down", message="Upstox 5xx", severity=NotificationSeverity.CRITICAL)
    _run(raise_alert())
    # Device one is gone (410), device two accepts (201): delivery succeeds and one is pruned.
    sent, failed = _dispatch(me["tenant_id"], _push_mock([410, 201], seen))
    assert (sent, failed) == (1, 0) and len(seen) == 2
    req = seen[0]
    assert req.headers["Content-Encoding"] == "aes128gcm" and req.headers["Authorization"].startswith("vapid t=") and req.headers["TTL"] == "3600"
    assert b"Broker down" not in req.content
    listing = client.get("/api/alert-channels", headers=headers).json()
    push = next(c for c in listing if c["channel_type"] == "PUSH")
    assert push["config"]["count"] == 1 and push["config"]["devices"][0]["label"] == "phone-2"
    # Explicit removal from the UI
    r = _put(headers, "push", {"remove_endpoint": "https://push.example/two"})
    assert r.status_code == 400  # a channel needs at least one device - remove the channel instead
    assert client.delete("/api/alert-channels/push", headers=headers).status_code == 204


def _run_session():
    return None


def test_push_all_devices_gone_reports_a_clear_error():
    headers, me = _auth("o3-push-gone@example.com")
    _, p1, a1 = _client_keys()
    _put(headers, "push", {"subscription": {"endpoint": "https://push.example/dead", "p256dh": p1, "auth": a1}})

    async def raise_alert():
        async with _session_factory() as session:
            await notify(session, me["tenant_id"], NotificationType.SYSTEM_FAILURE, title="T", message="m", severity=NotificationSeverity.CRITICAL)
    _run(raise_alert())
    sent, failed = _dispatch(me["tenant_id"], _push_mock([410], []))
    assert (sent, failed) == (0, 1)
    row = client.get("/api/alert-channels", headers=headers).json()[0]
    assert "unsubscribed" in (row["last_error"] or "")


# --- O3: SMS -----------------------------------------------------------------------------------------

MSG91 = {"url": "https://control.msg91.com/api/v5/flow/", "headers": {"authkey": "SECRET-KEY"},
         "body_template": '{"template_id":"abc","recipients":[{"mobiles":"{to}","message":"{text}"}]}', "to_numbers": ["+91 98123 45678"]}


def test_sms_config_validation_and_rendering_escapes_json():
    cfg = parse_config("SMS", MSG91)
    assert isinstance(cfg, SmsConfig) and cfg.to_numbers == ["+919812345678"]
    with pytest.raises(ValueError):
        parse_config("SMS", {**MSG91, "url": "http://insecure.example/sms"})
    with pytest.raises(ValueError):
        parse_config("SMS", {**MSG91, "to_numbers": ["not-a-number"]})
    with pytest.raises(ValueError):
        parse_config("SMS", {**MSG91, "body_template": '{"x":1}'})

    class _N:
        title = 'Stop "hit"'; message = "Line1\nLine2"; severity = "CRITICAL"; event_type = "STOP_LOSS_HIT"; id = 1
        created_at = datetime.now(timezone.utc)
    body, _ = dispatcher.render_sms(cfg, _N(), "+919812345678")
    parsed = json.loads(body)          # quotes and newlines inside the alert did not break the JSON
    assert parsed["recipients"][0]["mobiles"] == "+919812345678" and 'Stop "hit"' in parsed["recipients"][0]["message"]


def test_sms_channel_end_to_end_and_secret_headers_are_write_only():
    headers, me = _auth("o3-sms@example.com")
    r = _put(headers, "sms", MSG91, min_severity="WARNING")
    assert r.status_code == 200, r.text
    assert r.json()["config"]["headers_set"] == ["authkey"] and "SECRET-KEY" not in r.text
    # Update without headers keeps the stored key; the listing still shows only the header name.
    r = _put(headers, "sms", {**MSG91, "headers": {}, "to_numbers": ["+919812345678", "+919999999999"]})
    assert r.json()["config"]["headers_set"] == ["authkey"] and len(r.json()["config"]["to_numbers"]) == 2

    seen: List[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"type": "success"})
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def raise_alert():
        async with _session_factory() as session:
            await notify(session, me["tenant_id"], NotificationType.DAILY_LOSS_LIMIT, title="Daily loss limit", message="-2.1%", severity=NotificationSeverity.CRITICAL)
    _run(raise_alert())
    assert _dispatch(me["tenant_id"], mock) == (1, 0)
    assert len(seen) == 2 and all(r.headers["authkey"] == "SECRET-KEY" for r in seen)
    assert json.loads(seen[0].content)["recipients"][0]["mobiles"] == "+919812345678"
    assert "Daily loss limit" in json.loads(seen[1].content)["recipients"][0]["message"]

    # A gateway error is surfaced, not swallowed
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401, text="bad key")))
    _run(raise_alert())
    assert _dispatch(me["tenant_id"], bad) == (0, 1)
    row = next(c for c in client.get("/api/alert-channels", headers=headers).json() if c["channel_type"] == "SMS")
    assert "HTTP 401" in row["last_error"]


def test_merge_push_semantics():
    existing = {"subscriptions": [{"endpoint": "https://a", "p256dh": "x", "auth": "y"}]}
    assert merge_push({"remove_endpoint": "https://a"}, existing) == {"subscriptions": []}
    assert len(merge_push({"subscription": {"endpoint": "https://b", "p256dh": "x", "auth": "y"}}, existing)["subscriptions"]) == 2
    assert merge_push({"subscriptions": []}, existing) == {"subscriptions": []}


# --- O4: chaos -------------------------------------------------------------------------------------

def test_chaos_redis_down_does_not_stop_the_single_worker(monkeypatch):
    from app.cache import client as cache_client

    def boom():
        raise ConnectionError("redis unreachable")
    monkeypatch.setattr(cache_client, "_get_client", boom)
    t = _tenant("o4-redis@example.com")
    dep_id = _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert not report.skipped_lock and report.deployments_evaluated == 1 and report.signals_executed == 1
    assert _get(StrategyDeploymentRecord, dep_id).last_error is None


def test_chaos_live_broker_exception_marks_tenant_uncertain_and_opens_nothing(monkeypatch):
    class _Exploding(_FakeBroker):
        async def place_order(self, order):
            raise ConnectionError("broker socket reset mid-order")

    t = _tenant("o4-broker@example.com")
    dep_id = _deploy(t, mode="LIVE")
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _Exploding(ltp=101.0))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0
    assert all(tr.exit_time is not None or tr.mode != "LIVE" for tr in _trades(t["tenant_id"]))
    tenant = _get(Tenant, t["tenant_id"])
    assert tenant.broker_uncertain_since is not None          # Phase G1 flag: no new LIVE entries until reconciled
    # Next cycle: the same signal is refused as an entry rather than retried blindly.
    again = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))
    assert again.signals_executed == 0
    assert "uncertain" in (_get(StrategyDeploymentRecord, dep_id).last_error or "").lower() or _get(StrategyDeploymentRecord, dep_id).last_error


def test_chaos_market_data_failure_is_recorded_not_fatal(monkeypatch):
    class _NoData(_FakeBroker):
        async def get_intraday_candles(self, symbol, exchange, interval):
            raise TimeoutError("candle API timed out")
        async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
            raise TimeoutError("candle API timed out")

    t = _tenant("o4-data@example.com")
    dep_id = _deploy(t)
    worker = _worker(monkeypatch, _NoData())
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.errors and "TimeoutError" in (_get(StrategyDeploymentRecord, dep_id).last_error or "")
    assert _get(StrategyDeploymentRecord, dep_id).consecutive_failures == 1


def test_chaos_alert_dispatch_failure_never_breaks_trading(monkeypatch):
    from app.workers import trading_worker as tw

    async def broken_dispatch(session, **kwargs):
        raise RuntimeError("outbox table locked")
    monkeypatch.setattr(tw, "dispatch_pending", broken_dispatch)
    t = _tenant("o4-dispatch@example.com")
    _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 1 and any("alert dispatch" in e for e in report.errors)


# --- O4: metrics -------------------------------------------------------------------------------------

def test_ai_and_billing_metrics_are_registered():
    body = metrics.render()[0].decode()
    for name in ("atp_ai_provider_calls", "atp_ai_proposals", "atp_ai_decisions", "atp_billing_payments", "atp_billing_transitions"):
        assert name in body, name


# --- O1: least-privilege roles (needs a superuser: CI's service container user is one) --------------

def _superuser_url():
    url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    if not url.startswith("postgresql") or not shutil.which("psql"):
        return None
    try:
        out = subprocess.run(["psql", url, "-qAtc", "select rolsuper from pg_roles where rolname = current_user"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip() == "t":
            return url
    except Exception:  # noqa: BLE001
        return None
    return None


SU_URL = _superuser_url()


@pytest.mark.skipif(SU_URL is None, reason="needs psql and a superuser at DATABASE_URL")
def test_db_roles_script_gives_app_role_dml_only(tmp_path):
    scratch = "atp_roles_test"
    base = SU_URL.rsplit("/", 1)[0]
    subprocess.run(["psql", SU_URL, "-qc", f"DROP DATABASE IF EXISTS {scratch}"], check=True, capture_output=True)
    subprocess.run(["psql", SU_URL, "-qc", f"CREATE DATABASE {scratch}"], check=True, capture_output=True)
    scratch_url = f"{base}/{scratch}"
    try:
        script = Path(__file__).resolve().parents[1] / "scripts" / "db_roles.sql"
        run = subprocess.run(["psql", scratch_url, "-v", "app_password=app-pw", "-v", "migrator_password=mig-pw", "-v", f"DBNAME={scratch}",
                              "-f", str(script)], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        host = urlparse(scratch_url).netloc.split("@")[-1]
        mig = f"postgresql://atp_migrator:mig-pw@{host}/{scratch}"
        app = f"postgresql://atp_app:app-pw@{host}/{scratch}"
        assert subprocess.run(["psql", mig, "-qc", "CREATE TABLE t_roles(id serial primary key, v text)"], capture_output=True, text=True).returncode == 0
        ok = subprocess.run(["psql", app, "-qAtc", "INSERT INTO t_roles(v) VALUES ('x') RETURNING id"], capture_output=True, text=True)
        assert ok.returncode == 0 and ok.stdout.strip() == "1", ok.stderr
        for ddl in ("CREATE TABLE t_forbidden(id int)", "DROP TABLE t_roles", "ALTER TABLE t_roles ADD COLUMN z int"):
            denied = subprocess.run(["psql", app, "-qc", ddl], capture_output=True, text=True)
            assert denied.returncode != 0 and ("permission denied" in denied.stderr or "must be owner" in denied.stderr), (ddl, denied.stderr)
    finally:
        subprocess.run(["psql", SU_URL, "-qc", f"DROP DATABASE IF EXISTS {scratch}"], capture_output=True)
        subprocess.run(["psql", SU_URL, "-qc", "DROP ROLE IF EXISTS atp_app"], capture_output=True)
        subprocess.run(["psql", SU_URL, "-qc", "DROP ROLE IF EXISTS atp_migrator"], capture_output=True)


def test_pitr_scripts_parse_and_document_their_contract():
    root = Path(__file__).resolve().parents[2] / "scripts" / "backup"
    for name in ("base_backup.sh", "pitr_restore.sh"):
        assert subprocess.run(["sh", "-n", str(root / name)], capture_output=True).returncode == 0
    text = (root / "pitr_restore.sh").read_text()
    assert "recovery_target_time" in text and "recovery.signal" in text and "refusing to overwrite" in text
