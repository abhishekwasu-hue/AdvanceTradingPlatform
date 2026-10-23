"""Lane D: the pieces of the CI staging stack that can be checked without Docker (deploy/ci/, the load test's shard
mode). The stack itself runs in .github/workflows/staging-stack.yml.

* the fake broker answers the real Upstox and Dhan adapters (wired through an in-memory transport), records every call,
  and an order-API call is refused and recorded as such;
* its clock shift and stale mode reach the app's own staleness gate;
* the clock picker (deploy/ci/ci_clock.py) lands inside an NSE session on a trading day;
* the load test's shards merge into one verdict;
* the overlay keeps production untouched: deploy/atp.sh never reads it, and it publishes nothing beyond 127.0.0.1.
"""
import asyncio
import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from app.brokers.dhan import DhanBroker
from app.brokers.exceptions import BrokerAPIError
from app.brokers.models import BrokerCredentials, BrokerOrderRequest
from app.brokers.upstox import UpstoxBroker
from app.core.enums import OrderSide
from app.market_data.freshness import StaleMarketDataError
from app.market_data.service import MarketDataService

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / "deploy" / "ci"
IST = timezone(timedelta(hours=5, minutes=30))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fake = _load("ci_fake_broker", CI / "fake_broker.py")
clock = _load("ci_clock", CI / "ci_clock.py")
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
import loadtest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_fake():
    with fake.LOCK:
        fake.CALLS.clear()
        fake.STATE["stale_minutes"] = {"upstox": 0, "dhan": 0}
        fake.STATE["ltp"] = {}
    yield


def _client(base_url: str) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        reply = fake.dispatch(request.url.host, request.method, path, request.content, {k.lower(): v for k, v in request.headers.items()})
        kind = reply.kind or fake.classify(request.url.host, request.method, request.url.path)
        fake.record(request.url.host, request.method, request.url.path, kind, reply.status)
        return httpx.Response(reply.status, content=reply.raw, headers={"Content-Type": reply.content_type, **reply.headers})
    return httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(handle))


def _run(coro):
    return asyncio.run(coro)


def test_the_real_upstox_adapter_works_against_the_fake_and_its_orders_are_refused_and_recorded():
    broker = UpstoxBroker(BrokerCredentials(api_key="k", api_secret="s", request_token="code", redirect_uri="https://x/cb"),
                          client=_client(UpstoxBroker.BASE_URL))
    profile = _run(broker.authenticate())                        # the OAuth code exchange, then the profile
    assert profile.user_id and broker.access_token.startswith("ci-upstox-")
    ltp = _run(broker.get_ltp_for_symbol("RELIANCE"))
    quote = _run(broker.get_quote_for_symbol("RELIANCE"))
    assert ltp > 0 and abs(quote.ltp - ltp) < ltp * 0.01
    assert (datetime.now(timezone.utc) - quote.timestamp).total_seconds() < 120            # fresh: passes the G1 gate
    bars = _run(broker.get_intraday_candles("TCS", "NSE", "1min"))
    assert bars == sorted(bars, key=lambda b: b.timestamp)
    now = datetime.now(IST)
    daily = _run(broker.get_historical_data("INFY", "NSE", "day", now - timedelta(days=60), now - timedelta(days=1)))
    assert 30 <= len(daily) <= 45 and all(b.close > 0 for b in daily)
    with pytest.raises(BrokerAPIError):
        _run(broker.place_order(BrokerOrderRequest(symbol="RELIANCE", exchange="NSE", transaction_type=OrderSide.BUY, quantity=1, order_type="MARKET")))
    summary = fake.calls_summary()
    assert [c["path"] for c in summary["orders"]] == ["/v2/order/place"]
    assert summary["unknown"] == []


def test_the_real_dhan_adapter_works_against_the_fake_and_its_orders_are_refused_and_recorded():
    broker = DhanBroker(BrokerCredentials(client_id="CI1", access_token="t"), client=_client(DhanBroker.BASE_URL))
    assert _run(broker.get_profile()).user_id == "CI1"
    quote = _run(broker.get_quote_for_symbol("INFY"))
    assert quote.ltp > 0 and quote.timestamp is not None
    assert _run(broker.get_ltp_for_symbol("NIFTY")) > 10000                           # an index through IDX_I
    now = datetime.now(IST)
    five = _run(broker.get_historical_data("SBIN", "NSE", "5min", now - timedelta(days=5), now))
    assert five and all((b.timestamp.minute - 15) % 5 == 0 for b in five)
    with pytest.raises(BrokerAPIError):
        _run(broker.place_order(BrokerOrderRequest(symbol="INFY", exchange="NSE", transaction_type=OrderSide.SELL, quantity=1, order_type="MARKET")))
    assert [c["path"] for c in fake.calls_summary()["orders"]] == ["/v2/orders"]


def test_stale_mode_and_price_override_reach_the_apps_staleness_gate():
    broker = DhanBroker(BrokerCredentials(client_id="CI1", access_token="t"), client=_client(DhanBroker.BASE_URL))
    service = MarketDataService(broker)
    fake.STATE["ltp"]["TCS"] = 1234.5
    assert _run(service.get_ltp("TCS")) == 1234.5
    fake.STATE["stale_minutes"]["dhan"] = 60
    with pytest.raises(StaleMarketDataError):
        _run(service.get_ltp("TCS"))
    upstox = UpstoxBroker(BrokerCredentials(api_key="k", access_token="t"), client=_client(UpstoxBroker.BASE_URL))
    assert _run(MarketDataService(upstox).get_ltp("TCS")) == 1234.5                      # the other broker stays current


def test_fake_broker_helpers():
    assert fake.interval_minutes("1minute") == 1 and fake.interval_minutes("30minute") == 30 and fake.interval_minutes("5") == 5
    assert fake.interval_minutes("day") is None
    assert fake.classify("api.upstox.com", "POST", "/v2/order/place") == "order"
    assert fake.classify("api.upstox.com", "GET", "/v2/order/retrieve-all") == "read"
    assert fake.classify("api.dhan.co", "DELETE", "/v2/orders/12") == "order"
    assert fake.classify("api.dhan.co", "POST", "/v2/charts/intraday") == "read"
    monday = date(2026, 10, 12)
    day_bars = fake.bars("RELIANCE", 1, monday, monday, datetime(2026, 10, 12, 16, 0, tzinfo=IST))
    assert len(day_bars) == 375 and day_bars[0][0] == datetime(2026, 10, 12, 9, 15, tzinfo=IST)
    assert fake.bars("RELIANCE", 1, date(2026, 10, 11), date(2026, 10, 11), datetime(2026, 10, 12, tzinfo=IST)) == []   # Sunday
    assert fake.price_at("TCS", 10) == fake.price_at("TCS", 10) > 0                     # deterministic


HOLIDAYS = {date(2026, 10, 2), date(2026, 10, 20)}


@pytest.mark.parametrize("now_ist, expected", [
    (datetime(2026, 10, 12, 11, 0, tzinfo=IST), datetime(2026, 10, 12, 11, 0, tzinfo=IST)),     # inside the session: real
    (datetime(2026, 10, 12, 22, 0, tzinfo=IST), datetime(2026, 10, 12, 10, 0, tzinfo=IST)),     # evening: today 10:00
    (datetime(2026, 10, 12, 8, 0, tzinfo=IST), datetime(2026, 10, 9, 10, 0, tzinfo=IST)),       # Monday early: Friday
    (datetime(2026, 10, 11, 12, 0, tzinfo=IST), datetime(2026, 10, 9, 10, 0, tzinfo=IST)),      # Sunday: Friday
    (datetime(2026, 10, 21, 7, 0, tzinfo=IST), datetime(2026, 10, 19, 10, 0, tzinfo=IST)),      # after a holiday: the day before it
])
def test_the_clock_lands_inside_an_nse_session_on_a_trading_day(now_ist, expected):
    assert clock.pick(now_ist.astimezone(timezone.utc), HOLIDAYS) == expected
    assert clock.pick(now_ist.astimezone(timezone.utc), HOLIDAYS, mode="real") == now_ist


def test_clock_settings_and_the_seeded_holidays():
    days = clock.nse_holidays(ROOT / "backend" / "alembic" / "versions")
    assert date(2026, 1, 26) in days and date(2026, 10, 20) in days
    now = datetime(2026, 10, 12, 16, 30, tzinfo=timezone.utc)
    s = clock.settings(now, now - timedelta(hours=12))
    assert s["ATP_CI_OFFSET_SECONDS"] == "-43200" and s["ATP_CI_FAKETIME"] == "-43200" and s["ATP_CI_LD_PRELOAD"].endswith("libfaketimeMT.so.1")
    assert clock.settings(now, now)["ATP_CI_LD_PRELOAD"] == ""


def test_the_shifted_clock_keeps_real_waits_for_threads():
    """LD-7: with the monotonic clock real, libfaketime's forced monotonic fix (its default on glibc >= 2.30) mis-times
    every monotonic condition-variable wait - the GIL's - and a load run on the shifted clock measures spinning threads.
    The API and the worker (the two services that get the preload) run with it off."""
    overlay = yaml.safe_load((CI / "compose.ci.yml").read_text().replace("!override", "").replace("!reset", ""))
    for service in ("backend", "worker"):
        env = overlay["services"][service]["environment"]
        assert env["LD_PRELOAD"] == "${ATP_CI_LD_PRELOAD:-}" and env["FAKETIME_DONT_FAKE_MONOTONIC"] == "1", service
        assert env["FAKETIME_FORCE_MONOTONIC_FIX"] == "0", service


def test_load_test_shards_merge_into_one_verdict(tmp_path):
    paths = []
    for i, (latency, outcome) in enumerate(((20.0, "ok"), (40.0, "ok"))):
        stats = loadtest.Stats()
        for _ in range(10):
            stats.add("dashboard /api/auth/me", latency, outcome)
        stats.add("login", 300.0, "ok")
        stats.sessions["logged_in"] += 5
        path = tmp_path / f"shard-{i}.json"
        path.write_text(json.dumps({"users": 5, "seconds": 30 + i, **stats.raw()}))
        paths.append(str(path))
    merged = loadtest.merge(paths)
    assert merged["users"] == 10 and merged["seconds"] == 31 and merged["shards"] == 2 and merged["requests"] == 22
    assert merged["scenarios"]["dashboard /api/auth/me"]["n"] == 20 and merged["slo_pass"]
    md = loadtest.markdown(merged, "test host")
    assert "**10 users** (10 logged in, 0 without a login), 2 shards" in md and "**SLO PASS**" in md
    assert "| login | 2 |" in md


def test_each_profile_report_carries_the_runners_bcrypt_baseline_in_its_first_line(tmp_path):
    """Informational only: a slow runner's login failure is not misread as a regression. It never changes the verdict."""
    stats = loadtest.Stats()
    stats.add("dashboard /api/auth/me", 20.0, "ok")
    stats.add("login", 300.0, "ok")
    stats.sessions["logged_in"] += 1
    path = tmp_path / "shard-0.json"
    path.write_text(json.dumps({"users": 1, "seconds": 30, **stats.raw()}))
    merged = loadtest.merge([str(path)])
    plain = loadtest.markdown(merged, "ci host")
    merged["host_baseline"] = {"cpu_model": "AMD EPYC 9V45 96-Core Processor", "vcpus": "4", "bcrypt12_ms": 221}
    first = loadtest.markdown(merged, "ci host").splitlines()[0]
    assert "on ci host (AMD EPYC 9V45 96-Core Processor, 4 vCPU; bcrypt-12 check: 221 ms (CI reference: ~220 ms passes LD-9" in first
    assert first.index("bcrypt-12") < 300 and "**SLO PASS**" in first and plain.splitlines()[1:] == loadtest.markdown(merged, "ci host").splitlines()[1:]
    merged["host_baseline"] = {"cpu_model": "unknown", "vcpus": "4", "bcrypt12_ms": None}
    assert "(unknown, 4 vCPU; bcrypt-12 check: unknown (CI reference" in loadtest.markdown(merged, "ci host").splitlines()[0]
    stack = (CI / "stack.sh").read_text()
    measured, started = stack.index("bcrypt_baseline.py"), stack.index('scrape_metrics > "$STATE/$name/metrics-before.prom"')
    assert measured < started and '--bcrypt-ms "$bcrypt_ms"' in stack                     # before the load: uncontended


def test_the_overlay_leaves_production_alone():
    atp = (ROOT / "deploy" / "atp.sh").read_text()
    # deploy/ci/ is the CI-only folder; deploy/ci_gate.py (A3) is production code next to atp.sh, so match the folder
    assert "deploy/ci/" not in atp and "compose.ci" not in atp
    overlay = yaml.safe_load((CI / "compose.ci.yml").read_text().replace("!override", "").replace("!reset", ""))
    for name, svc in overlay["services"].items():
        for port in svc.get("ports") or []:
            assert str(port).startswith("127.0.0.1:"), (name, port)
    for name in ("backend", "worker", "ci-runner"):
        hosts = {h.split(":")[0] for h in overlay["services"][name]["extra_hosts"]}
        assert {"api.upstox.com", "assets.upstox.com", "api.dhan.co", "images.dhan.co"} <= hosts, name
    assert "SUPER_ADMIN_EMAILS: ${SUPER_ADMIN_EMAILS:-}" in (ROOT / "docker-compose.yml").read_text()
    stack = (CI / "stack.sh").read_text()
    assert '[ "$PROJECT" != "atp" ]' in stack                                            # refuses the production project


def test_after_ramp_split_is_a_diagnostic_beside_the_verdict(tmp_path):
    """LD-7: requests started after the login ramp are also reported on their own; the verdict still covers every
    request (a slow ramp keeps failing the run)."""
    paths = []
    for i in range(2):
        stats = loadtest.Stats(ramp_end=100.0)
        for at, ms in ((50.0, 3000.0), (150.0, 20.0), (160.0, 30.0)):
            stats.add("dashboard /api/auth/me", ms, "ok", at)
        path = tmp_path / f"shard-{i}.json"
        path.write_text(json.dumps({"users": 1, "seconds": 200, **stats.raw()}))
        paths.append(str(path))
    merged = loadtest.merge(paths)
    assert merged["after_ramp"]["dashboard /api/auth/me"]["n"] == 4 and merged["after_ramp"]["dashboard /api/auth/me"]["p95_ms"] == 30.0
    assert merged["scenarios"]["dashboard /api/auth/me"]["n"] == 6 and not merged["slo_pass"]   # the ramp's 3 s still counts
    md = loadtest.markdown(merged, "test host")
    assert "**SLO FAIL**" in md and "After the login ramp only (diagnostic" in md


def test_ci_postgres_is_hostingers_plus_pg_stat_statements_only():
    """LD-7 diagnostics: the CI overlay's Postgres command is the KVM 2's, unchanged, plus the statements extension."""
    def command(path: Path) -> list:
        return yaml.safe_load(path.read_text().replace("!override", "").replace("!reset", ""))["services"]["postgres"]["command"]

    hostinger, ci = command(ROOT / "docker-compose.hostinger.yml"), command(CI / "compose.ci.yml")
    assert ci == hostinger + ["-c", "shared_preload_libraries=pg_stat_statements", "-c", "pg_stat_statements.track=top"]


def test_metrics_summary_reports_route_time_shares_and_waits(tmp_path, capsys):
    summary = _load("metrics_summary", CI / "metrics_summary.py")
    route = 'atp_http_request_duration_seconds_{0}{{method="GET",route="{1}"}} {2}'
    before = tmp_path / "before.prom"
    before.write_text("\n".join([route.format("count", "/api/a", 10), route.format("sum", "/api/a", 1.0)]))
    after = tmp_path / "after.prom"
    after.write_text("\n".join([
        route.format("count", "/api/a", 110), route.format("sum", "/api/a", 4.0),
        route.format("count", "/api/b", 10), route.format("sum", "/api/b", 1.0),
        'atp_bcrypt_queue_wait_seconds_bucket{le="0.5"} 9', 'atp_bcrypt_queue_wait_seconds_bucket{le="1.0"} 10',
        'atp_bcrypt_queue_wait_seconds_bucket{le="+Inf"} 10',
        "atp_bcrypt_queue_wait_seconds_count 10", "atp_bcrypt_queue_wait_seconds_sum 4.0",
    ]))
    summary.main(str(before), str(after))
    out = capsys.readouterr().out
    assert "| GET /api/a | 100 | 3.0 | 30.0 | 75.0% |" in out and "| GET /api/b | 10 | 1.0 | 100.0 | 25.0% |" in out
    assert "| POST /api/auth/login | 0 | - | - |" in out                         # LD-9: login apart, none in this run
    login = 'atp_http_request_duration_seconds_{0}{{method="POST",route="/api/auth/login"{1}}} {2}'
    after.write_text(after.read_text() + "\n" + "\n".join([
        login.format("bucket", ',le="0.5"', 18), login.format("bucket", ',le="2.5"', 20), login.format("bucket", ',le="+Inf"', 20),
        login.format("count", "", 20), login.format("sum", "", 9.0)]))
    summary.main(str(before), str(after))
    out = capsys.readouterr().out
    assert "| POST /api/auth/login | 20 | 450.0 | 2500 |" in out
    after.write_text(after.read_text() + "\n" + "\n".join([                   # LD-10 review: where a chart waits
        'atp_api_budget_wait_seconds_bucket{kind="history",le="0.001"} 9', 'atp_api_budget_wait_seconds_bucket{kind="history",le="1.0"} 10',
        'atp_api_budget_wait_seconds_bucket{kind="history",le="+Inf"} 10', 'atp_api_budget_wait_seconds_count{kind="history"} 10',
        'atp_api_budget_wait_seconds_sum{kind="history"} 0.9',
        'atp_shared_candles_total{part="hist",outcome="copy"} 40', 'atp_shared_candles_total{part="today",outcome="filled"} 3']))
    summary.main(str(before), str(after))
    out = capsys.readouterr().out
    assert "| broker token (API budget, per call) | 10 | 0.9 | 90.0 | 1000 | 1000 |" in out
    assert "| candles: on another request's fetch | 0 |" in out
    assert "| hist | copy | 40 |" in out and "| today | filled | 3 |" in out
    assert "| bcrypt: waiting for a thread | 10 | 4.0 | 400.0 | 1000 | 1000 |" in out
    assert "| pool connection checkout | 0 |" in out


def test_shards_interleave_their_start_times_over_the_ramp():
    """LD-7: 25 shards of 20 users on a 150 s ramp start one user every 0.3 s between them - not 25 at once every 7.5 s
    (each shard's own schedule alone repeats at the same instants). The same 500 logins over the same 150 s."""
    shards, per, ramp = 25, 20, 150.0
    starts = sorted(t for k in range(shards) for t in loadtest.start_offsets(per, ramp, k, shards))
    assert len(starts) == 500 and starts[0] == 0.0 and starts[-1] < ramp
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) == pytest.approx(0.3) and max(gaps) == pytest.approx(0.3)
    assert loadtest.start_offsets(4, 8.0) == [0.0, 2.0, 4.0, 6.0]                      # one process: as before
    stack = (CI / "stack.sh").read_text()
    assert '--shard "$((in_step ? 0 : i))" --shards "$((in_step ? 1 : shards))" --start-at "$start_at"' in stack


@pytest.mark.parametrize("minutes", [1, 5, None])
def test_fake_broker_bars_per_minute_cache_changes_nothing(minutes):
    """LD-7: the fake's bars are computed once per minute (it shares the runner's CPUs with the stack under test); a
    bar starts on a minute, so the answer is the same as computing it for the exact instant."""
    for until in (datetime(2026, 10, 9, 11, 7, 42, tzinfo=IST), datetime(2026, 10, 9, 15, 29, 59, 999999, tzinfo=IST),
                  datetime(2026, 10, 9, 9, 15, tzinfo=IST), datetime(2026, 10, 10, 12, 0, 30, tzinfo=IST)):
        cached = fake.bars("RELIANCE", minutes, date(2026, 10, 5), date(2026, 10, 10), until)
        exact = list(fake._bars_per_minute.__wrapped__("RELIANCE", minutes, date(2026, 10, 5), date(2026, 10, 10), until))
        assert cached == exact and cached
