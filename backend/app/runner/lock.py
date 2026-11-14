"""Runner S2: the in-process lock that replaces the server worker's Redis replica lock (S1 carry-over).

The server runs several worker replicas, so a Redis lock decides which one trades (trading_worker.py LOCK_KEY). The
runner is one process for one user, so two things are enough, with no Redis:
- one runner process per folder: an exclusive OS file lock on `runner.lock` (non-blocking; the OS frees it if the
  process dies, so a crash never leaves a stale lock);
- one cycle at a time inside the process: an asyncio lock; a cycle that finds one still running is skipped, never queued.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO


class RunnerLockHeld(RuntimeError):
    """Another runner process already holds this folder."""


class RunnerLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: TextIO | None = None
        self._cycle = asyncio.Lock()

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(self) -> None:
        """Takes the folder for this process; raises RunnerLockHeld when another process has it."""
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RunnerLockHeld(f"another runner is already running in {self.path.parent}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    @asynccontextmanager
    async def cycle(self) -> AsyncIterator[bool]:
        """Yields True when this cycle may run, False when another cycle of this process is still running."""
        if self._cycle.locked():
            yield False
            return
        async with self._cycle:
            yield True


__all__ = ["RunnerLock", "RunnerLockHeld"]
