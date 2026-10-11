"""Z10-d: the session-shaped load test (scripts/loadtest.py) - its safety guard, how it counts outcomes and judges
the SLO - and the password checks that no longer hold the event loop (found by it).

The CI `perf` job runs the load test itself (20 users against the in-process test app); these tests cover the logic."""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import loadtest  # noqa: E402

from app.auth import security  # noqa: E402


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:9000", "https://staging.example.org", "https://api-staging.example.in"])
def test_localhost_and_staging_hosts_are_allowed(base):
    loadtest.check_host(base)


@pytest.mark.parametrize("base", ["https://example.org", "https://app.example.in", "http://203.0.113.7", "https://prod.example.com"])
def test_any_other_host_is_refused(base, monkeypatch):
    monkeypatch.delenv("LOADTEST_ALLOWED_HOSTS", raising=False)
    with pytest.raises(SystemExit) as refused:
        loadtest.check_host(base)
    assert "never run against the production VPS" in str(refused.value)


def test_a_named_staging_host_can_be_allowed(monkeypatch):
    monkeypatch.setenv("LOADTEST_ALLOWED_HOSTS", "qa.example.org, other.example.org")
    loadtest.check_host("https://qa.example.org")
    with pytest.raises(SystemExit):
        loadtest.check_host("https://example.org")


def test_outcomes_keep_refusals_apart_from_errors():
    assert loadtest.classify(200) == "ok" and loadtest.classify(204) == "ok"
    assert loadtest.classify(503, httpx.Headers({"X-Feature-Disabled": "screener_v2"})) == "disabled"
    assert loadtest.classify(503, httpx.Headers({})) == "server_error" and loadtest.classify(500) == "server_error"
    assert loadtest.classify(409) == "unavailable" and loadtest.classify(429) == "throttled" and loadtest.classify(422) == "client_error"
    assert loadtest.classify(None) == "transport"


def test_the_report_judges_each_scenario_against_its_own_p95_and_the_error_rate():
    stats = loadtest.Stats()
    for ms in range(1, 101):
        stats.add("dashboard /api/auth/me", float(ms), "ok")        # p95 95 ms
        stats.add("login", 900.0, "ok")                               # under login's 1,000 ms
        stats.add("chart", 5.0, "unavailable")                        # refused as a whole (no broker): reported, not judged
    r = loadtest.report(stats, users=3, elapsed=10.0)
    assert r["slo_pass"] and r["requests"] == 300 and r["error_rate"] == 0.0
    assert r["scenarios"]["dashboard /api/auth/me"]["p95_ms"] == 95.0 and r["scenarios"]["chart"]["outcomes"] == {"unavailable": 100}
    stats.add("login", 5_000.0, "ok")
    for _ in range(10):
        stats.add("login", 5_000.0, "ok")
    assert not loadtest.report(stats, 3, 10.0)["slo_pass"]                 # login p95 over its limit
    errors = loadtest.Stats()
    for i in range(200):
        errors.add("positions", 10.0, "server_error" if i < 2 else "ok")  # 1% server errors
    assert not loadtest.report(errors, 1, 1.0)["slo_pass"]
    assert not loadtest.report(loadtest.Stats(), 1, 1.0)["slo_pass"]       # nothing measured is not a pass


def test_percentile_is_nearest_rank():
    assert loadtest.percentile([], 95) == 0.0
    assert loadtest.percentile([5.0], 50) == 5.0
    assert loadtest.percentile([float(v) for v in range(1, 11)], 50) in (5.0, 6.0)


def test_password_checks_off_the_event_loop_give_the_same_answers():
    hashed = asyncio.run(security.hash_password_async("correct-horse-battery-staple"))
    assert asyncio.run(security.verify_password_async("correct-horse-battery-staple", hashed)) is True
    assert asyncio.run(security.verify_password_async("wrong", hashed)) is False
    with pytest.raises(ValueError):
        asyncio.run(security.hash_password_async("x" * 73))


def test_the_event_loop_keeps_serving_while_a_password_is_checked():
    hashed = security.hash_password("pw")

    async def go():
        ticks = 0
        check = asyncio.ensure_future(security.verify_password_async("pw", hashed))
        while not check.done():
            ticks += 1
            await asyncio.sleep(0.001)
        return ticks, check.result()
    ticks, ok = asyncio.run(go())
    assert ok and ticks > 5                                          # inline, the loop would not have ticked at all
