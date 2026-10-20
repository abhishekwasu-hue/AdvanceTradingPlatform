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


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:9000", "https://staging.example.org", "https://api.staging.example.in",
                                  "https://STAGING.example.org."])
def test_localhost_and_staging_hosts_are_allowed(base):
    loadtest.check_host(base)


@pytest.mark.parametrize("base", ["https://example.org", "https://app.example.in", "http://203.0.113.7", "https://prod.example.com",
                                  "https://api-staging.example.in", "https://notstaging.example.com", "https://example.com/staging",
                                  "https://staging-prod.example.com"])
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
    assert loadtest.classify(404, httpx.Headers({"X-Feature-Disabled": "paper_account"})) == "disabled"
    assert loadtest.classify(503, httpx.Headers({})) == "server_error" and loadtest.classify(500) == "server_error"
    assert loadtest.classify(409) == "unavailable" and loadtest.classify(429) == "throttled" and loadtest.classify(422) == "client_error"
    assert loadtest.classify(None) == "transport"


def test_the_report_judges_each_scenario_against_its_own_p95_and_the_error_rate():
    stats = loadtest.Stats()
    for ms in range(1, 101):
        stats.add("dashboard /api/auth/me", float(ms), "ok")        # p95 95 ms
        stats.add("login", 900.0, "ok")                               # under login's 2,000 ms (LD-9)
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


def test_login_is_judged_apart_at_two_seconds_with_no_failed_login_and_the_api_at_half_a_second():
    """LD-9 (owner, 20 Oct): login p95 <= 2,000 ms and no failed login; every other scenario keeps its own p95 limit
    (500 ms, the chart's 1,000 and the screener's 2,000) and the server-error limit over the API requests."""
    def stats(login_ms, login_outcomes=(), dashboard_ms=120.0):
        s = loadtest.Stats()
        for i in range(100):
            s.add("dashboard /api/auth/me", dashboard_ms, "ok")
            s.add("login", login_ms, "ok")
        for outcome in login_outcomes:
            s.add("login", 300.0, outcome)
        return s
    ok = loadtest.report(stats(1_900.0), 100, 10.0)
    assert ok["slo_pass"] and ok["api_pass"] and ok["login_pass"]
    assert ok["login"] == {"n": 100, "p95_ms": 1900.0, "p95_limit_ms": 2000.0, "errors": 0, "error_limit": 0, "pass": True}
    assert ok["scenarios"]["login"]["p95_limit_ms"] == 2000.0 and ok["scenarios"]["dashboard /api/auth/me"]["p95_limit_ms"] == 500.0
    slow = loadtest.report(stats(2_100.0), 100, 10.0)                     # login over 2 s: the API still passes
    assert not slow["slo_pass"] and slow["api_pass"] and not slow["login_pass"]
    for failed in ("transport", "server_error", "throttled", "client_error"):
        one = loadtest.report(stats(300.0, [failed]), 100, 10.0)          # one failed login fails it, whatever it was
        assert not one["login_pass"] and one["login"]["errors"] == 1 and one["api_pass"] and not one["slo_pass"], failed
    # A login time-out is the login verdict's, not the API's error budget (101 logins, 1 failed: 1% of all requests).
    assert one["error_rate"] == 0.0
    api = loadtest.report(stats(300.0, dashboard_ms=600.0), 100, 10.0)    # a read over 0.5 s fails the API verdict
    assert not api["slo_pass"] and not api["api_pass"] and api["login_pass"]
    for limit_ms, scenario in ((1_000.0, "chart"), (2_000.0, "screener_run")):
        s = stats(300.0)
        for _ in range(100):
            s.add(scenario, limit_ms - 1, "ok")
        assert loadtest.report(s, 100, 10.0)["api_pass"] and loadtest.report(s, 100, 10.0)["scenarios"][scenario]["p95_limit_ms"] == limit_ms
    md = loadtest.markdown(slow)
    assert "**SLO FAIL** (API PASS, login FAIL (p95 2,100 ms of 2,000; 0 failed of 100, 0 allowed)" in md
    token_only = loadtest.Stats()
    for _ in range(10):
        token_only.add("positions", 50.0, "ok")                            # --token: no login step at all
    r = loadtest.report(token_only, 1, 1.0)
    assert r["api_pass"] and not r["login_pass"] and not r["slo_pass"] and "login" not in r     # not measured is no pass
    assert "login FAIL (not measured" in loadtest.markdown(r)


def test_the_slo_factor_scales_the_p95_limits_never_the_error_limit():
    stats = loadtest.Stats()
    for _ in range(100):
        stats.add("positions", 700.0, "ok")
    assert not loadtest.report(stats, 1, 1.0)["slo_pass"]
    scaled = loadtest.report(stats, 1, 1.0, slo_factor=1.5)
    assert scaled["api_pass"] and scaled["scenarios"]["positions"]["p95_limit_ms"] == 750.0 and scaled["slo_factor"] == 1.5
    for _ in range(2):
        stats.add("positions", 10.0, "server_error")
    assert not loadtest.report(stats, 1, 1.0, slo_factor=10.0)["slo_pass"]
    assert loadtest._slo_factor("0.5") == 1.0 and loadtest._slo_factor("1.7") == 1.7


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


def test_the_paper_account_proposal_counts_in_one_query_match_the_four_counts():
    """Z10-d: `/api/paper/account` counts the user's entry proposals in one query; same numbers as one count per bucket."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.db.models import AiActionRecord, User
    from app.paper import service
    from tests.test_auth_api import _session_factory
    from tests.test_phase_l_ai import _owner

    _, me = _owner("z10d-counts@example.com")
    _, other = _owner("z10d-counts-other@example.com")
    later = datetime.now(timezone.utc) + timedelta(hours=1)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            rows = [("PROPOSED", None, "ENTRY", user.id), ("PROPOSED", None, "ENTRY", user.id), ("REJECTED", user.id, "ENTRY", user.id),
                    ("EXPIRED", None, "ENTRY", user.id), ("EXPIRED", user.id, "ENTRY", user.id), ("APPROVED", user.id, "ENTRY", user.id),
                    ("EXECUTED", user.id, "ENTRY", user.id), ("FAILED", None, "ENTRY", user.id), ("PROPOSED", None, "EXIT_POSITION", user.id),
                    ("REJECTED", user.id, "ENTRY", None)]
            for status, decided, action, owner in rows:
                session.add(AiActionRecord(tenant_id=user.tenant_id, action=action, rule="t", reason="t", status=status, decided_by=decided,
                                           owner_user_id=owner, expires_at=later))
            session.add(AiActionRecord(tenant_id=other["tenant_id"], action="ENTRY", rule="t", reason="t", status="PROPOSED", owner_user_id=other["id"],
                                       expires_at=later))
            await session.commit()

            async def count(*conditions):
                return int(await session.scalar(select(func.count()).select_from(AiActionRecord).where(
                    AiActionRecord.tenant_id == user.tenant_id, AiActionRecord.action == "ENTRY", AiActionRecord.owner_user_id == user.id, *conditions)) or 0)
            want = {"approved_by_you": await count(AiActionRecord.decided_by.is_not(None),
                                                   AiActionRecord.status.in_(("APPROVED", "EXECUTED", "FAILED", "EXPIRED"))),
                    "rejected_by_you": await count(AiActionRecord.status == "REJECTED"),
                    "pending": await count(AiActionRecord.status == "PROPOSED"),
                    "expired_unanswered": await count(AiActionRecord.status == "EXPIRED", AiActionRecord.decided_by.is_(None))}
            return want, await service._proposal_counts(session, user)
    want, got = asyncio.run(go())
    assert got == want == {"approved_by_you": 3, "rejected_by_you": 1, "pending": 2, "expired_unanswered": 1}


def test_bcrypt_runs_on_its_own_threads_sized_to_the_cpus():
    """bcrypt's own pool (never the shared thread pools), BCRYPT_CONCURRENCY threads per process, at a lower CPU
    priority (LD-7: BCRYPT_NICE) - the same check, ordered after interactive requests when the CPUs are busy."""
    import os
    import threading
    seen = []
    real = security.verify_password

    def spy(password, hashed):
        seen.append((threading.current_thread().name, os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
                     if hasattr(os, "getpriority") else None))
        return real(password, hashed)

    async def go():
        hashed = await security.hash_password_async("pw")
        return await security.verify_password_async("pw", hashed)
    original = security.verify_password
    security.verify_password = spy
    try:
        assert asyncio.run(go()) is True
    finally:
        security.verify_password = original
    executor = security._bcrypt_executor()
    assert executor._max_workers == security.BCRYPT_CONCURRENCY >= 1
    name, nice = seen[0]
    assert name.startswith("bcrypt")
    if nice is not None and security.BCRYPT_NICE:
        assert nice >= security.BCRYPT_NICE
