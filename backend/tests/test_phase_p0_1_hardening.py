"""P0.1 (pro-grade upgrade plan, P0 security): anonymous CPU-heavy endpoints closed and bodies capped (S3), staging
bound to loopback with production checks (S4), the worker's replica lock fail-closed for LIVE entries and atomic
release/renew (S2), no `create_all` outside dev/test (S12), METRICS_TOKEN required in hardened environments, OpenAPI
title/version, and the staging deploy workflow taking its inputs through the environment."""
import asyncio
import re
from pathlib import Path

from app.cache import client as cache_client
from app.core import config
from app.main import app
from tests.test_auth_api import _register, client
from app.workers import trading_worker as tw
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _signal, _tenant, _worker

ROOT = Path(__file__).resolve().parents[2]
CLOSED = ("/api/backtest", "/api/price-action/structure", "/api/price-action/patterns", "/api/support-resistance/zones",
          "/api/option-chain/analyze", "/api/option-chain/greeks", "/api/scanner/run")


def test_cpu_heavy_endpoints_need_a_login_and_bodies_are_capped():
    for path in CLOSED:
        assert client.post(path, json={}).status_code in (401, 403), path
    headers = {"Authorization": f"Bearer {_register('p0-closed@example.com')}"}
    assert client.post("/api/option-chain/greeks", headers=headers, json={"legs": []}).status_code == 200
    # The cap reads Content-Length before any parsing: a declared oversized body is a 413, never a parse attempt.
    res = client.post("/api/scanner/run", headers={**headers, "Origin": "https://app.example.com", "Content-Type": "application/json"},
                      content=b"x" * (config.MAX_REQUEST_BODY_BYTES + 1))
    assert res.status_code == 413 and "larger than" in res.json()["detail"]
    assert res.headers.get("access-control-allow-origin") and res.headers.get("x-request-id")      # CORS + request id wrap the limiter
    for path in ("/api/backtest/monte-carlo", "/api/backtest/walk-forward", "/api/backtest/optimize"):
        assert client.post(path, json={}).status_code in (401, 403), path
    assert app.title == "AMW Algorithmic Trading Platform API" and app.version == config.APP_VERSION


def test_hardened_environments_refuse_insecure_config_and_never_create_tables():
    base = dict(jwt_secret="real-secret", secrets_key="k" * 44, allowed_origins=["https://atp.example.com"], metrics_token="m",
                email_verification_required=False, smtp_host="")
    assert config.config_problems(environment="development", **{**base, "jwt_secret": config._INSECURE_DEFAULT_JWT_SECRET, "metrics_token": ""}) == []
    for env in ("production", "staging"):
        assert config.config_problems(environment=env, **base) == []
        problems = config.config_problems(environment=env, **{**base, "metrics_token": "", "allowed_origins": ["*"], "secrets_key": None})
        assert len(problems) == 3 and any("METRICS_TOKEN" in p for p in problems)
        assert config.tables_created_at_startup(env) is False
    assert config.tables_created_at_startup("development") and config.tables_created_at_startup("test")


def test_staging_and_local_overlays_publish_on_loopback_only_and_the_deploy_workflow_takes_inputs_via_env():
    for overlay in ("docker-compose.staging.yml", "docker-compose.local.yml"):
        text = ROOT.joinpath(overlay).read_text(encoding="utf-8")
        blocks = dict(re.findall(r"^  (\w+):\n((?:    .*\n?)+)", text, flags=re.M))
        with_ports = {name: body for name, body in blocks.items() if "ports" in body}
        assert set(with_ports) == {"postgres", "redis", "backend", "frontend"}, overlay
        for name, body in with_ports.items():
            assert "ports: !override" in body, (overlay, name)
            entries = re.findall(r'^\s+- "([^"]+)"', body, flags=re.M)
            assert entries and all(e.startswith("127.0.0.1:") for e in entries), (overlay, name, entries)
    workflow = ROOT.joinpath(".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    script = workflow.split("script: |", 1)[1].split("- name:", 1)[0]
    assert "${{" not in script                                  # no expression interpolated into the remote shell
    assert "DEPLOY_REF" in script and "REPO_PATH" in script and "envs: DEPLOY_REF,REPO_PATH" in workflow


def test_replica_lock_is_fail_closed_for_live_entries_and_atomic(monkeypatch):
    class _Down:
        """Redis unreachable: every call raises (CI runs a real Redis, so the outage is simulated, not assumed)."""
        def __getattr__(self, name):
            async def fail(*a, **k):
                raise ConnectionError("redis down")
            return fail
    monkeypatch.setattr(cache_client, "_get_client", lambda: _Down())
    assert asyncio.run(cache_client.cache_try_lock("p0:lock", "me", 5)) == (True, False)
    assert asyncio.run(cache_client.cache_renew_lock("p0:lock", "me", 5)) is False
    asyncio.run(cache_client.cache_release_lock("p0:lock", "me"))                 # never raises

    class _Redis:
        """Just enough of redis.asyncio for SET NX EX and the two Lua scripts."""
        def __init__(self):
            self.store = {}
            self.evals = []
        async def set(self, key, value, nx=False, ex=None):
            if nx and key in self.store:
                return False
            self.store[key] = value
            return True
        async def eval(self, script, numkeys, key, holder, *args):
            self.evals.append(script)
            if self.store.get(key) != holder:
                return 0
            if "del" in script:
                del self.store[key]
            return 1
    fake = _Redis()
    monkeypatch.setattr(cache_client, "_get_client", lambda: fake)
    assert asyncio.run(cache_client.cache_try_lock("k", "a", 5)) == (True, True)
    assert asyncio.run(cache_client.cache_try_lock("k", "b", 5)) == (False, True)
    assert asyncio.run(cache_client.cache_renew_lock("k", "b", 5)) is False and asyncio.run(cache_client.cache_renew_lock("k", "a", 5)) is True
    asyncio.run(cache_client.cache_release_lock("k", "b"))
    assert fake.store == {"k": "a"}                              # someone else's lock is never released
    asyncio.run(cache_client.cache_release_lock("k", "a"))
    assert fake.store == {} and all("redis.call('get', KEYS[1]) == ARGV[1]" in s for s in fake.evals)


def test_worker_pauses_live_entries_without_the_replica_lock_but_paper_and_exits_continue(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("p0-lock-live@example.com")
    live_id, paper_id = _deploy(t, mode="LIVE"), _deploy(t, mode="PAPER", symbol="INFY")
    broker = _FakeBroker(ltp=101.0)
    worker = _worker(monkeypatch, broker)
    _force_signal(monkeypatch, _signal)
    # Redis unreachable for the whole cycle (simulated - CI has a real Redis).
    async def down(key, holder, ttl):
        return True, False
    monkeypatch.setattr(tw, "cache_try_lock", down)

    worker.require_lock_for_live = True
    report = asyncio.run(worker.run_cycle(now=OPEN_NOW))
    assert report.lock_degraded is True and not report.skipped_lock
    assert broker.placed == []                                   # the LIVE entry was not sent to the broker
    assert report.signals_executed == 1                          # the PAPER sibling traded
    from sqlalchemy import select
    from app.db.models import StrategyDeploymentRecord
    from tests.test_auth_api import _session_factory

    async def errors():
        async with _session_factory() as session:
            return {r.id: r.last_error for r in await session.scalars(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.id.in_([live_id, paper_id])))}
    rows = asyncio.run(errors())
    assert "Redis replica lock unavailable" in (rows[live_id] or "") and "Redis replica lock" not in (rows[paper_id] or "")

    # A dev/test single replica (the default outside production/staging) still trades LIVE without Redis.
    worker.require_lock_for_live = False
    asyncio.run(worker.run_cycle(now=OPEN_NOW))
    assert [o.order_type for o in broker.placed] == ["MARKET", "SL-M"]
