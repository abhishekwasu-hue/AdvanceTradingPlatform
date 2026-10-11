"""Part D2 (rule IN-SEBI.ops.throttle): orders per second per client, exchange and segment - exits first.

SEBI's retail-algo framework counts *orders* (place, modify, cancel, stop) per second per client, separately from a
broker's API rate limit (`app/brokers/rate_budget.py`, which stays and counts every call). One `OpsThrottle` per
(tenant, broker account); inside it one bucket per exchange (in India the exchange code already names the segment:
NSE, NFO, BSE, BFO, MCX, CDS). The rate comes from the IN-SEBI rule-set (`ops_per_second`), never from code.

Two lanes, because ADR-0004 says exits are never blocked:
- **exit lane** - exits, protective stops, every modify and cancel, and any order not tagged as an entry: waits for
  a token, however long; while an exit waits, no entry may take a token;
- **entry lane** - orders tagged `...-ENT` (app/execution/tagging.py): takes a token if one is free right now, else
  the order is refused before it reaches the broker (a REJECTED response the router records as a refusal, reason
  `ops_throttle`), never queued behind the market.
A broker 429 pauses the exchange's bucket for the next back-off step (`backoff_seconds`); `critical_after_429s` in a
row raise the 429 metric's severity (Prometheus rule `OpsThrottle429Burst`). Off unless OPS_THROTTLE_ENABLED.
"""
import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional

from app.brokers.models import BrokerOrderRequest, BrokerOrderResponse
from app.compliance import rules
from app.execution.tagging import LEG_ENTRY
from app.observability.metrics import OPS_429, OPS_THROTTLED

logger = logging.getLogger(__name__)
RULE = "IN-SEBI.ops.throttle"
REFUSAL = "ops_throttle"


def is_entry(order: BrokerOrderRequest) -> bool:
    tag = (order.tag or "").upper()
    return tag == LEG_ENTRY or tag.endswith("-" + LEG_ENTRY)


class _Bucket:
    def __init__(self, rate: float, clock: Callable[[], float]) -> None:
        self.rate = rate
        self.capacity = max(1.0, rate)
        self.tokens = self.capacity
        self.clock = clock
        self.updated = clock()
        self.paused_until = 0.0
        self.exits_waiting = 0
        self.backoff_step = 0

    def _refill(self) -> float:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        return now

    def wait_for_token(self) -> float:
        """Seconds until a token can be taken (0 = now)."""
        now = self._refill()
        if now < self.paused_until:
            return self.paused_until - now
        return 0.0 if self.tokens >= 1 else (1 - self.tokens) / self.rate

    def take(self) -> None:
        self.tokens -= 1


class OpsThrottle:
    def __init__(self, ops_per_second: Optional[float] = None, *, backoff: Optional[List[float]] = None,
                 critical_after: Optional[int] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable = asyncio.sleep) -> None:
        rs = rules.load()
        self.rate = float(ops_per_second or rs.param(RULE, "ops_per_second"))
        self.backoff = list(backoff or rs.param(RULE, "backoff_seconds"))
        self.critical_after = int(critical_after or rs.param(RULE, "critical_after_429s"))
        self.clock = clock
        self.sleep = sleep
        self._buckets: Dict[str, _Bucket] = {}
        self._order_exchange: Dict[str, str] = {}
        self.consecutive_429 = 0

    def bucket(self, exchange: Optional[str]) -> _Bucket:
        key = (exchange or "*").upper()
        if key not in self._buckets:
            self._buckets[key] = _Bucket(self.rate, self.clock)
        return self._buckets[key]

    def exchange_of(self, order_id: str) -> Optional[str]:
        return self._order_exchange.get(order_id)

    def remember(self, response: BrokerOrderResponse, exchange: str) -> None:
        if response.order_id:
            self._order_exchange[response.order_id] = exchange

    def try_entry(self, exchange: Optional[str]) -> bool:
        bucket = self.bucket(exchange)
        if bucket.exits_waiting or bucket.wait_for_token() > 0:
            OPS_THROTTLED.labels(lane="entry", outcome="refused").inc()
            return False
        bucket.take()
        return True

    async def exit(self, exchange: Optional[str]) -> float:
        """Waits for a token in the exit lane; returns the seconds waited. Never refuses."""
        bucket = self.bucket(exchange)
        bucket.exits_waiting += 1
        waited = 0.0
        try:
            while True:
                wait = bucket.wait_for_token()
                if wait <= 0:
                    bucket.take()
                    if waited:
                        OPS_THROTTLED.labels(lane="exit", outcome="waited").inc()
                    return waited
                waited += wait
                await self.sleep(wait)
        finally:
            bucket.exits_waiting -= 1

    def rate_limited(self, exchange: Optional[str]) -> float:
        """A broker 429: pause this exchange's bucket for the next back-off step; returns the pause."""
        bucket = self.bucket(exchange)
        pause = self.backoff[min(bucket.backoff_step, len(self.backoff) - 1)]
        bucket.backoff_step += 1
        bucket.paused_until = max(bucket.paused_until, self.clock() + pause)
        self.consecutive_429 += 1
        severity = "critical" if self.consecutive_429 >= self.critical_after else "warning"
        OPS_429.labels(severity=severity).inc()
        (logger.critical if severity == "critical" else logger.warning)(
            "Broker 429 on %s (%d in a row) - orders paused %.1fs", exchange or "*", self.consecutive_429, pause)
        return pause

    def accepted(self, exchange: Optional[str]) -> None:
        self.bucket(exchange).backoff_step = 0
        self.consecutive_429 = 0


def refused_response() -> BrokerOrderResponse:
    return BrokerOrderResponse(order_id="", status="REJECTED",
                               message=f"{REFUSAL}: orders-per-second limit for this exchange reached - entry not sent (exits go first)")
