"""Phase E1/E2: request-id correlation, API versioning alias and HTTP metrics, as one pure-ASGI
middleware (no BaseHTTPMiddleware: it must not buffer streaming responses or break exports).

* **Request id**: honours an inbound `X-Request-ID` (sanitised, max 64 chars) or mints a UUID4,
  binds it into the structured log context for everything the request logs, and returns it in
  the response so a user-reported failure can be found in the logs by one string.
* **Versioning**: `/api/v1/...` is the canonical path and is rewritten to the routers' `/api/...`
  before routing, so every existing route is reachable under both. Every API response carries
  `X-API-Version: 1`; a request on the unversioned alias also gets
  `Deprecation: true` and a `Link: </api/v1/...>; rel="successor-version"` header so clients can
  find the canonical path (the alias itself is not scheduled for removal).
* **Metrics**: request count and latency by method, *route template* and status class.
"""
import re
import time
import uuid
from typing import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging_config import bind_log_context
from app.observability.metrics import observe_http

API_VERSION = "1"
VERSION_PREFIX = f"/api/v{API_VERSION}"
_SAFE_REQUEST_ID = re.compile(r"[^A-Za-z0-9._:-]")


def sanitize_request_id(raw: str) -> str:
    return _SAFE_REQUEST_ID.sub("", raw or "")[:64]


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        request_id = sanitize_request_id(headers.get("x-request-id", "")) or uuid.uuid4().hex
        original_path = scope["path"]
        versioned = original_path == VERSION_PREFIX or original_path.startswith(VERSION_PREFIX + "/")
        if versioned:
            scope["path"] = "/api" + original_path[len(VERSION_PREFIX):]
            if scope.get("raw_path"):
                scope["raw_path"] = scope["path"].encode("latin-1")
        is_api = scope["path"].startswith("/api/") or scope["path"] == "/api"
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                extra = [(b"x-request-id", request_id.encode("latin-1"))]
                if is_api:
                    extra.append((b"x-api-version", API_VERSION.encode()))
                    if not versioned:
                        extra.append((b"deprecation", b"true"))
                        successor = VERSION_PREFIX + scope["path"][len("/api"):]
                        extra.append((b"link", f'<{successor}>; rel="successor-version"'.encode("latin-1")))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        with bind_log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                route = scope.get("route")
                template = getattr(route, "path", None) or ("/api/*" if is_api else scope["path"])
                observe_http(scope.get("method", "GET"), template, status_holder["status"], time.perf_counter() - started)
