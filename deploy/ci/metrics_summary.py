"""LD-7 diagnostics: what the API itself measured during one load run - two scrapes of its /metrics (before and after,
Prometheus text, every API process merged) turned into:

* the server-side time per route (count, total seconds, mean, share of all request time) - where the request time
  goes, as the API saw it (Caddy and the network are outside it);
* login apart from the other routes (LD-9: judged on its own p95, 2 s, not in the API's 0.5 s): its server-side
  count, mean and approximate p95;
* the waits: for a pooled database connection (`atp_db_pool_wait_seconds`) and for a bcrypt thread
  (`atp_bcrypt_queue_wait_seconds`), with the bcrypt checks' own run time (`atp_bcrypt_seconds`).

Stdlib only; prints Markdown to stdout.

    python3 deploy/ci/metrics_summary.py BEFORE.prom AFTER.prom
"""
import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})?\s+([-+0-9.eEInfa]+)$')
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')

Key = Tuple[str, Tuple[Tuple[str, str], ...]]


def parse(text: str) -> Dict[Key, float]:
    out: Dict[Key, float] = defaultdict(float)
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = SAMPLE.match(line.strip())
        if not m:
            continue
        labels = tuple(sorted(LABEL.findall(m.group(3) or "")))
        try:
            out[(m.group(1), labels)] += float(m.group(4))
        except ValueError:
            continue
    return out


def delta(before: Dict[Key, float], after: Dict[Key, float]) -> Dict[Key, float]:
    # A restarted API (the Hostinger-shaped run) starts its counters again: a value below its "before" is all new.
    return {k: (v - before.get(k, 0.0) if v >= before.get(k, 0.0) else v) for k, v in after.items()}


def _quantile(buckets: List[Tuple[float, float]], q: float) -> float:
    """Upper bound of the bucket holding the q-quantile (cumulative bucket counts)."""
    if not buckets or buckets[-1][1] <= 0:
        return 0.0
    target = q * buckets[-1][1]
    for bound, count in buckets:
        if count >= target:
            return bound
    return buckets[-1][0]


LOGIN_ROUTE = "/api/auth/login"


def histogram(d: Dict[Key, float], name: str, route: str = "") -> Tuple[float, float, List[Tuple[float, float]]]:
    """(count, sum, cumulative buckets) of one histogram, every label set merged - or one route's only."""
    def wanted(labels) -> bool:
        return not route or dict(labels).get("route") == route
    count = sum(v for (n, labels), v in d.items() if n == f"{name}_count" and wanted(labels))
    total = sum(v for (n, labels), v in d.items() if n == f"{name}_sum" and wanted(labels))
    per_bound: Dict[float, float] = defaultdict(float)
    for (n, labels), v in d.items():
        if n == f"{name}_bucket" and wanted(labels):
            le = dict(labels).get("le", "+Inf")
            per_bound[float("inf") if le == "+Inf" else float(le)] += v
    return count, total, sorted(per_bound.items())


def main(before_path: str, after_path: str) -> None:
    with open(before_path, encoding="utf-8") as f:
        before = parse(f.read())
    with open(after_path, encoding="utf-8") as f:
        after = parse(f.read())
    d = delta(before, after)

    routes: Dict[str, List[float]] = defaultdict(lambda: [0.0, 0.0])
    for (name, labels), v in d.items():
        if name in ("atp_http_request_duration_seconds_count", "atp_http_request_duration_seconds_sum"):
            lab = dict(labels)
            key = f"{lab.get('method', '?')} {lab.get('route', '?')}"
            routes[key][0 if name.endswith("_count") else 1] += v
    grand = sum(t for _, t in routes.values()) or 1.0
    print("Server-side request time by route (the API's own histogram, all processes):\n")
    print("| Route | n | total s | mean ms | share of request time |")
    print("|---|---|---|---|---|")
    for key, (n, t) in sorted(routes.items(), key=lambda kv: -kv[1][1])[:15]:
        if n:
            print(f"| {key} | {int(n)} | {t:.1f} | {1000 * t / n:.1f} | {100 * t / grand:.1f}% |")

    print("\nLogin, measured apart (LD-9: p95 at or under 2 s and no failed login, not in the API's 0.5 s):\n")
    print("| Route | n | mean ms | ~p95 ms (bucket bound) |")
    print("|---|---|---|---|")
    n, t, buckets = histogram(d, "atp_http_request_duration_seconds", LOGIN_ROUTE)
    if n:
        print(f"| POST {LOGIN_ROUTE} | {int(n)} | {1000 * t / n:.1f} | {1000 * _quantile(buckets, 0.95):g} |")
    else:
        print(f"| POST {LOGIN_ROUTE} | 0 | - | - |")

    print("\nWaits:\n")
    print("| What | n | total s | mean ms | ~p95 ms (bucket bound) | ~p99 ms |")
    print("|---|---|---|---|---|---|")
    for label, name in (("pool connection checkout", "atp_db_pool_wait_seconds"),
                        ("bcrypt: waiting for a thread", "atp_bcrypt_queue_wait_seconds"),
                        ("bcrypt: the check itself", "atp_bcrypt_seconds")):
        n, t, buckets = histogram(d, name)
        if n:
            print(f"| {label} | {int(n)} | {t:.1f} | {1000 * t / n:.1f} | {1000 * _quantile(buckets, 0.95):g} | "
                  f"{1000 * _quantile(buckets, 0.99):g} |")
        else:
            print(f"| {label} | 0 | - | - | - | - |")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
