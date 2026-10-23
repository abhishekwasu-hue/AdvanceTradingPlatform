"""TM Part 1: the data hub (docs/design/DATA_HUB.md; flag `data_hub`, OFF by default).

* the stream says live / reconnecting / renew_needed, resubscribes every symbol after a drop, and (hub streams only)
  stops retrying a token the broker refused; streams outside the hub keep retrying as before;
* one stream per connection with the symbols of every consumer (positions first, inside the cap);
* ticks stay with their connection (never the platform-wide keys, never another organisation's);
* ONE renew prompt per ended login, and everything resumes after a renewed login;
* the browser: `PUT /api/live/ticks`, `GET /api/live/hub`, the `ticks` / `hub` events (only the tab's symbols, only on
  a change), 404 while the flag is off;
* the API reads (`/ltp`, `/quotes`) and the chain snapshot go through the hub when it is on, and stay as before when off;
* the worker streams the organisation's connection through the hub when the flag is on.
No network: fake brokers, fake websocket connections, an in-memory cache.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import select

from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import OptionChain
from app.core.enums import BrokerTokenStatus, NotificationType
from app.db.models import NotificationRecord, User
from app.live import routes as live_routes
from app.market_data import fanin
from app.market_data import hub as data_hub
from app.market_data import stream as st
from app.market_data.fanin import Priority
from app.market_data.service import MarketDataService
from app.market_data.stream import Tick, TickStream
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_market_data_service import _MemoryCache, _install_cache
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner

UTC = timezone.utc
run = asyncio.run


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    _install_cache(monkeypatch, _MemoryCache())
    monkeypatch.setattr(st, "BACKOFF_SECONDS", (0.01, 0.02))
    data_hub.reset()
    fanin.session_symbols._local.clear()
    st.tick_cache.clear()
    yield
    data_hub.reset()
    st.tick_cache.clear()


def _email(tag: str) -> str:
    return f"tm1-{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _flags(tenants: List[int], **names: bool) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            for name, on in names.items():
                have = set((flags.get(name) or {}).get("tenants") or [])
                flags[name] = {"on": False, "tenants": sorted(have | set(tenants)) if on else sorted(have - set(tenants))}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    run(go())


def _hub_user(tag: str, *, status: str = BrokerTokenStatus.VALID.value):
    headers, me = _owner(_email(tag))
    _store_credentials(me, status=status)
    _flags([me["tenant_id"]], ui_v2=True, data_hub=True)
    return headers, me


# --- fake sockets and streams -------------------------------------------------------------------------------------------

class _Conn:
    def __init__(self, frames: List[Any], *, fail: Optional[Exception] = None, hold: Optional[asyncio.Event] = None):
        self.frames = list(frames)
        self.fail = fail
        self.hold = hold
        self.sent: List[Any] = []

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self.frames:
            yield f
        if self.hold is not None:
            await self.hold.wait()
        if self.fail is not None:
            raise self.fail

    async def close(self):
        return None


class _Stream(TickStream):
    name = "upstox"

    def __init__(self, connector=None, *, refuse: Optional[Exception] = None, **kw):
        super().__init__(connector=connector, **kw)
        self.refuse = refuse
        self.attempts = 0

    async def connect_url(self):
        self.attempts += 1
        if self.refuse is not None:
            raise self.refuse
        return "wss://fake"

    async def wire_key(self, symbol, exchange):
        return f"{exchange}:{symbol}"

    def subscribe_frames(self, keys):
        return [json.dumps({"sub": list(keys)})]

    def unsubscribe_frames(self, keys):
        return [json.dumps({"unsub": list(keys)})]

    def decode(self, frame):
        return [(k, v, None) for k, v in json.loads(frame).items()]


async def _until(cond, tries: int = 300):
    for _ in range(tries):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


# --- the stream's states ------------------------------------------------------------------------------------------------

def test_stream_reports_live_reconnecting_live_and_resubscribes_every_symbol():
    async def go():
        hold = asyncio.Event()
        first = _Conn([json.dumps({"NSE:A": 1.0})], fail=ConnectionError("dropped"))
        second = _Conn([json.dumps({"NSE:A": 2.0})], hold=hold)
        conns = iter([first, second])

        async def connector(url):
            return next(conns)
        stream = _Stream(connector)
        states: List[str] = []

        async def on_state(s):
            states.append(s.state)
        stream.on_state = on_state
        await stream.set_symbols([("A", "NSE"), ("B", "NSE"), ("C", "NSE")])
        task = asyncio.create_task(stream.run())
        await _until(lambda: stream.reconnects >= 1 and stream.state == "live" and stream.ticks >= 2)
        stream.stop()
        hold.set()
        await asyncio.wait_for(task, 2)
        return stream, states, first, second
    stream, states, first, second = run(go())
    assert states == ["live", "reconnecting", "live"]
    assert json.loads(second.sent[0]) == {"sub": ["NSE:A", "NSE:B", "NSE:C"]}       # every symbol, on the new socket
    assert stream.reconnects == 1 and stream.auth_failed is False


def test_a_refused_token_stops_a_hub_stream_and_says_renew():
    async def go():
        stream = _Stream(refuse=BrokerAPIError("401 Unauthorized", status_code=401))
        stream.stop_on_auth_error = True
        await asyncio.wait_for(stream.run(), 2)          # returns by itself: no retry storm
        return stream
    stream = run(go())
    assert stream.attempts == 1 and stream.state == "renew_needed" and stream.auth_failed and stream.reconnects == 0


def test_outside_the_hub_a_refused_token_is_retried_as_before():
    async def go():
        stream = _Stream(refuse=BrokerAuthenticationError("expired"))
        task = asyncio.create_task(stream.run())
        await _until(lambda: stream.attempts >= 3)
        stream.stop()
        await asyncio.wait_for(task, 2)
        return stream
    stream = run(go())
    assert stream.attempts >= 3 and stream.auth_failed is False and stream.state == "reconnecting"


def test_a_network_failure_is_retried_even_on_a_hub_stream():
    async def go():
        stream = _Stream(refuse=ConnectionError("no route"))
        stream.stop_on_auth_error = True
        task = asyncio.create_task(stream.run())
        await _until(lambda: stream.attempts >= 3)
        stream.stop()
        await asyncio.wait_for(task, 2)
        return stream
    stream = run(go())
    assert stream.attempts >= 3 and not stream.auth_failed


# --- the worker side ----------------------------------------------------------------------------------------------------

class _Adapter:
    name = "upstox"

    def __init__(self, token="t1"):
        self.access_token = token


def _factory(made: List[_Stream], hold: asyncio.Event, refuse_token: str = "expired"):
    def factory(adapter):
        async def connector(url):
            return _Conn([], hold=hold)
        s = _Stream(connector, refuse=BrokerAPIError("401", status_code=401) if adapter.access_token == refuse_token else None)
        made.append(s)
        return s
    return factory


def test_one_stream_per_connection_carries_every_consumers_symbols_positions_first(monkeypatch):
    monkeypatch.setattr(fanin.config, "BYOB_SESSION_SYMBOL_CAP", 4)
    now = datetime.now(UTC)

    async def go():
        made: List[_Stream] = []
        hold = asyncio.Event()
        hub = data_hub.DataHub(stream_factory=_factory(made, hold))
        ns = data_hub.org_namespace(77)
        kept = await data_hub.register_symbols(ns, "upstox", [("NIFTY", "NSE"), ("TCS", "NSE")], Priority.CHART, now=now)
        await data_hub.register_symbols(ns, "upstox", [("W1", "NSE"), ("W2", "NSE"), ("W3", "NSE")], Priority.WATCHLIST, now=now)
        await hub.ensure_org(77, _Adapter(), [("LEG1", "NFO")], now)
        await _until(lambda: made and made[0].state == "live")
        await hub.ensure_org(77, _Adapter(), [("LEG1", "NFO")], now)       # the next cycle: the same stream
        wanted = dict(made[0]._wanted)
        status = await data_hub.read_status(ns)
        hold.set()
        await hub.stop_all()
        return made, kept, wanted, status
    made, kept, wanted, status = run(go())
    assert len(made) == 1 and kept == [("NIFTY", "NSE"), ("TCS", "NSE")]
    assert set(wanted) == {"NFO:LEG1", "NSE:NIFTY", "NSE:TCS", "NSE:W1"}          # the cap (4): position, charts, then the list
    assert status.state == "live" and status.subscribed == 4


def test_hub_ticks_stay_with_their_connection():
    async def go():
        hub = data_hub.DataHub()
        mine, other = _Stream(), _Stream()
        hub.attach(mine, data_hub.org_namespace(1), "upstox")
        hub.attach(other, data_hub.org_namespace(2), "upstox")
        await mine.set_symbols([("RELIANCE", "NSE")])
        await mine._handle(json.dumps({"NSE:RELIANCE": 2500.0}))
        return mine
    mine = run(go())
    assert mine.notify_listeners is False and mine.stop_on_auth_error is True
    one = run(data_hub.tick_cache_for(data_hub.org_namespace(1)).fresh("upstox", "NSE", "RELIANCE"))
    assert one is not None and one.ltp == 2500.0
    assert run(data_hub.tick_cache_for(data_hub.org_namespace(2)).get("upstox", "NSE", "RELIANCE")) is None
    assert run(st.tick_cache.get("upstox", "NSE", "RELIANCE")) is None                 # never the platform-wide keys


class _RestBroker:
    name = "upstox"

    def __init__(self):
        self.calls = 0

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        self.calls += 1
        return None

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        self.calls += 1
        return 99.0


def test_the_paper_engine_reads_the_hub_tick_and_falls_back_to_rest():
    ns = data_hub.org_namespace(5)
    cache = data_hub.HubTickCache(ns)
    data_hub._hub_caches[ns] = cache
    now = datetime.now(UTC)
    run(cache.put(Tick(broker="upstox", exchange="NSE", symbol="NIFTY", ltp=24000.0, exchange_ts=now, received_at=now)))
    broker = _RestBroker()
    svc = MarketDataService(broker)          # type: ignore[arg-type]
    svc.tick_namespace = ns
    assert run(svc.get_ltp("NIFTY", "NSE", now=now)) == 24000.0 and broker.calls == 0
    assert run(svc.get_ltp("TCS", "NSE", now=now)) == 99.0 and broker.calls == 2      # not streamed: REST, as before


def test_renew_prompt_once_then_resume_with_every_symbol_after_a_new_login():
    now = datetime.now(UTC)

    async def go():
        made: List[_Stream] = []
        hold = asyncio.Event()
        hub = data_hub.DataHub(stream_factory=_factory(made, hold))
        pairs = [("A", "NSE"), ("B", "NSE")]
        await hub.ensure_org(9, _Adapter("expired"), pairs, now)
        await _until(lambda: made[0].state == "renew_needed")
        for _ in range(4):                                      # four more worker cycles on the same dead login
            await hub.ensure_org(9, _Adapter("expired"), pairs, now)
        prompts = dict(hub.prompts)
        dead = await data_hub.read_status(data_hub.org_namespace(9))
        await hub.ensure_org(9, _Adapter("fresh"), pairs, now)   # the trader renewed the login
        await _until(lambda: len(made) == 2 and made[1].state == "live")
        live = await data_hub.read_status(data_hub.org_namespace(9))
        hold.set()
        await hub.stop_all()
        return made, prompts, dead, live
    made, prompts, dead, live = run(go())
    assert made[0].attempts == 1 and len(prompts) == 1 and dead.state == "renew_needed"
    assert len(made) == 2 and made[1].subscribed == 2 and live.state == "live"


# --- the browser ----------------------------------------------------------------------------------------------------------

SID = "tab0123456789"


def test_live_routes_answer_404_while_the_flag_is_off():
    headers, me = _owner(_email("off"))
    _store_credentials(me)
    assert client.put("/api/live/ticks", headers=headers, json={"sid": SID, "items": [{"symbol": "NIFTY"}]}).status_code == 404
    assert client.get("/api/live/hub", headers=headers).status_code == 404
    assert client.get("/api/live/hub").status_code == 401


def test_put_ticks_registers_on_the_organisations_connection_and_says_its_state():
    headers, me = _hub_user("put")
    res = client.put("/api/live/ticks", headers=headers, json={"sid": SID, "items": [{"symbol": "nifty"}, {"symbol": "TCS", "exchange": "NSE"}]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["accepted"] == 2 and body["streamed"] == 2
    assert body["status"]["state"] == "polling" and body["status"]["broker_name"] == "Upstox"
    assert "org." not in json.dumps(body)                                   # no internal ids to the browser
    plan = run(fanin.session_symbols.plan(data_hub.org_namespace(me["tenant_id"]), 50, datetime.now(UTC)))
    assert set(plan.kept) == {("NIFTY", "NSE"), ("TCS", "NSE")}
    bad = client.put("/api/live/ticks", headers=headers, json={"sid": "x", "items": []})
    assert bad.status_code == 422
    many = client.put("/api/live/ticks", headers=headers, json={"sid": SID, "items": [{"symbol": f"S{i}"} for i in range(51)]})
    assert many.status_code == 422


def test_hub_status_says_renew_when_the_login_ended_in_plain_words():
    headers, me = _hub_user("renew", status=BrokerTokenStatus.EXPIRED.value)
    status = client.get("/api/live/hub", headers=headers).json()["status"]
    assert status["state"] == "renew_needed" and status["message"] == "Your Upstox login has ended. Log in again to resume live prices."
    assert status["broker"] == "upstox" and status["account_label"] == "primary"


def test_hub_status_is_null_without_a_linked_broker():
    headers, me = _owner(_email("nobroker"))
    _flags([me["tenant_id"]], ui_v2=True, data_hub=True)
    assert client.get("/api/live/hub", headers=headers).json() == {"status": None}


def test_browser_feed_sends_only_the_tabs_symbols_once_per_change_and_the_state():
    headers, me = _hub_user("feed")
    other_headers, other = _hub_user("feed-other")
    client.put("/api/live/ticks", headers=headers, json={"sid": SID, "items": [{"symbol": "NIFTY"}]})
    ns = data_hub.org_namespace(me["tenant_id"])
    cache = data_hub.HubTickCache(ns)
    other_cache = data_hub.HubTickCache(data_hub.org_namespace(other["tenant_id"]))
    now = datetime.now(UTC)

    async def tick(c, symbol, ltp, ts):
        await c.put(Tick(broker="upstox", exchange="NSE", symbol=symbol, ltp=ltp, exchange_ts=ts, received_at=ts))
        await c.flush()

    async def go():
        await data_hub.publish_status(data_hub.HubStatus(namespace=ns, broker="upstox", state="live", since=now.isoformat()))
        await tick(cache, "NIFTY", 24000.0, now)
        await tick(cache, "TCS", 4000.0, now)                    # not registered by this tab
        await tick(other_cache, "NIFTY", 1.0, now)               # another organisation's connection
        clock = [0.0]
        feed = data_hub.BrowserFeed(_session_factory, me["id"], SID, clock=lambda: clock[0])
        first = await feed.poll()
        data_hub._snapshot_memo.clear()
        second = await feed.poll()                               # nothing moved: nothing sent
        await tick(cache, "NIFTY", 24001.5, now + timedelta(seconds=1))
        data_hub._snapshot_memo.clear()
        third = await feed.poll()
        await data_hub.publish_status(data_hub.HubStatus(namespace=ns, broker="upstox", state="reconnecting", since=now.isoformat()))
        clock[0] = 10.0
        fourth = await feed.poll()
        return first, second, third, fourth
    first, second, third, fourth = run(go())
    assert first[0][0] == "hub" and first[0][1]["state"] == "live"
    assert first[1] == ("ticks", {"items": [{"symbol": "NIFTY", "exchange": "NSE", "ltp": 24000.0, "ts": now.isoformat()}]})
    assert second == []
    assert third == [("ticks", {"items": [{"symbol": "NIFTY", "exchange": "NSE", "ltp": 24001.5, "ts": (now + timedelta(seconds=1)).isoformat()}]})]
    assert fourth[0][0] == "hub" and fourth[0][1]["state"] == "reconnecting"
    assert "reconnecting" in fourth[0][1]["message"] and "Upstox" in fourth[0][1]["message"]


def test_the_live_stream_carries_the_feeds_events_and_survives_a_failing_feed(monkeypatch):
    monkeypatch.setattr(live_routes, "session_factory", _session_factory)

    class _Feed:
        def __init__(self, fail=False):
            self.fail = fail

        async def poll(self):
            if self.fail:
                raise RuntimeError("boom")
            return [("ticks", {"items": [{"symbol": "NIFTY", "exchange": "NSE", "ltp": 1.0, "ts": "t"}]})]

    async def body(feed):
        out = []
        polls = [0]

        async def disconnected():
            polls[0] += 1
            return polls[0] > 1
        async for chunk in live_routes.events(1, disconnected, poll_seconds=0, feed=feed):
            out.append(chunk.decode())
        return "".join(out)
    good = run(body(_Feed()))
    assert "event: ticks" in good and '"ltp":1.0' in good
    bad = run(body(_Feed(fail=True)))
    assert "event: ready" in bad and "event: ticks" not in bad


# --- the API reads through the hub ------------------------------------------------------------------------------------------

def test_ltp_reads_the_organisations_hub_tick_not_the_shared_feed(monkeypatch):
    headers, me = _hub_user("ltp")
    now = datetime.now(UTC)
    ns = data_hub.org_namespace(me["tenant_id"])
    run(data_hub.HubTickCache(ns).put(Tick(broker="upstox", exchange="NSE", symbol="NIFTY", ltp=24010.0, exchange_ts=now, received_at=now)))
    run(st.tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol="NIFTY", ltp=1.0, exchange_ts=now, received_at=now)))
    body = client.get("/api/market-data/ltp?symbol=NIFTY", headers=headers).json()
    assert body["source"] == "tick" and body["ltp"] == 24010.0
    plan = run(fanin.session_symbols.plan(ns, 50, datetime.now(UTC)))
    assert ("NIFTY", "NSE") in plan.kept                                    # the chart's symbol is now on the hub stream


def test_ltp_with_the_hub_off_reads_the_shared_feed_as_before():
    headers, me = _owner(_email("ltp-off"))
    _store_credentials(me)
    now = datetime.now(UTC)
    run(st.tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol="NIFTY", ltp=24020.0, exchange_ts=now, received_at=now)))
    body = client.get("/api/market-data/ltp?symbol=NIFTY", headers=headers).json()
    assert body["source"] == "tick" and body["ltp"] == 24020.0


def test_quotes_read_hub_ticks_for_the_organisation():
    headers, me = _hub_user("quotes")
    now = datetime.now(UTC)
    ns = data_hub.org_namespace(me["tenant_id"])
    run(data_hub.HubTickCache(ns).put(Tick(broker="upstox", exchange="NSE", symbol="TCS", ltp=4001.0, exchange_ts=now, received_at=now)))
    res = client.post("/api/market-data/quotes", headers=headers, json={"items": [{"symbol": "TCS", "exchange": "NSE"}]})
    assert res.status_code == 200, res.text
    row = res.json()["items"][0]
    assert row["ltp"] == 4001.0 and res.json()["broker_calls"] == 0


def test_shared_chain_one_fetch_per_connection_in_the_window(monkeypatch):
    monkeypatch.setattr(data_hub, "CHAIN_SNAPSHOT_SECONDS", 5)
    calls: Dict[str, int] = {}

    def fetcher(tag):
        async def fetch():
            calls[tag] = calls.get(tag, 0) + 1
            await asyncio.sleep(0.01)
            return OptionChain(underlying="NIFTY", expiry="2026-10-28", underlying_ltp=24000.0, rows=[])
        return fetch

    async def go():
        a = await asyncio.gather(*[data_hub.shared_chain(1, "NIFTY", None, fetcher("c1")) for _ in range(5)])
        b = await data_hub.shared_chain(1, "NIFTY", None, fetcher("c1"))
        c = await data_hub.shared_chain(2, "NIFTY", None, fetcher("c2"))       # another connection: its own fetch
        return a, b, c
    a, b, c = run(go())
    assert calls == {"c1": 1, "c2": 1} and all(x.underlying_ltp == 24000.0 for x in a) and b.underlying == "NIFTY"
    monkeypatch.setattr(data_hub, "CHAIN_SNAPSHOT_SECONDS", 0)
    run(data_hub.shared_chain(1, "NIFTY", None, fetcher("c1")))
    assert calls["c1"] == 2                                                    # 0 = no sharing


def test_rate_limited_outcome_is_counted_apart():
    from app.brokers import circuit_breaker
    from app.observability.metrics import BROKER_CALLS
    before = BROKER_CALLS.labels(broker="tm1fake", method="get_quote", outcome="rate_limited")._value.get()
    circuit_breaker.observe_call("tm1fake", "get_quote", BrokerAPIError("Too Many Requests", status_code=429))
    after = BROKER_CALLS.labels(broker="tm1fake", method="get_quote", outcome="rate_limited")._value.get()
    assert after == before + 1


# --- the worker ---------------------------------------------------------------------------------------------------------

def test_worker_streams_the_organisation_through_the_hub_when_the_flag_is_on(monkeypatch):
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _tenant, _worker
    t = _tenant(_email("worker"))
    _deploy(t, symbol="RELIANCE")
    _flags([t["tenant_id"]], data_hub=True)
    broker = _FakeBroker()
    worker = _worker(monkeypatch, broker)
    made: List[_Stream] = []
    hold: Dict[str, asyncio.Event] = {}

    def factory(adapter):
        async def connector(url):
            return _Conn([], hold=hold["e"])
        s = _Stream(connector)
        made.append(s)
        return s
    worker.hub = data_hub.DataHub(stream_factory=factory)
    worker.byob.hub = worker.hub

    async def go():
        hold["e"] = asyncio.Event()
        await worker.run_cycle(OPEN_NOW)
        await _until(lambda: made and made[0].state == "live")
        await worker.run_cycle(OPEN_NOW + timedelta(minutes=1))
        status = await data_hub.read_status(data_hub.org_namespace(t["tenant_id"]))
        old = worker.streams.status()
        hold["e"].set()
        await worker.hub.stop_all()
        return status, old
    status, old = run(go())
    assert len(made) == 1 and "NSE:RELIANCE" in made[0]._wanted and status.state == "live"
    assert old == {}                                                          # no second (old) stream on the same token


def test_worker_with_an_ended_login_says_renew_once(monkeypatch):
    from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _tenant, _worker
    t = _tenant(_email("worker-exp"), token_status="EXPIRED")
    _deploy(t, symbol="RELIANCE")
    _flags([t["tenant_id"]], data_hub=True)
    broker = _FakeBroker()

    async def rejecting_profile():
        raise BrokerAuthenticationError("Invalid token")
    broker.get_profile = rejecting_profile
    worker = _worker(monkeypatch, broker)
    worker.hub = data_hub.DataHub(stream_factory=lambda adapter: None)
    run(worker.run_cycle(OPEN_NOW))
    run(worker.run_cycle(OPEN_NOW + timedelta(minutes=1)))
    status = run(data_hub.read_status(data_hub.org_namespace(t["tenant_id"])))
    assert status is not None and status.state == "renew_needed"

    async def titles():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord.title).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.event_type == NotificationType.TOKEN_EXPIRED.value)))
    # The hub sends no notice of its own: the worker's existing token check / login reminder is the one notification,
    # and the app's renew notice reads the status above.
    assert not [x for x in run(titles()) if x.startswith("Live prices")]
    assert len(worker.hub.prompts) == 1


def test_byob_hub_session_renews_once_and_resumes_after_the_login(monkeypatch):
    """A user's own session (byob on): the broker refuses the token mid-day -> ONE prompt, no retries; the user logs in
    again (a new fingerprint on the row) -> the session reopens and subscribes its symbols again."""
    now = datetime.now(UTC)

    class _Rec:
        def __init__(self, verified):
            self.id, self.user_id, self.tenant_id, self.broker_name, self.account_label = 501, 601, 701, "upstox", "primary"
            self.token_status, self.token_expires_at, self.last_verified_at = "VALID", None, verified

    made: List[_Stream] = []
    hold = asyncio.Event()

    def factory(adapter):
        async def connector(url):
            return _Conn([], hold=hold)
        s = _Stream(connector, refuse=BrokerAPIError("401", status_code=401) if adapter == "dead" else None)
        made.append(s)
        return s
    hub = data_hub.DataHub()
    fan = fanin.UserSessionFanIn(stream_factory=factory, adapter_factory=lambda r: "dead" if r.last_verified_at == 1 else "ok", hub=hub)
    fan._hub_all = True

    async def go():
        rec = _Rec(1)
        ns = fanin.SessionRef.of(rec).namespace
        await fan.open(rec, now)
        await fan.symbols.replace(ns, Priority.CHART, [("NIFTY", "NSE"), ("TCS", "NSE")], now)
        await _until(lambda: made[0].state == "renew_needed")
        for _ in range(3):
            await fan._hub_renewals(None, {ns: rec}, now)
        prompts = len(hub.prompts)
        renewed = _Rec(2)
        await fan._hub_renewals(None, {ns: renewed}, now)
        reopened = ns not in fan.sessions
        await fan.open(renewed, now)
        await fan.refresh(ns, now)
        await _until(lambda: len(made) == 2 and made[1].state == "live")
        wanted = dict(made[1]._wanted)
        hold.set()
        await fan.close_all()
        return prompts, reopened, wanted
    prompts, reopened, wanted = run(go())
    assert made[0].attempts == 1 and prompts == 1 and reopened
    assert set(wanted) == {"NSE:NIFTY", "NSE:TCS"}                             # kept across the renewal


def test_flag_is_registered_off_by_default_and_catalogued():
    from app.platform.flag_catalog import FLAG_INFO
    assert "data_hub" in controls.FEATURE_FLAGS and "data_hub" in controls.DEFAULT_OFF_FLAGS
    assert FLAG_INFO["data_hub"].depends_on == ("ui_v2",) and FLAG_INFO["data_hub"].area == "data"
    from app.platform import trader_pack
    assert "data_hub" in trader_pack.PACK and trader_pack.PACK.index("ui_v2") < trader_pack.PACK.index("data_hub")
    assert User  # the model the feed reads


def test_a_stream_gets_the_broker_adapter_under_the_workers_wrappers():
    class Inner:
        name = "upstox"
        access_token = "tok"

    class Wrapped:
        name = "upstox"

        def __init__(self, inner):
            self.inner = inner

        @property
        def access_token(self):
            return self.inner.access_token
    inner = Inner()
    assert data_hub.stream_adapter(Wrapped(Wrapped(inner))) is inner and data_hub.stream_adapter(inner) is inner
    renewed = Inner()
    renewed.access_token = "renewed"
    assert data_hub.adapter_fingerprint(Wrapped(inner)) == data_hub.adapter_fingerprint(inner) != data_hub.adapter_fingerprint(renewed)
    assert "tok" not in data_hub.adapter_fingerprint(inner)          # hashed, never the token
