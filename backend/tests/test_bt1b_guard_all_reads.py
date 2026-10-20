"""BT1b: with `byob_data_tier` on, every live read goes through the entitlement guard - not only the chart endpoints.

Covered (cross-user / cross-tenant, no network): the screener run and its alert rules, the Copilot's server evidence and
tools, the strategist's candles, market-memory refresh, portfolio exposure, open-position Greeks, the deployment
preview, the PAPER exit price lookup, the worker (no org stream / shared keys / pooled rows for a flag-on organisation,
its own DB session for the fan-in), broker-credential holder rules, badge staleness from the data's own time, and
`/sources` "usable". With the flag off each path behaves as before (asserted where it differs)."""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, List
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.brokers import token_lifecycle
from app.brokers.models import OptionChain, Quote
from app.core.models import OHLCVBar
from app.db.models import BrokerCredentialRecord, TradeRecord, User
from app.market_data import candles_routes, entitlement, live_access
from app.market_data.fanin import SessionRef, SessionSymbols, UserSessionFanIn
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_market_data_service import _MemoryCache, _install_cache
from tests.test_passwords import _headers, _tenant_pro
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")   # counts are per cycle (ADR-0004 sweep)

UTC = timezone.utc
run = asyncio.run
FLAG = "byob_data_tier"


def _email(tag: str) -> str:
    return f"bt1b-{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _flags(**entries):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            for name, tenants in entries.items():
                flags[name] = {"on": False, "tenants": list(tenants or [])}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    run(go())


def _byob(*tenant_ids):
    _flags(byob_data_tier=list(tenant_ids))


@pytest.fixture(autouse=True)
def _reset_flags():
    yield
    _flags(byob_data_tier=[], screener_v2=[])


class _Feed(_Broker):
    """One broker session with its own price, so whose data came back is visible. Records every call."""

    def __init__(self, price: float, bar_end: datetime | None = None, quote_ts: datetime | None | str = "now"):
        super().__init__()
        self.price = price
        self.bar_end = bar_end
        self.quote_ts = quote_ts
        self.reads: List[str] = []

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        self.reads.append(f"quote:{symbol}")
        ts = datetime.now(UTC) - timedelta(seconds=1) if self.quote_ts == "now" else self.quote_ts
        return Quote(symbol=symbol, ltp=self.price, close=self.price, timestamp=ts)

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        self.reads.append(f"ltp:{symbol}")
        return self.price

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.reads.append(f"hist:{symbol}:{interval}")
        end = self.bar_end or datetime.now(UTC) - timedelta(days=1)
        step = timedelta(days=1) if interval == "day" else timedelta(minutes=1)
        return [OHLCVBar(timestamp=end - step * (40 - i), open=self.price, high=self.price + 1, low=self.price - 1, close=self.price, volume=5)
                for i in range(40)]

    async def get_intraday_candles(self, symbol, exchange, interval):
        return []

    async def get_option_chain(self, underlying, expiry=None):
        self.reads.append(f"chain:{underlying}")
        return OptionChain(underlying=underlying, expiry="2026-10-27", underlying_ltp=self.price, rows=[])


def _route(monkeypatch, by_tenant: dict):
    """Every adapter built from a credential row -> that row's tenant's fake broker (all build sites)."""
    pick = lambda record, client=None: by_tenant[record.tenant_id]  # noqa: E731
    monkeypatch.setattr(token_lifecycle, "build_adapter", pick)
    monkeypatch.setattr(candles_routes, "build_adapter", pick)
    for mod in ("app.portfolio.routes", "app.trading.routes", "app.deployments.routes", "app.ai.worker_bridge"):
        monkeypatch.setattr(f"{mod}.build_adapter", pick, raising=False)


def _user(me) -> User:
    async def go():
        async with _session_factory() as session:
            return await session.get(User, me["id"])
    return run(go())


def _ns(me, broker="upstox") -> str:
    async def go():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == me["tenant_id"], BrokerCredentialRecord.broker_name == broker))
            return SessionRef.of(rec).namespace
    return run(go())


def _member(owner_headers):
    _tenant_pro(owner_headers)
    inv = client.post("/api/team/invites", headers=owner_headers, json={"email": _email("member"), "role": "USER"}).json()
    token = parse_qs(urlparse(inv["invite_url"]).query)["invite"][0]
    member = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()
    headers = _headers(member["access_token"])
    return headers, client.get("/api/auth/me", headers=headers).json()


# --- the screener run and its alert rules -------------------------------------------------------------------------------

def test_screener_run_reads_only_the_users_own_session(monkeypatch):
    shared = _MemoryCache()
    _install_cache(monkeypatch, shared)
    a_headers, a = _owner(_email("scr-a"))
    b_headers, b = _owner(_email("scr-b"))
    z_headers, z = _owner(_email("scr-z"))
    _store_credentials(a)
    _store_credentials(b)
    _store_credentials(z, broker="zerodha")
    feeds = {a["tenant_id"]: _Feed(111.0), b["tenant_id"]: _Feed(222.0), z["tenant_id"]: _Feed(333.0)}
    _route(monkeypatch, feeds)
    _flags(screener_v2=[a["tenant_id"], b["tenant_id"], z["tenant_id"]], byob_data_tier=[a["tenant_id"], b["tenant_id"], z["tenant_id"]])
    body = {"source": "close > 1", "base_tf": "1d", "symbols": ["RELIANCE"]}

    ra = client.post("/api/screener/run", headers=a_headers, json=body)
    assert ra.status_code == 200, ra.text
    rb = client.post("/api/screener/run", headers=b_headers, json=body).json()
    assert ra.json()["matched"] == ["RELIANCE"] and rb["matched"] == ["RELIANCE"]
    # LD-10 (owner, 20 Oct): public candles are one shared copy per broker - B's run reads the bars A's session fetched,
    # with no call through B's broker; no session-level or platform-wide request cache is written over the shared copy.
    assert feeds[a["tenant_id"]].reads and not feeds[b["tenant_id"]].reads
    keys = [k for k in shared.store if ":candles:" in k]
    assert keys == []
    badge = ra.json()["data_tier"]
    assert badge["tier"] == "A" and badge["label"] == "Live · your Upstox" and badge["stale"] is False   # yesterday's daily bar
    # An execution-only broker: no live run at all (409, the reason), and its broker is never called.
    rz = client.post("/api/screener/run", headers=z_headers, json=body)
    assert rz.status_code == 409 and rz.headers["X-Data-Tier"] == "C" and "execution-only" in rz.json()["detail"]
    assert feeds[z["tenant_id"]].reads == []


def test_screener_run_flag_off_is_unchanged(monkeypatch):
    _install_cache(monkeypatch, _MemoryCache())
    headers, me = _owner(_email("scr-off"))
    _store_credentials(me)
    _route(monkeypatch, {me["tenant_id"]: _Feed(10.0)})
    _flags(screener_v2=[me["tenant_id"]])
    out = client.post("/api/screener/run", headers=headers, json={"source": "close > 1", "base_tf": "1d", "symbols": ["TCS"]}).json()
    assert out["matched"] == ["TCS"] and "data_tier" not in out


def test_screener_run_stale_bars_and_teammate(monkeypatch):
    _install_cache(monkeypatch, _MemoryCache())
    owner_headers, owner = _owner(_email("scr-own"))
    _store_credentials(owner)
    member_headers, member = _member(owner_headers)
    _route(monkeypatch, {owner["tenant_id"]: _Feed(50.0, bar_end=datetime.now(UTC) - timedelta(days=30))})
    _flags(screener_v2=[owner["tenant_id"]], byob_data_tier=[owner["tenant_id"]])
    body = {"source": "close > 1", "base_tf": "1d", "symbols": ["INFY"]}
    out = client.post("/api/screener/run", headers=owner_headers, json=body).json()
    assert out["data_tier"]["stale"] is True and out["data_tier"]["alerts_suppressed"] is True      # a month-old daily bar
    res = client.post("/api/screener/run", headers=member_headers, json=body)
    assert res.status_code == 409 and "linked by a teammate is theirs" in res.json()["detail"]


def test_alert_rules_fetch_through_the_rule_owners_session_and_cache_per_user():
    from app.alerts import barclose

    class _Rule:
        def __init__(self, created_by):
            self.tenant_id, self.created_by, self.exchange, self.base_tf = 1, created_by, "NSE", "1d"
    calls: List[dict] = []

    async def fetch(session, tenant_id, symbols, exchange, tf, lookback, **kw):
        calls.append({"symbols": list(symbols), **kw})
        return [], {}, "broker:upstox"
    bar = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)

    async def go():
        cache = barclose.CycleCache()
        await barclose._current_universe(None, _Rule(7), ["TCS"], bar, {"1d": 5}, fetch, cache, per_user=True)
        await barclose._current_universe(None, _Rule(8), ["TCS"], bar, {"1d": 5}, fetch, cache, per_user=True)
        await barclose._current_universe(None, _Rule(8), ["TCS"], bar, {"1d": 5}, fetch, cache, per_user=True)
        shared = barclose.CycleCache()
        await barclose._current_universe(None, _Rule(7), ["TCS"], bar, {"1d": 5}, fetch, shared)
        await barclose._current_universe(None, _Rule(8), ["TCS"], bar, {"1d": 5}, fetch, shared)
    run(go())
    # Flag on: each rule owner's own session (user_id passed), user 8's second rule reuses user 8's bars only.
    assert [c.get("user_id") for c in calls[:2]] == [7, 8]
    # Flag off: the organisation's session, one fetch shared by both rules, no user_id passed (old six-argument shape).
    assert calls[2:] == [{"symbols": ["TCS"]}]


# --- the Copilot, the strategist and market memory ----------------------------------------------------------------------

def test_server_evidence_and_tools_use_the_users_own_session(monkeypatch):
    from app.ai import evidence
    from app.ai.tools import ToolContext
    from app.ai.tools import market as tools
    _install_cache(monkeypatch, _MemoryCache())
    a_headers, a = _owner(_email("ev-a"))
    z_headers, z = _owner(_email("ev-z"))
    _store_credentials(a)
    _store_credentials(z, broker="zerodha")
    feeds = {a["tenant_id"]: _Feed(101.0), z["tenant_id"]: _Feed(909.0)}
    _route(monkeypatch, feeds)
    _byob(a["tenant_id"], z["tenant_id"])
    ua, uz = _user(a), _user(z)

    async def go():
        async with _session_factory() as session:
            df, source = await evidence.server_frame(session, a["tenant_id"], "RELIANCE", "NSE", "day", user=ua)
            assert source == "broker:upstox" and float(df["close"].iloc[-1]) == 101.0
            with pytest.raises(Exception) as refused:
                await evidence.server_frame(session, z["tenant_id"], "RELIANCE", "NSE", "day", user=uz)
            assert getattr(refused.value, "status_code", None) == 409 and "execution-only" in refused.value.detail
            with pytest.raises(Exception) as no_user:
                await evidence.server_frame(session, a["tenant_id"], "RELIANCE", "NSE", "day")      # an org-level caller
            assert getattr(no_user.value, "status_code", None) == 409
            with pytest.raises(Exception) as other_org:
                await evidence.server_frame(session, a["tenant_id"], "RELIANCE", "NSE", "day", user=uz)   # never across organisations
            assert getattr(other_org.value, "status_code", None) == 409
            ctx = ToolContext(session=session, tenant_id=a["tenant_id"], user=ua)
            quote = await tools.get_quote(ctx, tools._QuoteArgs(symbol="TCS"))
            chain = await tools.get_option_chain(ctx, tools._ChainArgs(underlying="NIFTY"))
            assert quote.ok and quote.data["ltp"] == 101.0 and chain.source == "broker:upstox"
            zctx = ToolContext(session=session, tenant_id=z["tenant_id"], user=uz)
            zq = await tools.get_quote(zctx, tools._QuoteArgs(symbol="TCS"))
            assert not zq.ok and "execution-only" in zq.error
    run(go())
    assert feeds[z["tenant_id"]].reads == []


def test_market_memory_refresh_is_not_fed_from_a_users_session(monkeypatch):
    headers, me = _owner(_email("mem"))
    _store_credentials(me)
    feed = _Feed(5.0)
    _route(monkeypatch, {me["tenant_id"]: feed})
    _byob(me["tenant_id"])
    res = client.post("/api/ai/market-memory/refresh", headers=headers, json={})
    assert res.status_code == 409 and "shared in your organisation" in res.json()["detail"] and feed.reads == []


def test_strategist_frames_use_the_own_session_cache(monkeypatch):
    from app.ai import routes as ai_routes
    shared = _MemoryCache()
    _install_cache(monkeypatch, shared)
    headers, me = _owner(_email("strat"))
    _store_credentials(me)
    _route(monkeypatch, {me["tenant_id"]: _Feed(77.0)})
    _byob(me["tenant_id"])
    body = ai_routes.StrategistBody(symbol="RELIANCE")

    async def go():
        async with _session_factory() as session:
            return await ai_routes._strategist_frames(session, await session.get(User, me["id"]), body)
    df, _day, source = run(go())
    assert source == "broker:upstox" and float(df["close"].iloc[-1]) == 77.0
    assert all(k.startswith(f"md:byob:{_ns(me)}:") for k in shared.store if ":candles:" in k) and shared.store


# --- portfolio, Greeks, deployment preview, PAPER exit price ------------------------------------------------------------

def _open_trade(me, symbol="RELIANCE", kind="UNDERLYING", exchange="NSE", underlying=None, expiry=None):
    async def go():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol=symbol, strategy_id="s", direction="LONG",
                                    entry_time=datetime.now(UTC), entry_price=100.0, quantity=1, stop_loss=90.0, exchange=exchange,
                                    instrument_kind=kind, underlying_symbol=underlying, expiry=expiry))
            await session.commit()
    run(go())


def test_portfolio_exposure_prices_only_through_the_users_own_session(monkeypatch):
    a_headers, a = _owner(_email("pf-a"))
    z_headers, z = _owner(_email("pf-z"))
    _store_credentials(a)
    _store_credentials(z, broker="zerodha")
    _open_trade(a)
    _open_trade(z)
    feeds = {a["tenant_id"]: _Feed(150.0), z["tenant_id"]: _Feed(250.0)}
    _route(monkeypatch, feeds)
    _byob(a["tenant_id"], z["tenant_id"])
    assert client.get("/api/portfolio/exposure", headers=a_headers).json()["price_source"] == "ltp"
    assert client.get("/api/portfolio/exposure", headers=z_headers).json()["price_source"] == "entry"     # execution-only: no live read
    assert feeds[z["tenant_id"]].reads == []
    _flags(byob_data_tier=[])
    assert client.get("/api/portfolio/exposure", headers=z_headers).json()["price_source"] == "ltp"       # flag off: as before


def test_position_greeks_refuse_without_an_own_confirmed_session(monkeypatch):
    z_headers, z = _owner(_email("gk-z"))
    _store_credentials(z, broker="zerodha")
    _open_trade(z, symbol="NIFTY26OCT25000CE", kind="OPTION", exchange="NFO", underlying="NIFTY 50", expiry=date(2026, 10, 27))
    feed = _Feed(1.0)
    _route(monkeypatch, {z["tenant_id"]: feed})
    _byob(z["tenant_id"])
    res = client.get("/api/positions/greeks", headers=z_headers)
    assert res.status_code == 409 and "execution-only" in res.json()["detail"] and feed.reads == []


def test_deployment_preview_spot_and_exit_price_lookup(monkeypatch):
    from app.ai.worker_bridge import price_lookup_for_tenant
    from app.deployments import routes as dep_routes
    owner_headers, owner = _owner(_email("dp-own"))
    _store_credentials(owner)
    member_headers, member = _member(owner_headers)
    feed = _Feed(24000.0)
    _route(monkeypatch, {owner["tenant_id"]: feed})
    _byob(owner["tenant_id"])

    async def go():
        async with _session_factory() as session:
            mine = await dep_routes._spot_from_broker(session, owner["tenant_id"], "NIFTY", user=await session.get(User, owner["id"]))
            theirs = await dep_routes._spot_from_broker(session, owner["tenant_id"], "NIFTY", user=await session.get(User, member["id"]))
            no_user = await price_lookup_for_tenant(session, owner["tenant_id"])
            member_lookup = await price_lookup_for_tenant(session, owner["tenant_id"], user=await session.get(User, member["id"]))
            owner_lookup = await price_lookup_for_tenant(session, owner["tenant_id"], user=await session.get(User, owner["id"]))
            return mine, theirs, no_user, member_lookup, await owner_lookup("RELIANCE")
    mine, theirs, no_user, member_lookup, price = run(go())
    assert mine == 24000.0 and theirs is None                  # the teammate's preview gets no spot from the owner's session
    assert no_user is None and member_lookup is None and price == 24000.0


# --- the worker ---------------------------------------------------------------------------------------------------------

def test_worker_keeps_a_flag_on_organisation_out_of_shared_streams_keys_and_rows(monkeypatch):
    from app.core import config
    from app.market_data.service import MarketDataService
    from tests.test_trading_worker import OPEN_NOW, _deploy, _FakeBroker, _tenant, _worker
    monkeypatch.setattr(config, "STREAMING_QUOTES_ENABLED", True)
    t = _tenant(_email("wk"))
    _deploy(t, symbol="RELIANCE")
    worker = _worker(monkeypatch, _FakeBroker())
    worker.byob = UserSessionFanIn(symbols=SessionSymbols(), stream_factory=lambda a: None, adapter_factory=lambda r: object(), streaming=False)
    streamed: List[Any] = []
    recorded: List[Any] = []
    made: List[MarketDataService] = []

    async def stream_quotes(*args, **kw):
        streamed.append(args)

    async def record_chains(*args, **kw):
        recorded.append(args)
        return 0

    def factory(broker, **kw):
        made.append(MarketDataService(broker, **kw))
        return made[-1]
    monkeypatch.setattr(worker, "_stream_quotes", stream_quotes)
    monkeypatch.setattr(worker, "_record_chains", record_chains)
    worker.market_data_factory = factory
    run(worker.run_cycle(OPEN_NOW))
    assert streamed and recorded and made and all(m.cache_namespace is None for m in made)   # flag off: exactly as before
    streamed.clear()
    recorded.clear()
    made.clear()
    _byob(t["tenant_id"])
    report = run(worker.run_cycle(OPEN_NOW + timedelta(minutes=1)))
    assert streamed == [] and recorded == []                                      # no org stream, no platform-wide chain rows
    assert made and {m.cache_namespace for m in made} == {f"org.t{t['tenant_id']}"}   # never the md:candles / md:tick shared keys
    assert report.byob_sessions == 1                                              # the user's own session is the fan-in's
    run(worker.byob.close_all())


def test_worker_fan_in_failure_uses_its_own_session(monkeypatch):
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _worker

    class _Boom(UserSessionFanIn):
        async def sync(self, db, now, *, market_open):
            raise RuntimeError("fan-in exploded")
    worker = _worker(monkeypatch, _FakeBroker())
    worker.byob = _Boom(symbols=SessionSymbols(), stream_factory=lambda a: None, streaming=False)
    report = run(worker.run_cycle(OPEN_NOW))
    assert any("byob sessions: fan-in exploded" in e for e in report.errors)


def test_oi_banner_collector_skips_a_flag_on_organisation(monkeypatch):
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _tenant, _worker
    from app.option_chain import snapshots
    t = _tenant(_email("oi"))
    worker = _worker(monkeypatch, _FakeBroker())
    asked: List[int] = []

    async def enabled(session):
        return [(t["tenant_id"], "NIFTY", "NSE")]

    async def first_usable(session, tenant_id, now):
        asked.append(tenant_id)
        return None
    monkeypatch.setattr(snapshots, "enabled_underlyings", enabled)
    monkeypatch.setattr(worker, "_first_usable_broker", first_usable)
    _byob(t["tenant_id"])

    async def go():
        async with _session_factory() as session:
            return await worker._oi_banner(session, OPEN_NOW)
    run(go())
    assert asked == []


# --- broker credentials: the holder -------------------------------------------------------------------------------------

def test_a_teammate_cannot_take_over_or_log_in_to_another_members_credential(monkeypatch):
    owner_headers, owner = _owner(_email("cred-own"))
    _store_credentials(owner)                        # owner's row, with an access token on file
    member_headers, member = _member(owner_headers)
    _byob(owner["tenant_id"])                        # the holder rule applies with the data tier on (flag off: as before)
    # Pasting only a token over the owner's row: refused - nothing of theirs (secrets, session) is reused.
    res = client.post("/api/broker/upstox/credentials", headers=member_headers, json={"access_token": "member-token"})
    assert res.status_code == 400 and "nothing of theirs is kept" in res.json()["detail"]
    for path in ("/api/broker/upstox/authenticate", "/api/broker/upstox/oauth/start"):
        r = client.post(path, headers=member_headers) if path.endswith("authenticate") else client.get(path, headers=member_headers)
        assert r.status_code == 403 and "only they can use it" in r.json()["detail"] and "account label" not in r.json()["detail"], path
    # A forged / stale OAuth state of the member on the owner's row: back to the UI with an error, no token stored.
    state = token_lifecycle.create_oauth_state(owner["tenant_id"], member["id"], "upstox")
    r = client.get(f"/api/broker/upstox/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and "error=not_holder" in r.headers["location"]
    # With all of their own credentials the member replaces the row: it becomes theirs and the old token is gone.
    assert client.post("/api/broker/upstox/credentials", headers=member_headers, json={"api_key": "mk", "api_secret": "ms"}).status_code == 204

    async def go():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == owner["tenant_id"]))
            return rec.user_id, token_lifecycle.load_credentials(rec), rec.token_status
    holder, creds, status = run(go())
    assert holder == member["id"] and creds.access_token is None and creds.api_key == "mk" and status == "UNKNOWN"


def test_the_holder_still_updates_their_own_row_by_merging():
    headers, me = _owner(_email("cred-self"))
    _store_credentials(me)
    assert client.post("/api/broker/upstox/credentials", headers=headers, json={"access_token": "today"}).status_code == 204

    async def go():
        async with _session_factory() as session:
            rec = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == me["tenant_id"]))
            return token_lifecycle.load_credentials(rec)
    creds = run(go())
    assert creds.api_key == "k" and creds.access_token == "today"


# --- staleness from the data's own time, /sources -----------------------------------------------------------------------

def test_badges_are_timed_by_the_data_and_unknown_time_is_stale(monkeypatch):
    _install_cache(monkeypatch, _MemoryCache())
    headers, me = _owner(_email("stale"))
    _store_credentials(me)
    feed = _Feed(10.0, quote_ts=None)                # a quote with no exchange timestamp
    _route(monkeypatch, {me["tenant_id"]: feed})
    _byob(me["tenant_id"])
    ltp = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers).json()["data_tier"]
    assert ltp["stale"] is True and ltp["alerts_suppressed"] is True and ltp["as_of"] is None
    tier = client.get("/api/market-data/data-tier?symbol=RELIANCE", headers=headers).json()["badge"]
    assert tier["tier"] == "A" and tier["stale"] is True                       # no tick: age unknown
    chain = client.post("/api/market-data/option-chains", headers=headers, json={"underlyings": ["NIFTY"]}).json()["data_tier"]
    assert chain["stale"] is True                                              # a chain carries no exchange time
    # Candles: the newest bar decides, not the fetch time. Five-minute bars that ended two hours ago are stale.
    feed.bar_end = datetime.now(UTC) - timedelta(hours=2)
    old = client.post("/api/market-data/candles", headers=headers, json={"symbols": ["TCS"], "timeframe": "1min"}).json()["data_tier"]
    assert old["stale"] is True and "stale" in old["stale_reason"]
    # Daily bars after a long weekend / holiday are still current (the G1 daily allowance).
    feed.bar_end = datetime.now(UTC) - timedelta(days=3)
    daily = client.post("/api/market-data/candles", headers=headers, json={"symbols": ["INFY"], "timeframe": "day"}).json()["data_tier"]
    assert daily["stale"] is False


def test_badge_staleness_rules_directly():
    ref = SessionRef(user_id=1, tenant_id=1, credential_id=1, broker="upstox", account_label="primary")
    ent = entitlement.Entitlement(tier=entitlement.DataTier.LIVE_OWN_BROKER, session=ref)
    now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)                           # Monday 17:30 IST, after the close
    after_close = ent.badge(as_of=datetime(2026, 10, 12, 9, 59, 50, tzinfo=UTC), now=now)
    assert after_close["stale"] is True and after_close["alerts_suppressed"] is True   # no fresh trade after the close: no alerts
    friday_close = datetime(2026, 10, 9, 3, 45, tzinfo=UTC)                   # Friday's daily bar (09:15 IST open)
    assert ent.badge(now=now, timeframe="1d", last_bar_start=friday_close)["stale"] is False   # Monday after a weekend + holiday
    assert ent.badge(now=now)["stale"] is True                                # unknown time fails closed


def test_sources_usable_means_usable_for_data_with_the_flag_on():
    headers, me = _owner(_email("src"))
    _store_credentials(me, broker="zerodha")
    src = client.get("/api/market-data/sources", headers=headers).json()
    assert src["sources"][0]["usable"] is True and src["usable"] is True          # flag off: a valid token is usable, as before
    _byob(me["tenant_id"])
    src = client.get("/api/market-data/sources", headers=headers).json()
    item = src["sources"][0]
    assert item["usable"] is False and item["token_usable"] is True and item["tier_a"] is False and src["usable"] is False


def test_live_access_refuses_without_a_user_or_a_symbol():
    headers, me = _owner(_email("la"))
    _store_credentials(me)

    async def go():
        async with _session_factory() as session:
            with pytest.raises(live_access.LiveDataRefused):
                await live_access.user_source(session, None, [("TCS", "NSE")])
            with pytest.raises(live_access.LiveDataRefused):
                await live_access.user_source(session, await session.get(User, me["id"]), [])
            own = await live_access.user_source(session, await session.get(User, me["id"]), [("TCS", "NSE")])
            assert own.allows("tcs", "nse") and own.namespace == SessionRef.of(own.record).namespace and own.source == "broker:upstox"
    run(go())
    assert json.dumps(live_access.NO_USER)
