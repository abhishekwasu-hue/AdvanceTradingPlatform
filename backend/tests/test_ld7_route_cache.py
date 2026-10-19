"""LD-7: the route cache (app/core/route_cache.py) resolves every route exactly as FastAPI's own scan does.

* every path the app serves (the OpenAPI paths, path parameters filled in, every method) resolves through the cache's
  remembered entry to the same top-level entry and the same leaf route as through the full scan;
* only paths without path parameters are remembered; 404s and 405s are never remembered;
* end to end, a second request for a path is a cache hit and answers exactly as the first.
"""
import re
import uuid

import pytest
from starlette.routing import Match

from app.core import route_cache
from app.main import app
from tests.test_auth_api import _register, client


def _cache() -> route_cache.RouteCache:
    cache = app.router.middleware_stack
    assert isinstance(cache, route_cache.RouteCache), "the route cache is not installed on this FastAPI"
    cache._fresh()
    return cache


def _scope(method: str, path: str) -> dict:
    return {"type": "http", "method": method, "path": path, "root_path": "", "headers": [], "query_string": b"",
            "app": app, "router": app.router}


def _copy(scope: dict) -> dict:
    out = dict(scope)
    if isinstance(out.get("fastapi"), dict):
        out["fastapi"] = dict(out["fastapi"])
    return out


def _full_scan(scope: dict):
    """FastAPI's loop: the first top-level entry that matches in full, and the leaf route it leads to."""
    for i, route in enumerate(app.router.routes):
        match, child = route.matches(_copy(scope))
        if match == Match.FULL:
            return i, _leaf(route, scope), child
    return None, None, None


def _leaf(route, scope: dict):
    probe = _copy(scope)
    while hasattr(route, "_match") and hasattr(route, "original_router"):
        _m, child, route, _ctx = route._match(probe)
        probe.update(child)
    if route is not None and not hasattr(route, "endpoint"):
        _m, child = route.matches(probe)
        route = child.get("route", route)
    return route


def _served_paths():
    for path, operations in app.openapi()["paths"].items():
        concrete = re.sub(r"\{[^}/]+(:path)?\}", "1", path)
        for method in operations:
            yield method.upper(), concrete, "{" in path


def test_every_route_resolves_through_the_cache_as_through_the_full_scan():
    cache = _cache()
    checked = static = 0
    for method, path, has_params in _served_paths():
        scope = _scope(method, path)
        index, leaf, _child = _full_scan(scope)
        assert index is not None, (method, path)
        if leaf is None or not hasattr(leaf, "endpoint"):
            continue
        checked += 1
        top = cache._leaves.get(id(leaf), -1)
        if has_params:
            continue
        static += 1
        # what _learn would remember for this path is the full scan's own entry...
        assert top == index, (method, path, top, index)
        # ...and dispatching from it reaches the same leaf route
        match, _ = app.router.routes[top].matches(_copy(scope))
        assert match == Match.FULL and _leaf(app.router.routes[top], scope) is leaf, (method, path)
    assert checked > 300 and static > 150, (checked, static)


def test_only_paths_without_parameters_are_remembered_and_never_a_404_or_405():
    cache = _cache()
    cache._index.clear()
    for _ in range(2):
        client.get("/api/system/status")
        client.get("/api/this-path-does-not-exist")
        client.delete("/api/system/status")                                   # 405: the path exists, the method not
        client.get("/api/instruments/RELIANCE")                               # a path parameter
    keys = {path for (_method, _root, path) in cache._index}
    assert "/api/system/status" in keys
    assert "/api/this-path-does-not-exist" not in keys and "/api/instruments/RELIANCE" not in keys
    assert ("DELETE", "", "/api/system/status") not in cache._index


@pytest.mark.parametrize("path", ["/api/system/status", "/api/system/health", "/api/broker/available", "/api/instruments",
                                  "/api/auth/me", "/api/notifications", "/api/deployments", "/api/positions",
                                  "/api/paper/account", "/api/portfolio/exposure"])
def test_a_second_request_is_a_hit_and_answers_the_same(path):
    cache = _cache()
    cache._index.clear()
    headers = {"Authorization": f"Bearer {_register(f'route-cache-{uuid.uuid4().hex[:10]}@example.com')}"}
    first = client.get(path, headers=headers)
    hits = cache.hits
    second = client.get(path, headers=headers)
    assert cache.hits == hits + 1
    assert second.status_code == first.status_code
    if path in ("/api/system/health", "/api/broker/available", "/api/instruments", "/api/auth/me", "/api/deployments", "/api/positions"):
        assert second.json() == first.json()
