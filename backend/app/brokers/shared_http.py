"""LD-7: one HTTP client per broker host and event loop, for adapters built without a client of their own.

An adapter is built per request (a chart, a screener run, a quote): with a client of its own each time, every request
paid a new TLS context (the CA bundle loaded again) and a new TLS handshake to the broker, and the client was never
closed (its sockets waited for the garbage collector). The shared client keeps the connections open between requests.

What is shared is the connection only. Credentials never live on the client: the adapters that use it send them per
request (`headers=`), and the client keeps no cookies at all (a cookie set by the broker for one user's session must
never ride along on another user's request) - BT1b.

Per event loop, because an httpx client's connections belong to the loop that opened them (tests run many loops; the
API and the worker run one each). Without a running loop, a private client is returned, as before.

Per pool, too (LD-7 review N2): order and account calls (`pool="orders"`) get a client of their own, so data fetches -
charts, screens, quotes, a worker cycle's bars - can fill the data client's connections without an exit order queueing
behind them for the pool timeout (ADR-0004: exits are never blocked).
"""
import asyncio
import weakref
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Dict, Tuple

import httpx

_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[Tuple[str, float, str], httpx.AsyncClient]]" = weakref.WeakKeyDictionary()


def no_cookies() -> CookieJar:
    """A cookie jar that accepts no cookie from any domain."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


DATA_POOL, ORDER_POOL = "data", "orders"


def shared_client(base_url: str, timeout: float, pool: str = DATA_POOL) -> httpx.AsyncClient:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return httpx.AsyncClient(base_url=base_url, timeout=timeout, cookies=no_cookies())
    per_loop = _CLIENTS.get(loop)
    if per_loop is None:
        per_loop = _CLIENTS[loop] = {}
    key = (base_url, float(timeout), pool)
    client = per_loop.get(key)
    if client is None or client.is_closed:
        client = per_loop[key] = httpx.AsyncClient(base_url=base_url, timeout=timeout, cookies=no_cookies())
    return client


# --- M71 PL-2: every HTTP request a broker client sends is counted (calls per day, by broker / pool / endpoint kind) ------
def http_kind(method: str, path: str) -> str:
    """The endpoint kind of one broker HTTP request, from its method and path (broker_limits.KINDS)."""
    p = (path or "").lower()
    if any(word in p for word in ("historical", "candle", "chart")):
        return "history"
    if "order" in p and (method or "GET").upper() != "GET":
        return "orders"
    if any(word in p for word in ("quote", "ltp", "option", "market-feed", "marketfeed")):
        return "quote"
    return "account"


def count_requests(client: httpx.AsyncClient, broker: str) -> httpx.AsyncClient:
    """Hook `client` so every request it sends, and every 429 it gets back, is counted for `broker` per IST day
    (broker_limits.HTTP_COUNTER). Once per client (a shared client is hooked once). Never fails a request."""
    if not isinstance(client, httpx.AsyncClient) or getattr(client, "_atp_counted", False):
        return client
    from app.brokers import broker_limits
    name = (broker or "unknown").lower()

    async def on_request(request: httpx.Request) -> None:
        kind = http_kind(request.method, request.url.path)
        await broker_limits.HTTP_COUNTER.add(name, broker_limits.pool_of(kind), kind)

    async def on_response(response: httpx.Response) -> None:
        if response.status_code == 429:
            kind = http_kind(response.request.method, response.request.url.path)
            await broker_limits.HTTP_COUNTER.add(name, broker_limits.pool_of(kind), kind, rate_limited=True)

    hooks = dict(client.event_hooks)
    client.event_hooks = {"request": [*hooks.get("request", []), on_request],
                          "response": [*hooks.get("response", []), on_response]}
    client._atp_counted = True  # type: ignore[attr-defined]
    return client
