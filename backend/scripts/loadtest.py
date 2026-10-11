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

Prints p50/p95/p99 per scenario and the error rate, and exits 1 when an SLO is missed: p95 over 500 ms (login 1,000 ms:
password hashing is slow on purpose; chart 1,000 ms; screener run 2,000 ms) or server errors above 0.5% of requests.
Responses that are not failures of the API are counted apart, never as errors: a feature the operator has off (503 with
X-Feature-Disabled), no broker session (409) and rate limiting (429).

Safety: read paths plus login and a preview screen - it places, changes and approves nothing, and it never sends an
order. `--base` must be localhost or a staging host (its name contains "staging", or it is listed in
LOADTEST_ALLOWED_HOSTS): the script refuses anything else, so it is never pointed at the production VPS by mistake.
Accounts: `--accounts FILE` (one `email,password` per line; user i uses account i mod n, and the first user of each
account logs in for it - so keep the file to about 8 accounts: the login limit is 10 a minute per IP), `--token` (one
shared access token, no login step), or neither: it registers `--register` throwaway accounts (needs self sign-up on).
Access tokens live 15 minutes: keep `--ramp` + `--seconds` under 14 minutes, or late requests answer 401.
`--in-process` runs the test app (scripts/loadtest_app.py: in-memory SQLite, synthetic broker bars, the test suite's
rate-limit overrides) with no server and no network. Results go into docs/PERFORMANCE.md.
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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx

DASHBOARD = ["/api/auth/me", "/api/deployments", "/api/notifications", "/api/system/status", "/api/portfolio/exposure"]
# (scenario, probability per loop) - the dashboard poll runs every loop
EXTRA = [("proposals", 0.5), ("positions", 0.5), ("chart", 0.25), ("screener_run", 0.1)]
POSITIONS_PAGE = ["positions", "paper_account"]
P95_LIMIT_MS: Dict[str, float] = {"login": 1000.0, "chart": 1000.0, "screener_run": 2000.0}
DEFAULT_P95_MS = 500.0
ERROR_LIMIT = 0.005
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
    host = (urlparse(base).hostname or "").lower()
    allowed = {h.strip().lower() for h in os.environ.get("LOADTEST_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host in SAFE_HOSTS or "staging" in host or host in allowed:
        return
    raise SystemExit(f"loadtest refuses {host!r}: only localhost or a staging host. It must never run against the production VPS; "
                     "for a staging host without 'staging' in its name set LOADTEST_ALLOWED_HOSTS=<host>.")


@dataclass
class Stats:
    latency: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    outcomes: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def add(self, scenario: str, ms: float, outcome: str) -> None:
        self.latency[scenario].append(ms)
        self.outcomes[scenario][outcome] += 1


@dataclass
class Account:
    email: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)   # set once the account's first user has logged in


async def _timed(stats: Stats, scenario: str, call: Callable[[], Any]) -> Optional[httpx.Response]:
    started = time.perf_counter()
    try:
        response = await call()
    except httpx.HTTPError:
        stats.add(scenario, (time.perf_counter() - started) * 1000, "transport")
        return None
    stats.add(scenario, (time.perf_counter() - started) * 1000, classify(response.status_code, response.headers))
    return response


async def _login(client: httpx.AsyncClient, account: Account, stats: Stats) -> Optional[str]:
    if account.email is None or account.password is None:
        return account.token
    r = await _timed(stats, "login", lambda: client.post("/api/auth/login", json={"email": account.email, "password": account.password}))
    if r is not None and r.status_code == 200:
        return r.json().get("access_token") or account.token
    return account.token                                       # throttled or refused: the setup token keeps the session going


async def _session(i: int, make_client: Callable[[], httpx.AsyncClient], account: Account, leader: bool, stats: Stats, start_delay: float,
                   deadline: float, think: float, seed: int) -> None:
    """One user. The first user of each account logs in; the others open more sessions on that login (the per-IP login
    limit - 10 a minute - would refuse 500 logins from one load generator, and logins are rare next to polling)."""
    rng = random.Random(seed * 1_000 + i)
    await asyncio.sleep(start_delay)
    async with make_client() as client:
        if leader:
            account.token = await _login(client, account, stats)
            account.ready.set()
        else:
            await account.ready.wait()
        token = account.token
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
        }
        while time.monotonic() < deadline:
            await asyncio.gather(*(_timed(stats, f"dashboard {route}", lambda route=route: client.get(route)) for route in DASHBOARD))
            for scenario, probability in EXTRA:
                if rng.random() < probability and time.monotonic() < deadline:
                    together = POSITIONS_PAGE if scenario == "positions" else [scenario]       # the Positions page loads both
                    await asyncio.gather(*(_timed(stats, name, actions[name]) for name in together))
            if think:
                await asyncio.sleep(think * rng.uniform(0.5, 1.5))


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


def _read_accounts(path: str) -> List[Account]:
    rows = [line.strip() for line in Path(path).read_text().splitlines() if line.strip() and not line.startswith("#")]
    return [Account(*row.split(",", 1)) for row in rows]


def report(stats: Stats, users: int, elapsed: float) -> Dict[str, Any]:
    total = sum(len(v) for v in stats.latency.values())
    errors = sum(o.get("server_error", 0) + o.get("transport", 0) for o in stats.outcomes.values())
    out: Dict[str, Any] = {"users": users, "seconds": round(elapsed, 1), "requests": total, "rps": round(total / elapsed, 1) if elapsed else 0.0,
                           "error_rate": round(errors / total, 4) if total else 0.0, "scenarios": {}}
    slo = True
    for scenario in sorted(stats.latency):
        values = stats.latency[scenario]
        p95 = percentile(values, 95)
        limit = P95_LIMIT_MS.get(scenario, DEFAULT_P95_MS)
        outcomes = dict(stats.outcomes[scenario])
        measured = outcomes.get("ok", 0) > 0                   # a scenario the deployment refuses as a whole is reported, not judged
        passed = (not measured) or p95 <= limit
        slo = slo and passed
        out["scenarios"][scenario] = {"n": len(values), "p50_ms": round(percentile(values, 50), 1), "p95_ms": round(p95, 1),
                                      "p99_ms": round(percentile(values, 99), 1), "mean_ms": round(statistics.fmean(values), 1) if values else 0.0,
                                      "p95_limit_ms": limit, "pass": passed, "outcomes": outcomes}
    out["slo_pass"] = slo and out["error_rate"] <= ERROR_LIMIT and total > 0
    return out


def print_report(r: Dict[str, Any], annotate: bool = False) -> None:
    print(f"{r['users']} users x {r['seconds']}s: {r['requests']} requests, {r['rps']} rps, server errors {r['error_rate']:.2%}")
    print(f"{'scenario':38} {'n':>6} {'p50':>8} {'p95':>8} {'p99':>8} {'limit':>6}  outcomes")
    for name, s in r["scenarios"].items():
        print(f"{name:38} {s['n']:>6} {s['p50_ms']:>8} {s['p95_ms']:>8} {s['p99_ms']:>8} {s['p95_limit_ms']:>6.0f}  "
              f"{', '.join(f'{k} {v}' for k, v in sorted(s['outcomes'].items()))}{'' if s['pass'] else '  <- over the p95 limit'}")
    print(f"SLO (p95 per scenario, server errors <= {ERROR_LIMIT:.1%}):", "PASS" if r["slo_pass"] else "FAIL")
    if annotate:
        lines = [f"{name} n {s['n']} p50 {s['p50_ms']} p95 {s['p95_ms']} ms ({'ok' if s['pass'] else 'OVER'} {s['p95_limit_ms']:.0f})"
                 for name, s in r["scenarios"].items()]
        print(f"::notice title=loadtest::{r['users']} users, {r['requests']} requests, {r['rps']} rps, server errors {r['error_rate']:.2%}, "
              f"SLO {'PASS' if r['slo_pass'] else 'FAIL'}; " + "; ".join(lines))


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

    def make_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base, timeout=args.timeout, transport=transport)

    if args.token:
        accounts = [Account(token=args.token)]
    elif args.accounts:
        accounts = _read_accounts(args.accounts)
    else:
        accounts = await _register(make_client, max(1, min(args.register, args.users)))
    if harness is not None:
        await harness.prepare([a.email for a in accounts if a.email])
    stats = Stats()
    started = time.monotonic()
    deadline = started + args.ramp + args.seconds
    await asyncio.gather(*(_session(i, make_client, accounts[i % len(accounts)], i < len(accounts), stats, args.ramp * i / max(1, args.users), deadline,
                                    args.think, args.seed) for i in range(args.users)))
    return report(stats, args.users, time.monotonic() - started)


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
    args = parser.parse_args()
    if args.ramp is None:
        args.ramp = args.seconds / 10
    result = asyncio.run(run(args))
    if args.json:
        print(json.dumps(result, indent=2))
    if not args.json or args.annotate:
        print_report(result, annotate=args.annotate)
    return 0 if result["slo_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
