"""H-1 (OPEN_QUESTIONS, answered 2026-10-10): Redis capped with volatile-lru.

- the worker's replica lock carries a TTL, so eviction can take it: a lost lock makes the worker fail closed - no
  new entries for the rest of the cycle (exits continue) when it could take the lock back, the cycle stopped (no
  entries, no exits) when another holder has it - with one CRITICAL to the operators and a metric;
- Redis memory is read against its cap and a WARNING goes out at 70 %, before eviction starts;
- cache keys are short-lived (cache_set caps the TTL); the alert outbox and idempotency keys live in Postgres;
- exactly one worker replica runs on the production host.
"""
import asyncio
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.cache import client as cache_client
from app.core import config
from app.db.models import AlertDeliveryRecord, NotificationRecord, OrderRecord, StrategyDeploymentRecord
from app.observability import metrics
from app.workers import redis_guard as rg
from app.workers import trading_worker as tw
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _signal, _tenant, _worker

ROOT = Path(__file__).resolve().parents[2]
LOCK_TITLE = "Worker lock lost - failed closed"


def _run(coro):
    return asyncio.run(coro)


class _LockRedis:
    """The lock key as Redis holds it. `events` says what happens right after each acquisition (a cycle start or a take-back):
    "evict" (the key vanishes at the memory cap), "steal" (another worker has it by the next renewal), "down"
    (Redis stops answering), or None."""
    OTHER = "other-host:abcd1234"

    def __init__(self, monkeypatch, events):
        self.holder = None
        self.down = False
        self.events = list(events)
        self.tries = 0
        self.monitor_calls = 0
        monkeypatch.setattr(tw, "cache_try_lock", self.try_lock)
        monkeypatch.setattr(tw, "cache_renew_lock", self.renew)
        monkeypatch.setattr(tw, "cache_lock_holder", self.lock_holder)
        monkeypatch.setattr(tw, "cache_release_lock", self.release)
        monkeypatch.setattr(tw, "monitor_open_positions", self.monitor)

    async def try_lock(self, key, holder, ttl):
        self.tries += 1
        if self.down:
            return True, False
        if self.holder is not None:
            return False, True
        self.holder = holder
        event = self.events.pop(0) if self.events else None
        if event == "evict":
            self.holder = None
        elif event == "steal":
            self.holder = self.OTHER
        elif event == "down":
            self.down = True
        return True, True

    async def renew(self, key, holder, ttl):
        return not self.down and self.holder == holder

    async def lock_holder(self, key):
        return (None, False) if self.down else (self.holder, True)

    async def release(self, key, holder):
        if not self.down and self.holder == holder:
            self.holder = None

    async def monitor(self, *args, **kwargs):
        self.monitor_calls += 1
        return []


def _lock_alerts():
    async def go():
        async with _session_factory() as session:
            operators = await rg.operator_tenants(session)
            rows = list(await session.scalars(select(NotificationRecord).where(NotificationRecord.title == LOCK_TITLE)))
            return operators, rows
    return _run(go())


def _last_error(dep_id):
    async def go():
        async with _session_factory() as session:
            return (await session.get(StrategyDeploymentRecord, dep_id)).last_error
    return _run(go())


def _lost(outcome):
    return metrics.WORKER_LOCK_LOST.labels(outcome=outcome)._value.get()


def test_an_evicted_lock_is_taken_back_but_no_entry_is_placed_that_cycle_and_exits_continue(monkeypatch):
    t = _tenant("h1-evicted@example.com")
    dep = _deploy(t, mode="PAPER")
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    _force_signal(monkeypatch, _signal)
    redis = _LockRedis(monkeypatch, ["evict", None, "evict", None])   # per acquisition: cycle start, take-back, ...
    before, alerts_before = _lost("retaken"), len(_lock_alerts()[1])

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.lock_lost and not report.lock_aborted and not report.lock_degraded
    assert redis.tries == 2                                             # cycle start + the take-back
    assert report.signals_executed == 0 and report.tenants_processed == 1
    assert redis.monitor_calls == 1                                     # exits still ran (ADR-0004)
    assert "Worker lock was lost" in (_last_error(dep) or "")
    assert _lost("retaken") == before + 1
    operators, alerts = _lock_alerts()
    new = alerts[alerts_before:]
    assert new and {a.tenant_id for a in new} == set(operators) and all(a.severity == "CRITICAL" for a in new)
    assert all(a.event_type == "SYSTEM_FAILURE" and "taken back" in a.message for a in new)

    # Same process, next cycle loses it again: the metric counts it, the CRITICAL is not repeated (30-min cooldown).
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.lock_lost and _lost("retaken") == before + 2 and len(_lock_alerts()[1]) == len(alerts)

    # A cycle that keeps its lock trades again: the fail-closed state lasts one cycle, not forever.
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert not report.lock_lost and report.signals_executed == 1


def test_a_lock_another_worker_took_stops_the_cycle_entries_and_exits(monkeypatch):
    t = _tenant("h1-taken@example.com")
    _deploy(t, mode="PAPER")
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    _force_signal(monkeypatch, _signal)
    redis = _LockRedis(monkeypatch, ["steal"])
    before, alerts_before = _lost("held_elsewhere"), len(_lock_alerts()[1])

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.lock_lost and report.lock_aborted
    assert report.tenants_processed == 0 and report.signals_executed == 0
    assert redis.monitor_calls == 0                                     # the holder runs the exits, not this worker
    assert redis.holder == _LockRedis.OTHER                             # and its lock is never released by this one
    assert _lost("held_elsewhere") == before + 1
    new = _lock_alerts()[1][alerts_before:]
    assert new and all("other-host:abcd1234" in a.message and a.severity == "CRITICAL" for a in new)


def test_redis_down_mid_cycle_is_the_degraded_mode_not_a_lost_lock(monkeypatch):
    t = _tenant("h1-down@example.com")
    _deploy(t, mode="PAPER")
    worker = _worker(monkeypatch, _FakeBroker(ltp=101.0))
    _force_signal(monkeypatch, _signal)
    _LockRedis(monkeypatch, ["down"])                                    # Redis stops answering mid-cycle
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.lock_degraded and not report.lock_lost
    assert report.signals_executed == 1                                 # PAPER trades (S2: only LIVE pauses)


def test_redis_memory_is_read_on_its_interval_and_warns_once_an_hour_at_seventy_percent(monkeypatch):
    t = _tenant("h1-memory@example.com")
    _deploy(t, mode="PAPER")
    ratio = {"value": 0.72}

    async def memory():
        return ratio["value"]
    monkeypatch.setattr(rg, "cache_memory_ratio", memory)
    now = {"t": 1000.0}
    guard = rg.RedisGuard(clock=lambda: now["t"])

    def check():
        async def go():
            async with _session_factory() as session:
                return await guard.check_memory(session)
        return _run(go())

    def warnings():
        async def go():
            async with _session_factory() as session:
                return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.title.like("Redis memory at %"))))
        return _run(go())
    before = len(warnings())
    assert check() == 0.72 and metrics.REDIS_MEMORY_RATIO._value.get() == 0.72
    first = warnings()[before:]
    assert first and all(w.severity == "WARNING" and "72%" in w.title for w in first)
    assert check() is None                                              # not re-read inside REDIS_MEMORY_CHECK_SECONDS
    now["t"] += config.REDIS_MEMORY_CHECK_SECONDS
    assert check() == 0.72 and len(warnings()) == before + len(first)   # re-read, but one warning an hour
    now["t"] += rg.MEMORY_ALERT_COOLDOWN_SECONDS
    ratio["value"] = 0.69
    assert check() == 0.69 and len(warnings()) == before + len(first)  # below the line: nothing
    now["t"] += config.REDIS_MEMORY_CHECK_SECONDS
    ratio["value"] = 0.70
    assert check() == 0.70 and len(warnings()) == before + 2 * len(first)   # at the line: warned again


def test_memory_ratio_reads_used_over_maxmemory_and_none_without_a_cap(monkeypatch):
    class _Redis:
        def __init__(self, info):
            self._info = info
        async def info(self, section):
            assert section == "memory"
            return self._info
    monkeypatch.setattr(cache_client, "_get_client", lambda: _Redis({"used_memory": 300, "maxmemory": 400}))
    assert _run(cache_client.cache_memory_ratio()) == 0.75
    monkeypatch.setattr(cache_client, "_get_client", lambda: _Redis({"used_memory": 300, "maxmemory": 0}))
    assert _run(cache_client.cache_memory_ratio()) is None             # uncapped Redis: nothing to compare against


def test_warn_ratio_setting_rejects_nonsense(monkeypatch):
    monkeypatch.setenv("H1_TEST_RATIO", "70")                          # a percent typed where a fraction belongs
    assert config._ratio("H1_TEST_RATIO", 0.70) == 0.70
    monkeypatch.setenv("H1_TEST_RATIO", "0.8")
    assert config._ratio("H1_TEST_RATIO", 0.70) == 0.8
    monkeypatch.setenv("H1_TEST_RATIO", "x")
    assert config._ratio("H1_TEST_RATIO", 0.70) == 0.70


def test_cache_entries_always_expire_within_the_cap(monkeypatch):
    class _Redis:
        def __init__(self):
            self.ex = []
        async def set(self, key, value, ex=None):
            self.ex.append(ex)
    fake = _Redis()
    monkeypatch.setattr(cache_client, "_get_client", lambda: fake)
    for ttl in (5, 0, -3, 10 ** 6):
        _run(cache_client.cache_set("k", "v", ttl))
    assert fake.ex == [5, 1, 1, config.CACHE_MAX_TTL_SECONDS]
    from app.market_data import service as md, stream
    for ttl in (md.DEFAULT_CANDLE_CACHE_TTL_SECONDS, md.HISTORY_CACHE_TTL_SECONDS, stream.TICK_REDIS_TTL_SECONDS):
        assert 0 < ttl <= config.CACHE_MAX_TTL_SECONDS


def test_every_direct_redis_write_sets_an_expiry():
    """Code that talks to Redis without cache_set is a short reviewed list, and each write there carries a TTL.
    A new file reaching for the raw client fails here until its keys are checked the same way."""
    reviewed = {"app/cache/client.py", "app/billing/service.py", "app/core/rate_limit.py", "app/observability/routes.py"}
    backend = ROOT / "backend"
    users = {str(p.relative_to(backend)) for p in (backend / "app").rglob("*.py") if "_get_client()" in p.read_text(encoding="utf-8")}
    assert users <= reviewed, users - reviewed
    billing = (backend / "app/billing/service.py").read_text(encoding="utf-8")
    assert re.search(r"client\.set\(key, .*ex=", billing)                     # the usage counter: 2-day TTL
    assert "redis.call('expire'" in (backend / "app/core/rate_limit.py").read_text(encoding="utf-8")
    cache = (backend / "app/cache/client.py").read_text(encoding="utf-8")
    assert "nx=True, ex=ttl_seconds" in cache and "await client.expire(key, ttl_seconds)" in cache


def test_the_outbox_and_idempotency_keys_live_in_postgres_not_redis():
    assert AlertDeliveryRecord.__tablename__ == "alert_deliveries"
    assert any(getattr(c, "name", "") == "uq_tenant_idempotency_key" for c in OrderRecord.__table__.constraints)
    backend = ROOT / "backend/app"
    for module in ("alerts/dispatcher.py", "execution/order_persistence.py"):
        text = (backend / module).read_text(encoding="utf-8")
        assert "app.cache" not in text and "redis" not in text.lower(), module


def test_one_worker_replica_on_the_production_host():
    overlay = (ROOT / "docker-compose.hostinger.yml").read_text(encoding="utf-8")
    assert re.search(r"^  worker:\n    deploy:\n      replicas: 1\b", overlay, flags=re.M)
    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("docker compose not available for the merged check")
    env = ROOT / "backend/.pytest_env_h1"
    env.write_text((ROOT / ".env.example").read_text(encoding="utf-8") + "\nDOMAIN=203.0.113.7\nACME_EMAIL=ops@localhost\n")
    try:
        import yaml
        out = subprocess.run(["docker", "compose", "--env-file", str(env), "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml",
                              "-f", "docker-compose.hostinger.yml", "-p", "atp", "config"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    finally:
        env.unlink()
    assert yaml.safe_load(out)["services"]["worker"]["deploy"]["replicas"] == 1


def test_the_alert_rules_cover_both_signals():
    rules = (ROOT / "scripts/monitoring/prometheus-alerts.yml").read_text(encoding="utf-8")
    assert "alert: WorkerLockLost" in rules and "atp_worker_lock_lost_total" in rules
    assert "alert: RedisMemoryHigh" in rules and "atp_redis_memory_used_ratio >= 0.70" in rules
