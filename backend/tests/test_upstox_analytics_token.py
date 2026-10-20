"""Upstox Analytics Token: the read-only probe, per-class routing, encryption at rest, never returned, expiry, warning."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from sqlalchemy import select

from app.brokers import upstox_analytics as ua
from app.brokers.models import BrokerCredentials
from app.brokers.token_lifecycle import data_is_usable, load_credentials
from app.brokers.upstox import UpstoxBroker
from app.db.models import BrokerCredentialRecord, NotificationRecord
from app.market_data.calendar import IST
from app.secrets_store.encryption import encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL
from tests.broker_fakes import KITE_KEY, UPSTOX_KEY, UPSTOX_KEY_2, UPSTOX_SECRET
from tests.test_auth_api import _register, _session_factory, client

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
ANALYTICS = "an4lytics-T0KEN-value-never-shown"
DAILY = "daily-login-token"


def _jwt(exp: datetime) -> str:
    return jwt.encode({"sub": "U1", "exp": int(exp.timestamp())}, "upstox-side-secret", algorithm="HS256")


class _FakeWs:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def _mock_client(statuses: dict, seen: list) -> httpx.AsyncClient:
    """A mocked Upstox: `statuses` maps a class to (http status, Upstox error code or None)."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        cls = ua.classify(request.method, request.url.path)
        status, code = statuses.get(cls, (200, None))
        if status >= 400:
            return httpx.Response(status, json={"status": "error", "errors": [{"errorCode": code, "message": "nope"}]})
        data = {"authorized_redirect_uri": "wss://wsfeeder-api.upstox.com/market-data-feeder/v3/upstox-developer-api/feeds?requestId=x&code=y"} if cls == ua.WEBSOCKET else {}
        return httpx.Response(200, json={"status": "success", "data": data})
    return httpx.AsyncClient(base_url=ua.API_ROOT, transport=httpx.MockTransport(handler))


# --- the probe -----------------------------------------------------------------------------------

def test_probe_records_works_refused_unverified_per_class_and_sends_only_gets(caplog):
    caplog.set_level(logging.DEBUG)
    seen: list = []
    ws = _FakeWs()
    statuses = {ua.INTRADAY: (403, "UDAPI1154"), ua.OPTION_CHAIN: (500, None), ua.QUOTES: (401, "UDAPI100050")}

    async def connector(url):
        assert url == "wss://wsfeeder-api.upstox.com/market-data-feeder/v3/upstox-developer-api/feeds?requestId=x&code=y"
        return ws

    results = asyncio.run(ua.probe(ANALYTICS, client=_mock_client(statuses, seen), ws_connector=connector, now=NOW))
    assert results[ua.HISTORICAL]["status"] == ua.WORKS
    assert results[ua.INTRADAY] == {"status": ua.REFUSED, "http_status": 403, "error_code": "UDAPI1154", "probed_at": NOW.isoformat()}
    assert results[ua.QUOTES]["status"] == ua.REFUSED and results[ua.QUOTES]["error_code"] == "UDAPI100050"
    assert results[ua.OPTION_CHAIN]["status"] == ua.UNVERIFIED and results[ua.OPTION_CHAIN]["http_status"] == 500
    assert results[ua.WEBSOCKET]["status"] == ua.WORKS and ws.closed
    # One GET per class, nothing else; the token rides in the header only.
    assert sorted(ua.classify(r.method, r.url.path) for r in seen) == sorted(ua.CLASSES)
    assert {r.method for r in seen} == {"GET"}
    assert all(ANALYTICS not in str(r.url) for r in seen)
    assert ANALYTICS not in caplog.text
    assert ANALYTICS not in json.dumps(results)


def test_probe_websocket_handshake_refusal_and_network_failure():
    class _Refused(Exception):
        def __init__(self):
            super().__init__("handshake")
            self.response = type("R", (), {"status_code": 403})()

    async def refused(url):
        raise _Refused()

    async def broken(url):
        raise OSError("unreachable")

    out = asyncio.run(ua.probe(ANALYTICS, classes=[ua.WEBSOCKET], client=_mock_client({}, []), ws_connector=refused, now=NOW))
    assert out[ua.WEBSOCKET]["status"] == ua.REFUSED
    out = asyncio.run(ua.probe(ANALYTICS, classes=[ua.WEBSOCKET], client=_mock_client({}, []), ws_connector=broken, now=NOW))
    assert out[ua.WEBSOCKET]["status"] == ua.UNVERIFIED

    def boom(request):
        raise httpx.ConnectError("dns")
    out = asyncio.run(ua.probe(ANALYTICS, classes=[ua.QUOTES], now=NOW,
                               client=httpx.AsyncClient(base_url=ua.API_ROOT, transport=httpx.MockTransport(boom))))
    assert out[ua.QUOTES]["status"] == ua.UNVERIFIED and out[ua.QUOTES]["http_status"] is None


# --- expiry ----------------------------------------------------------------------------------------

def test_expiry_from_jwt_exp_claim_else_save_date_plus_a_year():
    exp = datetime(2027, 10, 1, 12, 0, tzinfo=timezone.utc)
    assert ua.token_expiry(_jwt(exp), NOW) == (exp, False)
    assert ua.token_expiry("opaque-token", NOW) == (NOW + timedelta(days=365), True)
    no_exp = jwt.encode({"sub": "U1"}, "k", algorithm="HS256")
    assert ua.token_expiry(no_exp, NOW) == (NOW + timedelta(days=365), True)
    meta = ua.new_meta(_jwt(exp), NOW)
    assert meta["expires_at"] == exp.isoformat() and meta["expiry_estimated"] is False
    assert all(v == {"status": ua.UNVERIFIED} for v in meta["coverage"].values())


# --- routing --------------------------------------------------------------------------------------

def _creds(coverage: dict, expires: datetime, **kw) -> BrokerCredentials:
    meta = ua.new_meta(ANALYTICS, NOW)
    meta["expires_at"] = expires.isoformat()
    meta["coverage"].update({c: {"status": s} for c, s in coverage.items()})
    return BrokerCredentials(api_key="k", access_token=kw.get("access_token", DAILY), analytics_token=ANALYTICS, analytics_meta=meta)


def _recording_adapter(creds: BrokerCredentials, refuse: tuple = ()):
    used: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        used.append((request.method, ua.classify(request.method, request.url.path) or request.url.path, token))
        if token == ANALYTICS and ua.classify(request.method, request.url.path) in refuse:
            return httpx.Response(401, json={"status": "error", "errors": [{"errorCode": "UDAPI100050"}]})
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "1"}})
    adapter = UpstoxBroker(creds, client=httpx.AsyncClient(base_url=UpstoxBroker.BASE_URL, transport=httpx.MockTransport(handler)))
    return adapter, used


def test_analytics_token_only_for_classes_that_work_daily_token_elsewhere():
    creds = _creds({ua.HISTORICAL: ua.WORKS, ua.INTRADAY: ua.WORKS, ua.QUOTES: ua.REFUSED}, datetime(2099, 1, 1, tzinfo=timezone.utc))
    adapter, used = _recording_adapter(creds)

    async def go():
        await adapter._request("GET", "/historical-candle/NSE_EQ|X/day/2026-10-19/2026-10-15")
        await adapter._request("GET", "/historical-candle/intraday/NSE_EQ|X/1minute")
        await adapter._request("GET", "/market-quote/ltp", params={"instrument_key": "NSE_EQ|X"})
        await adapter._request("GET", "/option/chain", params={"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": "2026-10-27"})
        await adapter._request("POST", "/order/place", json={})
        await adapter._request("GET", "/user/profile")
    asyncio.run(go())
    tokens = {cls: token for _, cls, token in used}
    assert tokens[ua.HISTORICAL] == ANALYTICS and tokens[ua.INTRADAY] == ANALYTICS
    assert tokens[ua.QUOTES] == DAILY                     # refused by the probe
    assert tokens[ua.OPTION_CHAIN] == DAILY               # unverified
    assert tokens["/v2/order/place"] == DAILY and tokens["/v2/user/profile"] == DAILY


def test_expired_analytics_token_is_not_used():
    creds = _creds({c: ua.WORKS for c in ua.CLASSES}, datetime.now(timezone.utc) - timedelta(minutes=1))
    adapter, used = _recording_adapter(creds)
    asyncio.run(adapter._request("GET", "/market-quote/ltp", params={"instrument_key": "NSE_EQ|X"}))
    assert used[-1][2] == DAILY
    assert ua.working_classes(creds) == set()


def test_refusal_on_a_real_call_falls_back_and_asks_for_a_reprobe():
    creds = _creds({ua.QUOTES: ua.WORKS}, datetime(2099, 1, 1, tzinfo=timezone.utc))
    adapter, used = _recording_adapter(creds, refuse=(ua.QUOTES,))
    asked: list = []
    adapter.on_analytics_refusal = asked.append

    async def go():
        await adapter._request("GET", "/market-quote/ltp", params={"instrument_key": "NSE_EQ|X"})
        await adapter._request("GET", "/market-quote/ltp", params={"instrument_key": "NSE_EQ|X"})
    asyncio.run(go())
    assert [t for _, _, t in used] == [ANALYTICS, DAILY, DAILY]   # refused once, then the daily token only
    assert asked == [ua.QUOTES]


def test_websocket_authorize_uses_analytics_token_when_it_works():
    from app.market_data.stream import UpstoxTickStream
    creds = _creds({ua.WEBSOCKET: ua.WORKS}, datetime(2099, 1, 1, tzinfo=timezone.utc))
    adapter, used = _recording_adapter(creds)
    asyncio.run(adapter._request("GET", UpstoxTickStream.AUTHORIZE_URL))
    assert used[-1][1:] == (ua.WEBSOCKET, ANALYTICS)


# --- storage, API views, warning ---------------------------------------------------------------------

def _tenant_of(token: str) -> int:
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]


@pytest.fixture
def mocked_upstox(monkeypatch):
    seen: list = []
    monkeypatch.setattr(ua, "_client", lambda: _mock_client({ua.OPTION_CHAIN: (403, "UDAPI1154")}, seen))

    async def fake_ws(url):
        return _FakeWs()
    monkeypatch.setattr(ua, "_default_ws_connect", fake_ws)
    return seen


async def _record(tenant_id: int, broker: str = "upstox") -> BrokerCredentialRecord:
    async with _session_factory() as session:
        return await session.scalar(select(BrokerCredentialRecord).where(
            BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == broker))


class _Report:
    ok, summary = True, "fake"

    def as_dict(self):
        return {"ok": True, "steps": []}


def test_saved_token_is_encrypted_probed_and_never_returned_or_logged(mocked_upstox, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)

    async def fake_smoke(adapter, account_label="primary"):
        return _Report()
    monkeypatch.setattr("app.brokers.routes.run_smoke", fake_smoke)
    bearer = _register("at-store@example.com")
    headers = {"Authorization": f"Bearer {bearer}"}
    tenant_id = _tenant_of(bearer)
    exp = datetime(2027, 9, 30, 18, 30, tzinfo=timezone.utc)
    token = _jwt(exp)
    sent = {"api_key": UPSTOX_KEY, "api_secret": UPSTOX_SECRET, "analytics_token": token,
            "analytics_meta": {"coverage": {c: {"status": "works"} for c in ua.CLASSES}}}   # client metadata is dropped
    response = client.post("/api/broker/upstox/credentials", headers=headers, json=sent)
    assert response.status_code == 204, response.text

    record = asyncio.run(_record(tenant_id))
    assert token not in record.encrypted_payload and "analytics" not in record.encrypted_payload
    stored = load_credentials(record)
    assert stored.analytics_token == token
    assert stored.analytics_meta["expires_at"] == exp.isoformat()
    coverage = stored.analytics_meta["coverage"]
    assert coverage[ua.OPTION_CHAIN]["status"] == ua.REFUSED and coverage[ua.HISTORICAL]["status"] == ua.WORKS
    assert {r.method for r in mocked_upstox} == {"GET"} and len(mocked_upstox) == len(ua.CLASSES)

    bodies = [client.get("/api/broker/token-status", headers=headers), client.get("/api/broker/credentials", headers=headers),
              client.post("/api/broker/upstox/analytics-token/check", headers=headers),
              client.post("/api/broker/upstox/smoke-test", headers=headers)]
    for body in bodies:
        assert body.status_code == 200, body.text
        assert token not in body.text and ANALYTICS not in body.text
    status = next(r for r in bodies[0].json() if r["broker_name"] == "upstox")
    assert status["analytics_token_set"] is True
    assert status["analytics_token_expires_at"] == exp.isoformat() and status["analytics_token_expiry_estimated"] is False
    assert status["analytics_coverage"][ua.QUOTES]["status"] == ua.WORKS
    listing = next(r for r in bodies[1].json() if r["broker_name"] == "upstox")
    assert listing["analytics_token_set"] is True and listing["analytics_token_expires_at"] == exp.isoformat()
    assert bodies[2].json()["analytics_coverage"][ua.OPTION_CHAIN]["status"] == ua.REFUSED
    assert bodies[3].json()["analytics_token"]["analytics_token_set"] is True
    assert token not in caplog.text


def test_saving_only_the_analytics_token_keeps_the_daily_session_and_other_brokers_refuse_it(mocked_upstox):
    bearer = _register("at-only@example.com")
    headers = {"Authorization": f"Bearer {bearer}"}
    tenant_id = _tenant_of(bearer)
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": UPSTOX_KEY, "access_token": DAILY}).status_code == 204

    async def mark_valid():
        async with _session_factory() as session:
            row = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id))
            row.token_status = "VALID"
            await session.commit()
    asyncio.run(mark_valid())
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"analytics_token": "opaque-one"}).status_code == 204
    record = asyncio.run(_record(tenant_id))
    assert record.token_status == "VALID"
    creds = load_credentials(record)
    assert creds.access_token == DAILY and creds.analytics_token == "opaque-one" and creds.analytics_meta["expiry_estimated"] is True

    bad = client.post("/api/broker/zerodha/credentials", headers=headers, json={"api_key": KITE_KEY, "analytics_token": "x"})
    assert bad.status_code == 400

    assert client.delete("/api/broker/upstox/analytics-token", headers=headers).status_code == 204
    creds = load_credentials(asyncio.run(_record(tenant_id)))
    assert creds.analytics_token is None and creds.analytics_meta is None and creds.access_token == DAILY


def test_data_gate_accepts_an_analytics_token_that_works_until_it_expires():
    def row(creds: BrokerCredentials) -> BrokerCredentialRecord:
        return BrokerCredentialRecord(tenant_id=1, user_id=1, broker_name="upstox", token_status="EXPIRED",
                                      encrypted_payload=encrypt_text(creds.model_dump_json(), 1, PURPOSE_BROKER_CREDENTIAL))
    live = _creds({ua.HISTORICAL: ua.WORKS}, datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert data_is_usable(row(live))
    assert not data_is_usable(row(_creds({ua.HISTORICAL: ua.WORKS}, NOW - timedelta(days=1))))
    assert not data_is_usable(row(_creds({ua.WEBSOCKET: ua.WORKS}, datetime(2099, 1, 1, tzinfo=timezone.utc))))
    assert not data_is_usable(row(_creds({}, datetime(2099, 1, 1, tzinfo=timezone.utc))))


def test_reprobe_marks_the_class_refused_on_the_stored_row(monkeypatch):
    tenant_id = _tenant_of(_register("at-reprobe@example.com"))
    creds = _creds({ua.QUOTES: ua.WORKS}, datetime(2099, 1, 1, tzinfo=timezone.utc))

    async def go():
        async with _session_factory() as session:
            row = BrokerCredentialRecord(tenant_id=tenant_id, user_id=1, broker_name="upstox", account_label="reprobe",
                                         encrypted_payload=encrypt_text(creds.model_dump_json(), tenant_id, PURPOSE_BROKER_CREDENTIAL))
            session.add(row)
            await session.commit()
            row_id = row.id
        other = await ua.reprobe_class(row_id, tenant_id + 999, ua.QUOTES, session_factory=_session_factory)
        assert other is None                       # another tenant's id never touches this row
        view = await ua.reprobe_class(row_id, tenant_id, ua.QUOTES, session_factory=_session_factory,
                                      client=_mock_client({ua.QUOTES: (401, "UDAPI100050")}, []))
        async with _session_factory() as session:
            stored = load_credentials(await session.get(BrokerCredentialRecord, row_id))
        return view, stored
    view, stored = asyncio.run(go())
    assert view["analytics_coverage"][ua.QUOTES]["status"] == ua.REFUSED
    assert stored.analytics_meta["coverage"][ua.QUOTES]["status"] == ua.REFUSED
    assert ua.QUOTES not in ua.working_classes(stored)


def test_expiry_warning_once_a_week_before_and_once_on_the_last_day():
    bearer = _register("at-warn@example.com")
    tenant_id = _tenant_of(bearer)
    user_id = client.get("/api/auth/me", headers={"Authorization": f"Bearer {bearer}"}).json()["id"]
    expiry = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)      # 20 Nov IST
    creds = _creds({ua.HISTORICAL: ua.WORKS}, expiry)

    async def go():
        async with _session_factory() as session:
            session.add(BrokerCredentialRecord(tenant_id=tenant_id, user_id=user_id, broker_name="upstox", account_label="warn",
                                               encrypted_payload=encrypt_text(creds.model_dump_json(), tenant_id, PURPOSE_BROKER_CREDENTIAL)))
            await session.commit()
        counts = []
        for day in (10, 13, 13, 16, 20, 20, 21):
            at = datetime(2026, 11, day, 9, 0, tzinfo=IST)
            async with _session_factory() as session:
                counts.append(await ua.send_expiry_warnings(session, at))
        async with _session_factory() as session:
            notes = list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == tenant_id, NotificationRecord.title.startswith(ua.TITLE_PREFIX))))
        return counts, notes
    counts, notes = asyncio.run(go())
    assert counts == [0, 1, 0, 0, 1, 0, 0]
    assert [n.title for n in notes] == ["Upstox analytics token (warn) expires on 2026-11-20",
                                        "Upstox analytics token (warn) expires today (2026-11-20)"]
    assert all(n.user_id == user_id and n.severity == "WARNING" for n in notes)
    assert all(ANALYTICS not in (n.message + n.title + (n.metadata_json or "")) for n in notes)


# --- security review fixes ---------------------------------------------------------------------------

INJECTED = "X|/../../../portfolio/short-term-positions?a="


def test_l1_injected_instrument_key_is_refused_before_any_request():
    from app.brokers.exceptions import BrokerAPIError
    for creds in (_creds({c: ua.WORKS for c in ua.CLASSES}, datetime(2099, 1, 1, tzinfo=timezone.utc)),
                  BrokerCredentials(api_key="k", access_token=DAILY)):          # the daily token is protected too
        adapter, used = _recording_adapter(creds)
        for symbol in (INJECTED, "X|/../../order/retrieve-all", "NSE_EQ|X%2F..", "NSE_EQ|X\tY", "NSE_EQ|X#a", "NSE_EQ|X?y=1"):
            with pytest.raises(BrokerAPIError):
                asyncio.run(adapter.get_intraday_candles(symbol, "NSE", "1min"))
            with pytest.raises(BrokerAPIError):
                asyncio.run(adapter.get_ltp_for_symbol(symbol, "NSE"))
        assert used == []
    # Classification runs on the normalised path the client sends: traversal never reads as market data.
    assert ua.classify("GET", "/v2/historical-candle/intraday/" + INJECTED + "/1minute") is None
    assert ua.classify("GET", "/v2/historical-candle/X|/../../order/retrieve-all/a/b") is None
    assert ua.classify("GET", "/v2/historical-candle/NSE_INDEX|Nifty 50/day/2026-10-19/2026-10-15") == ua.HISTORICAL
    from app.brokers.upstox import safe_instrument_key
    assert safe_instrument_key("NSE_INDEX|Nifty 50") == "NSE_INDEX|Nifty 50"


def test_l1_adapter_never_sends_the_analytics_token_to_a_traversed_path():
    creds = _creds({c: ua.WORKS for c in ua.CLASSES}, datetime(2099, 1, 1, tzinfo=timezone.utc))
    adapter, used = _recording_adapter(creds)
    asyncio.run(adapter._request("GET", "/historical-candle/intraday/X|/../../../portfolio/short-term-positions"))
    assert used[-1][1:] == ("/v2/portfolio/short-term-positions", DAILY)


def test_l2_probe_deadline_leaves_unfinished_classes_unverified():
    async def handler(request: httpx.Request) -> httpx.Response:
        if ua.classify(request.method, request.url.path) == ua.QUOTES:
            await asyncio.sleep(5)
        return httpx.Response(200, json={"status": "success", "data": {}})
    slow = httpx.AsyncClient(base_url=ua.API_ROOT, transport=httpx.MockTransport(handler))
    out = asyncio.run(ua.probe(ANALYTICS, classes=[ua.HISTORICAL, ua.QUOTES], client=slow, now=NOW, deadline=0.3))
    assert out[ua.HISTORICAL]["status"] == ua.WORKS
    assert out[ua.QUOTES] == {"status": ua.UNVERIFIED, "http_status": None, "error_code": "timeout", "probed_at": NOW.isoformat()}


def test_l2_check_endpoint_is_rate_limited_per_user(mocked_upstox):
    from app.core import rate_limit
    rate_limit.reset("analytics_check")
    headers = {"Authorization": f"Bearer {_register('at-rate@example.com')}"}
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": UPSTOX_KEY, "analytics_token": "opaque"}).status_code == 204
    codes = [client.post("/api/broker/upstox/analytics-token/check", headers=headers).status_code for _ in range(7)]
    rate_limit.reset("analytics_check")
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_l3_saving_only_the_analytics_token_keeps_the_holder(mocked_upstox):
    bearer = _register("at-holder@example.com")
    headers = {"Authorization": f"Bearer {bearer}"}
    tenant_id = _tenant_of(bearer)
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": UPSTOX_KEY, "access_token": DAILY}).status_code == 204

    async def set_holder(user_id):
        async with _session_factory() as session:
            row = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id))
            row.user_id = user_id
            await session.commit()
    asyncio.run(set_holder(987654))                                   # another member linked this connection
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"analytics_token": "opaque-two"}).status_code == 204
    assert asyncio.run(_record(tenant_id)).user_id == 987654
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"api_key": UPSTOX_KEY_2}).status_code == 204
    assert asyncio.run(_record(tenant_id)).user_id != 987654          # a session-field save still makes the saver the holder


def test_l6_gate_needs_a_working_candle_class():
    def row(creds: BrokerCredentials) -> BrokerCredentialRecord:
        return BrokerCredentialRecord(tenant_id=1, user_id=1, broker_name="upstox", token_status="EXPIRED",
                                      encrypted_payload=encrypt_text(creds.model_dump_json(), 1, PURPOSE_BROKER_CREDENTIAL))
    far = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert not data_is_usable(row(_creds({ua.QUOTES: ua.WORKS, ua.OPTION_CHAIN: ua.WORKS}, far)))
    assert data_is_usable(row(_creds({ua.INTRADAY: ua.WORKS}, far)))
    assert data_is_usable(row(_creds({ua.HISTORICAL: ua.WORKS}, far)))


def test_l4_worker_retries_a_failed_warning_run_the_same_day(monkeypatch):
    from tests.test_trading_worker import _FakeBroker, _worker
    worker = _worker(monkeypatch, _FakeBroker())
    calls: list = []

    async def flaky(session, now):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("db hiccup")
        return 0
    monkeypatch.setattr(ua, "send_expiry_warnings", flaky)
    start = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)
    first = asyncio.run(worker.run_cycle(start))
    assert any("analytics token expiry" in e for e in first.errors)
    asyncio.run(worker.run_cycle(start + timedelta(minutes=1)))      # within the retry spacing: not again yet
    asyncio.run(worker.run_cycle(start + timedelta(minutes=16)))     # retried, succeeds
    asyncio.run(worker.run_cycle(start + timedelta(minutes=40)))     # done for the day
    assert len(calls) == 2


def test_l5_expiry_warning_is_private_to_its_addressee():
    from app.notifications.routes import private_to_addressee
    tenant_id = _tenant_of(_register("at-private@example.com"))
    creds = _creds({ua.HISTORICAL: ua.WORKS}, datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc))

    async def go():
        async with _session_factory() as session:
            session.add(BrokerCredentialRecord(tenant_id=tenant_id, user_id=4242, broker_name="upstox", account_label="priv",
                                               encrypted_payload=encrypt_text(creds.model_dump_json(), tenant_id, PURPOSE_BROKER_CREDENTIAL)))
            await session.commit()
        async with _session_factory() as session:
            assert await ua.send_expiry_warnings(session, datetime(2026, 11, 28, 9, 0, tzinfo=IST)) == 1
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord.user_id).where(
                NotificationRecord.tenant_id == tenant_id, private_to_addressee())))
    assert asyncio.run(go()) == [4242]


def test_i1_probe_connects_only_to_an_upstox_wss_feed():
    connected: list = []

    async def connector(url):
        connected.append(url)
        return _FakeWs()

    def feed(url):
        def handler(request):
            return httpx.Response(200, json={"status": "success", "data": {"authorized_redirect_uri": url}})
        return httpx.AsyncClient(base_url=ua.API_ROOT, transport=httpx.MockTransport(handler))

    for bad in ("wss://evil.example/feed", "ws://wsfeeder-api.upstox.com/feed", "wss://upstox.com.evil.example/x",
                "https://wsfeeder-api.upstox.com/feed"):
        out = asyncio.run(ua.probe(ANALYTICS, classes=[ua.WEBSOCKET], client=feed(bad), ws_connector=connector, now=NOW))
        assert out[ua.WEBSOCKET]["status"] == ua.UNVERIFIED and out[ua.WEBSOCKET]["error_code"] == "unexpected_feed_url"
    assert connected == []
    good = "wss://wsfeeder-api.upstox.com/market-data-feeder/v3/upstox-developer-api/feeds?code=1"
    assert asyncio.run(ua.probe(ANALYTICS, classes=[ua.WEBSOCKET], client=feed(good), ws_connector=connector, now=NOW))[ua.WEBSOCKET]["status"] == ua.WORKS
    assert connected == [good]


# --- re-check hardening (R1-R3) ---------------------------------------------------------------------

def test_r1_instrument_key_ascii_allowlist():
    from app.brokers.exceptions import BrokerAPIError
    from app.brokers.upstox import safe_instrument_key
    real = ("NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank", "NSE_INDEX|NIFTY MID SELECT", "NSE_FO|56789",
            "BSE_EQ|INE467B01029", "MCX_FO|432293", "NSE_EQ|M&M", "NSE_EQ|BAJAJ-AUTO", "BSE_INDEX|SENSEX", "1minute", "day")
    for key in real:
        assert safe_instrument_key(key) == key
    bad = ("NSE_EQ|X\uff0e\uff0e\uff0fportfolio", "NSE_EQ|X\u2024\u2024", "NSE_EQ|X\u200bY", "NSE_EQ|X;Y", "NSE_EQ|X..Y",
           "NSE_EQ|X/Y", "NSE_EQ|X%2F", "NSE_EQ|X?a", "NSE_EQ|X#a", " NSE_EQ|X", "NSE_EQ|X ", "NSE_EQ|X  Y", "NSE_EQ|X\tY",
           "NSE_EQ|\u00c4", "")
    for key in bad:
        with pytest.raises(BrokerAPIError):
            safe_instrument_key(key)
    adapter, used = _recording_adapter(_creds({c: ua.WORKS for c in ua.CLASSES}, datetime(2099, 1, 1, tzinfo=timezone.utc)))
    for key in bad[:4]:
        with pytest.raises(BrokerAPIError):
            asyncio.run(adapter.get_intraday_candles(key, "NSE", "1min"))
    assert used == []


def test_r2_raw_path_with_escapes_gets_no_analytics_class():
    assert ua.classify("GET", "/v2/historical-candle/intraday/A/B%3F%2F..%2F..%2Fportfolio%2Fshort-term-positions") is None
    assert ua.classify("GET", "/v2/historical-candle/intraday/NSE_EQ%7CX/1minute") is None
    # The encoded space inside an index key is the one escape that stays market data.
    assert ua.classify("GET", "/v2/historical-candle/NSE_INDEX|Nifty%2050/day/2026-10-19/2026-10-15") == ua.HISTORICAL
    creds = _creds({c: ua.WORKS for c in ua.CLASSES}, datetime(2099, 1, 1, tzinfo=timezone.utc))
    adapter, used = _recording_adapter(creds)
    asyncio.run(adapter._request("GET", "/historical-candle/intraday/A/B%3F%2F..%2F..%2Fportfolio%2Fshort-term-positions"))
    assert used[-1][2] == DAILY
    asyncio.run(adapter._request("GET", "/historical-candle/NSE_INDEX|Nifty 50/day/2026-10-19/2026-10-15"))
    assert used[-1][2] == ANALYTICS


def test_r3_only_the_upstox_api_host_gets_a_class():
    assert ua.classify("GET", "https://evil.example/v2/market-quote/ltp") is None
    assert ua.classify("GET", "http://api.upstox.com/v2/market-quote/ltp") is None
    assert ua.classify("GET", "https://api.upstox.com/v2/market-quote/ltp") == ua.QUOTES
    adapter, used = _recording_adapter(_creds({c: ua.WORKS for c in ua.CLASSES}, datetime(2099, 1, 1, tzinfo=timezone.utc)))
    assert adapter.analytics_class_for("GET", "https://evil.example/v2/market-quote/ltp") is None
    assert adapter.analytics_class_for("GET", "https://api.upstox.com.evil.example/v2/market-quote/ltp") is None
    assert adapter.analytics_class_for("GET", "/market-quote/ltp") == ua.QUOTES
