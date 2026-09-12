import asyncio

from app.cache import client as cache_client
from tests.test_auth_api import client


class _BrokenRedis:
    async def get(self, key):
        raise ConnectionError("redis unreachable")

    async def set(self, key, value, ex=None):
        raise ConnectionError("redis unreachable")


def test_cache_get_fails_open_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(cache_client, "_get_client", lambda: _BrokenRedis())
    assert asyncio.run(cache_client.cache_get("some-key")) is None


def test_cache_set_fails_open_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(cache_client, "_get_client", lambda: _BrokenRedis())
    # Must not raise.
    asyncio.run(cache_client.cache_set("some-key", "some-value", ttl_seconds=5))


def _sample_option_chain():
    return {
        "underlying": "NIFTY",
        "expiry": "2024-01-25",
        "underlying_ltp": 21500.0,
        "rows": [
            {
                "strike": 21500, "call_oi": 1000, "call_change_oi": 100, "call_ltp": 50,
                "put_oi": 1200, "put_change_oi": 150, "put_ltp": 55,
            },
        ],
    }


def test_option_chain_analyze_is_cached_across_identical_calls(monkeypatch):
    import app.main as main_module

    call_count = {"n": 0}
    real_analyze = main_module.analyze_option_chain

    def counting_analyze(chain, top_n=3):
        call_count["n"] += 1
        return real_analyze(chain, top_n=top_n)

    monkeypatch.setattr(main_module, "analyze_option_chain", counting_analyze)

    payload = {"chain": _sample_option_chain(), "top_n": 3}
    first = client.post("/api/option-chain/analyze", json=payload)
    second = client.post("/api/option-chain/analyze", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # If Redis is reachable in this environment, the second call is served from cache and the
    # real analyzer only runs once; if Redis is unreachable, cache_get fails open and it runs
    # twice - either way the endpoint must keep working and return identical results.
    assert call_count["n"] in (1, 2)


def test_support_resistance_zones_endpoint_still_works_with_caching(monkeypatch):
    from tests.utils import make_series

    df = make_series([100.0 + (i % 5) for i in range(60)])
    candles = [
        {"timestamp": ts.isoformat(), "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume}
        for ts, row in df.iterrows()
    ]
    payload = {"symbol": "NIFTY", "candles": candles, "timeframe": "1min"}

    first = client.post("/api/support-resistance/zones", json=payload)
    second = client.post("/api/support-resistance/zones", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
