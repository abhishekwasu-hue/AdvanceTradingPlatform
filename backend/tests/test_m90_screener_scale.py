"""M90 (owner): the Screener without the fixed 50 and watchlist prices for a list of any size.

B2 a closed-bar scan reads the store for the whole list (zero broker calls); only a symbol the store lacks goes to the
   reader's OWN broker session, paced. B3 no fixed 50: the store takes the whole list, the broker what its budget allows
   per request - the rest comes back `pending`; only the request body has a ceiling (a watchlist's 500). B4 one batch
   quote call per `quote_batch_max` instruments. B5 a broker 429 is paced and retried, never an error ("slowed").
"""
import asyncio
import types
from datetime import UTC, datetime, timedelta

import pytest

from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import Quote
from app.market_data import api_budget, quotes_routes
from app.market_store import models as M
from app.market_store import reader as R
from tests.test_auth_api import client
from tests.test_copilot_v2_chat import _flag
from tests.test_m71_pl3_read_service import IST, _clear, _store_days, _weekdays
from tests.test_phase_l_ai import _owner
from tests.test_s1d_screener_api import _flag as _screener_flag
from tests.test_scan_universe import _Broker, _patch


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def screener_on():
    _screener_flag(True)
    R.reset_cache()
    api_budget.reset()
    yield
    _screener_flag(False)
    _run(_flag(M.FLAG, False))
    api_budget.reset()


def _stock(prefix: str, n: int) -> list[str]:
    today = datetime.now(UTC).astimezone(IST).date()
    days = _weekdays(30, today - timedelta(days=1))
    syms = [f"{prefix}{i:03d}" for i in range(n)]
    for s in syms:
        _store_days(f"NSE:{s}", days)
    return syms


class _NoBroker:
    """Counts every broker adapter built: the store path must build none."""
    def __init__(self, monkeypatch):
        from app.brokers import token_lifecycle
        self.built = 0

        def build(record):
            self.built += 1
            raise AssertionError("no broker adapter for a stored daily scan")
        monkeypatch.setattr(token_lifecycle, "build_adapter", build)


# --- B2 / B3: the store takes the whole list --------------------------------------------------------------------------------

def test_a_500_symbol_daily_run_reads_the_store_with_zero_broker_calls(screener_on, monkeypatch):
    headers, _ = _owner("m90-store500@example.com")
    syms = _stock("M90S", 500)
    spy = _NoBroker(monkeypatch)
    _run(_flag(M.FLAG, True))
    out = client.post("/api/screener/run", json={"source": "close > 0", "base_tf": "1d", "symbols": syms, "batch": True}, headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["scanned"] == 500 and len(body["matched"]) == 500 and "pending" not in body
    assert body["data_source"] == "lake:daily" and body["budget"]["broker_calls"] == 0
    assert spy.built == 0                                                      # no broker adapter, no broker call
    _clear(*[f"NSE:{s}" for s in syms])


def test_symbols_missing_in_the_store_go_to_the_own_broker_paced_and_the_rest_come_back_pending(screener_on, monkeypatch):
    headers, _ = _owner("m90-missing@example.com")
    stored = _stock("M90M", 3)
    missing = [f"M90X{i:03d}" for i in range(60)]
    broker = _Broker()
    _patch(monkeypatch, broker, [])
    _run(_flag(M.FLAG, True))
    out = client.post("/api/screener/run", json={"source": "close > 0", "base_tf": "1d", "symbols": stored + missing}, headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()
    from app.screener.universes import BATCH_MAX
    assert len(broker.calls) == BATCH_MAX and broker.calls == missing[:BATCH_MAX]  # only missing symbols, in order
    assert body["pending"] == missing[BATCH_MAX:]                              # not read now, never an error
    assert body["scanned"] == 3 + BATCH_MAX and body["data_source"] == "lake:daily+broker:zerodha"
    assert not [r for r in body["results"] if r["symbol"] in body["pending"]]
    _clear(*[f"NSE:{s}" for s in stored])


def test_a_broker_batch_reads_what_the_budget_allows_and_hands_the_rest_back(screener_on, monkeypatch):
    from app.screener.universes import batch_plan
    headers, _ = _owner("m90-broker@example.com")
    broker = _Broker()
    _patch(monkeypatch, broker, [1] * 1000)
    syms = [f"M90B{i:03d}" for i in range(120)]
    out = client.post("/api/screener/run", json={"source": "close > 1", "symbols": syms, "batch": True}, headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()
    size = batch_plan("zerodha")["size"]
    assert body["scanned"] == size and len(broker.calls) == size and body["pending"] == syms[size:]
    assert body["funnel"]["universe"] == size
    big = client.post("/api/screener/run", json={"source": "close > 1", "symbols": [f"S{i}" for i in range(501)], "batch": True},
                      headers=headers)
    assert big.status_code == 422                                              # only the body's ceiling (a watchlist's 500)


# --- B5: a broker 429 is paced and retried, never an error ------------------------------------------------------------------

class _Busy(_Broker):
    def __init__(self, busy: int):
        super().__init__()
        self.busy = busy

    async def get_historical_data(self, symbol, exchange, interval, start=None, end=None):
        if self.busy:
            self.busy -= 1
            raise BrokerAPIError("Too Many Requests", status_code=429)
        return await super().get_historical_data(symbol, exchange, interval, start, end)


def test_a_broker_429_is_retried_after_a_pause_and_the_run_says_slowed(screener_on, monkeypatch):
    from app.brokers import broker_limits
    headers, _ = _owner("m90-429@example.com")
    broker = _Busy(busy=2)
    _patch(monkeypatch, broker, [1] * 50)
    pauses: list = []
    monkeypatch.setattr(broker_limits, "backoff_delay", lambda attempt, **k: pauses.append(attempt) or 0.0)
    out = client.post("/api/screener/run", json={"source": "close > 1", "symbols": ["AAA", "BBB"], "batch": True}, headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()
    assert sorted(body["matched"]) == ["AAA", "BBB"] and body["unread"] == 0     # retried, no error, nothing lost
    assert body["budget"]["slowed"] is True and len(pauses) == 2                 # each 429 paced before its retry


# --- B4: one batch quote call per quote_batch_max instruments ---------------------------------------------------------------

class _Upstoxish:
    name = "upstox"
    multi_quote = True
    quote_batch_max = 500

    def __init__(self, busy: int = 0):
        self.calls: list = []
        self.busy = busy

    async def get_quotes_batch(self, items):
        if self.busy:
            self.busy -= 1
            raise BrokerAPIError("Too Many Requests", status_code=429)
        self.calls.append(len(items))
        return {pair: Quote(symbol=pair[1], ltp=100.0 + i, prev_close=99.0, timestamp=datetime.now(UTC)) for i, pair in enumerate(items)}


def _fill(adapter):
    record = types.SimpleNamespace(id=9_090_001, broker_name="upstox", account_label="primary")
    return quotes_routes._Fill(record, adapter, datetime.now(UTC), False)


@pytest.fixture
def tokens(monkeypatch):
    async def take(record_id, broker, wait_seconds=None, kind="quote", **k):
        return True
    monkeypatch.setattr(api_budget, "take", take)
    api_budget.reset()
    quotes_routes.reset()
    yield
    api_budget.reset()
    quotes_routes.reset()


@pytest.mark.parametrize("n,calls", [(500, [500]), (501, [500, 1])])
def test_a_watchlist_of_500_is_one_quote_call_and_501_is_two(tokens, n, calls):
    adapter = _Upstoxish()
    fill = _fill(adapter)
    pairs = [("NSE", f"Q{i:03d}") for i in range(n)]
    assert _run(fill.batch(pairs)) is True
    assert adapter.calls == calls and fill.broker_calls == len(calls) and not fill.unpriced(pairs)


def test_the_route_takes_a_whole_watchlist_in_one_request():
    assert quotes_routes.MAX_ITEMS == 500
    from app.brokers.dhan import DhanBroker
    from app.brokers.upstox import UpstoxBroker
    from app.brokers.zerodha import ZerodhaBroker
    assert all(quotes_routes._batch_keys(cls.__new__(cls)) == 500 for cls in (UpstoxBroker, ZerodhaBroker, DhanBroker))
    assert quotes_routes._batch_keys(object()) == quotes_routes.QUOTE_BATCH_DEFAULT


def test_a_quote_429_is_retried_once_then_says_slowed_never_an_error(tokens, monkeypatch):
    from app.brokers import broker_limits
    monkeypatch.setattr(broker_limits, "backoff_delay", lambda attempt, **k: 0.0)
    pairs = [("NSE", f"Q{i:03d}") for i in range(10)]
    once = _Upstoxish(busy=1)
    fill = _fill(once)
    _run(fill.batch(pairs))
    assert once.calls == [10] and fill.slowed and not fill.unpriced(pairs)            # paced retry, all priced
    others = [("NSE", f"R{i:03d}") for i in range(10)]                               # not in the shared quote cache
    always = _Upstoxish(busy=5)
    fill = _fill(always)
    _run(fill.batch(others))
    assert fill.slowed and all(fill.reasons[p] == quotes_routes.SLOWED_REASON for p in others)


def test_the_plan_names_the_timeframes_the_store_reads_whole(screener_on):
    headers, _ = _owner("m90-plan@example.com")
    assert client.get("/api/screener/universes", headers=headers).json()["batch"]["store_timeframes"] == []   # flag off
    _run(_flag(M.FLAG, True))
    assert client.get("/api/screener/universes", headers=headers).json()["batch"]["store_timeframes"] == ["1d", "1w", "1M"]
