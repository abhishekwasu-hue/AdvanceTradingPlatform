#!/usr/bin/env python
"""Phase O4 / section 54: API load and latency probe with SLO thresholds.

    python scripts/loadtest.py --base http://localhost:8000 --users 20 --seconds 30 [--json]

Registers one throwaway account, then hammers the read paths a dashboard hits every few seconds
(`/api/auth/me`, `/api/deployments`, `/api/trades`, `/api/notifications`, `/api/system/status`,
`/api/portfolio/exposure`) with `--users` concurrent clients for `--seconds`. Prints per-route
p50/p95/p99 and the error rate, and exits 1 when SLO-1 is missed (p95 over 500 ms or 5xx above
0.5%). Read-only against the API: it places no orders. Results go into docs/PERFORMANCE.md.
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from collections import defaultdict
from typing import Dict, List

import httpx

ROUTES = ["/api/auth/me", "/api/deployments", "/api/trades", "/api/notifications", "/api/system/status", "/api/portfolio/exposure"]
P95_LIMIT_MS = 500.0
ERROR_LIMIT = 0.005


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


async def _register(base: str) -> str:
    async with httpx.AsyncClient(base_url=base, timeout=10) as c:
        email = f"loadtest-{uuid.uuid4().hex[:10]}@example.com"
        r = await c.post("/api/auth/register", json={"email": email, "password": "L0adTest!Pass"})
        if r.status_code == 403:
            raise SystemExit("self sign-up is disabled - set LOADTEST_TOKEN to an existing access token instead")
        r.raise_for_status()
        return r.json()["access_token"]


async def _user(base: str, token: str, seconds: float, lat: Dict[str, List[float]], errors: Dict[str, int]) -> None:
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=10) as c:
        deadline = time.monotonic() + seconds
        i = 0
        while time.monotonic() < deadline:
            route = ROUTES[i % len(ROUTES)]
            i += 1
            started = time.perf_counter()
            try:
                r = await c.get(route)
                lat[route].append((time.perf_counter() - started) * 1000)
                if r.status_code >= 500:
                    errors[route] += 1
            except httpx.HTTPError:
                lat[route].append((time.perf_counter() - started) * 1000)
                errors[route] += 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--token", default=None, help="existing access token (skips registration)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    token = args.token or await _register(args.base)
    lat: Dict[str, List[float]] = defaultdict(list)
    errors: Dict[str, int] = defaultdict(int)
    started = time.monotonic()
    await asyncio.gather(*(_user(args.base, token, args.seconds, lat, errors) for _ in range(args.users)))
    elapsed = time.monotonic() - started
    total = sum(len(v) for v in lat.values())
    total_errors = sum(errors.values())
    report = {"users": args.users, "seconds": round(elapsed, 1), "requests": total, "rps": round(total / elapsed, 1) if elapsed else 0,
              "error_rate": round(total_errors / total, 4) if total else 0.0, "routes": {}}
    worst_p95 = 0.0
    for route in ROUTES:
        values = lat[route]
        p95 = percentile(values, 95)
        worst_p95 = max(worst_p95, p95)
        report["routes"][route] = {"n": len(values), "p50_ms": round(percentile(values, 50), 1), "p95_ms": round(p95, 1),
                                   "p99_ms": round(percentile(values, 99), 1), "mean_ms": round(statistics.fmean(values), 1) if values else 0.0,
                                   "errors": errors[route]}
    report["slo_1_pass"] = worst_p95 <= P95_LIMIT_MS and report["error_rate"] <= ERROR_LIMIT
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['users']} users x {report['seconds']}s: {report['requests']} requests, {report['rps']} rps, error rate {report['error_rate']:.2%}")
        print(f"{'route':32} {'n':>6} {'p50':>8} {'p95':>8} {'p99':>8} {'err':>5}")
        for route, r in report["routes"].items():
            print(f"{route:32} {r['n']:>6} {r['p50_ms']:>8} {r['p95_ms']:>8} {r['p99_ms']:>8} {r['errors']:>5}")
        print("SLO-1 (p95 <= 500 ms, 5xx <= 0.5%):", "PASS" if report["slo_1_pass"] else "FAIL")
    return 0 if report["slo_1_pass"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
