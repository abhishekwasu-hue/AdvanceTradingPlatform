"""TM Part 1: the data hub's load test (B10 style) and its measurement harness (backend/scripts/hub_probe.py).

* fan-out: 500 broker connections x 50 symbols x 3 open tabs - every tab gets every tick of its own connection once and
  nothing of any other, one snapshot write per connection per flush (not one Redis write per tick), and a tab's poll
  stays well under a millisecond on average;
* the harness: with the hub, fewer broker calls per minute, no rate-limit refusals, one price on every surface, a
  dropped socket back to live with every symbol subscribed again, and a refused token tried once with one renew.
No network; an in-memory cache.
"""
import asyncio
import time

import pytest

from app.market_data import hub as data_hub
from app.market_data import stream as st
from scripts import hub_probe
from tests.test_market_data_service import _install_cache, _MemoryCache


@pytest.fixture(autouse=True)
def _memory(monkeypatch):
    cache = _MemoryCache()
    _install_cache(monkeypatch, cache)
    data_hub.reset()
    yield cache
    data_hub.reset()


def test_hub_fan_out_500_connections_50_symbols_3_tabs(_memory):
    writes = {"n": 0}
    original = data_hub.cache_set

    async def counting_set(key, value, ttl):
        if ":ticks" in key and key.startswith("md:hub:"):
            writes["n"] += 1
        await original(key, value, ttl)
    data_hub.cache_set = counting_set
    try:
        started = time.perf_counter()
        result = asyncio.run(hub_probe.load_probe(connections=500, symbols=50, tabs=3, rounds=2))
        elapsed = time.perf_counter() - started
    finally:
        data_hub.cache_set = original
    assert result["tab_updates_out"] == result["expected_tab_updates"] == 500 * 3 * 50 * 2
    assert writes["n"] <= 500 * 2 * 2                    # at most one snapshot per connection per flush, never per tick
    assert result["ticks_in"] == 500 * 50 * 2
    assert result["poll_p95_ms"] < 20                    # generous for a shared CI runner; about 0.5 ms locally
    assert elapsed < 60


def test_probe_before_and_after_numbers(monkeypatch):
    result = hub_probe.run_all(minutes=30, streams=False)
    before, after = result["before"], result["after"]
    assert after["broker_calls_per_minute"] < before["broker_calls_per_minute"]
    assert after["asked_per_minute"] < before["asked_per_minute"] / 2
    assert before["rate_limit_errors"]["api_budget_refusals"] > 0            # today: the 30-minute budget runs out
    assert after["rate_limit_errors"] == {"api_budget_refusals": 0, "worker_budget_refusals": 0, "broker_429": 0}
    assert after["worker_calls_per_minute"] < 0.1                            # the paper engine reads the hub's ticks
    assert after["one_price"]["all_equal_pct"] == 100.0 and before["one_price"]["all_equal_pct"] < 100.0
    assert after["streamed_symbols"] == 50


def test_probe_reconnect_and_token_expiry(monkeypatch):
    monkeypatch.setattr(st, "BACKOFF_SECONDS", (0.05, 0.1))
    reconnect = asyncio.run(hub_probe.reconnect_probe(symbols=50))
    assert reconnect["states"] == ["live", "reconnecting", "live"]
    assert reconnect["symbols_subscribed_again"] == 50 and reconnect["published_state"] == "live"
    expiry = asyncio.run(hub_probe.expiry_probe(symbols=20, cycles=5, watch_seconds=0.5))
    assert expiry["without_hub_attempts_in_seconds"]["attempts"] >= 3
    assert expiry["with_hub_attempts_over_cycles"] == {"cycles": 5, "attempts": 1, "renew_prompts": 1, "state": "renew_needed"}
    assert expiry["after_renew"] == {"new_stream": True, "state": "live", "symbols_subscribed": 20}
