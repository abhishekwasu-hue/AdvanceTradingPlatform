"""HB1: fetching NSE's public archive files politely - an injectable seam, so tests and the manual-upload path never
touch the network.

`PoliteFetcher` (httpx): browser-like headers with the exchange's site as referer, ONE request at a time at least
`pause_seconds` apart, exponential backoff on 429 / 403 / 5xx / timeouts (a `Retry-After` header is honoured, capped
at `max_backoff`), `retries` attempts per URL, and a size cap. A 404 is an answer ("no file for that day": a holiday,
a weekend, or not published yet), never retried. A 403 that survives every retry is reported as `blocked` - the
admin uploads that file by hand instead of the job hammering the site. `sleep` and `clock` are injectable so the
backoff is tested without waiting. Tests pass an `httpx.MockTransport`; nothing here is called by the test suite
against a real host.
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Protocol

import httpx

from app.core import config
from app.instruments import nse_expiries

MAX_BYTES = 64 * 1024 * 1024
RETRY_STATUSES = {403, 408, 425, 429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    status: str                       # ok | not_found | blocked | error
    content: bytes = b""
    http_status: Optional[int] = None
    attempts: int = 0
    detail: str = ""


class Fetcher(Protocol):
    async def get(self, url: str) -> FetchResult: ...

    async def aclose(self) -> None: ...


def default_headers() -> Dict[str, str]:
    headers = dict(nse_expiries.HEADERS)
    if config.HISTORY_BACKFILL_USER_AGENT:
        headers["User-Agent"] = config.HISTORY_BACKFILL_USER_AGENT
    return headers


class PoliteFetcher:
    def __init__(self, *, transport: Optional[httpx.AsyncBaseTransport] = None, pause_seconds: Optional[float] = None,
                 retries: Optional[int] = None, backoff_base: float = 2.0, max_backoff: Optional[float] = None,
                 timeout: float = 60.0, max_bytes: int = MAX_BYTES,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.pause = config.HISTORY_BACKFILL_PAUSE_SECONDS if pause_seconds is None else pause_seconds
        self.retries = max(1, config.HISTORY_BACKFILL_RETRIES if retries is None else retries)
        self.backoff_base = backoff_base
        self.max_backoff = config.HISTORY_BACKFILL_MAX_BACKOFF_SECONDS if max_backoff is None else max_backoff
        self.max_bytes = max_bytes
        self.sleep, self.clock = sleep, clock
        self.requests = 0
        self.waits: list = []                     # every pause / backoff taken, in seconds (tests and the run log)
        self._last: Optional[float] = None
        self._client = httpx.AsyncClient(transport=transport, headers=default_headers(), timeout=timeout, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _wait(self, seconds: float) -> None:
        if seconds > 0:
            self.waits.append(round(seconds, 3))
            await self.sleep(seconds)

    async def _pace(self) -> None:
        if self._last is not None:
            await self._wait(self.pause - (self.clock() - self._last))
        self._last = self.clock()

    def _backoff(self, attempt: int, response: Optional[httpx.Response]) -> float:
        wait = self.backoff_base * (2 ** (attempt - 1))
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after and retry_after.strip().isdigit():
            wait = max(wait, float(retry_after.strip()))
        return min(wait, self.max_backoff)

    async def get(self, url: str) -> FetchResult:
        last_status: Optional[int] = None
        detail = ""
        for attempt in range(1, self.retries + 1):
            await self._pace()
            self.requests += 1
            response: Optional[httpx.Response] = None
            try:
                response = await self._client.get(url)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                detail = f"{type(exc).__name__}: {exc}"[:300]
                last_status = None
            else:
                last_status = response.status_code
                if response.status_code == 200:
                    if len(response.content) > self.max_bytes:
                        return FetchResult(url, "error", http_status=200, attempts=attempt,
                                           detail=f"larger than {self.max_bytes} bytes - refused")
                    return FetchResult(url, "ok", response.content, 200, attempt)
                if response.status_code == 404:
                    return FetchResult(url, "not_found", http_status=404, attempts=attempt)
                if response.status_code not in RETRY_STATUSES:
                    return FetchResult(url, "error", http_status=response.status_code, attempts=attempt,
                                       detail=f"HTTP {response.status_code}")
                detail = f"HTTP {response.status_code}"
            if attempt < self.retries:
                await self._wait(self._backoff(attempt, response))
        status = "blocked" if last_status == 403 else "error"
        return FetchResult(url, status, http_status=last_status, attempts=self.retries,
                           detail=f"{detail} after {self.retries} attempt(s)")
