"""LD-7: CPU-bound request work (pandas, a screen's evaluation) on a worker thread, a few at a time per API process.

A request handler that resamples a few thousand bars or evaluates a screen must not run on the event loop: every other
request of the process would wait for it. On a worker thread it still needs the GIL for most of its time, and the event
loop has to win the GIL back for each step of every other request - with many such threads at once, each of those steps
waits behind all of them (the dashboard's p95 in the 500-user test sat around 1 s while its p50 was under 0.1 s). Python
runs one of them at a time anyway, so a small per-process limit costs no throughput and keeps the loop's waits short.
"""
import asyncio
import os
import weakref
from typing import Any, Callable, TypeVar

T = TypeVar("T")

CPU_THREADS = max(1, int(os.environ.get("API_CPU_THREADS", "2") or "2"))
_limiters: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]" = weakref.WeakKeyDictionary()


def _limiter() -> Any:
    import anyio
    loop = asyncio.get_running_loop()
    limiter = _limiters.get(loop)
    if limiter is None:
        limiter = _limiters[loop] = anyio.CapacityLimiter(CPU_THREADS)
    return limiter


async def run_cpu(fn: Callable[..., T], *args: Any) -> T:
    """`fn(*args)` on a worker thread, at most CPU_THREADS at once in this process (keyword arguments: use a lambda or
    functools.partial)."""
    import anyio.to_thread
    return await anyio.to_thread.run_sync(fn, *args, limiter=_limiter())
