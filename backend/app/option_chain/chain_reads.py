"""M71 W2-E (owner order M71, PL-2 / PL-3): the option chain pages' broker reads - paced, shared and store-first.

With the flag `market_store_reads` ON (OFF = every chain read exactly as before):

* **Paced.** Every chain call takes a token from the credential's shared data budget first
  (`market_store.reader.paced_adapter` -> `api_budget.BudgetedAdapter`, Redis per credential) - the data pool, never
  the trading worker's order budget. No call starts after CHAIN_WAIT_SECONDS of waiting.
* **Shared.** One chain call per credential, underlying and expiry per snapshot window, whoever asks (the data hub's
  `shared_chain`, keyed by the credential row: one user's chain is never read under another's, M71Q-2). A second
  refresh inside the window makes no broker call.
* **Closed market.** While the underlying's exchange is closed, a chain read through the same credential after the
  last session close and less than CLOSED_REUSE_SECONDS ago is the answer (no broker call; the numbers do not move).
* **Busy broker.** A 429 / "too many requests" or no budget token: no user-facing failure. The credential backs off
  (exponential, with jitter, BACKOFF_FIRST_SECONDS .. BACKOFF_MAX_SECONDS) and the answer is the last chain read
  through the same credential with its capture time (its age); else the user's own stored chain of today
  (`own_snapshots`, his own rows only); else a plain "busy" state. A 429 here never reaches the order breaker.

Read-only: only `get_option_chain` is called; nothing here places, changes or suggests an order (ADR-0006).
No sample data: a chain that cannot be read is missing, with the reason.
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChain

logger = logging.getLogger(__name__)

CHAIN_WAIT_SECONDS = 2.0          # no chain call starts after this (the credential's budget is shared)
LAST_KEEP_SECONDS = 3600          # the last good chain per credential (Redis caps a cache entry at an hour)
CLOSED_REUSE_SECONDS = 300        # closed market: a chain read after the close this recently is reused
BACKOFF_FIRST_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0
LAST_PREFIX = "md:oc:last"
COOL_PREFIX = "md:oc:cool"

OK, BUSY, UNAVAILABLE, NOT_OFFERED = "ok", "busy", "unavailable", "not_offered"
BUSY_MESSAGE = "Your broker is busy right now. The chain loads on the next refresh."

_last_local: dict[str, tuple[float, str]] = {}
_cool_local: dict[int, tuple[float, int]] = {}       # credential -> (wall-clock until, back-off level)


@dataclass
class ChainGot:
    """One chain read: the chain (or None with `state`), the broker calls it made and - for an earlier chain served
    while the broker is busy or the market is closed - its capture time."""
    chain: OptionChain | None
    state: str = OK
    calls: int = 0
    captured_at: datetime | None = None
    reused: bool = False              # an earlier chain of the same credential (no broker call)
    stale: bool = False               # served because the broker was busy

    def age_seconds(self, now: datetime) -> int | None:
        if self.captured_at is None:
            return None
        return max(0, int((now - self.captured_at).total_seconds()))

    def snapshot(self, now: datetime) -> dict[str, Any] | None:
        """The answer's `snapshot` block for a chain served from an earlier read while the broker was busy, else None."""
        if not self.stale or self.captured_at is None:
            return None
        return {"captured_at": self.captured_at.isoformat(), "age_seconds": self.age_seconds(now), "reason": BUSY}


async def store_on(session: AsyncSession, tenant_id: int | None) -> bool:
    """The flag `market_store_reads` for the caller's organisation."""
    from app.market_store.models import FLAG as STORE_FLAG
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, STORE_FLAG, tenant_id)


def is_busy(exc: BaseException) -> bool:
    """A 429 / "too many requests" from the broker, or no budget token (the call was not made)."""
    from app.brokers.exceptions import BrokerAPIError, BrokerBudgetUsed
    from app.market_data import api_budget
    if isinstance(exc, (api_budget.BudgetUsed, BrokerBudgetUsed)):
        return True
    if isinstance(exc, BrokerAPIError) and exc.status_code == 429:
        return True
    return "too many requests" in str(exc).lower()


def _key(record_id: int, underlying: str, expiry: Any) -> str:
    return f"{LAST_PREFIX}:c{int(record_id)}:{underlying.upper()}:{expiry or 'near'}"


async def _keep_last(key: str, chain: OptionChain, at: datetime) -> None:
    from app.cache.client import cache_set
    raw = json.dumps({"at": at.isoformat(), "chain": chain.model_dump(mode="json")})
    _last_local[key] = (time.monotonic(), raw)
    await cache_set(key, raw, LAST_KEEP_SECONDS)


async def last_chain(record_id: int, underlying: str, expiry: Any) -> tuple[OptionChain, datetime] | None:
    """The last good chain read through this credential (underlying, expiry) and its capture time, or None."""
    from app.cache.client import cache_get
    key = _key(record_id, underlying, expiry)
    raw = await cache_get(key)
    if not raw:
        local = _last_local.get(key)
        raw = local[1] if local is not None and time.monotonic() - local[0] < LAST_KEEP_SECONDS else None
    if not raw:
        return None
    try:
        body = json.loads(raw)
        return OptionChain.model_validate(body["chain"]), datetime.fromisoformat(body["at"])
    except (KeyError, TypeError, ValueError):
        return None


def _cool_key(record_id: int) -> str:
    return f"{COOL_PREFIX}:c{int(record_id)}"


async def _cool_state(record_id: int) -> tuple[float, int]:
    """(wall-clock end of the back-off, its level) - shared by every process through Redis, else this process's own."""
    from app.cache.client import cache_get
    raw = await cache_get(_cool_key(record_id))
    if raw:
        try:
            level, until = raw.split(":", 1)
            return float(until), int(level)
        except ValueError:
            pass
    return _cool_local.get(int(record_id), (0.0, 0))


async def _cooling(record_id: int) -> bool:
    return time.time() < (await _cool_state(record_id))[0]


async def _back_off(record_id: int) -> float:
    """After a 429: no chain call through this credential for an exponentially growing time, with jitter."""
    from app.cache.client import cache_set
    level = (await _cool_state(record_id))[1] + 1
    delay = min(BACKOFF_MAX_SECONDS, BACKOFF_FIRST_SECONDS * 2 ** (level - 1))
    delay = delay * (0.5 + random.random() / 2)  # nosec B311 - jitter for a back-off, not security
    until = time.time() + delay
    _cool_local[int(record_id)] = (until, level)
    # the level outlives the back-off itself (a second 429 soon after doubles it); a good read clears it
    await cache_set(_cool_key(record_id), f"{level}:{until}", int(BACKOFF_MAX_SECONDS * 4))
    return delay


async def _cool_done(record_id: int) -> None:
    from app.cache.client import cache_set
    if _cool_local.pop(int(record_id), None) is not None or await _cooling_level(record_id):
        await cache_set(_cool_key(record_id), "0:0", 1)


async def _cooling_level(record_id: int) -> int:
    return (await _cool_state(record_id))[1]


def reset() -> None:
    """Tests: forget the process-local copies and back-offs."""
    _last_local.clear()
    _cool_local.clear()


def _same_expiry(chain: OptionChain, expiry: Any) -> bool:
    return expiry is None or (chain.expiry or "")[:10] == str(expiry)[:10]


async def _own_stored(session: AsyncSession, user: Any, record: Any, underlying: str, expiry: Any,
                      now: datetime) -> tuple[OptionChain, datetime] | None:
    """The user's own stored chain of today (the newest one of the expiry), only through a credential he linked himself."""
    if user is None or getattr(record, "user_id", None) != getattr(user, "id", None) \
            or getattr(record, "tenant_id", None) != getattr(user, "tenant_id", None):
        return None
    from app.option_chain import own_snapshots
    try:
        stored = await own_snapshots.day(session, user, underlying, own_snapshots.today(now))
    except Exception as exc:  # noqa: BLE001 - no stored chain: the plain busy state
        logger.info("Own stored chain not read for %s: %s", underlying, type(exc).__name__)
        return None
    kept = [s for s in stored if s.captured_at < now and _same_expiry(s.chain(), expiry)]
    if not kept:
        return None
    newest = max(kept, key=lambda s: s.captured_at)
    return newest.chain(), newest.captured_at


async def _earlier(session: AsyncSession, user: Any, record: Any, underlying: str, expiry: Any, now: datetime) -> ChainGot:
    found = await last_chain(record.id, underlying, expiry) or await _own_stored(session, user, record, underlying, expiry, now)
    if found is None:
        return ChainGot(None, BUSY)
    return ChainGot(found[0], OK, captured_at=found[1], reused=True, stale=True)


async def read(session: AsyncSession, user: Any, record: Any, underlying: str, expiry: Any, *, now: datetime | None = None,
               closed_since: datetime | None = None, build: Any = None) -> ChainGot:
    """One chain for the page (see the module docstring). `closed_since`: the last session close while the market is
    closed (None while it is open). `build`: the adapter factory (the caller's `build_adapter`)."""
    from app.market_data import hub as data_hub
    from app.market_store.models import RestSource
    from app.market_store.reader import paced_adapter
    now = now or datetime.now(UTC)
    if closed_since is not None:
        found = await last_chain(record.id, underlying, expiry)
        if found is not None and found[1] >= closed_since and (now - found[1]).total_seconds() < CLOSED_REUSE_SECONDS:
            return ChainGot(found[0], OK, captured_at=found[1], reused=True)
    if await _cooling(record.id):
        return await _earlier(session, user, record, underlying, expiry, now)
    calls = 0
    try:
        if build is None:
            from app.brokers.token_lifecycle import build_adapter
            build = build_adapter
        adapter = paced_adapter(RestSource(build(record), int(record.id), str(record.broker_name), CHAIN_WAIT_SECONDS))

        async def fetch() -> Any:
            nonlocal calls
            calls += 1
            return await adapter.get_option_chain(underlying, expiry)

        chain = await data_hub.shared_chain(record.id, underlying, expiry, fetch)
    except NotImplementedError:
        return ChainGot(None, NOT_OFFERED, calls)
    except Exception as exc:  # noqa: BLE001 - every broker failure is a plain state; a busy broker serves the last chain
        if is_busy(exc):
            delay = await _back_off(record.id)
            logger.info("Option chain: broker busy for %s via %s, backing off %.1fs", underlying, record.broker_name, delay)
            got = await _earlier(session, user, record, underlying, expiry, now)
            got.calls = calls
            return got
        logger.info("Option chain: chain read failed for %s via %s: %s", underlying, record.broker_name, type(exc).__name__)
        return ChainGot(None, UNAVAILABLE, calls)
    if chain is None:
        return ChainGot(None, UNAVAILABLE, calls)
    if calls:                         # a fresh read: the back-off ends; kept as this credential's last good chain
        await _cool_done(record.id)
        await _keep_last(_key(record.id, underlying, expiry), chain, now)
    return ChainGot(chain, OK, calls, reused=not calls)


__all__ = ["ChainGot", "read", "store_on", "is_busy", "last_chain", "reset", "BUSY_MESSAGE", "OK", "BUSY", "UNAVAILABLE",
           "NOT_OFFERED", "CHAIN_WAIT_SECONDS", "CLOSED_REUSE_SECONDS", "BACKOFF_FIRST_SECONDS", "BACKOFF_MAX_SECONDS"]
