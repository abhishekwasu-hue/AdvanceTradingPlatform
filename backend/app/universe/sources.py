"""U1: where reference files come from - a seam, so jobs and tests never care.

* `NseArchiveSource` downloads the exchange's published files (the URLs are config, `UNIVERSE_FILE_URLS`) politely:
  a browser-like user agent, a pause between files, a timeout and one retry. It is used only when the operator turns
  `UNIVERSE_SYNC_ENABLED` on, after the files' terms are recorded in docs/DATA_SOURCES.md.
* `StaticSource` serves bytes held in memory: fixture files in tests, and the admin's manual upload when a site blocks.

Every fetch returns the bytes and their SHA-256, which is stored on each row the file produced.
"""
import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Protocol

import httpx

from app.core import config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fetched:
    name: str
    content: bytes
    source: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class ReferenceSource(Protocol):
    async def fetch(self, name: str) -> Optional[Fetched]:
        """The named file (e.g. "equity_list"), or None when this source does not have it."""


class StaticSource:
    def __init__(self, files: Dict[str, bytes], source: str = "upload"):
        self.files, self.source = dict(files), source

    async def fetch(self, name: str) -> Optional[Fetched]:
        data = self.files.get(name)
        return Fetched(name, data, self.source) if data is not None else None


class NseArchiveSource:
    """The exchange's published download files. URLs come from config; nothing is scraped from pages."""

    def __init__(self, urls: Optional[Dict[str, str]] = None, *, pause_seconds: float = 2.0, timeout: float = 30.0):
        self.urls = urls if urls is not None else config.UNIVERSE_FILE_URLS
        self.pause_seconds, self.timeout = pause_seconds, timeout

    async def fetch(self, name: str) -> Optional[Fetched]:
        url = self.urls.get(name)
        if not url:
            return None
        headers = {"User-Agent": config.UNIVERSE_USER_AGENT, "Accept": "text/csv,application/octet-stream,*/*"}
        last: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    await asyncio.sleep(self.pause_seconds)              # polite: one file at a time, a pause between
                    return Fetched(name, response.content, "nse_archives")
            except Exception as exc:  # noqa: BLE001 - one retry, then the caller records the failure
                last = exc
                await asyncio.sleep(self.pause_seconds * (attempt + 1))
        log.warning("Universe file %s not fetched: %s", name, last)
        return None
