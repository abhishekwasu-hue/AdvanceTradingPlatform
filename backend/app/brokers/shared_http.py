"""LD-7: one HTTP client per broker host and event loop, for adapters built without a client of their own.

An adapter is built per request (a chart, a screener run, a quote): with a client of its own each time, every request
paid a new TLS context (the CA bundle loaded again) and a new TLS handshake to the broker, and the client was never
closed (its sockets waited for the garbage collector). The shared client keeps the connections open between requests.

What is shared is the connection only. Credentials never live on the client: the adapters that use it send them per
request (`headers=`), and the client keeps no cookies at all (a cookie set by the broker for one user's session must
never ride along on another user's request) - BT1b.

Per event loop, because an httpx client's connections belong to the loop that opened them (tests run many loops; the
API and the worker run one each). Without a running loop, a private client is returned, as before.
"""
import asyncio
import weakref
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Dict, Tuple

import httpx

_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[Tuple[str, float], httpx.AsyncClient]]" = weakref.WeakKeyDictionary()


def no_cookies() -> CookieJar:
    """A cookie jar that accepts no cookie from any domain."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


def shared_client(base_url: str, timeout: float) -> httpx.AsyncClient:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return httpx.AsyncClient(base_url=base_url, timeout=timeout, cookies=no_cookies())
    per_loop = _CLIENTS.get(loop)
    if per_loop is None:
        per_loop = _CLIENTS[loop] = {}
    key = (base_url, float(timeout))
    client = per_loop.get(key)
    if client is None or client.is_closed:
        client = per_loop[key] = httpx.AsyncClient(base_url=base_url, timeout=timeout, cookies=no_cookies())
    return client
