"""Phase G2: a per-broker circuit breaker on broker-call health (master prompt section 49).

Distinct from the kill switch: a kill switch is a *person's* decision to stop trading; the
breaker is the *platform's* observation that a broker's API is failing. When more than
`failure_ratio` of the last `window_seconds` of calls to a broker failed (after at least
`min_calls`), the breaker OPENS and new LIVE entries to that broker are refused platform-wide -
every tenant, every deployment - for `open_seconds`. Then it goes HALF_OPEN: one entry is let
through as a probe; success CLOSES the breaker, failure re-opens it.

Only *new entries* are refused. Exits, cancels and protective stops are still attempted: an
order that fails is recorded on the trail either way, while an exit never sent is a position
left open on purpose, which is worse. Failures counted are the ones that mean "the broker is
unhealthy": timeouts, connection errors, 5xx, 429, malformed payloads. A 4xx business answer
(insufficient margin, invalid instrument, expired token) is the broker working correctly and
does not move the breaker.

State lives in-process (one breaker per broker name per process). The worker is the process
that submits autonomously, so that is where it matters; the API process keeps its own view.
"""
import logging
import time
from collections import deque
from enum import Enum
from typing import Callable, Deque, Dict, Optional, Tuple

from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError, BrokerOrderRejected
from app.core import config
from app.observability.metrics import BROKER_CALLS, BROKER_CIRCUIT_REJECTIONS, BROKER_CIRCUIT_STATE

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


_STATE_VALUE = {CircuitState.CLOSED: 0, CircuitState.HALF_OPEN: 1, CircuitState.OPEN: 2}


def is_health_failure(exc: BaseException) -> bool:
    """Does this exception say the broker is unhealthy (True) or that it answered correctly with
    a business refusal (False)?"""
    if isinstance(exc, (BrokerAuthenticationError, BrokerOrderRejected)):
        return False
    if isinstance(exc, BrokerAPIError):
        code = exc.status_code
        if code is None:
            return True  # malformed / non-JSON response
        if code == 429 or code >= 500:
            return True
        return False
    return True  # timeouts, connection errors, anything unexpected


class CircuitBreaker:
    def __init__(
        self, name: str, *, window_seconds: Optional[float] = None, min_calls: Optional[int] = None,
        failure_ratio: Optional[float] = None, open_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.window_seconds = window_seconds if window_seconds is not None else config.BROKER_CIRCUIT_WINDOW_SECONDS
        self.min_calls = min_calls if min_calls is not None else config.BROKER_CIRCUIT_MIN_CALLS
        self.failure_ratio = failure_ratio if failure_ratio is not None else config.BROKER_CIRCUIT_FAILURE_RATIO
        self.open_seconds = open_seconds if open_seconds is not None else config.BROKER_CIRCUIT_OPEN_SECONDS
        self._clock = clock
        self._events: Deque[Tuple[float, bool]] = deque()  # (monotonic time, failed)
        self._state = CircuitState.CLOSED
        self._opened_at: Optional[float] = None
        self._probe_in_flight = False
        self.opened_count = 0
        self.last_reason: Optional[str] = None
        self._publish()

    # --- recording ---------------------------------------------------------------------------

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def record_success(self) -> None:
        now = self._clock()
        self._events.append((now, False))
        self._prune(now)
        if self._state == CircuitState.HALF_OPEN:
            self._close("probe succeeded")
        self._publish()

    def record_failure(self, reason: str = "") -> None:
        now = self._clock()
        self._events.append((now, True))
        self._prune(now)
        if self._state == CircuitState.HALF_OPEN:
            self._open(now, f"probe failed: {reason}")
        elif self._state == CircuitState.CLOSED:
            failures, total = self.counts()
            if total >= self.min_calls and failures / total > self.failure_ratio:
                self._open(now, f"{failures}/{total} calls failed in {int(self.window_seconds)}s (last: {reason})")
        self._publish()

    def record(self, exc: Optional[BaseException]) -> None:
        """One call finished: `exc` is None on success, else the exception it raised."""
        if exc is None:
            self.record_success()
        elif is_health_failure(exc):
            self.record_failure(f"{type(exc).__name__}: {exc}"[:200])
        # a business 4xx is neither: the broker is healthy, the request was wrong

    def counts(self) -> Tuple[int, int]:
        self._prune(self._clock())
        failures = sum(1 for _, failed in self._events if failed)
        return failures, len(self._events)

    # --- state ------------------------------------------------------------------------------

    def _open(self, now: float, reason: str) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = now
        self._probe_in_flight = False
        self.opened_count += 1
        self.last_reason = reason
        logger.error("Circuit OPEN for broker %s: %s - new LIVE entries paused for %ss", self.name, reason, int(self.open_seconds))

    def _close(self, reason: str) -> None:
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._probe_in_flight = False
        self._events.clear()
        logger.warning("Circuit CLOSED for broker %s: %s", self.name, reason)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._opened_at is not None \
                and self._clock() - self._opened_at >= self.open_seconds:
            self._state = CircuitState.HALF_OPEN
            self._publish()
        return self._state

    def seconds_until_half_open(self) -> float:
        if self._state != CircuitState.OPEN or self._opened_at is None:
            return 0.0
        return max(0.0, self.open_seconds - (self._clock() - self._opened_at))

    def allow_submission(self) -> bool:
        """May a *new entry* be sent now? CLOSED: yes. OPEN: no. HALF_OPEN: exactly one probe."""
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        BROKER_CIRCUIT_REJECTIONS.labels(broker=self.name).inc()
        return False

    def refusal_reason(self) -> str:
        wait = int(self.seconds_until_half_open())
        detail = self.last_reason or "broker calls failing"
        if wait > 0:
            return f"Broker circuit open for {self.name} ({detail}) - new LIVE entries paused for another {wait}s"
        return f"Broker circuit half-open for {self.name} ({detail}) - one probe entry in flight"

    def reset(self) -> None:
        self._close("manual reset")
        self._publish()

    def snapshot(self) -> Dict[str, object]:
        failures, total = self.counts()
        return {"broker": self.name, "state": self.state.value, "failures_in_window": failures, "calls_in_window": total,
                "window_seconds": self.window_seconds, "opened_count": self.opened_count, "last_reason": self.last_reason,
                "seconds_until_half_open": round(self.seconds_until_half_open(), 1)}

    def _publish(self) -> None:
        BROKER_CIRCUIT_STATE.labels(broker=self.name).set(_STATE_VALUE[self._state])


_BREAKERS: Dict[str, CircuitBreaker] = {}


def breaker_for(broker_name: str) -> CircuitBreaker:
    name = (broker_name or "unknown").lower()
    breaker = _BREAKERS.get(name)
    if breaker is None:
        breaker = _BREAKERS[name] = CircuitBreaker(name)
    return breaker


def all_breakers() -> Dict[str, CircuitBreaker]:
    return dict(_BREAKERS)


def reset_all() -> None:
    """Tests only: forget every breaker (state is per process)."""
    for breaker in _BREAKERS.values():
        breaker.reset()
    _BREAKERS.clear()


def observe_call(broker_name: str, method: str, exc: Optional[BaseException]) -> None:
    """Record one broker call's outcome on the metrics and the broker's breaker."""
    outcome = "ok" if exc is None else ("failure" if is_health_failure(exc) else "rejected")
    BROKER_CALLS.labels(broker=(broker_name or "unknown").lower(), method=method, outcome=outcome).inc()
    breaker_for(broker_name).record(exc)
