"""CH5-a review (ADR-0004): `/api/market-data/ltp` is limited per user, and every broker call it makes draws from the
session's API budget first - none within a moment answers 429 instead of queueing, so a polling page can never spend
the broker allowance the trading worker's exits need. A fresh tick costs no token."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from app.brokers.models import Quote
from app.brokers.rate_budget import RateBudget, RateLimits
from app.cache.client import cache_set
from app.core import rate_limit
from app.market_data import api_budget, candles_routes
from app.market_data.stream import Tick, _tick_key, tick_cache
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_an_ltp import _QuoteBroker, client_get
from tests.test_phase_k_commercial import _owner


class _Counting(_QuoteBroker):
    calls = 0

    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        type(self).calls += 1
        return await super().get_quote_for_symbol(symbol, exchange)


def _quote(symbol="RELIANCE"):
    return Quote(symbol=symbol, ltp=2874.2, timestamp=datetime.now(timezone.utc) - timedelta(seconds=2))


def _unique_symbol():
    """A symbol no other test reads: the tick cache mirrors every tick to Redis (CI sets REDIS_URL, 180 s TTL) and
    `tick_cache.clear()` only empties the process-local dict - a RELIANCE tick left there was read as the price by the
    position monitor in tests/test_exit_rules_and_backtest_depth.py (upstox / NSE / RELIANCE)."""
    return "LTPB" + uuid.uuid4().hex[:8].upper()


def test_every_broker_call_takes_a_token_from_the_sessions_api_budget(monkeypatch):
    headers, me = _owner("ch5a-ltp-budget@example.com")
    _store_credentials(me)
    tick_cache.clear()
    api_budget.reset()
    _Counting.calls = 0
    symbol = _unique_symbol()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _Counting(quote=_quote(symbol)))
    tight = RateBudget(RateLimits(per_second=0.01, burst=1, per_minute=1))
    monkeypatch.setattr(api_budget, "budget_for", lambda tenant_id, broker, label="primary": tight)
    monkeypatch.setattr(api_budget, "TOKEN_WAIT_SECONDS", 0.0)
    first = client_get(headers, symbol)
    assert first.status_code == 200 and first.json()["source"] == "quote"
    second = client_get(headers, symbol)
    assert second.status_code == 429 and second.headers.get("retry-after") == "5"
    assert _Counting.calls == 1                                          # the refused request never reached the broker
    now = datetime.now(timezone.utc)
    try:
        asyncio.run(tick_cache.put(Tick(broker="upstox", exchange="NSE", symbol=symbol, ltp=2875.0, exchange_ts=now, received_at=now)))
        assert client_get(headers, symbol).json()["source"] == "tick"     # a fresh tick costs no token
    finally:
        tick_cache.clear()
        asyncio.run(cache_set(_tick_key("upstox", "NSE", symbol), "", 1))   # and its Redis mirror (an empty value reads as no tick)


def test_the_api_budget_is_one_per_session_and_below_the_workers_limits():
    api_budget.reset()
    a = api_budget.budget_for(1, "upstox", "primary")
    assert api_budget.budget_for(1, "upstox", "primary") is a
    assert api_budget.budget_for(2, "upstox", "primary") is not a and api_budget.budget_for(1, "upstox", "second") is not a
    assert a.limits.per_minute == 100 and a.limits.burst == 5                # half of upstox's conservative 200 / 10
    api_budget.reset()


def test_ltp_is_limited_per_user(monkeypatch):
    headers, me = _owner("ch5a-ltp-limit@example.com")
    _store_credentials(me)
    tick_cache.clear()
    rate_limit.reset("market_data_ltp")
    api_budget.reset()
    monkeypatch.setattr(candles_routes, "build_adapter", lambda record: _QuoteBroker(quote=_quote()))
    monkeypatch.setattr(api_budget, "take", lambda budget, wait_seconds=0.0: _true())
    codes = [client_get(headers, "RELIANCE").status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429
    other, him = _owner("ch5a-ltp-limit-2@example.com")
    _store_credentials(him)
    assert client_get(other, "RELIANCE").status_code == 200             # per user, not global
    rate_limit.reset("market_data_ltp")


async def _true():
    return True
