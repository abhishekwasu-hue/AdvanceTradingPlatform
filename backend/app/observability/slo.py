"""AUD-T8: the service level objectives the public status page publishes, mirrored from docs/SLO.md.

`objective` and `target` are the doc's own table cells, word for word (tests/test_aud_t8_status_page.py reads the doc and
fails when they drift: change both together). `public` is the plain sentence the status page shows - no metric names, no
settings, no internals. Only the objectives a visitor can hold the platform to are published; the LIVE broker objectives
(SLO-5 to SLO-7) wait for LIVE trading. They are targets the platform is built to meet, not measured results.
"""
from __future__ import annotations

from typing import List, NamedTuple


class Slo(NamedTuple):
    id: str
    objective: str
    target: str
    public: str


SLOS: List[Slo] = [
    Slo("SLO-1", "API availability and latency",
        "99.5% of requests non-5xx over 30 days; p95 latency under 500 ms for reads",
        "Website and API: 99.5% of requests answered without a server error over 30 days; most reads answered within half a second."),
    Slo("SLO-2", "Engine liveness on trading days",
        "Heartbeat never older than 3 cycles (180 s) while the market is open, 99.9% of market minutes",
        "Trading engine: checks in at least every 3 minutes while a market is open, in 99.9% of market minutes."),
    Slo("SLO-3", "Cycle time",
        "99% of cycles complete within one base candle (60 s)",
        "Engine cycles: 99% finish within one minute."),
    Slo("SLO-4", "Market-data freshness",
        "Fewer than 5 decisions per 10 minutes refused for stale data; zero signals evaluated on candles more than "
        "`MARKET_DATA_MAX_STALE_BARS` behind (enforced, not just measured)",
        "Market data: signals are evaluated on fresh data only; fewer than 5 checks in any 10 minutes wait for late data."),
    Slo("SLO-8", "Alert delivery",
        "Out-of-app alerts (Telegram/email) leave within 60 s; outbox never above 20 for 10 minutes",
        "Alerts: Telegram and email alerts leave within 60 seconds."),
    Slo("SLO-9", "Recovery",
        "RTO 30 min / RPO 5 min for the database (WAL archiving + weekly base backup, docs/OPERATIONS.md 1.2a); nightly "
        "logical backup verified by restore",
        "Recovery: service back within 30 minutes with at most 5 minutes of data lost; backups are checked by a test restore every night."),
]


def public_slos() -> List[dict]:
    """The status page's list: id, the objective's name and the plain target."""
    return [{"id": s.id, "objective": s.objective, "target": s.public} for s in SLOS]
