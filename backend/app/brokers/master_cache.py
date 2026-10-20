"""Process-level shared instrument masters (UI2-4a2-pre review).

A broker adapter is built per request (`build_adapter`), so a master cached on the adapter instance was downloaded and
parsed again on every chart load and every watchlist poll - tens of MB for Angel One's scrip master. Dhan already keeps
its symbol map per process (LD-7, `dhan._SHARED_SYMBOLS`); this is the same idea for the others, keyed by
(broker, key) - the key is usually an exchange.

- **Shared per process**, kept for `ttl` seconds (MASTER_TTL_SECONDS by default), then built again on the next use.
- **Single-flight**: one build per key at a time per event loop; the others wait for it and use its result.
- **A failed build stores nothing** (the next use tries again) and raises to the caller, as a per-instance build did.
- Only adapters built without an HTTP client of their own use it (the app's path), like Dhan's map; an adapter given a
  client (tests, a one-off tool) keeps its own copy unless it opts in.
Masters are public data, the same for every user: nothing per account is kept here.
"""
import asyncio
import time
import weakref
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar

T = TypeVar("T")
MASTER_TTL_SECONDS = 6 * 60 * 60

_ENTRIES: Dict[Tuple[str, str], Tuple[float, float, Any]] = {}          # key -> (built at, ttl, value)
_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[Tuple[str, str], asyncio.Lock]]" = weakref.WeakKeyDictionary()
builds: Dict[Tuple[str, str], int] = {}                                    # key -> builds made (observability, tests)


def _fresh(key: Tuple[str, str]) -> Optional[Tuple[Any]]:
    entry = _ENTRIES.get(key)
    if entry is not None and (time.monotonic() - entry[0]) < entry[1]:
        return (entry[2],)
    return None


async def shared(broker: str, key: str, build: Callable[[], Awaitable[T]], ttl: float = MASTER_TTL_SECONDS) -> T:
    """The cached value for (broker, key), or the result of `build()` - made once while others wait for it."""
    k = ((broker or "").lower(), str(key))
    hit = _fresh(k)
    if hit is not None:
        return hit[0]
    loop = asyncio.get_running_loop()
    locks = _LOCKS.get(loop)
    if locks is None:
        locks = _LOCKS[loop] = {}
    lock = locks.get(k)
    if lock is None:
        lock = locks[k] = asyncio.Lock()
    async with lock:
        hit = _fresh(k)
        if hit is not None:
            return hit[0]
        value = await build()
        _ENTRIES[k] = (time.monotonic(), ttl, value)
        builds[k] = builds.get(k, 0) + 1
        return value


def reset() -> None:
    """Tests: forget every shared master."""
    _ENTRIES.clear()
    builds.clear()
