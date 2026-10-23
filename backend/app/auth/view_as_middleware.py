"""MO3-A1: the one server chokepoint that keeps a "View as user" session read-only (app/auth/view_as.py).

A pure ASGI middleware in front of every route. For a request whose bearer token says it is a view token:

- anything that is not a read (and the reads in `view_as.BLOCKED_READS`) is answered 403 here, before routing, body
  parsing or any dependency runs - so no route, old or new, can forget the check; the only write let through is ending
  the view itself;
- a read runs with `view_as.active()` set (no AI call is made for it), and a JSON answer is scrubbed on the way out
  (secrets masked, e-mail and IP addresses cut);
- every request of a genuine view token (signature checked) is recorded in `view_session_requests`.

The refusal looks only at the token's claims, not its signature: a forged token that merely claims to be a view is
refused writes too, which is always safe. Recording needs the signature. Requests without such a token pass through
untouched (one cheap header look-up).
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from app.auth import view_as

logger = logging.getLogger(__name__)


def _bearer(scope: Dict[str, Any]) -> Optional[str]:
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            raw = value.decode("latin-1")
            scheme, _, token = raw.partition(" ")
            return token.strip() if scheme.lower() == "bearer" and token.strip() else None
    return None


@asynccontextmanager
async def _db(app: Any) -> AsyncIterator[Any]:
    """The request database session the way the routes get it - honouring a test's `dependency_overrides`."""
    from app.db.session import get_session
    factory = getattr(app, "dependency_overrides", {}).get(get_session, get_session)
    generator = factory()
    session = await generator.__anext__()
    try:
        yield session
    finally:
        await generator.aclose()


async def _refuse(scope: Any, receive: Any, send: Any) -> None:
    response = JSONResponse(status_code=403, content={"detail": view_as.READ_ONLY_MESSAGE, "code": view_as.READ_ONLY_CODE},
                            headers={view_as.READ_ONLY_HEADER: "1"})
    await response(scope, receive, send)


class ViewSessionReadOnly:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "websocket" and view_as.unverified_view_claims(_bearer(scope)) is not None:
            await receive()                                          # websocket.connect, then refuse the handshake
            await send({"type": "websocket.close", "code": 4403})   # no two-way channel for a read-only view (none exists today)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _bearer(scope)
        claims = view_as.unverified_view_claims(token)
        if claims is None:
            await self.app(scope, receive, send)
            return
        method, path = str(scope.get("method", "GET")).upper(), str(scope.get("path", ""))
        query = (scope.get("query_string") or b"").decode("latin-1")
        verified = view_as.verified_view_claims(token)
        began = datetime.now(timezone.utc)
        if view_as.refusal(method, path, query) is not None:
            await _refuse(scope, receive, send)
            await self._record(scope, verified, method, path, 403, refused=True, began=began)
            return

        status = {"code": 500}
        held: List[Dict[str, Any]] = []
        chunks: List[bytes] = []
        blocked = {"on": False}

        async def send_scrubbed(message: Dict[str, Any]) -> None:
            if blocked["on"]:
                return                                # the refused answer's body never leaves
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if not view_as.allowed_content_type(headers.get("content-type", ""), int(message.get("status", 500))):
                    blocked["on"] = True              # fail closed: a download or page a view may not receive
                    status["code"] = 403
                    await _refuse(scope, receive, send)
                    return
                status["code"] = int(message.get("status", 500))
                headers[view_as.READ_ONLY_HEADER] = "1"
                if headers.get("content-type", "").startswith("application/json"):
                    held.append(message)          # sent with the scrubbed body below
                    return
                await send(message)
                return
            if message["type"] == "http.response.body" and held:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = view_as.scrub_json(b"".join(chunks))
                start = held.pop()
                MutableHeaders(scope=start)["content-length"] = str(len(body))
                await send(start)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            await send(message)

        marker = view_as.set_active(True)
        expiry = view_as.set_expiry(float(verified["exp"]) if verified and isinstance(verified.get("exp"), (int, float)) else None)
        try:
            await self.app(scope, receive, send_scrubbed)
        finally:
            view_as.reset_expiry(expiry)
            view_as.reset_active(marker)
            await self._record(scope, verified, method, path, status["code"], refused=blocked["on"], began=began)

    async def _record(self, scope: Any, claims: Optional[Dict[str, Any]], method: str, path: str, status: int, *, refused: bool,
                      began: datetime) -> None:
        if claims is None:
            return
        try:
            async with _db(scope.get("app")) as session:
                await view_as.record_request(session, int(claims["vs"]), method, path, status, refused, began)
        except Exception as exc:  # noqa: BLE001 - recording must never turn an answer into an error
            logger.warning("view session request not recorded (%s %s): %s", method, path, exc)
