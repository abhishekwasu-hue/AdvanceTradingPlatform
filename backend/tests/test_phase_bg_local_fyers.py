"""Phase BG: the local-PC PAPER week on Fyers.

The Fyers adapter was verified against a mocked transport only; this is the end-to-end path the
operator runs every morning, over the real seams: credentials stored encrypted -> the daily
paste-the-code login (yesterday's token must not win over today's code) -> `build_adapter`
(Fyers behind the contract-symbol translator) -> the read-only smoke test with the option-chain
step -> `first_paper_day_check` reporting it. Plus the symbol-master parsing that the whole
option path depends on, against both documented column layouts."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from app.brokers import routes as broker_routes
from app.brokers.fyers import FyersBroker, _contract_fields, _exchange_of
from app.brokers.models import BrokerCredentials
from app.brokers.smoke import run_smoke
from app.brokers.token_lifecycle import build_adapter, load_credentials
from app.db.models import BrokerCredentialRecord, Tenant, User
from app.platform import first_day
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _me

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 8, 10, 30, tzinfo=IST)            # a Thursday, market open
EXPIRY_EPOCH = int(datetime(2026, 10, 13, 15, 30, tzinfo=IST).timestamp())
NEXT_EPOCH = int(datetime(2026, 10, 20, 15, 30, tzinfo=IST).timestamp())
# 17-column layout (as the adapter was first written against): ..., scrip code, underlying, strike, option type, underlying fytoken
CM_CSV = "\n".join([
    "101000000003045,STATE BANK OF INDIA,0,1,0.05,INE062A01020,0915-1530,2026-10-07,0,NSE:SBIN-EQ,10,10,3045,,,,,",
    "101000000026000,NIFTY 50,0,1,0.05,,0915-1530,2026-10-07,0,NSE:NIFTY50-INDEX,10,10,26000,,,,,",
])
FO_CSV_17 = "\n".join([
    f"101100000012345,NIFTY 13 OCT 26 26000 CE,14,75,0.05,,0915-1530,2026-10-07,{EXPIRY_EPOCH},NSE:NIFTY2610326000CE,10,11,12345,NIFTY,26000,CE,101000000026000,",
    f"101100000012346,NIFTY 13 OCT 26 26000 PE,14,75,0.05,,0915-1530,2026-10-07,{EXPIRY_EPOCH},NSE:NIFTY2610326000PE,10,11,12346,NIFTY,26000,PE,101000000026000,",
    f"101100000012347,NIFTY 20 OCT 26 26100 CE,14,75,0.05,,0915-1530,2026-10-07,{NEXT_EPOCH},NSE:NIFTY2612026100CE,10,11,12347,NIFTY,26100,CE,101000000026000,",
    f"101100000012348,NIFTY 27 OCT 26 FUT,11,75,0.05,,0915-1530,2026-10-07,{NEXT_EPOCH},NSE:NIFTY26OCTFUT,10,11,12348,NIFTY,,,101000000026000,",
])
# 18-column layout (the docs' current ordering): ..., scrip code, underlying symbol, underlying scrip code, strike, option type, ...
FO_CSV_18 = "\n".join([
    f"101100000012345,NIFTY 13 OCT 26 26000 CE,14,75,0.05,,0915-1530,2026-10-07,{EXPIRY_EPOCH},NSE:NIFTY2610326000CE,10,11,12345,NIFTY,26000,26000,CE,101000000026000,",
    f"101100000012346,NIFTY 13 OCT 26 26000 PE,14,75,0.05,,0915-1530,2026-10-07,{EXPIRY_EPOCH},NSE:NIFTY2610326000PE,10,11,12346,NIFTY,26000,26000,PE,101000000026000,",
    f"101100000012349,BANKNIFTY 13 OCT 26 58500 CE,14,35,0.05,,0915-1530,2026-10-07,{EXPIRY_EPOCH},NSE:BANKNIFTY2610358500CE,10,11,12349,BANKNIFTY,26009,58500,CE,101000000026009,",
    f"101100000012350,BAJAJ-AUTO 27 OCT 26 FUT,11,75,0.05,,0915-1530,2026-10-07,{NEXT_EPOCH},NSE:BAJAJ-AUTO26OCTFUT,10,11,12350,BAJAJ-AUTO,16669,,,101000000016669,",
])


def _ok(**data):
    return httpx.Response(200, json={"s": "ok", "code": 200, "message": "", **data})


class _Fyers:
    """A Fyers-shaped server: auth-code exchange, profile, funds, quotes, chain, positions, orders; nothing else."""
    def __init__(self, fo_csv=FO_CSV_17, token="tok-today", code="auth-code-today"):
        self.fo_csv, self.token, self.code = fo_csv, token, code
        self.exchanges, self.calls = [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, url = request.url.path, str(request.url)
        self.calls.append((request.method, path))
        if "public.fyers.in" in url:
            return httpx.Response(200, text=CM_CSV if "NSE_CM" in url else self.fo_csv)
        if path.endswith("/validate-authcode"):
            body = json.loads(request.content)
            self.exchanges.append(body["code"])
            if body["code"] != self.code:
                return httpx.Response(200, json={"s": "error", "code": -413, "message": "Invalid auth code"})
            assert body["appIdHash"] == hashlib.sha256(b"APP-100:secret-1").hexdigest()
            return _ok(access_token=self.token, refresh_token="ref")
        if request.headers.get("Authorization") != f"APP-100:{self.token}":
            return httpx.Response(200, json={"s": "error", "code": -16, "message": "Could not authenticate the user"})
        if path.endswith("/profile"):
            return _ok(data={"fy_id": "XA12345", "name": "Test Trader"})
        if path.endswith("/funds"):
            return _ok(fund_limit=[{"id": 1, "equityAmount": 150000.5}, {"id": 2, "equityAmount": 30000}, {"id": 10, "equityAmount": 120000}])
        if path.endswith("/quotes"):
            symbols = parse_qs(request.url.query.decode())["symbols"][0].split(",")
            price = {"NSE:NIFTY50-INDEX": 26050.0, "NSE:NIFTY2610326000CE": 210.5, "NSE:SBIN-EQ": 812.5}
            now = int(datetime.now(timezone.utc).timestamp())
            return _ok(d=[{"n": s, "s": "ok", "v": {"lp": price.get(s, 100.0), "open_price": 1, "high_price": 1, "low_price": 1, "prev_close_price": 1, "volume": 1, "tt": now}} for s in symbols])
        if path.endswith("/options-chain-v3"):
            return _ok(data={"expiryData": [{"date": "13-10-2026", "expiry": str(EXPIRY_EPOCH)}],
                             "optionsChain": [{"symbol": "NSE:NIFTY50-INDEX", "option_type": "", "strike_price": -1, "ltp": 26050.0},
                                              {"symbol": "NSE:NIFTY2610326000CE", "option_type": "CE", "strike_price": 26000, "ltp": 210.5, "oi": 5000},
                                              {"symbol": "NSE:NIFTY2610326000PE", "option_type": "PE", "strike_price": 26000, "ltp": 180.0, "oi": 7000},
                                              {"symbol": "NSE:NIFTY2610326100CE", "option_type": "CE", "strike_price": 26100, "ltp": 160.0, "oi": 3000}]})
        if path.endswith("/positions"):
            return _ok(netPositions=[{"symbol": "NSE:NIFTY2610326000CE", "netQty": 75, "netAvg": 200.0, "ltp": 210.5, "pl": 787.5, "productType": "INTRADAY"}])
        if path.endswith("/orders"):
            return _ok(orderBook=[])
        return httpx.Response(404, json={"s": "error", "code": -404, "message": f"no route {path}"})


def _run(coro):
    return asyncio.run(coro)


def _record(headers):
    tenant_id = _me(headers)["tenant_id"]

    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == "fyers"))
    return _run(go())


def test_symbol_master_parsing_survives_both_column_layouts():
    # Ticker first: the documented spelling carries strike and right; a weekly code (26103 = 2026, Oct 13) parses too.
    assert _contract_fields([], "NSE:NIFTY2610326000CE") == (26000.0, "CE", "NIFTY")
    assert _contract_fields([], "NSE:NIFTY26OCT26000PE") == (26000.0, "PE", "NIFTY")
    assert _contract_fields([], "NSE:BAJAJ-AUTO26OCTFUT") == (None, "", "BAJAJ-AUTO")
    assert _contract_fields([], "NSE:SBIN-EQ") == (None, "", None)
    row18 = FO_CSV_18.splitlines()[2].split(",")
    assert _contract_fields(row18, row18[9]) == (58500.0, "CE", "BANKNIFTY")           # not 26009, the index's scrip code
    row17 = FO_CSV_17.splitlines()[0].split(",")
    assert _contract_fields(row17, row17[9]) == (26000.0, "CE", "NIFTY")
    assert _exchange_of("NSE:NIFTY2610326000CE") == "NFO" and _exchange_of("NSE:SBIN-EQ") == "NSE" and _exchange_of("BSE:SENSEX2610382000PE") == "BFO"
    assert _exchange_of("NSE:SBIN-EQ", 11) == "NFO" and _exchange_of("NSE:NIFTY26OCTFUT", "10") == "NSE" and _exchange_of("MCX:CRUDEOIL26OCTFUT", 20) == "MCX"
    decimal = "x,SBIN 27 OCT 26 820.5 CE,14,750,0.05,,,,1,NSE:SBIN26OCT820.5CE,10,11,1,SBIN,3045,820.5,CE,".split(",")
    assert _contract_fields(decimal, decimal[9]) == (820.5, "CE", "SBIN")

    assert _contract_fields([], "NSE:360ONE26OCT1000CE") == (1000.0, "CE", "360ONE") and _exchange_of("NSE:360ONE26OCT1000CE") == "NFO"
    assert _contract_fields([], "NSE:NIFTYNXT5026OCT70000PE") == (70000.0, "PE", "NIFTYNXT50")
    sixteen = "x,ABCDE 27 OCT 26 100 CE,14,1,0.05,,,,1,NSE:ABCDE26OCT100CE,10,11,1,100,CE,".split(",")
    assert _contract_fields(sixteen, sixteen[9]) == (100.0, "CE", "ABCDE")              # a CE/PE cell is never the underlying
    for csv_text, fut_name in ((FO_CSV_17, "NIFTY"), (FO_CSV_18, "BAJAJ-AUTO")):
        server = _Fyers(fo_csv=csv_text)
        broker = FyersBroker(BrokerCredentials(api_key="APP-100", access_token="tok-today"), client=httpx.AsyncClient(transport=httpx.MockTransport(server)))
        rows = {i.tradingsymbol: i for i in _run(broker.get_instruments("NFO"))}
        ce = rows["NIFTY2610326000CE"]
        assert ce.strike == 26000.0 and ce.instrument_type == "CE" and ce.name == "NIFTY" and ce.expiry == "2026-10-13" and ce.lot_size == 75
        futs = [i for i in rows.values() if i.instrument_type == "FUT"]
        assert len(futs) == 1 and futs[0].strike is None and futs[0].name == fut_name
        assert all(i.instrument_type in ("CE", "PE", "FUT") for i in rows.values())


def test_daily_paste_the_code_login_replaces_yesterdays_token(monkeypatch):
    headers = _auth("bg-fyers-login@example.com")
    # Day 0: App ID, secret and the app's redirect URL are stored once; the login URL is built from them (no secret in it).
    assert client.post("/api/broker/fyers/credentials", headers=headers, json={"api_key": "APP-100", "api_secret": "secret-1"}).status_code == 204
    assert client.get("/api/broker/fyers/login-url", headers=headers).status_code == 400                           # redirect URI missing
    assert client.post("/api/broker/fyers/credentials", headers=headers, json={"redirect_uri": "https://trade.example.com/redirect"}).status_code == 204
    url = client.get("/api/broker/fyers/login-url", headers=headers).json()
    q = parse_qs(urlparse(url["authorization_url"]).query)
    assert url["authorization_url"].startswith("https://api-t1.fyers.in/api/v3/generate-authcode?") and url["code_param"] == "auth_code"
    assert q["client_id"] == ["APP-100"] and q["redirect_uri"] == ["https://trade.example.com/redirect"] and q["response_type"] == ["code"]
    assert "secret-1" not in url["authorization_url"]
    assert client.get("/api/broker/upstox/login-url", headers=headers).status_code == 400              # Upstox: the OAuth button, not a pasted code
    status = {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["fyers"]
    assert status["login_url_supported"] is True and status["code_param"] == "auth_code" and status["oauth_supported"] is False

    server = _Fyers(token="tok-day1", code="code-day1")
    monkeypatch.setattr(broker_routes, "get_broker_adapter", lambda name, creds, client=None: FyersBroker(creds, client=httpx.AsyncClient(transport=httpx.MockTransport(server))))
    # Day 1: the whole redirected address is accepted; the code is exchanged and the token stored encrypted.
    res = client.post("/api/broker/fyers/login-code", headers=headers, json={"code": "https://trade.example.com/redirect?s=ok&code=200&auth_code=code-day1&state=atp"})
    assert res.status_code == 200, res.text
    assert res.json()["token_status"] == "VALID" and res.json()["needs_login"] is False and server.exchanges == ["code-day1"]
    creds = load_credentials(_record(headers))
    assert creds.access_token == "tok-day1" and creds.request_token is None and creds.api_secret == "secret-1"

    # Day 2: yesterday's token is dead at the broker. Pasting today's code must exchange it, not retry the stale token.
    server.token, server.code = "tok-day2", "code-day2"
    res = client.post("/api/broker/fyers/login-code", headers=headers, json={"code": "  code-day2  "})
    assert res.status_code == 200, res.text
    assert server.exchanges == ["code-day1", "code-day2"] and load_credentials(_record(headers)).access_token == "tok-day2"
    # The same holds for the long way round (Store a Request Token, then Authenticate): the stored token is dropped.
    server.token, server.code = "tok-day3", "code-day3"
    assert client.post("/api/broker/fyers/credentials", headers=headers, json={"request_token": "code-day3"}).status_code == 204
    merged = load_credentials(_record(headers))
    assert merged.request_token == "code-day3" and merged.access_token is None and merged.api_key == "APP-100"
    assert client.post("/api/broker/fyers/authenticate", headers=headers).status_code == 200
    assert server.exchanges[-1] == "code-day3" and load_credentials(_record(headers)).access_token == "tok-day3"
    # A wrong / used code is a clean 502 and the session is marked EXPIRED, never a 500.
    res = client.post("/api/broker/fyers/login-code", headers=headers, json={"code": "code-used"})
    assert res.status_code == 502 and "Invalid auth code" in res.json()["detail"]
    assert {r["broker_name"]: r for r in client.get("/api/broker/token-status", headers=headers).json()}["fyers"]["token_status"] == "EXPIRED"
    assert client.post("/api/broker/fyers/login-code", headers=headers, json={"code": "   "}).status_code == 400
    # Odd pastes never 500: a stray "[" (urlparse raises), a code carried in the fragment, a bare JWT-looking code.
    assert client.post("/api/broker/fyers/login-code", headers=headers, json={"code": "https://[abc?auth_code=x"}).status_code == 502
    server.token, server.code = "tok-day4", "code-day4"
    assert client.post("/api/broker/fyers/login-code", headers=headers, json={"code": "https://trade.example.com/redirect#auth_code=code-day4&state=atp"}).status_code == 200
    assert broker_routes._code_from_paste("eyJhbGciOi.payload.sig", "auth_code") == "eyJhbGciOi.payload.sig"
    assert broker_routes._code_from_paste("?request_token=abc&action=login", "request_token") == "abc"


def test_first_day_check_runs_the_read_only_probes_over_a_stored_fyers_session():
    headers = _auth("bg-fyers-firstday@example.com")
    assert client.post("/api/broker/fyers/credentials", headers=headers, json={"api_key": "APP-100", "api_secret": "secret-1", "access_token": "tok-today"}).status_code == 204
    me = _me(headers)
    server = _Fyers()
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(server))

    async def mark_valid():
        async with _session_factory() as session:
            record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"], BrokerCredentialRecord.broker_name == "fyers"))
            record.token_status, record.token_expires_at = "VALID", NOW.astimezone(timezone.utc) + timedelta(hours=8)
            await session.commit()
    _run(mark_valid())

    # The smoke test alone, through build_adapter (Fyers behind the contract-symbol translator): 9 probes, no order.
    async def smoke():
        async with _session_factory() as session:
            record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"], BrokerCredentialRecord.broker_name == "fyers"))
            return await run_smoke(build_adapter(record, mock_client), account_label="primary", today=NOW.date())
    report = _run(smoke())
    by_name = {s.name: s for s in report.steps}
    assert report.ok and report.summary.startswith("9 ok, 0 failed, 0 skipped"), report.summary
    assert "fyers account XA12345" in by_name["profile"].detail and "available cash 120,000.00" in by_name["funds"].detail
    assert by_name["quote"].detail.startswith("NIFTY 50 26,050.00")
    assert "nearest NIFTY expiry 2026-10-13, probe NIFTY 26000 CE 13 OCT 26" in by_name["derivatives"].detail
    assert "NIFTY 26000 CE 13 OCT 26 premium 210.50 via the broker's own symbol" in by_name["contract_quote"].detail
    assert by_name["option_chain"].detail.startswith("2 strikes for NIFTY expiring 2026-10-13, 2 with a premium, underlying 26,050.00")
    assert by_name["positions"].detail == "1 open position(s): NIFTY 26000 CE 13 OCT 26 75"          # restored to the platform spelling
    assert by_name["orders"].detail == "0 order(s) in today's book"
    assert not any(m in ("POST", "PATCH", "DELETE") and "orders" in p for m, p in server.calls)        # read-only, as promised

    # The morning script reports that same probe.
    async def check():
        async with _session_factory() as session:
            tenant, user = await session.get(Tenant, me["tenant_id"]), await session.get(User, me["id"])
            return await first_day.run(session, tenant, user, now=NOW.astimezone(timezone.utc), adapter_factory=lambda record: build_adapter(record, mock_client))
    result = _run(check())
    smoke_checks = [c for c in result.checks if c.key.startswith("broker_smoke.")]
    assert len(smoke_checks) == 1 and smoke_checks[0].status == "ok" and "fyers (primary)" in smoke_checks[0].title and "9 ok" in smoke_checks[0].detail
    assert {c.key: c for c in result.checks}["paper_deployment"].status == "fail"                       # nothing deployed yet: the report says so

    # Without a usable session the fix line names the paste-the-code route, not only Upstox's button.
    other = _me(_auth("bg-fyers-nosession@example.com"))

    async def bare():
        async with _session_factory() as session:
            tenant, user = await session.get(Tenant, other["tenant_id"]), await session.get(User, other["id"])
            return await first_day.run(session, tenant, user, now=NOW.astimezone(timezone.utc))
    bare_report = _run(bare())
    assert "Fyers/Kite" in {c.key: c for c in bare_report.checks}["broker_smoke"].fix


def test_local_compose_overlay_binds_every_published_port_to_loopback():
    """Compose concatenates `ports` across files; without `!override` the base file's 0.0.0.0 binding would stay
    next to the loopback one and the overlay would protect nothing (docker-compose.prod.yml uses the same tag)."""
    import re
    from pathlib import Path
    text = Path(__file__).resolve().parents[2].joinpath("docker-compose.local.yml").read_text(encoding="utf-8")
    services = re.findall(r"^  (\w+):\n((?:    .*\n?)+)", text, flags=re.M)
    assert {name for name, _ in services} == {"postgres", "redis", "backend", "frontend"}
    for name, body in services:
        assert "ports: !override" in body, name
        entries = re.findall(r'^\s+- "([^"]+)"', body, flags=re.M)
        assert entries and all(e.startswith("127.0.0.1:") for e in entries), (name, entries)
        assert "restart: unless-stopped" in body, name
