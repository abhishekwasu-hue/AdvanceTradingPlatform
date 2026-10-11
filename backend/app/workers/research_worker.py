"""H-C3c: the research worker - runs queued strategy research studies one at a time (`app/ai/research_jobs.py`).

A separate process (compose service `research-worker`, profile `research`, not started by default), so a study's model
calls and backtests never share an event loop with the trading worker (exits are never delayed by research, ADR-0004)
or with API requests. Studies stay queued until it runs. It places no orders and saves no strategies (ADR-0006).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging_config import configure_logging

logger = logging.getLogger(__name__)

POLL_SECONDS = float(os.getenv("RESEARCH_POLL_SECONDS", "15"))


async def run_once(session_factory: async_sessionmaker[AsyncSession]) -> Optional[str]:
    """One queue step: the next study run to its end (None when nothing is queued)."""
    from app.ai import research_jobs
    async with session_factory() as session:
        return await research_jobs.run_next(session)


async def run_forever(session_factory: async_sessionmaker[AsyncSession], stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            ran = await run_once(session_factory)
        except Exception:  # noqa: BLE001 - one bad step (a database blip) never ends the worker
            logger.exception("Research worker step failed")
            ran = None
        if ran:
            logger.info("Research study %s finished", ran)
            continue                                   # more may be queued
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
    except (NotImplementedError, ImportError):  # Windows / restricted environments
        pass
    logger.info("Research worker started (poll every %ss)", POLL_SECONDS)
    await run_forever(_session_factory, stop)


if __name__ == "__main__":
    asyncio.run(main())
