"""N7 C6: a backtest sweep worker - claims and runs sweep tasks (`app/backtest/sweeps.py`) until stopped.

Optional in Phase 0: the API runs queued tasks itself while BACKTEST_SWEEP_INLINE is on. Any number of these processes
may run at once (`python -m app.workers.backtest_worker`), on this host or another one that reaches the same database;
claims are atomic, so each task runs once (a task whose worker died is queued again). No orders, no strategy changes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging_config import configure_logging

logger = logging.getLogger(__name__)

POLL_SECONDS = float(os.getenv("BACKTEST_WORKER_POLL_SECONDS", "10"))


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def run_forever(factory: async_sessionmaker[AsyncSession], stop: asyncio.Event, name: str) -> None:
    from app.backtest import sweeps
    while not stop.is_set():
        try:
            ran = await sweeps.drain(factory, name, limit=20)
        except Exception:  # noqa: BLE001 - a database blip never ends the worker
            logger.exception("Backtest worker step failed")
            ran = 0
        if ran:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    configure_logging()
    from app.db.session import _session_factory
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        import signal
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    except (NotImplementedError, ImportError):
        pass
    name = worker_name()
    logger.info("Backtest sweep worker %s started (poll every %ss)", name, POLL_SECONDS)
    await run_forever(_session_factory, stop, name)


if __name__ == "__main__":
    asyncio.run(main())
