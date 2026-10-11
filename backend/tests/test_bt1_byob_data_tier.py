"""BT1: bring-your-own-broker data tier (beta spec §2 / §4a, Phase 0), flag `byob_data_tier` (off by default).

* the data-terms registry the code reads agrees with docs/BROKER_DATA_TERMS.md; caps are never invented;
* the entitlement guard: Tier A only through the user's own session at a confirmed broker with a usable token and the
  symbol inside the session budget; else Tier C (EOD) with the reason;
* isolation: user B never gets ticks or candles cached from user A's session; a teammate's credential is not the user's
  session; per-user streams never feed platform-wide listeners;
* per-user fan-in: priority (positions > charts > watchlist > screener), degradation, open after login / close at day end;
* flag off = the routes behave exactly as before (no `data_tier`, shared caches as today).
No network: fake brokers, fake websocket connections."""
import asyncio
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.brokers.models import OptionChain, Quote
from app.core import config as app_config
from app.core.enums import BrokerTokenStatus
from app.core.models import OHLCVBar
from app.db.models import BrokerCredentialRecord, IndexEodRecord, TradeRecord, User
from app.market_data import candles_routes, data_terms, entitlement, fanin
from app.market_data import stream as st
from app.market_data.entitlement import DataTier
from app.market_data.fanin import Priority, SessionRef, SessionSymbols, UserSessionFanIn, plan_symbols
from app.market_data.service import MarketDataService
from app.market_data.stream import Tick, TickCache, TickStream, tick_cache
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_market_data_service import _MemoryCache, _install_cache
from tests.test_passwords import _headers, _tenant_pro
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

UTC = timezone.utc
REPO = Path(__file__).resolve().parents[2]
run = asyncio.run
FLAG = "byob_data_tier"


def _email(tag: str) -> str:
    return f"bt1-{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _flag(on: bool, tenants: List[int] | None = None) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags[FLAG] = {"on": on, "tenants": list(tenants or [])}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    run(go())


def _record(me, broker="upstox") -> BrokerCredentialRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == me["tenant_id"], BrokerCredentialRecord.broker_name == broker))
    return run(go())


def _ns(me, broker="upstox") -> str:
    return SessionRef.of(_record(me, broker)).namespace


class _PriceBroker(_Broker):
    """A broker session answering quotes / candles with its own prices (so whose data came back is visible)."""

    def __init__(self, price: float):
        super().__init__()
        self.price = price
        self.quote_calls = 0

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        self.quote_calls += 1
        return Quote(symbol=symbol, ltp=self.price, timestamp=datetime.now(UTC) - timedelta(seconds=1))

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        return self.price

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.calls.append((symbol, interval))
        start = datetime(2026, 9, 28, 3, 45, tzinfo=UTC)
        return [OHLCVBar(timestamp=start + timedelta(minutes=i), open=self.price, high=self.price + 1, low=self.price - 1,
                         close=self.price, volume=1) for i in range(5)]

    async def get_option_chain(self, underlying, expiry=None):
        return OptionChain(underlying=underlying, expiry="2026-10-27", underlying_ltp=self.price, rows=[])


def _route_brokers(monkeypatch, by_tenant: dict):
    """build_adapter(record) -> the fake broker of that record's tenant."""
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: by_tenant[record.tenant_id])


# --- the data-terms registry ------------------------------------------------------------------------------------------

def test_registry_statuses_match_the_spec_and_never_invent_caps():
    reg = data_terms.registry()
    assert reg["upstox"].status is data_terms.DataTermsStatus.CONFIRMED and reg["upstox"].tier_a_allowed
    assert reg["dhan"].status is data_terms.DataTermsStatus.CONFIRMED and reg["dhan"].requires_user_data_subscription
    assert reg["zerodha"].status is data_terms.DataTermsStatus.EXECUTION_ONLY and not reg["zerodha"].tier_a_allowed
    for name in ("angel_one", "fyers", "groww", "kotak_neo", "icici_breeze", "five_paisa", "shoonya", "alice_blue"):
        assert reg[name].status is data_terms.DataTermsStatus.PENDING and not reg[name].tier_a_allowed, name
    assert data_terms.confirmed_brokers() == {"upstox", "dhan"}
    # No broker cap is recorded yet (none confirmed in writing): every one is "to confirm", never a guessed number.
    assert all(t.symbol_cap is None for t in reg.values())
    assert reg["upstox"].as_dict()["symbol_cap_note"] == "to confirm"
    # A broker that is not recorded is never shown live.
    assert data_terms.terms_for("coindcx").status is data_terms.DataTermsStatus.PENDING
    assert not data_terms.terms_for("somebroker").tier_a_allowed


def test_registry_and_broker_data_terms_md_agree():
    doc = (REPO / "docs" / "BROKER_DATA_TERMS.md").read_text(encoding="utf-8")
    rows = {}
    for line in doc.splitlines():
        m = re.match(r"^\|\s*`([a-z_0-9]+)`\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|", line)
        if m:
            rows[m.group(1)] = [c.strip() for c in m.groups()[1:]]
    reg = data_terms.registry()
    assert set(rows) == set(reg), "every broker in broker_data_terms.json has a row in the Tier A registry table and vice versa"
    for name, terms in reg.items():
        display, status, cap, subscription = rows[name]
        assert display == terms.display_name and status == terms.status.value, name
        assert cap == ("to confirm" if terms.symbol_cap is None else str(terms.symbol_cap)), name
        assert subscription == ("yes" if terms.requires_user_data_subscription else "no"), name


def test_registry_rejects_an_invented_or_malformed_cap():
    with pytest.raises(ValueError):
        data_terms._parse({"brokers": {"x": {"status": "confirmed", "symbol_cap": 0}}})
    with pytest.raises(ValueError):
        data_terms._parse({"brokers": {"x": {"status": "maybe"}}})


def test_flag_is_off_by_default():
    assert FLAG in controls.FEATURE_FLAGS and FLAG in controls.DEFAULT_OFF_FLAGS


# --- the symbol plan (fan-in budget) ----------------------------------------------------------------------------------

def test_plan_keeps_positions_then_charts_then_watchlist_then_screener_and_degrades():
    t0 = 1_000.0
    entries = {"NSE:SCR1": [4, t0, t0], "NSE:WL1": [3, t0, t0], "NSE:CH1": [2, t0 + 5, t0 + 5], "NSE:CH0": [2, t0 + 9, t0],
               "NSE:POS": [1, t0, t0 + 50]}
    plan = plan_symbols(entries, 3)
    assert plan.kept == (("POS", "NSE"), ("CH0", "NSE"), ("CH1", "NSE"))   # positions first; the chart held longest keeps its slot
    assert set(plan.dropped) == {("WL1", "NSE"), ("SCR1", "NSE")} and plan.degraded and plan.poll_seconds == 15
    assert plan_symbols(entries, 10).poll_seconds == 5 and not plan_symbols(entries, 10).degraded   # 5 of 10: normal refresh
    assert plan_symbols(entries, 6).poll_seconds == 10                                             # over half the cap: slower
    # Within the screener universe the latest scan wins (a new scan is not starved by the previous one).
    scans = {"NSE:OLD": [4, t0, t0], "NSE:NEW": [4, t0 + 60, t0 + 60], "NSE:CH": [2, t0, t0]}
    assert plan_symbols(scans, 2).kept == (("CH", "NSE"), ("NEW", "NSE"))


def test_session_symbols_admit_register_idle_and_replace():
    reg = SessionSymbols()
    now = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)

    async def go():
        ns = "u1.c1"
        assert (await reg.admit(ns, "reliance", "nse", Priority.CHART, 2, now))[0]
        assert (await reg.admit(ns, "TCS", "NSE", Priority.CHART, 2, now))[0]
        # The cap is full of charts: a third chart waits (no flapping between charts).
        inside, plan = await reg.admit(ns, "INFY", "NSE", Priority.CHART, 2, now + timedelta(seconds=1))
        assert not inside and plan.degraded
        # A lookup without register takes no slot.
        reg2 = SessionSymbols()
        assert (await reg2.admit("u2.c2", "X", "NSE", Priority.CHART, 1, now, register=False))[0] is True
        assert await reg2.load("u2.c2") == {}
        # A position outranks every chart.
        await reg.replace(ns, Priority.POSITION, [("HDFCBANK", "NSE")], now)
        plan = await reg.plan(ns, 2, now)
        assert plan.kept[0] == ("HDFCBANK", "NSE") and len(plan.kept) == 2
        # Charts lapse when not re-asserted (page closed); positions stay until replaced.
        later = now + timedelta(seconds=fanin.IDLE_SECONDS[int(Priority.CHART)] + 5)
        plan = await reg.plan(ns, 2, later)
        assert plan.kept == (("HDFCBANK", "NSE"),)
        await reg.replace(ns, Priority.POSITION, [], later)
        assert (await reg.plan(ns, 2, later)).kept == ()
    run(go())


# --- cache isolation --------------------------------------------------------------------------------------------------

def test_tick_cache_namespaces_never_meet(monkeypatch):
    cache = _MemoryCache()
    _install_cache(monkeypatch, cache)
    now = datetime.now(UTC)

    async def go():
        a, b = TickCache(namespace="u1.c1"), TickCache(namespace="u2.c2")
        await a.put(Tick(broker="upstox", exchange="NSE", symbol="RELIANCE", ltp=2874.0, exchange_ts=now, received_at=now))
        assert (await a.get("upstox", "NSE", "RELIANCE")).ltp == 2874.0
        assert await b.get("upstox", "NSE", "RELIANCE") is None            # not through B's local dict nor its Redis key
        assert await TickCache(namespace="u2.c2").get("upstox", "NSE", "RELIANCE") is None
        assert await tick_cache.get("upstox", "NSE", "RELIANCE") is None   # never into the shared worker cache
        # A's own key is readable from another process (Redis mirror) - only under A's namespace.
        assert (await TickCache(namespace="u1.c1").get("upstox", "NSE", "RELIANCE")).ltp == 2874.0
        assert cache.store and all(k.startswith("md:byob:u1.c1:tick:") for k in cache.store)
    run(go())
    tick_cache.clear()


def test_candle_cache_is_per_session_with_a_namespace_and_shared_without(monkeypatch):
    _install_cache(monkeypatch, _MemoryCache())

    async def go():
        a, b = _PriceBroker(100.0), _PriceBroker(200.0)
        bars_a = await MarketDataService(a, cache_namespace="u1.c1").get_candles("RELIANCE", "NSE", "1min")
        bars_b = await MarketDataService(b, cache_namespace="u2.c2").get_candles("RELIANCE", "NSE", "1min")
        assert bars_a[0].close == 100.0 and bars_b[0].close == 200.0 and b.calls   # B fetched through its own session
        # Without a namespace (flag off, the worker) the cache is shared exactly as before.
        c, d = _PriceBroker(300.0), _PriceBroker(400.0)
        await MarketDataService(c).get_candles("TCS", "NSE", "1min")
        shared = await MarketDataService(d).get_candles("TCS", "NSE", "1min")
        assert shared[0].close == 300.0 and not d.calls
    run(go())


# --- routes: flag off (unchanged) -------------------------------------------------------------------------------------

def test_flag_off_routes_are_unchanged(monkeypatch):
    _flag(False)
    headers, me = _owner(_email("off"))
    _store_credentials(me)
    _route_brokers(monkeypatch, {me["tenant_id"]: _PriceBroker(101.0)})
    tick_cache.clear()
    body = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers).json()
    assert body["ltp"] == 101.0 and "data_tier" not in body
    res = client.post("/api/market-data/candles", headers=headers, json={"symbols": ["RELIANCE"], "timeframe": "1min"}).json()
    assert "data_tier" not in res and "platform-wide" in res["note"]
    assert client.get("/api/market-data/data-tier", headers=headers).json() == {"enabled": False}
    src = client.get("/api/market-data/sources", headers=headers).json()["sources"][0]
    assert "data_status" not in src


# --- routes: flag on --------------------------------------------------------------------------------------------------

def test_no_broker_or_execution_only_broker_is_tier_c(monkeypatch):
    headers, me = _owner(_email("none"))
    _flag(False, [me["tenant_id"]])    # on for this organisation only (staged rollout)
    res = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers)
    assert res.status_code == 409 and res.headers["X-Data-Tier"] == "C" and "No broker linked" in res.json()["detail"]
    tier = client.get("/api/market-data/data-tier?symbol=RELIANCE", headers=headers).json()
    assert tier["enabled"] and tier["badge"]["tier"] == "C" and tier["badge"]["label"].startswith("EOD") and tier["sessions"] == []

    z_headers, z = _owner(_email("zerodha"))
    _flag(False, [me["tenant_id"], z["tenant_id"]])
    _store_credentials(z, broker="zerodha")
    _route_brokers(monkeypatch, {z["tenant_id"]: _PriceBroker(1.0)})
    for res in (client.get("/api/market-data/ltp?symbol=RELIANCE", headers=z_headers),
                client.post("/api/market-data/candles", headers=z_headers, json={"symbols": ["RELIANCE"]}),
                client.post("/api/market-data/option-chains", headers=z_headers, json={"underlyings": ["NIFTY"]})):
        assert res.status_code == 409 and "execution-only" in res.json()["detail"], res.text
    src = client.get("/api/market-data/sources", headers=z_headers).json()["sources"][0]
    assert src["data_status"] == "execution-only" and src["tier_a"] is False
    sessions = client.get("/api/market-data/data-tier", headers=z_headers).json()["sessions"]
    assert sessions[0]["data_status"] == "execution-only" and sessions[0]["tier_a"] is False
    _flag(False)


def test_expired_token_is_tier_c_with_the_login_hint():
    headers, me = _owner(_email("expired"))
    _flag(False, [me["tenant_id"]])
    _store_credentials(me, status=BrokerTokenStatus.EXPIRED.value)
    res = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers)
    assert res.status_code == 409 and "no valid token today" in res.json()["detail"]
    _flag(False)


def test_own_confirmed_session_is_tier_a_with_badge(monkeypatch):
    headers, me = _owner(_email("upstox"))
    _flag(False, [me["tenant_id"]])
    _store_credentials(me)
    _route_brokers(monkeypatch, {me["tenant_id"]: _PriceBroker(2874.5)})
    body = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers).json()
    assert body["ltp"] == 2874.5 and body["source"] == "quote"
    badge = body["data_tier"]
    assert badge["tier"] == "A" and badge["label"] == "Live · your Upstox" and badge["source"] == "broker:upstox"
    assert badge["as_of"] and badge["stale"] is False and badge["alerts_suppressed"] is False and badge["cap"] == app_config.BYOB_SESSION_SYMBOL_CAP
    res = client.post("/api/market-data/candles", headers=headers, json={"symbols": ["RELIANCE"], "timeframe": "1min"}).json()
    assert res["data_tier"]["tier"] == "A" and res["symbols"]["RELIANCE"]["count"] == 5 and "only to you" in res["note"]
    chain = client.post("/api/market-data/option-chains", headers=headers, json={"underlyings": ["NIFTY"]}).json()
    assert chain["data_tier"]["tier"] == "A" and chain["symbols"]["NIFTY"]["error"] is None
    tier = client.get("/api/market-data/data-tier?symbol=RELIANCE", headers=headers).json()
    assert tier["badge"]["tier"] == "A" and tier["sessions"][0]["tier_a"] is True and tier["sessions"][0]["symbols"] >= 1
    _flag(False)


def test_stale_quote_suppresses_alerts_on_the_badge(monkeypatch):
    headers, me = _owner(_email("stale"))
    _flag(False, [me["tenant_id"]])
    _store_credentials(me)

    class _Old(_PriceBroker):
        async def get_quote_for_symbol(self, symbol, exchange="NSE"):
            return Quote(symbol=symbol, ltp=self.price, timestamp=datetime.now(UTC) - timedelta(hours=18))
    _route_brokers(monkeypatch, {me["tenant_id"]: _Old(99.0)})
    badge = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers).json()["data_tier"]
    assert badge["tier"] == "A" and badge["stale"] is True and badge["alerts_suppressed"] is True and badge["stale_reason"]
    _flag(False)


def _candle_cache_keys(store: _MemoryCache) -> List[str]:
    return [k for k in store.store if ":candles:" in k]


def test_user_b_never_gets_ticks_or_candles_from_user_a(monkeypatch):
    shared_redis = _MemoryCache()
    _install_cache(monkeypatch, shared_redis)    # one Redis shared by the users, as in production
    a_headers, a = _owner(_email("a"))
    b_headers, b = _owner(_email("b"))
    _flag(False, [a["tenant_id"], b["tenant_id"]])
    _store_credentials(a)
    _store_credentials(b)
    broker_a, broker_b = _PriceBroker(111.0), _PriceBroker(222.0)
    _route_brokers(monkeypatch, {a["tenant_id"]: broker_a, b["tenant_id"]: broker_b})
    now = datetime.now(UTC)
    tick_cache.clear()

    # A's own session streamed a tick for NIFTY 50; the shared worker cache also holds one (another org's automation).
    run(TickCache(namespace=_ns(a)).put(Tick(broker="upstox", exchange="NSE", symbol="NIFTY 50", ltp=26000.0, exchange_ts=now, received_at=now)))
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol="NIFTY 50", ltp=25999.0, exchange_ts=now, received_at=now)))
    body_a = client.get("/api/market-data/ltp?symbol=NIFTY%2050", headers=a_headers).json()
    assert body_a["source"] == "tick" and body_a["ltp"] == 26000.0
    body_b = client.get("/api/market-data/ltp?symbol=NIFTY%2050", headers=b_headers).json()
    assert body_b["source"] == "quote" and body_b["ltp"] == 222.0 and broker_b.quote_calls == 1   # B's own session, not A's tick

    # Candles: A's fetch is cached for A only; B's request goes to B's own broker and returns B's prices.
    ca = client.post("/api/market-data/candles", headers=a_headers, json={"symbols": ["RELIANCE"], "timeframe": "1min"}).json()
    cb = client.post("/api/market-data/candles", headers=b_headers, json={"symbols": ["RELIANCE"], "timeframe": "1min"}).json()
    assert ca["symbols"]["RELIANCE"]["bars"][0]["close"] == 111.0 and cb["symbols"]["RELIANCE"]["bars"][0]["close"] == 222.0
    keys = _candle_cache_keys(shared_redis)
    assert broker_b.calls and len(keys) == 2 and all(k.startswith("md:byob:") for k in keys)
    assert {k.split(":")[2] for k in keys} == {_ns(a), _ns(b)}
    tick_cache.clear()
    _flag(False)


def test_teammates_credential_is_not_the_users_session(monkeypatch):
    owner_headers, owner = _owner(_email("owner"))
    _tenant_pro(owner_headers)
    _store_credentials(owner)
    inv = client.post("/api/team/invites", headers=owner_headers, json={"email": _email("member"), "role": "USER"}).json()
    token = parse_qs(urlparse(inv["invite_url"]).query)["invite"][0]
    member = client.post(f"/api/auth/invite/{token}/accept", json={"password": "Member-Pass-2026"}).json()
    member_headers = _headers(member["access_token"])
    _route_brokers(monkeypatch, {owner["tenant_id"]: _PriceBroker(50.0)})

    _flag(False)    # flag off: the tenant's credential serves every member exactly as before
    assert client.get("/api/market-data/ltp?symbol=RELIANCE", headers=member_headers).status_code == 200
    _flag(False, [owner["tenant_id"]])
    res = client.get("/api/market-data/ltp?symbol=RELIANCE", headers=member_headers)
    assert res.status_code == 409 and "linked by a teammate is theirs" in res.json()["detail"]
    assert client.get("/api/market-data/sources", headers=member_headers).json()["sources"] == []
    assert client.get("/api/market-data/ltp?symbol=RELIANCE", headers=owner_headers).json()["data_tier"]["tier"] == "A"
    _flag(False)


def test_symbols_outside_the_session_budget_are_eod(monkeypatch):
    monkeypatch.setattr(app_config, "BYOB_SESSION_SYMBOL_CAP", 2)
    headers, me = _owner(_email("cap"))
    _flag(False, [me["tenant_id"]])
    _store_credentials(me)
    _route_brokers(monkeypatch, {me["tenant_id"]: _PriceBroker(10.0)})
    for symbol in ("AAA", "BBB"):
        assert client.get(f"/api/market-data/ltp?symbol={symbol}", headers=headers).status_code == 200
    res = client.get("/api/market-data/ltp?symbol=CCC", headers=headers)
    assert res.status_code == 409 and res.headers["X-Data-Tier"] == "C" and "outside your Upstox session budget (2 symbols" in res.json()["detail"]
    # Candles for several symbols: the ones in the budget load, the rest say EOD - one does not lose the others.
    body = client.post("/api/market-data/candles", headers=headers, json={"symbols": ["AAA", "DDD"], "timeframe": "1min"}).json()
    assert body["symbols"]["AAA"]["count"] == 5 and body["symbols"]["DDD"]["data_tier"]["tier"] == "C" and body["data_tier"]["degraded"]
    assert "outside your Upstox session budget" in body["symbols"]["DDD"]["error"]
    # The badge lookup takes no slot.
    assert client.get("/api/market-data/data-tier?symbol=EEE", headers=headers).json()["badge"]["tier"] == "C"
    _flag(False)


def test_dhan_is_offered_and_its_subscription_need_is_explained(monkeypatch):
    headers, me = _owner(_email("dhan"))
    _flag(False, [me["tenant_id"]])
    _store_credentials(me, broker="dhan")
    _route_brokers(monkeypatch, {me["tenant_id"]: _PriceBroker(5.0)})
    assert client.get("/api/market-data/ltp?symbol=RELIANCE", headers=headers).json()["data_tier"]["label"] == "Live · your Dhan"
    sessions = client.get("/api/market-data/data-tier", headers=headers).json()["sessions"]
    assert sessions[0]["requires_user_data_subscription"] is True
    _flag(False)


def test_eod_badge_date_and_staleness():
    now = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)          # Monday 11:30 IST: the last closed session is Friday 9 Oct
    assert entitlement.eod_staleness(date(2026, 10, 9), now) is None
    assert "last closed session was 2026-10-09" in entitlement.eod_staleness(date(2026, 10, 8), now)
    assert entitlement.eod_staleness(None, now) == "no EOD data ingested yet"
    badge = entitlement.eod_badge(date(2026, 10, 9), now)
    assert badge["label"] == "EOD · 2026-10-09" and badge["tier"] == "C" and not badge["alerts_suppressed"]
    assert entitlement.eod_badge(date(2026, 10, 8), now)["alerts_suppressed"] is True
    # A holiday on Friday moves the expectation back to Thursday.
    assert entitlement.eod_staleness(date(2026, 10, 8), now, holidays={date(2026, 10, 9)}) is None


def test_market_wide_badge_reads_the_newest_eod_file():
    headers, me = _owner(_email("eod"))
    _flag(False, [me["tenant_id"]])

    async def seed():
        async with _session_factory() as session:
            session.add(IndexEodRecord(index_code=f"BT1{uuid.uuid4().hex[:6]}", trade_date=date(2031, 1, 3), close=1.0, source="test", checksum="x"))
            await session.commit()
    run(seed())
    body = client.get("/api/market-data/data-tier", headers=headers).json()
    assert body["eod_date"] == "2031-01-03" and body["market_wide"]["label"] == "EOD · 2031-01-03" and body["badge"] == body["market_wide"]
    _flag(False)


def test_resolve_prefers_a_confirmed_session_over_an_execution_only_one():
    headers, me = _owner(_email("both"))
    _store_credentials(me, broker="zerodha")
    _store_credentials(me, broker="upstox")

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            ent = await entitlement.resolve(session, user, "RELIANCE", "NSE", registry=SessionSymbols())
            assert ent.tier is DataTier.LIVE_OWN_BROKER and ent.session.broker == "upstox" and ent.segment == "NSE_EQ"
            ent = await entitlement.resolve(session, user, "RELIANCE", "NSE", broker="zerodha", registry=SessionSymbols())
            assert ent.tier is DataTier.EOD and "execution-only" in ent.reason
            ent = await entitlement.resolve(session, user, "NIFTY", "NFO", registry=SessionSymbols())
            assert ent.segment == "NSE_FO"
    run(go())


# --- the worker side: per-user fan-in ---------------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, frames: List[Any], hold: asyncio.Event):
        self.frames, self.hold, self.sent = list(frames), hold, []

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        while not self.sent and not self.hold.is_set():    # a feed sends prices only after a subscription
            await asyncio.sleep(0.005)
        for f in self.frames:
            yield f
        await self.hold.wait()

    async def close(self):
        pass


class _JsonUpstoxStream(TickStream):
    name = "upstox"

    def __init__(self, conn: _FakeConn, **kw):
        async def connector(url):
            return conn
        super().__init__(connector=connector, **kw)
        self.conn = conn

    async def connect_url(self):
        return "wss://fake"

    async def wire_key(self, symbol, exchange):
        return f"{exchange}:{symbol}"

    def subscribe_frames(self, keys):
        return [json.dumps({"sub": list(keys)})]

    def decode(self, frame):
        return [(k, v, None) for k, v in json.loads(frame).items()]


def test_fan_in_opens_after_login_streams_into_the_users_own_cache_and_closes_at_day_end():
    a_headers, a = _owner(_email("fan-a"))
    b_headers, b = _owner(_email("fan-b"))
    z_headers, z = _owner(_email("fan-z"))
    _store_credentials(a)
    _store_credentials(b, status=BrokerTokenStatus.EXPIRED.value)   # no daily login yet
    _store_credentials(z, broker="zerodha")                          # execution-only: never a data session
    seen_by_listeners: List[Tick] = []
    listener = seen_by_listeners.append
    st.tick_listeners.append(listener)
    now = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)   # Monday 10:30 IST

    async def seed_position():
        async with _session_factory() as session:
            session.add(TradeRecord(tenant_id=a["tenant_id"], user_id=a["id"], mode="PAPER", symbol="RELIANCE", strategy_id="s", direction="LONG",
                                    entry_time=now, entry_price=100.0, quantity=1, stop_loss=90.0, exchange="NSE"))
            await session.commit()
    run(seed_position())

    streams: List[_JsonUpstoxStream] = []
    ns_a, ns_b = _ns(a), _ns(b)

    async def go():
        hold = asyncio.Event()

        def factory(adapter):
            s = _JsonUpstoxStream(_FakeConn([json.dumps({"NSE:RELIANCE": 2875.0})], hold))
            streams.append(s)
            return s

        fan = UserSessionFanIn(symbols=SessionSymbols(), stream_factory=factory, adapter_factory=lambda record: object(), streaming=True)
        try:
            async with _session_factory() as session:
                # Flag off: nothing opens.
                assert (await fan.sync(session, now, market_open=True)).active == 0
                current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
                flags = dict(current.get("flags") or {})
                flags[FLAG] = {"on": False, "tenants": [a["tenant_id"], b["tenant_id"], z["tenant_id"]]}
                await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
                await session.commit()
                report = await fan.sync(session, now, market_open=True)
                assert report.opened == 1 and report.active == 1 and set(fan.sessions) == {ns_a}, report
                live = fan.sessions[ns_a]
                assert live.stream is streams[0] and live.stream.cache is live.cache and live.cache.namespace == ns_a
                assert live.stream.notify_listeners is False
                assert live.plan is not None and live.plan.kept == (("RELIANCE", "NSE"),)       # the user's open position
                for _ in range(400):
                    if live.stream.ticks >= 1:
                        break
                    await asyncio.sleep(0.01)
                assert (await live.cache.get("upstox", "NSE", "RELIANCE")).ltp == 2875.0
                assert await tick_cache.get("upstox", "NSE", "RELIANCE") is None                # not the shared cache
                assert await TickCache(namespace=ns_b).get("upstox", "NSE", "RELIANCE") is None
                assert seen_by_listeners == []                                                   # never pooled (lake recorder)
                # Same day, next cycle: the session is kept, not reopened.
                assert (await fan.sync(session, now + timedelta(minutes=1), market_open=True)).opened == 0
                # Day end: the market closes -> every session is torn down.
                report = await fan.sync(session, now + timedelta(hours=6), market_open=False)
                assert report.closed == 1 and fan.sessions == {}
                # Token no longer usable (expired overnight) -> not reopened.
                record = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == a["tenant_id"]))
                record.token_status = BrokerTokenStatus.EXPIRED.value
                await session.commit()
                assert (await fan.sync(session, now + timedelta(days=1), market_open=True)).active == 0
        finally:
            hold.set()
            await fan.close_all()
    try:
        run(go())
    finally:
        st.tick_listeners.remove(listener)
        _flag(False)


def test_fan_in_without_streaming_keeps_a_rest_session_and_a_new_day_reopens():
    headers, me = _owner(_email("rest"))
    _store_credentials(me)
    now = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)
    _flag(False, [me["tenant_id"]])

    async def go():
        fan = UserSessionFanIn(symbols=SessionSymbols(), stream_factory=lambda adapter: None, adapter_factory=lambda r: object(), streaming=False)
        try:
            async with _session_factory() as session:
                assert (await fan.sync(session, now, market_open=True)).opened == 1
                live = next(iter(fan.sessions.values()))
                assert live.stream is None and live.status()["streaming"] is False and live.plan is not None
                # The next IST day: yesterday's session is closed and a fresh one opened.
                report = await fan.sync(session, now + timedelta(days=1), market_open=True)
                assert report.closed == 1 and report.opened == 1
        finally:
            await fan.close_all()
    try:
        run(go())
    finally:
        _flag(False)


# --- the daily-login reminder for a user's own data session -----------------------------------------------------------

def test_pre_open_data_login_reminder_per_user_once_a_day():
    from app.brokers import login_reminder as lr
    from app.db.models import NotificationRecord
    from app.market_data.calendar import IST, MARKET_OPEN

    headers, me = _owner(_email("remind"))
    _store_credentials(me, status=BrokerTokenStatus.EXPIRED.value)       # no login yet today
    z_headers, z = _owner(_email("remind-z"))
    _store_credentials(z, broker="zerodha", status=BrokerTokenStatus.EXPIRED.value)   # execution-only: no data reminder
    today = datetime.now(IST).date()
    day = today - timedelta(days=today.weekday() + 7)                    # a past Monday (created_at is real time)
    now = datetime.combine(day, MARKET_OPEN, tzinfo=IST) - timedelta(minutes=10)

    def send():
        async def go():
            async with _session_factory() as session:
                return await lr.send_reminders(session, now, holidays=())
        return run(go())

    def mine(user):
        async def go():
            async with _session_factory() as session:
                return list(await session.scalars(select(NotificationRecord).where(
                    NotificationRecord.user_id == user["id"], NotificationRecord.title.startswith(lr.DATA_TITLE_PREFIX))))
        return run(go())

    send()
    assert mine(me) == []                                                 # flag off: nothing new
    _flag(False, [me["tenant_id"], z["tenant_id"]])
    try:
        send()
        send()                                                            # a restarted worker: still one per user per day
        sent = mine(me)
        assert len(sent) == 1 and "upstox" in sent[0].title and sent[0].severity == "WARNING" and sent[0].tenant_id == me["tenant_id"]
        assert mine(z) == []
    finally:
        _flag(False)
