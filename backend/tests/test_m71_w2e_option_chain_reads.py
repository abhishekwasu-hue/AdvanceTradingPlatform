"""M71 W2-E: the option chain pages' broker reads - paced (the credential's data budget), shared per credential,
store-first for the spot's candles, and quiet on a busy broker (app/option_chain/chain_reads.py).

Calls per chain refresh before / after (spy broker), a second refresh inside the snapshot window = no extra chain call
and no extra history call, a 429 -> the last chain with its age (no failure, no usage count), the flag off = exactly as
before. Made-up chains only (strikes 100-140, an underlying called OCW2E...) - no real market data.
"""
import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import OptionChain, OptionChainRow
from app.market_data import api_budget
from app.market_data import hub as data_hub
from app.market_data.calendar import IST
from app.market_store import models as M
from app.market_store import reader as R
from app.option_chain import as_of, chain_reads
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_m71_pl3_read_service import DAY, Spy, _clear, _minutes, _store_minutes
from tests.test_oc2_option_chain_analyses import _ChainBroker, _install, _usage, _v2_owner
from tests.test_phase_aa_market_data_api import _store_credentials

URL = "/api/option-chain/analyses"
CHARTS = "/api/option-chain/charts"
STORE_FLAG = M.FLAG
run = asyncio.run


def _name() -> str:
    return "OCWTWOE" + uuid.uuid4().hex[:6].upper()


def _chain(name: str, spot: float = 118.0) -> OptionChain:
    expiry = (datetime.now(UTC).astimezone(IST).date() + timedelta(days=7)).isoformat()
    rows = [OptionChainRow(strike=float(k), call_oi=1000.0 + k, put_oi=900.0 + 2 * k, call_ltp=max(1.0, 130.0 - k),
                           put_ltp=max(1.0, k - 105.0), call_iv=15.0, put_iv=16.0) for k in range(100, 145, 5)]
    return OptionChain(underlying=name, expiry=expiry, underlying_ltp=spot, rows=rows)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    chain_reads.reset()
    api_budget.reset()
    R.reset_cache()
    # the market is open in these tests unless one says otherwise (the closed market's spot path is tested apart)
    monkeypatch.setattr(as_of, "as_of_for", lambda now, holidays, exchange: as_of.AsOf(now, False))
    yield
    chain_reads.reset()


def _owner(tag: str, store: bool):
    headers, me = _v2_owner(f"w2e-{tag}")
    _store_credentials(me)
    if store:
        _set_flag(STORE_FLAG, me["tenant_id"], on=True)
    return headers, me


def _numbers(body):
    """What each analysis shows now (the session path's last point carries the request time, so it is left out)."""
    return [(a["key"], a["state"], a.get("values"), (a.get("tables") or [{}])[0].get("rows")) for a in body["analyses"]]


def _refresh(headers, name, n=2, url=URL):
    return [client.get(url, headers=headers, params={"symbol": name}).json() for _ in range(n)]


# --- calls per refresh: before (flag off) / after (flag on) -------------------------------------------------------------

def test_flag_off_reads_the_broker_on_every_refresh_exactly_as_before(monkeypatch):
    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    _install(monkeypatch, broker)
    taken = []
    real_take = api_budget.take

    async def spy_take(*a, **kw):
        taken.append(a)
        return await real_take(*a, **kw)
    monkeypatch.setattr(api_budget, "take", spy_take)
    headers, me = _owner("off", store=False)
    bodies = _refresh(headers, name)
    assert [b["state"] for b in bodies] == ["ok", "ok"]
    assert len(broker.reads) == 2 and taken == []           # BEFORE: one unpaced chain call per refresh
    assert "snapshot" not in bodies[1] and _usage(me["tenant_id"]) == 2


def test_flag_on_paces_the_call_and_a_second_refresh_in_the_window_makes_no_broker_call(monkeypatch):
    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    _install(monkeypatch, broker)
    taken = []
    real_take = api_budget.take

    async def spy_take(record_id, broker_name, wait_seconds=None, kind="quote"):
        taken.append((record_id, kind))
        return await real_take(record_id, broker_name, wait_seconds, kind)
    monkeypatch.setattr(api_budget, "take", spy_take)
    headers, me = _owner("on", store=True)
    first, second = _refresh(headers, name)
    assert first["state"] == second["state"] == "ok"
    assert len(broker.reads) == 1                           # AFTER: one chain call per credential per snapshot window
    assert len(taken) == 1 and taken[0][1] == "quote"       # ... drawn from the credential's data budget first
    assert _numbers(first) == _numbers(second)
    assert "snapshot" not in first and "snapshot" not in second


def test_the_analytics_output_is_the_same_with_the_flag_on_or_off(monkeypatch):
    name = _name()
    chain = _chain(name)
    _install(monkeypatch, _ChainBroker({name: chain}))
    off_headers, _ = _owner("same-off", store=False)
    on_headers, _ = _owner("same-on", store=True)
    off = client.get(URL, headers=off_headers, params={"symbol": name}).json()
    on = client.get(URL, headers=on_headers, params={"symbol": name}).json()
    assert off["state"] == on["state"] == "ok"
    assert _numbers(off) == _numbers(on) and off["spot"] == on["spot"] and off["expiry"] == on["expiry"]
    assert set(on) == set(off)                              # no new key on a fresh read


def test_chains_are_shared_per_credential_only(monkeypatch):
    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    _install(monkeypatch, broker)
    a, _ = _owner("share-a", store=True)
    b, _ = _owner("share-b", store=True)
    _refresh(a, name, 1)
    _refresh(b, name, 1)
    assert len(broker.reads) == 2                           # another organisation's credential never reads this copy


# --- a busy broker: the last chain with its age ---------------------------------------------------------------------------

def test_a_429_shows_the_last_chain_with_its_age_and_backs_off(monkeypatch):
    monkeypatch.setattr(data_hub, "CHAIN_SNAPSHOT_SECONDS", 0)       # every refresh asks the broker (no shared copy)
    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    _install(monkeypatch, broker)
    headers, me = _owner("429", store=True)
    first = client.get(URL, headers=headers, params={"symbol": name}).json()
    assert first["state"] == "ok" and _usage(me["tenant_id"]) == 1
    broker.chains[name] = BrokerAPIError("Too Many Requests", status_code=429)
    res = client.get(URL, headers=headers, params={"symbol": name})
    assert res.status_code == 200
    busy = res.json()
    assert busy["state"] == "ok" and _numbers(busy) == _numbers(first)
    snap = busy["snapshot"]
    assert snap["reason"] == "busy" and snap["age_seconds"] >= 0
    assert busy["data_as_of"] == snap["captured_at"]          # the page's "Data as of" is the earlier read's time
    assert datetime.fromisoformat(snap["captured_at"]) <= datetime.fromisoformat(busy["generated_at"])
    assert _usage(me["tenant_id"]) == 1                       # no new read, nothing counted
    reads = len(broker.reads)
    again = client.get(URL, headers=headers, params={"symbol": name}).json()
    assert again["state"] == "ok" and "snapshot" in again
    assert len(broker.reads) == reads                         # backing off: no broker call during the back-off


def test_a_429_without_an_earlier_chain_is_a_plain_busy_state(monkeypatch):
    name = _name()
    _install(monkeypatch, _ChainBroker({name: BrokerAPIError("rate limited", status_code=429)}))
    headers, me = _owner("429-none", store=True)
    res = client.get(URL, headers=headers, params={"symbol": name})
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "unavailable" and body["message"] == chain_reads.BUSY_MESSAGE and body["analyses"] == []
    assert "429" not in body["message"] and _usage(me["tenant_id"]) == 0


def test_no_budget_token_never_reaches_the_broker_and_is_quiet(monkeypatch):
    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    _install(monkeypatch, broker)

    async def no_token(*a, **kw):
        return False
    monkeypatch.setattr(api_budget, "take", no_token)
    headers, _ = _owner("budget", store=True)
    body = client.get(URL, headers=headers, params={"symbol": name}).json()
    assert broker.reads == [] and body["state"] == "unavailable" and body["message"] == chain_reads.BUSY_MESSAGE


def test_charts_page_reads_the_same_way(monkeypatch):
    monkeypatch.setattr(data_hub, "CHAIN_SNAPSHOT_SECONDS", 0)
    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    _install(monkeypatch, broker)
    headers, _ = _owner("charts", store=True)
    first = client.get(CHARTS, headers=headers, params={"symbol": name}).json()
    assert first["state"] == "ok" and len(broker.reads) == 1
    broker.chains[name] = BrokerAPIError("Too Many Requests", status_code=429)
    busy = client.get(CHARTS, headers=headers, params={"symbol": name}).json()
    assert busy["state"] == "ok" and busy["snapshot"]["reason"] == "busy" and busy["data_as_of"] == busy["snapshot"]["captured_at"]


# --- the chain read itself (no route) ------------------------------------------------------------------------------------

def _record(rid: int):
    return SimpleNamespace(id=rid, broker_name="upstox", account_label="primary", user_id=None, tenant_id=None)


def _read(record, broker, name, **kw):
    async def go():
        async with _session_factory() as session:
            return await chain_reads.read(session, None, record, name, None, build=lambda r: broker, **kw)
    return run(go())


def test_a_closed_market_reuses_the_chain_read_after_the_close(monkeypatch):
    monkeypatch.setattr(data_hub, "CHAIN_SNAPSHOT_SECONDS", 0)
    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    record = _record(9_400_000 + uuid.uuid4().int % 100_000)
    close = datetime.now(UTC) - timedelta(minutes=30)
    got = _read(record, broker, name, closed_since=close)
    assert got.chain is not None and got.calls == 1 and not got.reused
    again = _read(record, broker, name, closed_since=close)
    assert again.calls == 0 and again.reused and not again.stale and len(broker.reads) == 1
    assert again.chain == got.chain
    later = _read(record, broker, name, closed_since=datetime.now(UTC) + timedelta(seconds=1))   # read before that close
    assert later.calls == 1 and len(broker.reads) == 2


def test_the_back_off_grows_and_ends_after_a_good_read(monkeypatch):
    monkeypatch.setattr(data_hub, "CHAIN_SNAPSHOT_SECONDS", 0)
    monkeypatch.setattr(chain_reads, "BACKOFF_FIRST_SECONDS", 0.01)
    name = _name()
    record = _record(9_500_000 + uuid.uuid4().int % 100_000)
    broker = _ChainBroker({name: BrokerAPIError("Too many requests", status_code=429)})
    first = _read(record, broker, name)
    assert first.state == chain_reads.BUSY and first.calls == 1
    assert chain_reads._cool_local[record.id][1] == 1
    run(asyncio.sleep(0.05))
    second = _read(record, broker, name)
    assert second.state == chain_reads.BUSY and chain_reads._cool_local[record.id][1] == 2
    run(asyncio.sleep(0.1))
    broker.chains[name] = _chain(name)
    ok = _read(record, broker, name)
    assert ok.chain is not None and record.id not in chain_reads._cool_local


def test_busy_detection_covers_429_budget_and_words():
    assert chain_reads.is_busy(BrokerAPIError("x", status_code=429))
    assert chain_reads.is_busy(api_budget.BudgetUsed("no token"))
    assert chain_reads.is_busy(RuntimeError("HTTP error: Too Many Requests"))
    assert not chain_reads.is_busy(BrokerAPIError("bad", status_code=400))


# --- the spot's candles: store first, a second refresh = zero history calls -----------------------------------------------

def test_the_last_index_close_reads_the_store_and_a_second_refresh_makes_zero_history_calls(monkeypatch):
    from app.market_store import sources
    from tests.test_m71_pl3_read_service import _user
    user = _user()
    key = "NSE:OCWTWOESPOT"
    now = datetime.combine(DAY, time(13, 5), IST)
    _store_minutes(key, _minutes(DAY, datetime.combine(DAY, time(11, 0), IST)), user.tenant_id, f"broker:upstox:u{user.id}")
    spy = Spy(upto=now)
    rid = 9_600_000 + uuid.uuid4().int % 100_000

    async def own(session, u, symbol, exchange, *, broker=None):
        return M.RestSource(adapter=spy, record_id=rid, broker="upstox")
    monkeypatch.setattr(sources, "own_rest", own)

    async def go():
        async with _session_factory() as session:
            return await as_of.stored_last_close(user, session, "OCWTWOESPOT", "NSE", "upstox", now=now)
    first = run(go())
    assert first is not None and spy.calls == 1             # only the newest bars, once
    assert first[1] + timedelta(minutes=1) <= now           # a closed bar only (no look-ahead)
    second = run(go())
    assert second == first and spy.calls == 1               # SECOND refresh: zero extra history calls
    _clear(key)


def test_flag_on_the_last_close_comes_from_the_store_not_the_chart_read(monkeypatch):
    from app.market_data import candles_routes
    from app.option_chain import chain_reads as CR
    chart_reads = []

    async def chart(*a, **kw):
        chart_reads.append(1)
        return {"symbols": {}}
    monkeypatch.setattr(candles_routes, "candles", chart)
    stamp = datetime(2026, 10, 28, 9, 59, tzinfo=UTC)

    async def stored(user, session, symbol, exchange, broker=None, now=None):
        return 24100.5, stamp
    monkeypatch.setattr(as_of, "stored_last_close", stored)
    user = SimpleNamespace(id=1, tenant_id=1)

    async def flag(on):
        async def store_on(session, tenant_id):
            return on
        monkeypatch.setattr(CR, "store_on", store_on)
        async with _session_factory() as session:
            return await as_of.own_last_close(user, session, "NIFTY")
    assert run(flag(True)) == (24100.5, stamp) and chart_reads == []
    assert run(flag(False)) is None and chart_reads == [1]   # OFF: the chart's read, as before


# --- the chain history recorder: the data pool under the flag ----------------------------------------------------------

def test_the_recorder_slot_uses_the_data_pool_and_no_token_is_a_gap(monkeypatch):
    from app.instruments import master
    from app.market_lake import chain_recorder, chain_store, intraday_history

    class Budget:
        spare = 0

        def now(self):
            return 0.0

        async def acquire_spare(self, reserve, deadline=None):
            Budget.spare += 1
            return True

    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    record = SimpleNamespace(id=4343, broker_name="upstox", account_label="primary")
    user = SimpleNamespace(id=7)
    expiry = date.today() + timedelta(days=7)

    async def plan(session, now, report):
        return [(name, expiry), (name, expiry + timedelta(days=7))]

    async def owner_tenant(session):
        return 1

    async def owner_session(session, tenant_id, now):
        return user, record

    async def on(session, *a):
        return True

    async def exists(*a, **kw):
        return False

    async def write(own, tenant_id, underlying, exp, chain, captured, **kw):
        return SimpleNamespace(status="OK", raw_bytes=1, stored_bytes=1)
    monkeypatch.setattr(chain_recorder, "_plan", plan)
    monkeypatch.setattr(intraday_history, "owner_tenant", owner_tenant)
    monkeypatch.setattr(intraday_history, "owner_session", owner_session)
    monkeypatch.setattr(chain_recorder, "enabled", on)
    monkeypatch.setattr(chain_recorder, "store_first", on)
    monkeypatch.setattr(chain_store, "exists", exists)
    monkeypatch.setattr(chain_store, "write", write)
    monkeypatch.setattr(master, "INDEX_SYMBOLS", {})
    grants = [True, False]

    async def take(record_id, broker_name, wait_seconds=None, kind="quote"):
        return grants.pop(0)
    monkeypatch.setattr(api_budget, "take", take)
    report = run(chain_recorder.record_slot(_session_factory, budget_for=lambda t, k: Budget(),
                                            adapter_factory=lambda r: broker))
    assert Budget.spare == 0                                  # never the worker's order budget
    assert report.reads == 1 and report.stored == 1 and len(broker.reads) == 1
    assert report.status == "rate_budget" and report.skipped == 1


def test_ask_reads_the_same_way(monkeypatch):
    from app.option_chain import ask_adapter
    from tests.test_oc2_option_chain_analyses import _v2_owner as owner
    monkeypatch.setattr(data_hub, "CHAIN_SNAPSHOT_SECONDS", 0)
    name = _name()
    broker = _ChainBroker({name: _chain(name)})
    _install(monkeypatch, broker)
    headers, me = owner("w2e-ask")
    _store_credentials(me)
    _set_flag(STORE_FLAG, me["tenant_id"], on=True)
    from app.db.models import User

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            return await ask_adapter.read(session, user, name, None, datetime.now(UTC))
    first = run(go())
    assert first.state == "ok" and first.snapshot is None and len(broker.reads) == 1
    broker.chains[name] = BrokerAPIError("Too Many Requests", status_code=429)
    busy = run(go())
    assert busy.state == "ok" and busy.snapshot is not None and busy.snapshot["reason"] == "busy"
    assert busy.rows == first.rows
