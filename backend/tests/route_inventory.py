"""Z10-c (G6): every HTTP route the FastAPI app serves, flattened, with what guards it.

FastAPI 0.14x keeps included routers lazily (`_IncludedRouter`), so `app.routes` alone lists only the app's own routes;
older versions flatten them. `iter_api_routes` handles both, so the sweep sees every route whichever version CI
installs (requirements pin `fastapi>=0.110,<1.0`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    endpoint: Callable
    dependant: Dependant

    @property
    def key(self) -> Tuple[str, str]:
        return (self.method, self.path)

    @property
    def mutating(self) -> bool:
        return self.method in MUTATING


def _api_routes(routes) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "effective_route_contexts"):
            for context in route.effective_route_contexts():
                original = context.original_route
                if isinstance(original, APIRoute):
                    assert original.path == context.path, "an include_router(prefix=...) would need the context path"
                    yield original


def iter_api_routes(app: FastAPI, prefix: str = "/api") -> List[Route]:
    found = []
    for route in _api_routes(app.routes):
        if route.path.startswith(prefix):
            for method in sorted(route.methods or ()):
                if method not in ("HEAD", "OPTIONS"):
                    found.append(Route(method, route.path, route.endpoint, route.dependant))
    return found


def dependency_calls(dependant: Dependant) -> Iterator[Callable]:
    for dep in dependant.dependencies:
        if dep.call is not None:
            yield dep.call
        yield from dependency_calls(dep)


def _closure(call: Callable) -> dict:
    code = getattr(call, "__code__", None)
    cells = getattr(call, "__closure__", None) or ()
    return dict(zip(code.co_freevars, (c.cell_contents for c in cells))) if code is not None else {}


def gate_of(route: Route) -> Optional[str]:
    """How a route checks the caller's role or scope in its dependencies (None when it does not):
    `role:OWNER,USER,... scope:trading:write` for `require_role(...)`, `scope:x` for `require_scope(x)`,
    `api_key:x` for a public API key scope, `admin` for SUPER_ADMIN only."""
    gates = []
    for call in dependency_calls(route.dependant):
        name = getattr(call, "__qualname__", "")
        module = getattr(call, "__module__", "")
        cells = _closure(call)
        if module == "app.auth.dependencies" and name.startswith("require_role."):
            roles = sorted(getattr(r, "value", r) for r in cells.get("allowed", ()))
            scope = cells.get("scope")
            gates.append(("admin" if not roles else "role:" + ",".join(roles)) + (f" scope:{scope}" if scope else ""))
        elif module == "app.auth.scopes" and name.startswith("require_scope."):
            gates.append(f"scope:{cells.get('scope')}")
        elif module == "app.public_api.keys" and name.startswith("api_key_auth."):
            gates.append(f"api_key:{cells.get('scope')}")
    return "; ".join(sorted(set(gates))) or None


def authenticates(route: Route) -> bool:
    """Whether a dependency requires a logged-in user or an API key (an optional user does not count)."""
    for call in dependency_calls(route.dependant):
        name = f"{getattr(call, '__module__', '')}.{getattr(call, '__qualname__', '')}"
        if name in ("app.auth.dependencies.get_current_user", "app.auth.dependencies.require_mfa_session") \
                or name.startswith(("app.auth.dependencies.require_role.", "app.auth.scopes.require_scope.", "app.public_api.keys.api_key_auth.")):
            return True
    return False
