"""UI2-4a2-pre: `POST /api/market-data/quotes` - the watchlist's batch quotes.

* one broker multi-quote call for many symbols, counted once against the credential's quote budget;
* the order: fresh ticks (under BT1 the session's own only), then this credential's cached quotes, then one batch for
  the still-unpriced symbols (single-flight per credential), its rows cached for `/ltp` and the next poll;
* no multi-quote call: one call per symbol within the budget, at most PER_SYMBOL_MAX_CALLS and none after the deadline;
* the budget refuses: nothing is called and every symbol says "budget used" (never a made-up price);
* a symbol with no price comes back missing with `ltp: null` - never 0;
* the previous close is remembered per broker, symbol and IST day;
* tenant-scoped (another organisation's broker is never used), the BT1 entitlement guard (Tier C 409, mixed live and EOD
  rows), input validation, the per-user rate limit, and the adapters' own multi-quote calls (Kite, Upstox, Angel One,
  Dhan, Fyers, through the contract-symbol wrapper), the shared instrument masters and the broker timestamps.
No network: fake brokers and mock HTTP transports."""
import asyncio
import dataclasses
import gzip
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import List
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.brokers import master_cache
from app.brokers.angel_one import AngelOneBroker
from app.brokers.contract_symbols import wrap_contract_symbols
from app.brokers.dhan import DhanBroker
from app.brokers.fyers import FyersBroker
from app.brokers.models import BrokerCredentials, Quote
from app.brokers.shoonya import ShoonyaBroker
from app.brokers.timestamps import parse_broker_timestamp
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.core import rate_limit
from app.db.models import BrokerCredentialRecord
from app.market_data import api_budget, entitlement, quotes_routes
from app.market_data.entitlement import DataTier
from app.market_data.fanin import SessionRef
from app.market_data.stream import Tick, TickCache, tick_cache
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_phase_aa_market_data_api import _Broker, _store_credentials
from tests.test_phase_k_commercial import _owner

run = asyncio.run
UTC = timezone.utc


def _email(tag: str) -> str:
    return f"ui24q-{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _sym(prefix: str) -> str:
    """A symbol no other test has a tick, a shared quote or a previous close for."""
    return f"{prefix}{uuid.uuid4().hex[:5].upper()}"


class _MultiBroker(_Broker):
    """A broker with a multi-quote call. Prices: 100 + index; `absent` are left out, `zero` come back at 0, `untimed`
    come without a time; `none` = the adapter has no call for this list after all."""
    multi_quote = True

    def __init__(self, absent=(), zero=(), untimed=(), fail=False, none=False):
        super().__init__()
        self.batch_calls: List[list] = []
        self.single_calls: List[str] = []
        self.absent, self.zero, self.untimed, self.fail, self.none = set(absent), set(zero), set(untimed), fail, none

    async def get_quotes_batch(self, items):
        if self.none:
            return None
        self.batch_calls.append(list(items))
        if self.fail:
            raise RuntimeError("socket closed by broker")
        now = datetime.now(UTC)
        out = {}
        for i, (exchange, symbol) in enumerate(items):
            if symbol in self.absent:
                continue
            price = 0.0 if symbol in self.zero else 100.0 + i
            out[(exchange, symbol)] = Quote(symbol=symbol, ltp=price, prev_close=price - 2 if price else None,
                                            timestamp=None if symbol in self.untimed else now - timedelta(seconds=1))
        return out

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        self.single_calls.append(symbol)
        return Quote(symbol=symbol, ltp=77.0, timestamp=datetime.now(UTC))


class _SingleBroker(_Broker):
    """No multi-quote call: one quote per symbol (optionally slow)."""

    def __init__(self, zero=(), delay=0.0):
        super().__init__()
        self.single_calls: List[str] = []
        self.zero, self.delay = set(zero), delay

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        self.single_calls.append(symbol)
        if self.delay:
            await asyncio.sleep(self.delay)
        return Quote(symbol=symbol, ltp=0.0 if symbol in self.zero else 50.0, timestamp=datetime.now(UTC))

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        raise AssertionError("an adapter with a quote call is asked once per symbol, never twice")


class _LtpOnlyBroker(_Broker):
    """No quote call of its own (the interface default): its LTP call is the one call per symbol."""

    def __init__(self):
        super().__init__()
        self.ltp_calls: List[str] = []

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        self.ltp_calls.append(symbol)
        return 42.0


@pytest.fixture(autouse=True)
def _clean():
    api_budget.reset()
    quotes_routes.reset()
    rate_limit.reset("market_data_quotes")
    yield
    api_budget.reset()
    quotes_routes.reset()
    rate_limit.reset("market_data_quotes")


def _count_tokens(monkeypatch, grant=True):
    taken: List[int] = []

    async def take(record_id, broker, wait_seconds=None, kind="quote"):
        taken.append(record_id)
        return grant(len(taken)) if callable(grant) else grant
    monkeypatch.setattr(api_budget, "take", take)
    return taken


def _post(headers, items, **extra):
    return client.post("/api/market-data/quotes", headers=headers, json={"items": items, **extra})


def _record(me, broker="upstox") -> BrokerCredentialRecord:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(BrokerCredentialRecord).where(
                BrokerCredentialRecord.tenant_id == me["tenant_id"], BrokerCredentialRecord.broker_name == broker))
    return run(go())


# --- the batch call ------------------------------------------------------------------------------------------------------

def test_one_broker_call_for_the_whole_list_counted_once_and_missing_is_never_zero(monkeypatch):
    headers, me = _owner(_email("batch"))
    _store_credentials(me)
    broker = _MultiBroker(absent={"NOSUCH"}, zero={"HALTED"})
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    taken = _count_tokens(monkeypatch)
    names = [_sym("S") for _ in range(12)]
    items = [{"symbol": n.lower(), "exchange": "nse"} for n in names] + [{"symbol": "NOSUCH"}, {"symbol": "HALTED"},
                                                                         {"symbol": "NIFTY 50", "exchange": "NSE"}, {"symbol": names[0]}]
    res = _post(headers, items)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(broker.batch_calls) == 1 and broker.single_calls == []         # ONE broker call for 15 symbols
    assert len(taken) == 1 and body["broker_calls"] == 1                       # counted once against the budget
    assert len(broker.batch_calls[0]) == 15                                    # the duplicate asked once
    rows = body["items"]
    assert [r["symbol"] for r in rows] == names + ["NOSUCH", "HALTED", "NIFTY 50"]
    first = rows[0]
    assert first["ltp"] == 100.0 and first["prev_close"] == 98.0 and first["change"] == 2.0
    assert first["change_pct"] == pytest.approx(2.0 / 98.0 * 100, abs=1e-3)
    assert first["missing"] is False and first["source"] == "quote" and first["stale"] is False and first["timestamp"]
    for row in (rows[12], rows[13]):                                           # not returned / returned at 0
        assert row["missing"] is True and row["ltp"] is None and row["change"] is None and "no price" in row["error"]
    assert body["missing"] == 2 and body["source"]["broker"] == "upstox"


def test_a_failed_batch_reports_the_brokers_words_and_never_a_price(monkeypatch):
    headers, me = _owner(_email("fail"))
    _store_credentials(me)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _MultiBroker(fail=True))
    _count_tokens(monkeypatch)
    body = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert all(r["missing"] and r["ltp"] is None and "socket closed" in r["error"] for r in body["items"])
    assert body["broker_calls"] == 1 and "socket closed" in body["warnings"][0]


def test_budget_refused_means_no_broker_call_and_every_symbol_says_budget_used(monkeypatch):
    headers, me = _owner(_email("budget"))
    _store_credentials(me)
    broker = _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    _count_tokens(monkeypatch, grant=False)
    body = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert broker.batch_calls == [] and body["broker_calls"] == 0
    assert all(r["missing"] and r["ltp"] is None and r.get("budget_used") for r in body["items"])
    assert any("budget used" in w for w in body["warnings"])


def test_the_real_budget_is_shared_with_ltp_and_refuses_once_the_minute_is_used(monkeypatch):
    """With the credential's API budget at one call a minute, the first poll takes it and the next is refused at once
    (no wait, no broker call) - the same budget /ltp draws from."""
    headers, me = _owner(_email("realbudget"))
    _store_credentials(me)
    broker = _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: False)
    monkeypatch.setattr(api_budget, "api_limits", lambda name: api_budget.ApiLimits(per_second=1, per_minute=1))
    first = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert first["broker_calls"] == 1 and first["missing"] == 0
    second = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert second["broker_calls"] == 0 and second["missing"] == 2 and len(broker.batch_calls) == 1


def test_batch_rows_are_shared_with_ltp_and_the_next_poll_and_only_unpriced_symbols_are_batched(monkeypatch):
    """Review item 7: every batch row is cached per credential and symbol (BATCH_CACHE_SECONDS) in /ltp's own shape;
    /ltp answers from it without a broker call, and the next poll batches only what is not cached."""
    headers, me = _owner(_email("shared"))
    _store_credentials(me)
    broker = _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    from app.market_data import candles_routes
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: (_ for _ in ()).throw(AssertionError("no broker call")))
    _count_tokens(monkeypatch)
    a, b, c = _sym("A"), _sym("B"), _sym("C")
    assert _post(headers, [{"symbol": a}, {"symbol": b}]).json()["broker_calls"] == 1
    ltp = client.get(f"/api/market-data/ltp?symbol={a}", headers=headers).json()
    assert ltp["ltp"] == 100.0 and ltp["shared"] is True and ltp["source"] == "quote"
    record_id = _record(me).id
    cached = run(api_budget.cached_quote(record_id, "NSE", b))
    assert cached["ltp"] == 101.0 and cached["prev_close"] == 99.0 and cached["source_broker"] == "upstox"
    body = _post(headers, [{"symbol": a}, {"symbol": b}, {"symbol": c}]).json()
    assert broker.batch_calls[-1] == [("NSE", c)] and body["broker_calls"] == 1     # only the uncached one
    assert [r["ltp"] for r in body["items"]] == [100.0, 101.0, 100.0] and body["items"][0]["shared"] is True
    assert body["items"][0]["change"] == 2.0                                   # the cached row keeps its previous close
    assert _post(headers, [{"symbol": a}, {"symbol": b}]).json()["broker_calls"] == 0
    assert quotes_routes.BATCH_CACHE_SECONDS == 8


def test_one_batch_fetch_per_credential_at_a_time(monkeypatch):
    """Review item 7: while another poll on the credential holds the batch fetch, this one takes that poll's rows from
    the shared cache and makes no call of its own; what the other poll did not cover says "budget used"."""
    headers, me = _owner(_email("flight"))
    _store_credentials(me)
    broker = _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    monkeypatch.setattr(quotes_routes, "QUOTE_WAIT_SECONDS", 0.4)
    _count_tokens(monkeypatch)
    record_id = _record(me).id
    a, b = _sym("A"), _sym("B")
    holder = run(api_budget.claim_fetch(record_id, *quotes_routes.BATCH_LOCK))
    assert holder is not None
    run(api_budget.store_quote(record_id, "NSE", a, {"symbol": a, "exchange": "NSE", "ltp": 250.0, "timestamp": datetime.now(UTC).isoformat(),
                                                    "source": "quote", "prev_close": 245.0}, ttl=8))
    body = _post(headers, [{"symbol": a}, {"symbol": b}]).json()
    assert broker.batch_calls == [] and body["broker_calls"] == 0
    assert body["items"][0]["ltp"] == 250.0 and body["items"][0]["change"] == 5.0
    assert body["items"][1]["missing"] and body["items"][1]["budget_used"] is True
    run(api_budget.release_fetch(record_id, *quotes_routes.BATCH_LOCK, holder))
    assert _post(headers, [{"symbol": b}]).json()["broker_calls"] == 1              # free again: fetched


def test_an_adapter_without_a_call_for_the_list_uses_the_batch_token_for_its_first_symbol(monkeypatch):
    """Review item 8a: get_quotes_batch answering None made no broker call, so its token pays for the first per-symbol
    call - never two tokens for one call."""
    headers, me = _owner(_email("none"))
    _store_credentials(me)
    broker = _MultiBroker(none=True)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    taken = _count_tokens(monkeypatch)
    body = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert len(broker.single_calls) == 2 and body["broker_calls"] == 2 and len(taken) == 2
    assert [r["ltp"] for r in body["items"]] == [77.0, 77.0]


# --- without a multi-quote call --------------------------------------------------------------------------------------------

def test_without_a_multi_quote_call_ticks_first_then_one_call_per_symbol_within_the_budget(monkeypatch):
    headers, me = _owner(_email("single"))
    _store_credentials(me)
    ticked, a, b, c = _sym("T"), _sym("A"), _sym("B"), _sym("C")
    now = datetime.now(UTC)
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol=ticked, ltp=321.5, exchange_ts=now - timedelta(seconds=1), received_at=now)))
    broker = _SingleBroker(zero={b})
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    taken = _count_tokens(monkeypatch, grant=lambda n: n <= 2)               # two tokens, then the budget is used
    body = _post(headers, [{"symbol": ticked}, {"symbol": a}, {"symbol": b}, {"symbol": c}]).json()
    rows = {r["symbol"]: r for r in body["items"]}
    assert rows[ticked]["ltp"] == 321.5 and rows[ticked]["source"] == "tick" and rows[ticked]["change"] is None
    assert ticked not in broker.single_calls                                  # a fresh tick costs no broker call
    assert rows[a]["ltp"] == 50.0 and rows[a]["source"] == "quote"
    # b: its quote was 0 -> missing, never 0, and no second call; c: no token left -> budget used.
    assert rows[b]["missing"] and rows[b]["ltp"] is None and not rows[b].get("budget_used")
    assert rows[c]["missing"] and rows[c]["ltp"] is None and rows[c]["budget_used"] is True
    assert broker.single_calls == [a, b] and len(taken) == 3 and body["broker_calls"] == 2


def test_per_symbol_calls_are_capped_per_request(monkeypatch):
    """Review item 2: however fast the broker and however full the budget, at most PER_SYMBOL_MAX_CALLS calls."""
    headers, me = _owner(_email("cap5"))
    _store_credentials(me)
    broker = _SingleBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    _count_tokens(monkeypatch)
    body = _post(headers, [{"symbol": _sym("S")} for _ in range(12)]).json()
    assert len(broker.single_calls) == quotes_routes.PER_SYMBOL_MAX_CALLS == 5 and body["broker_calls"] == 5
    assert sum(1 for r in body["items"] if r.get("budget_used")) == 7


def test_no_per_symbol_call_starts_after_the_deadline(monkeypatch):
    """Review item 2 (the reviewer's slow broker): a token is granted every time, but each call is slow - calls stop
    at the deadline instead of running one per symbol."""
    headers, me = _owner(_email("deadline"))
    _store_credentials(me)
    broker = _SingleBroker(delay=0.25)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    monkeypatch.setattr(quotes_routes, "QUOTE_WAIT_SECONDS", 0.4)
    _count_tokens(monkeypatch)
    started = time.monotonic()
    body = _post(headers, [{"symbol": _sym("S")} for _ in range(5)]).json()
    assert len(broker.single_calls) == 2 and body["broker_calls"] == 2       # at 0 s and 0.25 s; 0.5 s is past 0.4 s
    assert time.monotonic() - started < 2.0
    assert sum(1 for r in body["items"] if r.get("budget_used")) == 3


def test_an_adapter_without_a_quote_call_spends_one_token_on_its_ltp_call(monkeypatch):
    """Review item 2: the interface's default quote call asks no broker - no token is spent on it."""
    headers, me = _owner(_email("ltponly"))
    _store_credentials(me)
    broker = _LtpOnlyBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: wrap_contract_symbols(broker))
    taken = _count_tokens(monkeypatch)
    body = _post(headers, [{"symbol": _sym("A")}, {"symbol": _sym("B")}]).json()
    assert len(broker.ltp_calls) == 2 and len(taken) == 2 and body["broker_calls"] == 2
    assert all(r["ltp"] == 42.0 and r["source"] == "ltp" for r in body["items"])


def test_the_previous_close_of_the_day_gives_a_tick_its_change(monkeypatch):
    headers, me = _owner(_email("prev"))
    _store_credentials(me)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _MultiBroker())
    _count_tokens(monkeypatch)
    a = _sym("A")
    assert _post(headers, [{"symbol": a}]).json()["items"][0]["prev_close"] == 98.0
    api_budget.reset()                                                         # the 8 s quote cache gone, the close kept
    now = datetime.now(UTC)
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol=a, ltp=103.0, exchange_ts=now, received_at=now)))
    row = _post(headers, [{"symbol": a}]).json()["items"][0]
    assert row["source"] == "tick" and row["prev_close"] == 98.0 and row["change"] == 5.0


# --- scope, the guard, validation, the rate limit ---------------------------------------------------------------------------

def test_tenant_scoped_another_organisations_broker_is_never_used(monkeypatch):
    owner_headers, owner = _owner(_email("tenant-a"))
    _store_credentials(owner)
    other_headers, other = _owner(_email("tenant-b"))
    used = []

    def build(record):
        used.append(record.tenant_id)
        return _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", build)
    _count_tokens(monkeypatch)
    res = _post(other_headers, [{"symbol": "RELIANCE"}])
    assert res.status_code == 409 and "Settings > Brokers" in res.json()["detail"]
    assert _post(other_headers, [{"symbol": "RELIANCE"}], broker="upstox").status_code == 404
    assert used == []                                                          # tenant A's credential was never built
    assert _post(owner_headers, [{"symbol": "RELIANCE"}]).status_code == 200 and used == [owner["tenant_id"]]
    assert client.post("/api/market-data/quotes", json={"items": [{"symbol": "RELIANCE"}]}).status_code == 401


def test_validation_caps_the_list_and_refuses_keys_a_batch_could_be_split_by(monkeypatch):
    headers, me = _owner(_email("cap"))
    _store_credentials(me)
    broker = _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    _count_tokens(monkeypatch)
    assert _post(headers, [{"symbol": f"S{i}"} for i in range(51)]).status_code == 422
    assert _post(headers, []).status_code == 422
    for bad in ({"symbol": "   "}, {"symbol": "NSE_EQ|INE002A01018"}, {"symbol": "RELIANCE,TCS"}, {"symbol": "A" * 41},
                {"symbol": "RELIANCE<x>"}, {"symbol": "RELIANCE", "exchange": "XYZ"}, {"symbol": "RELIANCE", "exchange": "NSE|X"},
                {"symbol": "BSE:RELIANCE", "exchange": "NSE"}, {"symbol": "NSE:"}):
        assert _post(headers, [bad]).status_code == 422, bad
    assert broker.batch_calls == []
    body = _post(headers, [{"symbol": "nse:reliance", "exchange": "NSE"}, {"symbol": "M&M"}, {"symbol": "nifty  50"}]).json()
    assert [r["symbol"] for r in body["items"]] == ["RELIANCE", "M&M", "NIFTY 50"]
    assert _post(headers, [{"symbol": f"S{i}"} for i in range(50)]).status_code == 200


def test_rate_limited_per_user(monkeypatch):
    headers, me = _owner(_email("rl"))
    _store_credentials(me)
    other_headers, other = _owner(_email("rl-other"))
    _store_credentials(other)
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _MultiBroker())
    monkeypatch.setattr(rate_limit, "redis_backed", lambda: False)
    _count_tokens(monkeypatch)
    for _ in range(30):
        assert _post(headers, [{"symbol": "RELIANCE"}]).status_code == 200
    assert _post(headers, [{"symbol": "RELIANCE"}]).status_code == 429
    assert _post(other_headers, [{"symbol": "RELIANCE"}]).status_code == 200    # per user, not per organisation


def _byob_flag(on: bool, tenants):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["byob_data_tier"] = {"on": on, "tenants": list(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    run(go())


@pytest.fixture
def byob():
    tenants: List[int] = []
    yield lambda tenant_id: (tenants.append(tenant_id), _byob_flag(False, tenants))
    _byob_flag(False, [])


def test_byob_entitlement_tier_c_409_without_the_users_own_confirmed_session(monkeypatch, byob):
    headers, me = _owner(_email("byob"))
    _store_credentials(me, broker="zerodha")                                   # execution-only broker: never Tier A
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: _MultiBroker())
    _count_tokens(monkeypatch)
    byob(me["tenant_id"])
    res = _post(headers, [{"symbol": "RELIANCE"}, {"symbol": "TCS"}])
    assert res.status_code == 409 and res.headers.get("X-Data-Tier") == "C"
    _store_credentials(me, broker="upstox")                                   # the user's own confirmed session
    res = _post(headers, [{"symbol": _sym("R")}])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"]["broker"] == "upstox" and body["items"][0]["ltp"] == 100.0 and body["data_tier"]["tier"] == "A"
    assert body["data_tier"]["stale"] is False


def test_byob_reads_only_the_sessions_own_ticks_never_the_shared_feed(monkeypatch, byob):
    """Review item 3: under BT1 the shared live feed is not read (as BT1 /ltp); the session's own tick is."""
    from tests.test_market_data_service import _install_cache, _MemoryCache
    _install_cache(monkeypatch, _MemoryCache())                               # a session's ticks live in the cache
    headers, me = _owner(_email("byob-ticks"))
    _store_credentials(me)
    byob(me["tenant_id"])
    broker = _MultiBroker()
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    _count_tokens(monkeypatch)
    shared_only, own = _sym("S"), _sym("O")
    now = datetime.now(UTC)
    run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol=shared_only, ltp=999.0, exchange_ts=now, received_at=now)))
    namespace = SessionRef.of(_record(me)).namespace
    run(TickCache(namespace=namespace).put(Tick(broker="upstox", exchange="NSE", symbol=own, ltp=555.0, exchange_ts=now, received_at=now)))
    rows = {r["symbol"]: r for r in _post(headers, [{"symbol": shared_only}, {"symbol": own}]).json()["items"]}
    assert rows[own]["ltp"] == 555.0 and rows[own]["source"] == "tick"
    assert rows[shared_only]["ltp"] == 100.0 and rows[shared_only]["source"] == "quote"     # the broker, not the shared tick
    assert broker.batch_calls == [[("NSE", shared_only)]]


def test_byob_mixed_live_and_eod_rows_and_an_untimed_price_makes_the_badge_stale(monkeypatch, byob):
    """Review items 8b / 8e: a symbol outside the session's budget answers EOD-only on its own row while the others load;
    a priced row without a time means the list's age is unknown - the badge says stale."""
    headers, me = _owner(_email("byob-mixed"))
    _store_credentials(me)
    byob(me["tenant_id"])
    live_sym, eod_sym, untimed = _sym("L"), _sym("E"), _sym("U")
    broker = _MultiBroker(untimed={untimed})
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: broker)
    _count_tokens(monkeypatch)
    real = entitlement.resolve_many

    async def mixed(*args, **kwargs):
        ents = await real(*args, **kwargs)
        key = (eod_sym, "NSE")
        if key in ents:
            ents[key] = dataclasses.replace(ents[key], tier=DataTier.EOD, reason="outside your session's symbol budget: EOD only")
        return ents
    monkeypatch.setattr(entitlement, "resolve_many", mixed)
    body = _post(headers, [{"symbol": live_sym}, {"symbol": eod_sym}]).json()
    rows = {r["symbol"]: r for r in body["items"]}
    assert rows[live_sym]["ltp"] == 100.0 and rows[live_sym]["missing"] is False
    assert rows[eod_sym]["missing"] and rows[eod_sym]["ltp"] is None and "EOD only" in rows[eod_sym]["error"]
    assert rows[eod_sym]["data_tier"]["tier"] == "C"
    assert broker.batch_calls == [[("NSE", live_sym)]]                        # the EOD symbol never reaches the broker
    assert body["data_tier"]["stale"] is False
    body = _post(headers, [{"symbol": untimed}, {"symbol": _sym("T")}]).json()
    assert all(not r["missing"] for r in body["items"]) and body["items"][0]["timestamp"] is None
    assert body["data_tier"]["stale"] is True and body["data_tier"]["as_of"] is None


# --- the adapters' own multi-quote calls ---------------------------------------------------------------------------------

def test_kite_batch_is_one_quote_call_with_the_previous_close_and_exchange_time():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(urlparse(str(request.url)).query)["i"])
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE:RELIANCE": {"last_price": 2900.0, "ohlc": {"open": 2880, "high": 2910, "low": 2870, "close": 2850.0},
                             "last_trade_time": "2026-10-20 10:15:00"},
            "NSE:NIFTY 50": {"last_price": 25000.0, "ohlc": {"close": 24900.0}},
        }})
    broker = ZerodhaBroker(BrokerCredentials(api_key="k", access_token="t"),
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ZerodhaBroker.BASE_URL))
    wrapped = wrap_contract_symbols(broker)
    assert wrapped.multi_quote is True
    got = run(wrapped.get_quotes_batch([("NSE", "RELIANCE"), ("NSE", "NIFTY 50"), ("NSE", "NOSUCH")]))
    assert len(seen) == 1 and seen[0] == ["NSE:RELIANCE", "NSE:NIFTY 50", "NSE:NOSUCH"]
    assert set(got) == {("NSE", "RELIANCE"), ("NSE", "NIFTY 50")}
    assert got[("NSE", "RELIANCE")].prev_close == 2850.0 and got[("NSE", "RELIANCE")].timestamp is not None
    assert got[("NSE", "RELIANCE")].timestamp.astimezone(UTC).hour == 4                # 10:15 IST


def _upstox(handler) -> UpstoxBroker:
    return UpstoxBroker(BrokerCredentials(api_key="c", access_token="t"),
                        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=UpstoxBroker.BASE_URL))


UPSTOX_MASTER = gzip.compress(json.dumps([
    {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "trading_symbol": "RELIANCE", "instrument_type": "EQ"},
    {"instrument_key": "NSE_EQ|INE467B01029", "exchange": "NSE", "trading_symbol": "TCS", "instrument_type": "EQ"},
]).encode())


def test_upstox_batch_is_one_quote_call_matched_back_by_instrument_key_with_its_time():
    quote_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=UPSTOX_MASTER)
        quote_calls.append(request.url.params["instrument_key"])
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE_EQ:RELIANCE": {"instrument_token": "NSE_EQ|INE002A01018", "last_price": 2900.0, "net_change": 50.0,
                                "ohlc": {"close": 2900.0}, "last_trade_time": "1760935500000"},
            "NSE_EQ:TCS": {"instrument_token": "NSE_EQ|INE467B01029", "last_price": 4000.0},
        }})
    got = run(_upstox(handler).get_quotes_batch([("NSE", "RELIANCE"), ("NSE", "TCS"), ("NSE", "NOSUCH")]))
    assert quote_calls == ["NSE_EQ|INE002A01018,NSE_EQ|INE467B01029"]           # one call; the unknown symbol left out
    assert got[("NSE", "RELIANCE")].ltp == 2900.0 and got[("NSE", "RELIANCE")].prev_close == 2850.0
    # Review item 5: Upstox's last_trade_time is a string of epoch milliseconds - it is the quote's time now.
    assert got[("NSE", "RELIANCE")].timestamp == datetime(2025, 10, 20, 4, 45, tzinfo=UTC)
    assert got[("NSE", "TCS")].prev_close is None                                # no net_change: unknown, not invented


def test_upstox_single_quote_keeps_its_time_too():
    def handler(request: httpx.Request) -> httpx.Response:
        if "assets.upstox.com" in str(request.url):
            return httpx.Response(200, content=UPSTOX_MASTER)
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE_EQ:RELIANCE": {"instrument_token": "NSE_EQ|INE002A01018", "last_price": 2900.0, "last_trade_time": "1760935500000"}}})
    quote = run(_upstox(handler).get_quote_for_symbol("RELIANCE", "NSE"))
    assert quote.timestamp == datetime(2025, 10, 20, 4, 45, tzinfo=UTC)


def _angel(handler) -> AngelOneBroker:
    return AngelOneBroker(BrokerCredentials(api_key="k", access_token="jwt"),
                          client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=AngelOneBroker.BASE_URL))


def test_angel_one_batch_is_one_request_across_exchanges_and_prices_every_pair_of_one_token():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": True, "data": {"fetched": [
            {"exchange": "NSE", "symbolToken": "2885", "ltp": 2900.0, "close": 2850.0},
            {"exchange": "BSE", "symbolToken": "500325", "ltp": 2901.0, "close": 2851.0},
            {"exchange": "NSE", "symbolToken": "99926000", "ltp": 25000.0, "close": 24900.0},
        ]}})
    broker = _angel(handler)
    broker._symbol_map.update({("NSE", "RELIANCE"): ("2885", "RELIANCE-EQ"), ("BSE", "RELIANCE"): ("500325", "RELIANCE"),
                               ("NSE", "NIFTY 50"): ("99926000", "Nifty 50"), ("NSE", "NIFTY"): ("99926000", "Nifty 50")})
    got = run(broker.get_quotes_batch([("NSE", "RELIANCE"), ("BSE", "RELIANCE"), ("NSE", "NIFTY 50"), ("NSE", "NIFTY")]))
    assert len(bodies) == 1 and bodies[0]["exchangeTokens"] == {"NSE": ["2885", "99926000"], "BSE": ["500325"]}
    assert got[("NSE", "RELIANCE")].prev_close == 2850.0 and got[("BSE", "RELIANCE")].ltp == 2901.0
    assert got[("NSE", "NIFTY 50")].ltp == got[("NSE", "NIFTY")].ltp == 25000.0     # review item 8d: both pairs priced


def test_angel_one_batch_refuses_a_symbol_naming_another_exchange():
    """Review item 8c: "BSE:X" asked as NSE would be quoted on BSE by `_resolve`; it is left out instead."""
    calls = []
    broker = _angel(lambda request: calls.append(request) or httpx.Response(200, json={"status": True, "data": {"fetched": []}}))
    broker._symbol_map.update({("BSE", "RELIANCE"): ("500325", "RELIANCE")})
    assert run(broker.get_quotes_batch([("NSE", "BSE:RELIANCE")])) == {} and calls == []


def test_dhan_batch_is_one_marketfeed_call_and_prices_every_pair_of_one_instrument():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE_EQ": {"2885": {"last_price": 2900.0, "net_change": 50.0, "ohlc": {"close": 2850.0}}},
            "IDX_I": {"13": {"last_price": 25000.0, "net_change": -100.0}}}})
    broker = DhanBroker(BrokerCredentials(api_key="c", access_token="t", client_id="c"),
                        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=DhanBroker.BASE_URL))
    resolved = {("NSE", "RELIANCE"): ("NSE_EQ", "2885", "EQUITY"), ("NSE", "NIFTY 50"): ("IDX_I", "13", "INDEX"),
                ("NSE", "NIFTY"): ("IDX_I", "13", "INDEX")}

    async def resolve(symbol, exchange):
        if (exchange, symbol) not in resolved:
            from app.brokers.exceptions import BrokerAPIError
            raise BrokerAPIError(f"{exchange}:{symbol} not found")
        return resolved[(exchange, symbol)]
    broker._resolve = resolve
    assert broker.multi_quote is True
    got = run(broker.get_quotes_batch([("NSE", "RELIANCE"), ("NSE", "NIFTY 50"), ("NSE", "NIFTY"), ("NSE", "NOSUCH")]))
    assert len(bodies) == 1 and bodies[0] == {"NSE_EQ": [2885], "IDX_I": [13]}
    assert got[("NSE", "RELIANCE")].prev_close == 2850.0
    assert got[("NSE", "NIFTY 50")].ltp == got[("NSE", "NIFTY")].ltp == 25000.0 and got[("NSE", "NIFTY")].prev_close == 25100.0
    assert ("NSE", "NOSUCH") not in got


def test_fyers_batch_is_one_quotes_call_through_the_contract_symbol_wrapper():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["symbols"])
        return httpx.Response(200, json={"s": "ok", "d": [
            {"n": "NSE:RELIANCE-EQ", "s": "ok", "v": {"lp": 2900.0, "prev_close_price": 2850.0, "tt": "1760935500"}},
            {"n": "NSE:NIFTY50-INDEX", "s": "ok", "v": {"lp": 25000.0, "prev_close_price": 24900.0}}]})
    broker = FyersBroker(BrokerCredentials(api_key="APP-100", access_token="t"),
                         client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    broker._tickers.update({("NSE", "RELIANCE"): "NSE:RELIANCE-EQ"})
    broker._masters["NSE"] = (time.monotonic(), [])                           # the master is "loaded": no download
    wrapped = wrap_contract_symbols(broker)
    assert wrapped.multi_quote is True
    got = run(wrapped.get_quotes_batch([("NSE", "RELIANCE"), ("NSE", "NIFTY 50"), ("NSE", "NIFTY"), ("NSE", "NOSUCH")]))
    assert len(calls) == 1 and set(calls[0].split(",")) == {"NSE:RELIANCE-EQ", "NSE:NIFTY50-INDEX"}
    assert got[("NSE", "RELIANCE")].prev_close == 2850.0 and got[("NSE", "RELIANCE")].timestamp is not None
    assert got[("NSE", "NIFTY 50")].ltp == got[("NSE", "NIFTY")].ltp == 25000.0     # an index and its alias: both priced
    assert ("NSE", "NOSUCH") not in got


def test_shoonya_single_quote_is_one_getquotes_call_with_the_previous_close():
    """Review item 2: Shoonya had no quote call (the per-symbol path spent a token for nothing); it now has one -
    /GetQuotes for the symbol's token, `c` as the previous close and `lut` (epoch seconds) as the time."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "QuickAuth":
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "s", "actid": "FA1"})
        payload = json.loads(parse_qs(request.content.decode())["jData"][0])
        seen.append((path, payload))
        if path == "SearchScrip":
            return httpx.Response(200, json={"stat": "Ok", "values": [{"exch": "NSE", "token": "2885", "tsym": "RELIANCE-EQ"},
                                                                       {"exch": "NSE", "token": "9999", "tsym": "RELIANCEPP-EQ"}]})
        return httpx.Response(200, json={"stat": "Ok", "lp": "2900.50", "c": "2850.00", "lut": "1760935500", "v": "1000"})
    broker = ShoonyaBroker(BrokerCredentials(client_id="FA1", api_secret="p", api_key="k", totp_secret="123456"),
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ShoonyaBroker.BASE_URL))
    run(broker.authenticate())
    quote = run(broker.get_quote_for_symbol("RELIANCE", "NSE"))
    assert quote.ltp == 2900.5 and quote.prev_close == 2850.0 and quote.timestamp == datetime(2025, 10, 20, 4, 45, tzinfo=UTC)
    assert [p for p, _ in seen] == ["SearchScrip", "GetQuotes"] and seen[1][1]["token"] == "2885"
    run(broker.get_quote_for_symbol("RELIANCE", "NSE"))
    assert [p for p, _ in seen].count("SearchScrip") == 1                      # the token is looked up once


# --- shared instrument masters and broker timestamps ---------------------------------------------------------------------

def test_two_adapter_instances_share_one_instrument_master_download():
    """Review item 6: an adapter is built per request; the master is downloaded once per process (single-flight)."""
    master_cache.reset()
    downloads = []

    def upstox_handler(request: httpx.Request) -> httpx.Response:
        downloads.append(str(request.url))
        return httpx.Response(200, content=UPSTOX_MASTER)

    async def two_at_once():
        first, second = _upstox(upstox_handler), _upstox(upstox_handler)
        first._shared_master = second._shared_master = True
        return await asyncio.gather(first.get_instruments("NSE"), second.get_instruments("NSE"))
    a, b = run(two_at_once())
    assert len(downloads) == 1 and a is b and [i.tradingsymbol for i in a] == ["RELIANCE", "TCS"]
    third = _upstox(upstox_handler)
    third._shared_master = True
    assert run(third.get_instruments("NSE")) is a and len(downloads) == 1

    angel_rows = [{"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "exch_seg": "NSE"},
                  {"token": "500325", "symbol": "RELIANCE", "name": "RELIANCE", "exch_seg": "BSE"}]
    angel_downloads = []

    async def angel_handler(request: httpx.Request) -> httpx.Response:
        angel_downloads.append(str(request.url))
        await asyncio.sleep(0.05)                                              # a download takes a moment
        return httpx.Response(200, content=json.dumps(angel_rows).encode())
    first, second, third = _angel(angel_handler), _angel(angel_handler), _angel(angel_handler)
    first._shared_master = second._shared_master = third._shared_master = True

    async def two_exchanges_at_once():
        return await asyncio.gather(first._resolve("RELIANCE", "NSE"), second._resolve("RELIANCE", "BSE"))
    assert run(two_exchanges_at_once()) == [("2885", "RELIANCE-EQ"), ("500325", "RELIANCE")]
    assert len(angel_downloads) == 1                                           # one download in flight, shared
    assert run(third._resolve("RELIANCE", "NSE")) == ("2885", "RELIANCE-EQ") and len(angel_downloads) == 1
    # Re-check finding 1: the raw scrip master is never kept after the build - only the per-exchange maps are.
    assert ("angel_one", "*") not in master_cache._ENTRIES
    assert {("angel_one", "NSE"), ("angel_one", "BSE")} <= set(master_cache._ENTRIES)
    # Built with a client of its own (a test, a tool), an adapter keeps its own copy, as Dhan's map does.
    assert _upstox(upstox_handler)._shared_master is False
    assert UpstoxBroker(BrokerCredentials(api_key="c", access_token="t"))._shared_master is True
    master_cache.reset()


def test_broker_timestamps_accept_epoch_digit_strings():
    """Review item 5: Upstox's quote `last_trade_time` and Noren's `lut` are strings of epoch ms / seconds."""
    when = datetime(2025, 10, 20, 4, 45, tzinfo=UTC)
    assert parse_broker_timestamp("1760935500000") == when                    # milliseconds (above 1e11)
    assert parse_broker_timestamp("1760935500") == when                       # seconds
    assert parse_broker_timestamp(" 1760935500 ") == when
    assert parse_broker_timestamp(1760935500000) == when and parse_broker_timestamp(1760935500) == when
    assert parse_broker_timestamp("0") is None and parse_broker_timestamp("000000000") is None
    assert parse_broker_timestamp("20260925") == datetime(2026, 9, 24, 18, 30, tzinfo=UTC)   # a compact date stays a date
    assert parse_broker_timestamp("99999999999999999999") is None             # out of range: unparseable, not an error
    assert parse_broker_timestamp("2026-09-25 10:29:58") == datetime(2026, 9, 25, 4, 59, 58, tzinfo=UTC)


def test_expired_master_entries_are_removed_not_kept(monkeypatch):
    """Re-check finding 1: an expired entry is gone once it is found expired, and every build sweeps the others."""
    master_cache.reset()
    clock = [1000.0]
    monkeypatch.setattr(master_cache.time, "monotonic", lambda: clock[0])

    async def build(value):
        return value
    assert run(master_cache.shared("x", "a", lambda: build("A"), ttl=10)) == "A"
    assert run(master_cache.shared("x", "b", lambda: build("B"), ttl=100)) == "B"
    clock[0] += 11                                                             # "a" expired, "b" not
    assert set(master_cache._ENTRIES) == {("x", "a"), ("x", "b")}
    assert run(master_cache.shared("x", "b", lambda: build("B2"), ttl=100)) == "B"   # a hit: nothing rebuilt
    assert run(master_cache.shared("x", "a", lambda: build("A2"), ttl=10)) == "A2"   # found expired: removed, rebuilt
    assert master_cache.builds[("x", "a")] == 2
    clock[0] += 11                                                             # "a" expired again
    assert run(master_cache.shared("x", "c", lambda: build("C"), ttl=10)) == "C"     # a build sweeps the expired "a"
    assert ("x", "a") not in master_cache._ENTRIES and set(master_cache._ENTRIES) == {("x", "b"), ("x", "c")}
    clock[0] += 200
    assert master_cache.sweep() == 2 and master_cache._ENTRIES == {}
    master_cache.reset()


def test_once_shares_a_build_in_flight_and_keeps_nothing():
    master_cache.reset()
    builds = []

    async def build():
        builds.append(1)
        await asyncio.sleep(0.05)
        return [1, 2, 3]

    async def together():
        return await asyncio.gather(*(master_cache.once("x", "raw", build) for _ in range(3)))
    assert run(together()) == [[1, 2, 3]] * 3 and len(builds) == 1
    assert master_cache._ENTRIES == {}
    run(master_cache.once("x", "raw", build))
    assert len(builds) == 2                                                    # nothing kept: the next use builds again

    async def failing():
        raise RuntimeError("download failed")
    with pytest.raises(RuntimeError):
        run(master_cache.once("x", "raw", failing))
    run(master_cache.once("x", "raw", build))                                 # a failure leaves nothing behind
    assert len(builds) == 3


def test_previous_closes_of_an_earlier_ist_day_are_dropped_on_write():
    """Re-check finding 4: the in-process close cache holds one IST day; the first write of a new day drops the rest."""
    from datetime import date
    quotes_routes.reset()
    day1, day2 = date(2026, 10, 19), date(2026, 10, 20)
    run(quotes_routes._prev_close_put("upstox", ("NSE", "A"), day1, 100.0))
    run(quotes_routes._prev_close_put("upstox", ("NSE", "B"), day1, 200.0))
    assert len(quotes_routes._PREV_LOCAL) == 2
    run(quotes_routes._prev_close_put("upstox", ("NSE", "A"), day2, 101.0))
    assert list(quotes_routes._PREV_LOCAL) == [quotes_routes._prev_key("upstox", ("NSE", "A"), day2)]
    assert run(quotes_routes._prev_close_get("upstox", ("NSE", "B"), day1)) is None
    assert run(quotes_routes._prev_close_get("upstox", ("NSE", "A"), day2)) == 101.0


def test_the_per_symbol_path_budgets_shoonyas_symbol_lookup(monkeypatch):
    """Re-check finding 3: through the route, Shoonya's /SearchScrip takes a token like the quote call (two per symbol
    the first time, one once the lookup is cached); with no token for it, the symbol says "budget used"."""
    headers, me = _owner(_email("shoonya"))
    _store_credentials(me, broker="shoonya")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "QuickAuth":
            return httpx.Response(200, json={"stat": "Ok", "susertoken": "s", "actid": "FA1"})
        payload = json.loads(parse_qs(request.content.decode())["jData"][0])
        calls.append(path)
        if path == "SearchScrip":
            return httpx.Response(200, json={"stat": "Ok", "values": [{"exch": "NSE", "token": "1", "tsym": payload["stext"] + "-EQ"}]})
        return httpx.Response(200, json={"stat": "Ok", "lp": "10.5", "c": "10", "lut": str(int(time.time()))})
    broker = ShoonyaBroker(BrokerCredentials(client_id="FA1", api_secret="p", api_key="k", totp_secret="123456"),
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=ShoonyaBroker.BASE_URL))
    run(broker.authenticate())
    monkeypatch.setattr(quotes_routes, "build_adapter", lambda record: wrap_contract_symbols(broker))
    taken = _count_tokens(monkeypatch)
    a, b = _sym("A"), _sym("B")
    body = _post(headers, [{"symbol": a}, {"symbol": b}]).json()
    assert calls == ["SearchScrip", "GetQuotes", "SearchScrip", "GetQuotes"] and len(taken) == 4 and body["broker_calls"] == 4
    assert [r["ltp"] for r in body["items"]] == [10.5, 10.5] and body["items"][0]["change"] == 0.5

    calls.clear()
    c = _sym("C")
    _count_tokens(monkeypatch, grant=lambda n: n == 1)                         # one token: the quote of a (cached lookup)
    api_budget.reset()
    body = _post(headers, [{"symbol": a}, {"symbol": c}]).json()
    assert calls == ["GetQuotes"] and body["items"][0]["ltp"] == 10.5
    assert body["items"][1]["missing"] and body["items"][1]["budget_used"] is True
