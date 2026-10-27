#!/usr/bin/env python
"""Phase O4 / section 54, Z10-d: session-shaped API load test with SLO thresholds.

    python scripts/loadtest.py --in-process --users 20 --seconds 20                 # CI: the test app, in this process
    python scripts/loadtest.py --base https://staging.example.org --users 500 --seconds 600 --ramp 120 \\
        --accounts accounts.csv                                                      # the owner, against staging

Each virtual user behaves like a person with the app open: it logs in once, then loops - a dashboard poll (the five
read paths the shell polls: `/api/auth/me`, `/api/deployments`, `/api/notifications`, `/api/system/status`,
`/api/portfolio/exposure`, sent together as a browser does), and with some probability per loop the proposal list
(`/api/ai/actions?status=PROPOSED`), the PAPER positions (`/api/positions` with the PAPER account card,
`/api/paper/account`), a chart (`POST /api/market-data/candles`,
5-minute bars) and a screener run (`POST /api/screener/run`, `preview: true` - evaluated, never stored) - then waits
a think time. Users start spread over `--ramp` seconds, so the logins do not all land in the same second.

Prints p50/p95/p99 per scenario and the error rate, and exits 1 when an SLO is missed. Two verdicts (LD-9, owner,
20 Oct: login is measured apart from the API target):

* **API** - every scenario but login: p95 at or under 500 ms (chart 1,000 ms; screener run 2,000 ms - their documented
  limits) and server errors at most 0.5% of the API requests. Responses that are not failures of the API are counted
  apart, never as errors: a feature the operator has off (503 with X-Feature-Disabled), no broker session (409) and rate
  limiting (429).
* **Login** - p95 at or under 2,000 ms (password hashing is slow on purpose) and no errors at all: every login attempt
  that does not answer 200 (a time-out, a 5xx, a 429, a refusal) fails it. A run with no login (`--token`) fails it
  too: not measured is not a pass.

Both must pass.

Safety: read paths plus login and a preview screen - it places, changes and approves nothing, and it never sends an
order. `--base` must be localhost or a staging host (one of its DNS labels is exactly "staging", as in
staging.example.org or api.staging.example.org, or it is listed in LOADTEST_ALLOWED_HOSTS): the script refuses anything else, so it is never pointed at the production VPS by mistake.
Accounts: `--accounts FILE` (one `email,password` per line; user i uses account i mod n, and the first user of each
account logs in for it - so keep the file to about 8 accounts: the login limit is 10 a minute per IP), `--token` (one
shared access token, no login step), or neither: it registers `--register` throwaway accounts (needs self sign-up on).
Access tokens live 15 minutes: keep `--sign-in` + `--ramp` + `--seconds` under 14 minutes, or late requests answer 401.
Real host (`deploy/atp.sh loadtest`, docs/deploy/LOADTEST_REAL_HOST.md): `--chart-lake` adds the `chart_lake` scenario
(with the chart's probability: a year of daily bars from the shared market lake, `GET /api/market-data/history` - a
database read, no broker), the chart data an organisation without a broker session can see; an answer without bars counts
as `ok_empty`, apart from `ok`. `--host-header` / `--no-verify-hostname` reach Caddy by its container address when the
edge serves a bare IP (CADDYFILE=Caddyfile.ip: no SNI, Caddy's own CA). The host must still be allowed
(LOADTEST_ALLOWED_HOSTS), which `atp.sh loadtest` sets for that run only.
`--in-process` runs the test app (scripts/loadtest_app.py: a temporary SQLite file, synthetic broker bars, the test
suite's rate-limit overrides) with no server and no network. `--slo-factor auto` (CI) scales the p95 limits by the
machine's CI factor (scripts/bench_screener.py calibration, never below 1); a staging run keeps the plain limits.
Results go into docs/PERFORMANCE.md.

Sharded (Lane D, deploy/ci/stack.sh load): many processes, each on its own client IP, each driving a slice of the users
with its own accounts (`--accounts FILE --account-offset K`, one account per user, so every user logs in), each writing
its raw samples (`--raw-out FILE`); then `--merge FILE...` judges them together and writes `--json-out` / `--markdown`.
`--shard K --shards N --start-at T` puts the shards on one schedule: all ramps start at Unix time T and shard K's users
take the K/N-th part of each start slot, so the logins of all shards together arrive evenly over the ramp.
The per-IP login limit is never bypassed: more client IPs carry the login wave, as real users' do. `--cacert` verifies
an HTTPS edge with its own CA (the CI stack's Caddy).

Sign-in first, then a login probe (CI load-500; `--sign-in S --login-probe-every P`): with the 500 password logins inside
the ramp, bcrypt (cost 12, never lowered) and the sessions that had already started polling shared the server's two CPUs,
and the login p95 followed the queue, not the code: 1.2-1.4 s on a runner whose bcrypt-12 check took 234 ms, 5-24 s at
255-303 ms (13 main runs, 27 Oct). Real users at market open mostly reuse a session they already hold; password logins
arrive as a thin stream. So the run has three phases on one shared clock: (1) sign-in, S seconds: every user logs in once
(the same per-IP-safe spread as the old ramp); each must succeed, the latency is reported as `login (sign-in)` and not
judged; (2) the ramp: the users open the app on those sessions, spread over `--ramp`, no password login; (3) the steady
window: all sessions polling (the API verdict, unchanged), and a probe - one password login every P seconds, all shards
together, open loop (a slow login never delays the next arrival), each on a new connection as a returning user's would be
- is the `login` scenario, judged by LD-9 (p95 budget, no failed login). The budget is LOGIN_P95_MS, scaled by this host's
bcrypt-12 check against the reference core (`--bcrypt-ms`, merge only; never below LOGIN_P95_MS) - see login_budget().
Without `--sign-in` the run is the one-phase shape above (the real-host test, the in-process CI test, the burst profile).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx

DASHBOARD = ["/api/auth/me", "/api/deployments", "/api/notifications", "/api/system/status", "/api/portfolio/exposure"]
# (scenario, probability per loop) - the dashboard poll runs every loop
EXTRA = [("proposals", 0.5), ("positions", 0.5), ("chart", 0.25), ("screener_run", 0.1)]
POSITIONS_PAGE = ["positions", "paper_account"]
P95_LIMIT_MS: Dict[str, float] = {"chart": 1000.0, "screener_run": 2000.0}
DEFAULT_P95_MS = 500.0
ERROR_LIMIT = 0.005
# LD-9: login is judged apart from the API scenarios - its own p95 limit, and no failed login at all.
LOGIN = "login"
LOGIN_P95_MS = 2000.0
LOGIN_ERROR_LIMIT = 0
# --sign-in: the logins that establish the sessions before the ramp - every one must succeed (LOGIN_ERROR_LIMIT), the
# latency is reported, not judged; `login` is then the probe's logins in the steady window.
LOGIN_SIGN_IN = "login (sign-in)"
LOGINS = (LOGIN, LOGIN_SIGN_IN)
SAFE_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}
SCREEN = "close > SMA(close, 20) AND RSI(14) > 40 AND PctChange(close, 5) > -10"
SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "LT", "ITC", "AXISBANK", "KOTAKBANK"]
PASSWORD = "L0adTest!Pass"


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


# Outcomes that are an answer from the API (judged on latency); `ok_empty` = 200 without data (chart_lake on an empty lake).
ANSWERED = ("ok", "ok_empty")
LAKE_DAYS = 365


def classify(status: Optional[int], headers: Optional[httpx.Headers] = None) -> str:
    """ok, disabled (feature flag off), unavailable (no broker session), throttled, client_error, server_error, transport."""
    if status is None:
        return "transport"
    if status >= 400 and headers is not None and headers.get("X-Feature-Disabled"):     # 503 (most flags) or 404 (PAPER account)
        return "disabled"
    if status < 400:
        return "ok"
    if status == 409:
        return "unavailable"
    if status == 429:
        return "throttled"
    return "server_error" if status >= 500 else "client_error"


def check_host(base: str) -> None:
    """Refuse anything but localhost or a staging host (never the production VPS)."""
    host = (urlparse(base).hostname or "").lower().rstrip(".")
    allowed = {h.strip().lower().rstrip(".") for h in os.environ.get("LOADTEST_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host in SAFE_HOSTS or "staging" in host.split(".") or host in allowed:          # a whole DNS label, never a substring
        return
    raise SystemExit(f"loadtest refuses {host!r}: only localhost or a staging host. It must never run against the production VPS; "
                     "for a staging host without a 'staging' label in its name set LOADTEST_ALLOWED_HOSTS=<host>.")


@dataclass
class Stats:
    latency: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    outcomes: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    sessions: Dict[str, int] = field(default_factory=lambda: defaultdict(int))      # logged_in / no_login
    # LD-7 diagnostics: the latencies of requests started after the login ramp (time.monotonic() >= ramp_end) as well,
    # so a report can say whether the tail comes from the ramp. The verdict always covers every request.
    ramp_end: Optional[float] = None
    after_ramp: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))

    def add(self, scenario: str, ms: float, outcome: str, started_at: Optional[float] = None) -> None:
        self.latency[scenario].append(ms)
        self.outcomes[scenario][outcome] += 1
        if self.ramp_end is not None and started_at is not None and started_at >= self.ramp_end:
            self.after_ramp[scenario].append(ms)

    def raw(self) -> Dict[str, Any]:
        return {"latency": {k: [round(v, 2) for v in vals] for k, vals in self.latency.items()},
                "outcomes": {k: dict(v) for k, v in self.outcomes.items()}, "sessions": dict(self.sessions),
                "after_ramp": {k: [round(v, 2) for v in vals] for k, vals in self.after_ramp.items()}}

    def absorb(self, raw: Dict[str, Any]) -> None:
        for scenario, values in (raw.get("latency") or {}).items():
            self.latency[scenario].extend(float(v) for v in values)
        for scenario, counts in (raw.get("outcomes") or {}).items():
            for outcome, n in counts.items():
                self.outcomes[scenario][outcome] += int(n)
        for key, n in (raw.get("sessions") or {}).items():
            self.sessions[key] += int(n)
        for scenario, values in (raw.get("after_ramp") or {}).items():
            self.after_ramp[scenario].extend(float(v) for v in values)


@dataclass
class Account:
    email: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)   # set once the account's first user has logged in


async def _timed(stats: Stats, scenario: str, call: Callable[[], Any]) -> Optional[httpx.Response]:
    started, at = time.perf_counter(), time.monotonic()
    try:
        response = await call()
    except httpx.HTTPError:
        stats.add(scenario, (time.perf_counter() - started) * 1000, "transport", at)
        return None
    elapsed = (time.perf_counter() - started) * 1000
    outcome = classify(response.status_code, response.headers)
    if outcome == "ok" and scenario == "chart_lake" and not _has_candles(response):
        outcome = "ok_empty"
    stats.add(scenario, elapsed, outcome, at)
    return response


def _has_candles(response: httpx.Response) -> bool:
    try:
        return bool(response.json().get("candles"))
    except ValueError:
        return False


def lake_params(symbol: str, now: Optional[float] = None) -> Dict[str, str]:
    """A year of daily bars of one NSE symbol from the shared lake (the history API's own query)."""
    end = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc).replace(microsecond=0)
    return {"instrument_key": f"NSE:{symbol}", "timeframe": "day", "start": (end - timedelta(days=LAKE_DAYS)).isoformat(),
            "end": end.isoformat()}


async def _login(client: httpx.AsyncClient, account: Account, stats: Stats, scenario: str = LOGIN) -> Optional[str]:
    if account.email is None or account.password is None:
        return account.token
    r = await _timed(stats, scenario, lambda: client.post("/api/auth/login", json={"email": account.email, "password": account.password}))
    if r is not None and r.status_code == 200:
        return r.json().get("access_token") or account.token
    return account.token                                       # throttled or refused: the setup token keeps the session going


async def _session(i: int, make_client: Callable[[], httpx.AsyncClient], account: Account, leader: bool, stats: Stats, start_delay: float,
                   deadline: float, think: float, seed: int, extra: Optional[List[Any]] = None, open_at: Optional[float] = None) -> None:
    """One user. The first user of each account logs in; the others open more sessions on that login (the per-IP login
    limit - 10 a minute - would refuse 500 logins from one load generator, and logins are rare next to polling).
    `open_at` (--sign-in): the login is the sign-in phase's; the user opens the app (starts polling) only at this
    time.monotonic() instant, on the session it holds."""
    rng = random.Random(seed * 1_000 + i)
    await asyncio.sleep(start_delay)
    async with make_client() as client:
        if leader:
            account.token = await _login(client, account, stats, LOGIN if open_at is None else LOGIN_SIGN_IN)
            account.ready.set()
        else:
            await account.ready.wait()
        if open_at is not None:
            await asyncio.sleep(max(0.0, open_at - time.monotonic()))
        token = account.token
        stats.sessions["logged_in" if token else "no_login"] += 1
        if not token:
            return
        client.headers["Authorization"] = f"Bearer {token}"
        symbol = SYMBOLS[i % len(SYMBOLS)]
        actions: Dict[str, Callable[[], Any]] = {
            "proposals": lambda: client.get("/api/ai/actions", params={"status": "PROPOSED"}),
            "positions": lambda: client.get("/api/positions"),
            "paper_account": lambda: client.get("/api/paper/account"),
            "chart": lambda: client.post("/api/market-data/candles", json={"symbols": [symbol], "timeframe": "5min", "lookback_days": 5}),
            "screener_run": lambda: client.post("/api/screener/run", json={"source": SCREEN, "base_tf": "1d", "preview": True,
                                                                           "symbols": rng.sample(SYMBOLS, 5)}),
            "chart_lake": lambda: client.get("/api/market-data/history", params=lake_params(symbol)),
        }
        while time.monotonic() < deadline:
            await asyncio.gather(*(_timed(stats, f"dashboard {route}", lambda route=route: client.get(route)) for route in DASHBOARD))
            for scenario, probability in (extra if extra is not None else EXTRA):
                if rng.random() < probability and time.monotonic() < deadline:
                    together = POSITIONS_PAGE if scenario == "positions" else [scenario]       # the Positions page loads both
                    await asyncio.gather(*(_timed(stats, name, actions[name]) for name in together))
            if think:
                await asyncio.sleep(think * rng.uniform(0.5, 1.5))


async def _login_probe(make_client: Callable[[], httpx.AsyncClient], accounts: List[Account], stats: Stats, at: List[float]) -> None:
    """The steady window's password logins (--login-probe-every): one at each time.monotonic() instant of `at`, round
    robin over this process's accounts, each on a new connection (a returning user's browser), open loop - every login
    starts at its own instant, whatever the ones before it are still waiting for, so a queue shows in the p95."""
    usable = [a for a in accounts if a.email and a.password]

    async def one(when: float, account: Account) -> None:
        await asyncio.sleep(max(0.0, when - time.monotonic()))
        async with make_client() as client:
            await _login(client, account, stats, LOGIN)

    if usable:
        await asyncio.gather(*(one(when, usable[j % len(usable)]) for j, when in enumerate(at)))


def probe_offsets(window: float, every: float, shard: int = 0, shards: int = 1) -> List[float]:
    """When this process's probe logins start, in seconds after the steady window begins: one login every `every`
    seconds over all shards together, shard k taking the k-th of every `shards` slots - so each client IP makes one
    login every every x shards seconds (2 s x 25 shards: 1.2 a minute, far under the 10-a-minute per-IP limit)."""
    if every <= 0 or window <= 0:
        return []
    shards = max(1, shards)
    shard = min(max(0, shard), shards - 1)
    out, j = [], 0
    while (t := every * (shard + shards * j)) < window:
        out.append(t)
        j += 1
    return out


async def _register(make_client: Callable[[], httpx.AsyncClient], count: int) -> List[Account]:
    accounts: List[Account] = []
    async with make_client() as c:
        for _ in range(count):
            email = f"loadtest-{uuid.uuid4().hex[:10]}@example.com"
            r = await c.post("/api/auth/register", json={"email": email, "password": PASSWORD})
            if r.status_code == 403:
                raise SystemExit("self sign-up is disabled - pass --accounts FILE or --token instead")
            r.raise_for_status()
            accounts.append(Account(email, PASSWORD, r.json()["access_token"]))
    return accounts


def scenarios(chart_lake: bool = False) -> List[Any]:
    """The per-loop extras; `chart_lake` adds the shared-lake chart with the broker chart's probability."""
    return EXTRA + [("chart_lake", dict(EXTRA)["chart"])] if chart_lake else list(EXTRA)


def ssl_context(cacert: Optional[str], verify_hostname: bool = True) -> Any:
    """One context for every client of this process (each client building its own from the CA bundle cost about 0.7 MB
    a client): the public CAs httpx uses, or `cacert`; `verify_hostname=False` keeps the chain check and drops the name
    check (Caddy's internal certificate for the server's IP, reached at its container address)."""
    import ssl
    if not cacert:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    ctx = ssl.create_default_context(cafile=cacert)
    if not verify_hostname:
        ctx.check_hostname = False
    return ctx


def _read_accounts(path: str) -> List[Account]:
    rows = [line.strip() for line in Path(path).read_text().splitlines() if line.strip() and not line.startswith("#")]
    return [Account(*row.split(",", 1)) for row in rows]


def login_budget(slo_factor: float = 1.0, bcrypt_ms: Optional[int] = None) -> Dict[str, Any]:
    """The login p95 budget. A login's own work is one bcrypt cost-12 check, so LD-9's LOGIN_P95_MS holds on the core it
    was set on (bcrypt_baseline.REFERENCE_MS, ~220 ms a check); on a slower core every login - and its queue - is slower
    in proportion. So: LOGIN_P95_MS x (this host's measured check / the reference), never below LOGIN_P95_MS (a faster
    core keeps the plain budget), and no double scaling with `slo_factor` (the larger of the two). The cost itself is
    never touched: production hashes at cost 12. Unknown check time: the plain budget."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import bcrypt_baseline
        reference = float(bcrypt_baseline.REFERENCE_MS)
    except Exception:  # noqa: BLE001 - no calibration: the plain budget
        bcrypt_ms = None
        reference = 0.0
    bcrypt_scale = max(1.0, bcrypt_ms / reference) if bcrypt_ms and reference else 1.0
    factor = max(slo_factor, bcrypt_scale)
    note = ("" if factor <= 1.0 else
            f"{LOGIN_P95_MS:,.0f} ms x {factor:.2f} (bcrypt-12 {bcrypt_ms} ms here / {reference:.0f} ms reference)"
            if bcrypt_scale >= slo_factor else f"{LOGIN_P95_MS:,.0f} ms x {factor:.2f}")
    return {"limit_ms": round(LOGIN_P95_MS * factor, 1), "factor": round(factor, 2), "bcrypt_ms": bcrypt_ms, "note": note}


def report(stats: Stats, users: int, elapsed: float, slo_factor: float = 1.0, bcrypt_ms: Optional[int] = None) -> Dict[str, Any]:
    """`slo_factor` scales the p95 limits (a slower CI machine), never the error limits. LD-9: the API verdict covers
    every scenario but the logins (p95 per scenario, server errors over the API requests); the login verdict is login's
    p95 against login_budget() and the failed attempts of every login (sign-in and probe) against LOGIN_ERROR_LIMIT
    (none). `login (sign-in)` (--sign-in) is reported with its latency, not judged on it."""
    total = sum(len(v) for v in stats.latency.values())
    api_total = sum(len(v) for k, v in stats.latency.items() if k not in LOGINS)
    errors = sum(o.get("server_error", 0) + o.get("transport", 0) for k, o in stats.outcomes.items() if k not in LOGINS)
    budget = login_budget(slo_factor, bcrypt_ms)
    out: Dict[str, Any] = {"users": users, "seconds": round(elapsed, 1), "requests": total, "rps": round(total / elapsed, 1) if elapsed else 0.0,
                           "error_rate": round(errors / api_total, 4) if api_total else 0.0, "slo_factor": round(slo_factor, 2), "scenarios": {},
                           "sessions": dict(stats.sessions)}
    api_pass = True
    login_pass = False            # LD-9 review: no login sample is no pass - "not measured" fails the login verdict
    sign_in_failed = 0
    for scenario in sorted(stats.latency):
        values = stats.latency[scenario]
        p95 = percentile(values, 95)
        outcomes = dict(stats.outcomes[scenario])
        limit: Optional[float]
        if scenario == LOGIN:
            limit = budget["limit_ms"]
            failed = sum(n for outcome, n in outcomes.items() if outcome != "ok")
            passed = len(values) > 0 and p95 <= limit and failed <= LOGIN_ERROR_LIMIT
            login_pass = passed
            out["login"] = {"n": len(values), "p95_ms": round(p95, 1), "p95_limit_ms": limit, "errors": failed,
                            "error_limit": LOGIN_ERROR_LIMIT, "pass": passed, "budget": budget["note"]}
        elif scenario == LOGIN_SIGN_IN:
            limit = None                                       # reported, not judged on latency
            sign_in_failed = sum(n for outcome, n in outcomes.items() if outcome != "ok")
            passed = sign_in_failed <= LOGIN_ERROR_LIMIT
            out["sign_in"] = {"n": len(values), "p95_ms": round(p95, 1), "errors": sign_in_failed, "pass": passed}
        else:
            limit = P95_LIMIT_MS.get(scenario, DEFAULT_P95_MS) * slo_factor
            measured = sum(outcomes.get(k, 0) for k in ANSWERED) > 0   # a scenario the deployment refuses as a whole is reported, not judged
            passed = (not measured) or p95 <= limit
            api_pass = api_pass and passed
        out["scenarios"][scenario] = {"n": len(values), "p50_ms": round(percentile(values, 50), 1), "p95_ms": round(p95, 1),
                                      "p99_ms": round(percentile(values, 99), 1), "mean_ms": round(statistics.fmean(values), 1) if values else 0.0,
                                      "p95_limit_ms": limit, "pass": passed, "outcomes": outcomes}
    out["api_pass"] = api_pass and out["error_rate"] <= ERROR_LIMIT and api_total > 0
    out["login_pass"] = login_pass and sign_in_failed <= LOGIN_ERROR_LIMIT    # every session's sign-in must succeed too
    out["slo_pass"] = out["api_pass"] and out["login_pass"]
    if stats.after_ramp:
        # Diagnostic only (LD-7): the same scenarios over the requests started after the login ramp.
        out["after_ramp"] = {name: {"n": len(values), "p50_ms": round(percentile(values, 50), 1), "p95_ms": round(percentile(values, 95), 1)}
                             for name, values in sorted(stats.after_ramp.items())}
    return out


def _verdicts(r: Dict[str, Any]) -> str:
    """'API PASS, login FAIL (p95 2,410 ms of 2,000; 3 failed)' - the two LD-9 verdicts in one line."""
    login, sign_in = r.get("login"), r.get("sign_in")
    text = f"API {'PASS' if r.get('api_pass', r['slo_pass']) else 'FAIL'}"
    signed = (f"; sign-in: {sign_in['errors']} failed of {sign_in['n']}, p95 {sign_in['p95_ms']:,.0f} ms not judged"
              if sign_in else "")
    if login is None:
        return text + f", login FAIL (not measured: no login was made{signed})"
    budget = f" = {login['budget']}" if login.get("budget") else ""
    return (text + f", login {'PASS' if r.get('login_pass', login['pass']) else 'FAIL'} (p95 {login['p95_ms']:,.0f} ms of "
            f"{login['p95_limit_ms']:,.0f}{budget}; {login['errors']} failed of {login['n']}, {login['error_limit']} allowed{signed})")


def _limit(s: Dict[str, Any]) -> str:
    """A scenario's p95 limit for a table, "-" for one not judged on latency (login (sign-in))."""
    return "-" if s.get("p95_limit_ms") is None else f"{s['p95_limit_ms']:.0f}"


def print_report(r: Dict[str, Any], annotate: bool = False) -> None:
    print(f"{r['users']} users x {r['seconds']}s: {r['requests']} requests, {r['rps']} rps, server errors {r['error_rate']:.2%}, "
          f"p95 limits x {r.get('slo_factor', 1.0)}")
    print(f"{'scenario':38} {'n':>6} {'p50':>8} {'p95':>8} {'p99':>8} {'limit':>6}  outcomes")
    for name, s in r["scenarios"].items():
        print(f"{name:38} {s['n']:>6} {s['p50_ms']:>8} {s['p95_ms']:>8} {s['p99_ms']:>8} {_limit(s):>6}  "
              f"{', '.join(f'{k} {v}' for k, v in sorted(s['outcomes'].items()))}{'' if s['pass'] else '  <- over the p95 limit'}")
    print(f"SLO (API: p95 per scenario, server errors <= {ERROR_LIMIT:.1%}; login: p95 <= {LOGIN_P95_MS:,.0f} ms x factor, no failed login):",
          "PASS" if r["slo_pass"] else "FAIL", f"- {_verdicts(r)}")
    if annotate:
        lines = [f"{name} n {s['n']} p50 {s['p50_ms']} p95 {s['p95_ms']} ms ({'ok' if s['pass'] else 'OVER'} {_limit(s)})"
                 for name, s in r["scenarios"].items()]
        print(f"::notice title=loadtest::{r['users']} users, {r['requests']} requests, {r['rps']} rps, server errors {r['error_rate']:.2%}, "
              f"p95 limits x {r.get('slo_factor', 1.0)}, "
              f"SLO {'PASS' if r['slo_pass'] else 'FAIL'} ({_verdicts(r)}); " + "; ".join(lines))


def start_offsets(users: int, ramp: float, shard: int = 0, shards: int = 1) -> List[float]:
    """When each of this process's users starts, in seconds after the ramp begins. One process: evenly over the ramp.
    Sharded (LD-7): shard k of n takes its users' slots shifted by k/n of a slot, so the shards together start a user
    every ramp / (users x n) seconds - the logins arrive evenly over the ramp, as the profile says, and never as a burst
    of one login per shard at the same instant (25 shards started in step sent 25 logins at once every 7.5 s)."""
    shards = max(1, shards)
    shard = min(max(0, shard), shards - 1)
    return [ramp * (i + shard / shards) / max(1, users) for i in range(users)]


async def run(args: argparse.Namespace) -> Dict[str, Any]:
    transport: Optional[httpx.AsyncBaseTransport] = None
    base = args.base
    harness: Any = None
    if args.in_process:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import loadtest_app as harness
        transport, base = httpx.ASGITransport(app=await harness.build()), "http://testserver"
    else:
        check_host(base)

    verify: Any = ssl_context(getattr(args, "cacert", None), not getattr(args, "no_verify_hostname", False))
    host_header = getattr(args, "host_header", None)
    headers = {"Host": host_header} if host_header else None

    def make_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base, timeout=args.timeout, transport=transport, verify=verify, headers=headers)

    if args.token:
        accounts = [Account(token=args.token)]
    elif args.accounts:
        accounts = _read_accounts(args.accounts)[getattr(args, "account_offset", 0):]
        if not accounts:
            raise SystemExit(f"{args.accounts}: no account at offset {args.account_offset}")
    else:
        accounts = await _register(make_client, max(1, min(args.register, args.users)))
    if harness is not None:
        await harness.prepare([a.email for a in accounts if a.email])
    stats = Stats()
    started = time.monotonic()
    start_at = getattr(args, "start_at", None)
    if start_at is not None:
        # LD-7: every shard's ramp starts at the same wall-clock instant (the containers share the host clock); a shard
        # whose container came up late keeps the shared schedule rather than shifting its own.
        started += start_at - time.time()
        if started < time.monotonic() - 1.0:
            print(f"loadtest: started {time.monotonic() - started:.1f} s after --start-at", file=sys.stderr)
    shard, shards = getattr(args, "shard", 0), getattr(args, "shards", 1)
    sign_in = max(0.0, float(getattr(args, "sign_in", 0.0) or 0.0))
    # --sign-in: the logins take [started, started + sign_in), spread as the ramp's would be; the ramp (the users
    # opening the app) and the steady window follow it. Without it, sign_in is 0 and the ramp carries the logins.
    opened = started + sign_in
    stats.ramp_end = opened + args.ramp
    deadline = opened + args.ramp + args.seconds
    offsets = start_offsets(args.users, sign_in or args.ramp, shard, shards)
    opens = [opened + t for t in start_offsets(args.users, args.ramp, shard, shards)] if sign_in else [None] * args.users
    extra = scenarios(getattr(args, "chart_lake", False))
    probes = [stats.ramp_end + t for t in probe_offsets(args.seconds, float(getattr(args, "login_probe_every", 0.0) or 0.0), shard, shards)]
    users_accounts = accounts[:max(1, min(len(accounts), args.users))]
    await asyncio.gather(_login_probe(make_client, users_accounts, stats, probes),
                         *(_session(i, make_client, accounts[i % len(accounts)], i < len(accounts), stats,
                                    max(0.0, started + offsets[i] - time.monotonic()), deadline, args.think, args.seed, extra, opens[i])
                           for i in range(args.users)))
    elapsed = time.monotonic() - opened                  # from the ramp: the requests/s stay comparable with one-phase runs
    if getattr(args, "raw_out", None):
        Path(args.raw_out).write_text(json.dumps({"users": args.users, "seconds": round(elapsed, 2), "base": base, **stats.raw()}))
    return report(stats, args.users, elapsed, slo_factor=_slo_factor(args.slo_factor))


def merge(paths: List[str], slo_factor: float = 1.0, bcrypt_ms: Optional[int] = None) -> Dict[str, Any]:
    """One verdict over several shards' raw samples (`--raw-out`): every sample counts, the users add up, the duration
    is the longest shard's. `bcrypt_ms`: this host's bcrypt-12 check (--bcrypt-ms), for the login budget."""
    stats = Stats()
    users, elapsed = 0, 0.0
    for path in paths:
        raw = json.loads(Path(path).read_text())
        stats.absorb(raw)
        users += int(raw.get("users") or 0)
        elapsed = max(elapsed, float(raw.get("seconds") or 0.0))
    out = report(stats, users, elapsed, slo_factor=slo_factor, bcrypt_ms=bcrypt_ms)
    out["shards"] = len(paths)
    return out


def markdown(r: Dict[str, Any], label: str = "", stats_csv: Optional[str] = None) -> str:
    """The result as the Markdown block docs/reports/load_test_500.md carries."""
    sessions = r.get("sessions") or {}
    baseline = r.get("host_baseline")
    if baseline:                                          # informational: the login p95 follows the core's bcrypt speed
        try:                                              # never fails the report
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import bcrypt_baseline
            label = f"{label or 'this host'} ({bcrypt_baseline.note(baseline.get('cpu_model'), baseline.get('vcpus'), baseline.get('bcrypt12_ms'))})"
        except Exception:  # noqa: BLE001 - informational only
            pass
    lines = [f"**{r['users']} users** ({sessions.get('logged_in', 0)} logged in, {sessions.get('no_login', 0)} without a login)"
             + (f", {r['shards']} shards (client IPs)" if r.get("shards") else "") + f" on {label or 'this host'}: "
             f"{r['requests']:,} requests in {r['seconds']:.0f} s ({r['rps']} requests/s), server errors {r['error_rate']:.2%}. "
             f"**SLO {'PASS' if r['slo_pass'] else 'FAIL'}** ({_verdicts(r)}; p95 limits x {r.get('slo_factor', 1.0)}, "
             f"API server errors <= {ERROR_LIMIT:.1%}, login judged apart: p95 <= {LOGIN_P95_MS:,.0f} ms"
             + (" x the bcrypt factor" if (r.get("login") or {}).get("budget") else "") + ", no failed login"
             + ("; sessions signed in before the ramp, `login` = the steady window's probe" if r.get("sign_in") else "") + ").",
             "", "| Route / scenario | n | p50 ms | p95 ms | p99 ms | p95 limit | verdict | outcomes |", "|---|---|---|---|---|---|---|---|"]
    for name, s in r["scenarios"].items():
        verdict = "pass" if s["pass"] else "**over**"
        if not any(s["outcomes"].get(k) for k in ANSWERED):
            verdict = "not measured"
        outcomes = ", ".join(f"{k} {v}" for k, v in sorted(s["outcomes"].items()))
        lines.append(f"| {name} | {s['n']} | {s['p50_ms']} | {s['p95_ms']} | {s['p99_ms']} | {_limit(s)} | {verdict} | {outcomes} |")
    if r.get("after_ramp"):
        lines += ["", "After the login ramp only (diagnostic - the verdict above covers every request):", "",
                  "| Route / scenario | n | p50 ms | p95 ms |", "|---|---|---|---|"]
        lines += [f"| {name} | {s['n']} | {s['p50_ms']} | {s['p95_ms']} |" for name, s in r["after_ramp"].items()]
    if stats_csv and Path(stats_csv).exists():
        peaks: Dict[str, List[float]] = {}
        for row in Path(stats_csv).read_text().splitlines()[1:]:
            parts = row.split(",")
            if len(parts) >= 3 and parts[2].endswith("%"):
                try:
                    peaks.setdefault(parts[1], []).append(float(parts[2].rstrip("%")))
                except ValueError:
                    continue
        if peaks:
            lines += ["", "| Container | CPU % mean | CPU % peak |", "|---|---|---|"]
            for name, values in sorted(peaks.items()):
                lines.append(f"| {name} | {statistics.fmean(values):.0f} | {max(values):.0f} |")
    return "\n".join(lines) + "\n"


def _slo_factor(value: str) -> float:
    """A number, or "auto": the CI factor of scripts/bench_screener.py (this machine's time for a fixed workload over the
    reference runner's, never below 1)."""
    if value != "auto":
        return max(1.0, float(value))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench_screener
    return float(bench_screener.ci_factor())


def _bcrypt_ms(value: Optional[str]) -> Optional[int]:
    """--bcrypt-ms as whole ms, or None (not measured: "unknown", empty, anything else)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import bcrypt_baseline
        return bcrypt_baseline.as_ms(value)
    except Exception:  # noqa: BLE001 - no calibration: the plain budget
        return None


def pin_cpus(spec: str) -> set:
    """"2,3" or "2-3" -> {2, 3}."""
    cpus: set = set()
    for part in spec.split(","):
        low, _, high = part.strip().partition("-")
        cpus.update(range(int(low), int(high or low) + 1))
    return cpus


def main() -> int:
    parser = argparse.ArgumentParser(description="Session-shaped API load test (see the module docstring).")
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--in-process", action="store_true", help="the test app in this process (CI); --base is ignored")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=30, help="duration after the ramp")
    parser.add_argument("--ramp", type=float, default=None, help="seconds over which users start (default: a tenth of --seconds)")
    parser.add_argument("--think", type=float, default=1.0, help="mean pause between loops of one user, seconds")
    parser.add_argument("--accounts", default=None, help="file of email,password lines")
    parser.add_argument("--token", default=None, help="one existing access token for every user (no login step)")
    parser.add_argument("--register", type=int, default=5, help="throwaway accounts to register when neither --accounts nor --token")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=54)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--annotate", action="store_true", help="also print a GitHub Actions notice with the summary")
    parser.add_argument("--slo-factor", default="1", help='scale the p95 limits (>= 1), or "auto" for the CI factor (CI only)')
    parser.add_argument("--cacert", default=None, help="CA file that verifies an HTTPS --base (the CI stack's Caddy)")
    parser.add_argument("--no-verify-hostname", action="store_true", help="with --cacert: check the chain, not the name "
                                                                          "(Caddy's certificate for the server's IP, reached at its container address)")
    parser.add_argument("--host-header", default=None, help="send this Host header (the site Caddy serves) instead of --base's host")
    parser.add_argument("--chart-lake", action="store_true", help="add the chart_lake scenario (daily bars from the shared lake, no broker)")
    parser.add_argument("--account-offset", type=int, default=0, help="start at this line of --accounts (a shard's slice)")
    parser.add_argument("--raw-out", default=None, help="also write the raw samples here, for --merge")
    parser.add_argument("--shard", type=int, default=0, help="this process's index among --shards (interleaves the start times)")
    parser.add_argument("--shards", type=int, default=1, help="how many processes share the ramp")
    parser.add_argument("--start-at", type=float, default=None, help="Unix time at which the run starts (the same for every shard)")
    parser.add_argument("--sign-in", type=float, default=0.0, help="seconds of a sign-in phase before the ramp: every user logs in "
                                                                   "there (spread as the ramp's logins would be), then opens the app in the ramp")
    parser.add_argument("--login-probe-every", type=float, default=0.0, help="one password login every this many seconds in the "
                                                                             "steady window, all --shards together (the judged `login`)")
    parser.add_argument("--merge", nargs="+", default=None, help="judge these --raw-out files together instead of running")
    parser.add_argument("--json-out", default=None, help="write the report as JSON here")
    parser.add_argument("--markdown", default=None, help="write the report as a Markdown table here")
    parser.add_argument("--label", default="", help="where it ran (for --markdown)")
    parser.add_argument("--stats", default=None, help="docker stats CSV to summarise in --markdown")
    parser.add_argument("--cpu-model", default=None, help="the host's CPU model, for the report (informational)")
    parser.add_argument("--vcpus", default=None, help="the host's vCPU count, for the report (informational)")
    parser.add_argument("--bcrypt-ms", default=None, help="one bcrypt-12 check in ms (scripts/bcrypt_baseline.py), for the report and, "
                                                          "with --merge, the login budget (login_budget: scaled up on a slower core only)")
    parser.add_argument("--pin-cpus", default=None, help="run on these CPUs only, e.g. 2,3 (Linux; the CI's KVM 2 shape keeps the "
                                                          "load clients off the server's CPUs)")
    args = parser.parse_args()
    if args.pin_cpus:
        os.sched_setaffinity(0, pin_cpus(args.pin_cpus))
    if args.ramp is None:
        args.ramp = args.seconds / 10
    if args.merge:
        result = merge(args.merge, slo_factor=_slo_factor(args.slo_factor), bcrypt_ms=_bcrypt_ms(args.bcrypt_ms))
    else:
        result = asyncio.run(run(args))
    if args.cpu_model is not None or args.vcpus is not None or args.bcrypt_ms is not None:
        try:                                              # informational only: never changes the run's result or rc
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import bcrypt_baseline
            result["host_baseline"] = {"cpu_model": args.cpu_model or "unknown", "vcpus": args.vcpus,
                                       "bcrypt12_ms": bcrypt_baseline.as_ms(args.bcrypt_ms)}
        except Exception:  # noqa: BLE001
            pass
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2))
    if args.markdown:
        Path(args.markdown).write_text(markdown(result, args.label, args.stats))
    if args.json:
        print(json.dumps(result, indent=2))
    if not args.json or args.annotate:
        print_report(result, annotate=args.annotate)
    return 0 if result["slo_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
