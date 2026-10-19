"""LD-7 (the 500-user load criterion): several API processes and no pool connection held across a slow step.

* `release_connection` ends a read-only transaction (the pooled connection goes back) and refuses unsaved changes;
* the login, sign-up and password routes release before bcrypt; the candles route and the screener run before their
  broker calls;
* bcrypt's thread limit is shared out between the API processes of a host;
* /metrics merges every process's series when PROMETHEUS_MULTIPROC_DIR is set;
* the container command, the compose files and the pool sizes add up: Hostinger's pools stay under its Postgres
  `max_connections`.
"""
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from app.db.models import User
from tests.test_auth_api import _register, _session_factory, client

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


# --- release_connection --------------------------------------------------------------------------------------------

def test_release_connection_ends_a_read_transaction_and_keeps_the_objects():
    from app.db.session import release_connection
    _register("ld7-release@example.com")

    async def go():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == "ld7-release@example.com"))
            assert session.in_transaction()
            await release_connection(session)
            assert not session.in_transaction()
            assert user.email == "ld7-release@example.com"        # still loaded (no expiry, no lazy load)
            await release_connection(session)                     # nothing open: a no-op
            user.is_active = True                                 # an unsaved change ...
            with pytest.raises(RuntimeError):
                await release_connection(session)                 # ... is never committed half-way
    asyncio.run(go())


def _order_of(monkeypatch, module, slow_name: str) -> list:
    """Records the order of release_connection and the slow step in `module`."""
    calls: list = []
    real_release = module.release_connection
    real_slow = getattr(module, slow_name)

    async def release(session):
        calls.append("release")
        await real_release(session)

    async def slow(*args, **kwargs):
        calls.append(slow_name)
        return await real_slow(*args, **kwargs)
    monkeypatch.setattr(module, "release_connection", release)
    monkeypatch.setattr(module, slow_name, slow)
    return calls


def test_login_releases_the_connection_before_bcrypt(monkeypatch):
    from app.auth import routes
    _register("ld7-login@example.com")
    calls = _order_of(monkeypatch, routes, "verify_password_async")
    r = client.post("/api/auth/login", json={"email": "ld7-login@example.com", "password": "S3cur3Pass!"})
    assert r.status_code == 200, r.text
    assert calls[:2] == ["release", "verify_password_async"]
    # a wrong password and an unknown email take the same path (same timing, same release)
    calls.clear()
    assert client.post("/api/auth/login", json={"email": "ld7-nobody@example.com", "password": "x" * 12}).status_code == 401
    assert calls[:2] == ["release", "verify_password_async"]


def test_register_releases_the_connection_before_bcrypt(monkeypatch):
    from app.auth import routes
    calls = _order_of(monkeypatch, routes, "hash_password_async")
    r = client.post("/api/auth/register", json={"email": "ld7-register@example.com", "password": "S3cur3Pass!"})
    assert r.status_code == 201, r.text
    assert calls[:2] == ["release", "hash_password_async"]


def test_change_password_releases_the_connection_before_bcrypt(monkeypatch):
    from app.auth import routes
    token = _register("ld7-change@example.com")
    calls = _order_of(monkeypatch, routes, "verify_password_async")
    r = client.post("/api/auth/password/change", headers={"Authorization": f"Bearer {token}"},
                    json={"current_password": "S3cur3Pass!", "new_password": "another-long-passphrase-42"})
    assert r.status_code == 200, r.text
    assert calls[:2] == ["release", "verify_password_async"]


def test_slow_routes_release_before_their_broker_calls():
    """Source-level guard for the routes the load profile hits: the release sits before the broker loop / the screen."""
    candles = (BACKEND / "app/market_data/candles_routes.py").read_text()
    body = candles[candles.index("async def candles("):candles.index("async def ltp(")]
    assert body.index("await release_connection(session)") < body.index("for raw in body.symbols:")
    screener = (BACKEND / "app/screener/routes.py").read_text()
    fetch = screener[screener.index("async def fetch_frames("):screener.index("async def _attach_daily(")]
    assert fetch.index("await release_connection(session)") < fetch.index("await asyncio.gather(")
    run = screener[screener.index("async def run("):]
    assert "release=True" in run[:run.index("screen_and_funnel")]
    assert run.index("await release_connection(session)") < run.index("asyncio.to_thread(screen_and_funnel")


# --- several processes -------------------------------------------------------------------------------------------------

def test_cpu_count_honours_a_containers_cpu_quota(tmp_path, monkeypatch):
    """LD-7: a container capped at 2 CPUs (`docker --cpus 2`) on a 4-CPU host sizes its bcrypt threads for 2."""
    from app.auth import security
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("200000 100000\n")
    assert security._cgroup_cpu_quota(str(cpu_max)) == 2
    cpu_max.write_text("150000 100000\n")
    assert security._cgroup_cpu_quota(str(cpu_max)) == 2                       # 1.5 CPUs: rounded up
    cpu_max.write_text("max 100000\n")
    assert security._cgroup_cpu_quota(str(cpu_max)) is None
    assert security._cgroup_cpu_quota(str(tmp_path / "missing")) is None
    monkeypatch.setattr(security, "_cgroup_cpu_quota", lambda path=None: 2)
    monkeypatch.setattr(security.os, "sched_getaffinity", lambda pid: {0, 1, 2, 3}, raising=False)
    assert security._cpu_count() == 2
    monkeypatch.setattr(security, "_cgroup_cpu_quota", lambda path=None: None)
    assert security._cpu_count() == 4


def test_bcrypt_limit_is_shared_between_api_processes(monkeypatch):
    from app.auth import security
    from app.core import config
    monkeypatch.setattr(security, "_cpu_count", lambda: 4)
    monkeypatch.setattr(config, "API_WORKERS", 1)
    assert security._bcrypt_concurrency() == 4
    monkeypatch.setattr(config, "API_WORKERS", 2)
    assert security._bcrypt_concurrency() == 2
    monkeypatch.setattr(config, "API_WORKERS", 3)
    assert security._bcrypt_concurrency() == 2                 # rounded up (the CI runner: 4 CPUs, 3 processes)
    monkeypatch.setattr(security, "_cpu_count", lambda: 2)
    monkeypatch.setattr(config, "API_WORKERS", 2)
    assert security._bcrypt_concurrency() == 1                 # the KVM 2: one each
    monkeypatch.setattr(config, "API_WORKERS", 4)
    assert security._bcrypt_concurrency() == 1                 # never below one


def test_metrics_merge_every_process_when_multiprocess(tmp_path):
    """Two processes write their counters to the shared directory; /metrics (render) in either one shows the sum, and a
    DB gauge shows the value set last."""
    script = (
        "import sys; from app.observability import metrics as m\n"
        "m.LOGIN_ATTEMPTS.labels(success='true', reason='password').inc(int(sys.argv[1]))\n"
        "m.OPEN_POSITIONS.labels(mode='PAPER').set(int(sys.argv[1]))\n"
        "print(m.render()[0].decode())\n"
    )
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path), "PYTHONPATH": str(BACKEND)}
    for n in ("2", "3"):
        out = subprocess.run([sys.executable, "-c", script, n], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
    text = out.stdout
    assert re.search(r'atp_login_attempts_total\{reason="password",success="true"\} 5\.0', text), text
    assert re.search(r'atp_open_positions\{mode="PAPER"\} 3\.0', text), text


def test_container_command_and_sizes():
    dockerfile = (BACKEND / "Dockerfile").read_text()
    assert 'CMD ["sh", "scripts/serve_api.sh"]' in dockerfile
    serve = (BACKEND / "scripts/serve_api.sh").read_text()
    assert '--workers "$workers"' in serve and "migrate_guard.py" in serve and "FORWARDED_ALLOW_IPS:-127.0.0.1" in serve
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["backend"]["environment"]
    assert base["API_WORKERS"] == "${API_WORKERS:-1}"                        # dev / plain compose: one process, as before
    hostinger = (ROOT / "docker-compose.hostinger.yml").read_text()
    host = yaml.safe_load(hostinger.replace("!override", "").replace("!reset", ""))["services"]

    def default(value: str) -> int:
        return int(re.fullmatch(r"\$\{[A-Z_]+:-(\d+)\}", value).group(1))
    workers = default(host["backend"]["environment"]["API_WORKERS"])
    overflow = default(host["backend"]["environment"]["DB_MAX_OVERFLOW"])
    pool = default(host["backend"]["environment"]["DB_POOL_SIZE"])
    max_connections = int(re.search(r"max_connections=\$\{PG_MAX_CONNECTIONS:-(\d+)\}", hostinger).group(1))
    assert workers == 2                                                       # KVM 2: one per vCPU
    assert pool >= 10                                                         # kept warm (LD-7: no overflow churn)
    # API pools + the trading worker's and the research worker's default pools (5 + 10 each) + Postgres' 3 reserved,
    # and at least 10 left for a backup, a restore, a migration and psql
    assert workers * (pool + overflow) + 2 * 15 + 3 + 10 <= max_connections
    assert max_connections * 10 + 512 <= 2048                                 # about 10 MB a backend + shared_buffers, in 2 GB


# --- the public status: a few seconds per process ----------------------------------------------------------------------

def test_public_status_is_cached_briefly_and_dropped_on_an_admin_change(monkeypatch):
    from sqlalchemy import event

    from app.platform import routes as platform_routes
    from tests.test_auth_api import _engine
    statements: list = []

    def count(conn, cursor, statement, params, context, many):
        statements.append(statement)
    event.listen(_engine.sync_engine, "before_cursor_execute", count)
    try:
        first = client.get("/api/system/status").json()
        assert statements                                          # read from the database
        statements.clear()
        assert client.get("/api/system/status").json() == first
        assert statements == []                                    # served from the cache
        monkeypatch.setattr(platform_routes, "STATUS_CACHE_SECONDS", 0.0)
        client.get("/api/system/status")
        assert statements                                          # expired: read again
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", count)



# --- the chart path's pandas: faster, the same frames and bars ---------------------------------------------------------

def _old_bars_to_dataframe(bars):
    import pandas as pd
    df = pd.DataFrame([b.model_dump() for b in bars])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def _old_frame_to_bars(df):
    from app.core.models import OHLCVBar
    df = df.dropna(subset=["open", "close"])
    return [OHLCVBar(timestamp=ts.to_pydatetime(), open=float(r.open), high=float(r.high), low=float(r.low), close=float(r.close),
                     volume=float(r.volume) if "volume" in df and r.volume == r.volume else 0.0) for ts, r in df.iterrows()]


_ZONES = {
    "kolkata": lambda: __import__("zoneinfo").ZoneInfo("Asia/Kolkata"),
    "zoneinfo-utc": lambda: __import__("zoneinfo").ZoneInfo("UTC"),
    "timezone-utc": lambda: __import__("datetime").timezone.utc,
    "fixed+0530": lambda: __import__("datetime").timezone(__import__("datetime").timedelta(hours=5, minutes=30)),
    "new-york-dst": lambda: __import__("zoneinfo").ZoneInfo("America/New_York"),
    "naive": lambda: None,
}


@pytest.mark.parametrize("zone", sorted(_ZONES))
def test_bars_to_dataframe_and_frame_to_bars_are_unchanged(zone):
    import random
    from datetime import datetime, timedelta

    import numpy as np
    import pandas as pd

    from app.core.models import OHLCVBar, bars_to_dataframe
    from app.market_data.candles_routes import _frame_to_bars
    from app.market_data.service import build_frames
    rng = random.Random(7)
    tz = _ZONES[zone]()
    # 2026-03-05 .. : the New York zone crosses its DST change (2026-03-08) inside the series
    start = datetime(2026, 3, 5, 9, 15, tzinfo=tz)
    bars = [OHLCVBar(timestamp=start + timedelta(minutes=i + (1440 * (i // 375)), microseconds=rng.choice([0, 0, 250])),
                     open=100 + rng.random(), high=101 + rng.random(), low=99 - rng.random(), close=100 + rng.random(),
                     volume=float(rng.randint(0, 5000))) for i in range(1875)]
    bars[10], bars[11] = bars[11], bars[10]                              # out of order: both sort
    bars.append(bars[5].model_copy())                                    # a duplicate timestamp
    new, old = bars_to_dataframe(bars), _old_bars_to_dataframe(bars)
    pd.testing.assert_frame_equal(new, old)
    assert str(new.index.dtype) == str(old.index.dtype) and new.index.name == old.index.name
    if tz is not None:
        frame = build_frames(bars, "1min", ["5min"])["5min"]
        frame.iloc[3, frame.columns.get_loc("volume")] = np.nan           # a missing volume reads as 0
        frame.iloc[4, frame.columns.get_loc("close")] = np.nan            # a bar without a close is dropped
        assert [b.model_dump(mode="json") for b in _frame_to_bars(frame)] == [b.model_dump(mode="json") for b in _old_frame_to_bars(frame)]
        no_volume = frame.drop(columns=["volume"])
        assert [b.model_dump() for b in _frame_to_bars(no_volume)] == [b.model_dump() for b in _old_frame_to_bars(no_volume)]


def test_bars_to_dataframe_mixed_zones_take_the_old_path():
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    import pandas as pd

    from app.core.models import OHLCVBar, bars_to_dataframe
    stamps = [datetime(2026, 9, 21, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")), datetime(2026, 9, 21, 3, 46, tzinfo=timezone.utc)]
    bars = [OHLCVBar(timestamp=t + timedelta(minutes=i), open=1, high=2, low=0.5, close=1.5) for i, t in enumerate(stamps)]
    try:
        old = _old_bars_to_dataframe(bars)
    except Exception as exc:  # noqa: BLE001 - whatever pandas does with mixed zones, the new code does the same
        with pytest.raises(type(exc)):
            bars_to_dataframe(bars)
        return
    pd.testing.assert_frame_equal(bars_to_dataframe(bars), old)


def test_dhan_bars_are_the_same_as_validated_ones():
    from datetime import datetime, timezone

    from app.brokers.dhan import IST
    from app.core.models import OHLCVBar
    stamp = 1790000000
    built = OHLCVBar.model_construct(timestamp=datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(IST), open=1.0, high=2.0,
                                     low=0.5, close=1.5, volume=10.0)
    validated = OHLCVBar(timestamp=datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(IST), open=1.0, high=2.0, low=0.5,
                         close=1.5, volume=10.0)
    assert built == validated and built.model_dump(mode="json") == validated.model_dump(mode="json")


# --- where requests wait (diagnostics) ------------------------------------------------------------------------------

def _hist_count(histogram) -> float:
    return next(s.value for m in histogram.collect() for s in m.samples if s.name.endswith("_count"))


def test_password_checks_record_their_queue_wait_and_run_time():
    from app.auth import security
    from app.observability.metrics import BCRYPT_QUEUE_WAIT, BCRYPT_SECONDS
    waits, runs = _hist_count(BCRYPT_QUEUE_WAIT), _hist_count(BCRYPT_SECONDS)
    hashed = asyncio.run(security.hash_password_async("correct-horse-battery-staple"))
    assert asyncio.run(security.verify_password_async("correct-horse-battery-staple", hashed)) is True
    assert asyncio.run(security.verify_password_async("wrong", hashed)) is False
    assert _hist_count(BCRYPT_QUEUE_WAIT) == waits + 3 and _hist_count(BCRYPT_SECONDS) == runs + 3


def test_the_timed_pool_records_each_checkout_and_changes_nothing_else(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.session import TimedQueuePool
    from app.observability.metrics import DB_POOL_WAIT

    async def go():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}", poolclass=TimedQueuePool, pool_size=1, max_overflow=0)
        before = _hist_count(DB_POOL_WAIT)
        for _ in range(3):
            async with engine.connect() as conn:
                assert (await conn.execute(text("select 1"))).scalar() == 1
        await engine.dispose()
        return _hist_count(DB_POOL_WAIT) - before

    assert asyncio.run(go()) == 3


def test_a_bcrypt_nice_that_is_not_a_number_falls_back_with_a_warning(caplog):
    """LD-7 review N8: BCRYPT_NICE=abc must not stop the API at import; it falls back to the default and says so."""
    import logging
    import subprocess
    import sys

    from app.auth import security
    with caplog.at_level(logging.WARNING, logger="app.auth.security"):
        assert security._bcrypt_nice("abc") == security.BCRYPT_NICE_DEFAULT == 5
    assert "BCRYPT_NICE='abc' is not a whole number" in caplog.text
    assert security._bcrypt_nice(None) == 5 and security._bcrypt_nice("") == 0 and security._bcrypt_nice(" 7 ") == 7
    assert security._bcrypt_nice("-3") == 0 and security._bcrypt_nice("40") == 19
    # the module itself imports with the bad value (a fresh interpreter, as the API process starts)
    env = {**__import__("os").environ, "BCRYPT_NICE": "high"}
    done = subprocess.run([sys.executable, "-c", "from app.auth import security; print(security.BCRYPT_NICE)"], cwd=str(BACKEND),
                          env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]
    assert done.stdout.strip().splitlines()[-1] == "5"
