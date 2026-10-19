"""LD-7: remember which top-level route answers a request path, so the router does not scan every route first.

FastAPI finds a request's route by asking each top-level route in order whether it matches; an included router answers
by scanning its own routes. With about 500 routes behind 98 top-level entries, a request for a route near the end
(`/api/system/status`, `/api/paper/account`) spent 1.5-3.5 ms in that scan - 12.7% of the API's CPU in the 500-user test
on the KVM 2 shape.

The scan's answer depends only on the request's type, method, root path and path (the route list being fixed), so it is
remembered per (method, root path, path): the index of the first top-level route that matched in full. On the next
request with that key, the routes before it - which did not match in full - are skipped; the remembered route is asked
again and, when it matches in full, is handled exactly as FastAPI's own loop handles its first full match (the code
below mirrors `APIRouter.app`'s full-match branch, which `install` checks in the installed FastAPI before switching on).
Anything else - no full match (404, 405), a route that no longer matches, websockets, lifespan, a changed route list -
goes through FastAPI's own loop unchanged.

Only paths that match a route without path parameters are remembered ("static" paths: `/api/auth/me`, not
`/api/trades/123`), so the cache holds at most one entry per (method, static path) the app serves.
"""
import inspect
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from starlette.routing import Match, Mount

logger = logging.getLogger(__name__)

# The full-match branch of the installed FastAPI's APIRouter.app that `_dispatch` reproduces; checked at install time.
_EXPECTED_BRANCH = (
    "match, child_scope = route.matches(scope)",
    "if match == Match.FULL:",
    "scope.update(child_scope)",
    "_route_selected(",
    "mount=isinstance(route, routing.Mount),",
    "await route.handle(scope, receive, send)",
)

Key = Tuple[str, str, str]


class RouteCache:
    def __init__(self, router: Any, route_selected: Callable[..., None], original: Callable[..., Any]) -> None:
        self.router = router
        self._route_selected = route_selected
        self._original = original
        self._index: Dict[Key, int] = {}
        self._leaves: Dict[int, int] = {}                 # id(leaf route) -> top-level index (-1: in several)
        self._routes_seen: Tuple[int, int] = (-1, -1)
        self.hits = 0
        self.misses = 0

    def _fresh(self) -> None:
        seen = (id(self.router.routes), len(self.router.routes))
        if seen != self._routes_seen:                     # first use, or routes added or replaced (tests): start again
            self._index.clear()
            self._leaves = self._leaf_map()
            self._routes_seen = seen

    def _leaf_map(self) -> Dict[int, int]:
        """Every leaf route -> the top-level entry it sits under (an included router, or the route itself). A leaf
        reachable under two entries (a router included twice) is marked -1 and never cached."""
        out: Dict[int, int] = {}

        def walk(route: Any, top: int, depth: int = 0) -> None:
            inner = getattr(route, "original_router", None)
            if inner is not None and depth < 20:
                for child in getattr(inner, "routes", []):
                    walk(child, top, depth + 1)
                return
            out[id(route)] = top if out.get(id(route), top) == top else -1

        for i, route in enumerate(self.router.routes):
            walk(route, i)
        return out

    def _learn(self, key: Key, scope: Dict[str, Any]) -> None:
        """After FastAPI's own loop handled the request: remember the top-level entry of the leaf it picked, when the
        path matched it without path parameters."""
        leaf = scope.get("route")
        if leaf is None or scope.get("path_params"):
            return
        fmt = getattr(leaf, "path_format", None)
        if not isinstance(fmt, str) or "{" in fmt:
            return
        top = self._leaves.get(id(leaf), -1)
        if top >= 0:
            self._index[key] = top

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._original(scope, receive, send)
            return
        self._fresh()
        key = (scope.get("method", ""), scope.get("root_path", ""), scope.get("path", ""))
        index = self._index.get(key)
        if index is not None and index < len(self.router.routes):
            if "router" not in scope:
                scope["router"] = self.router
            route = self.router.routes[index]
            match, child_scope = route.matches(scope)
            if match == Match.FULL:
                self.hits += 1
                scope.update(child_scope)
                self._route_selected(scope=scope, path=getattr(route, "path_format", None), mount=isinstance(route, Mount))
                await route.handle(scope, receive, send)
                return
            self._index.pop(key, None)
        self.misses += 1
        await self._original(scope, receive, send)
        self._learn(key, scope)


def install(app: Any) -> Optional[RouteCache]:
    """Put the cache in front of `app.router`'s own dispatch, when the installed FastAPI has the loop it mirrors."""
    try:
        import fastapi.routing as fastapi_routing
        router = app.router
        source = inspect.getsource(type(router).app)
        route_selected = getattr(fastapi_routing, "_route_selected", None)
        if route_selected is None or not all(part in source for part in _EXPECTED_BRANCH):
            logger.warning("route cache not installed: this FastAPI's router loop is not the one it mirrors")
            return None
        if getattr(router, "middleware_stack", None) != router.app:
            logger.warning("route cache not installed: the router has its own middleware")
            return None
        cache = RouteCache(router, route_selected, router.app)
        router.middleware_stack = cache
        return cache
    except Exception:  # noqa: BLE001 - never stop the app over an optimisation
        logger.exception("route cache not installed")
        return None
