"""M55 Q1: the app's full route table, flattened in match order, as plain data - so a refactor that moves routes
between modules can be proven to change nothing a caller sees.

Each row holds what a request meets: the path, the methods, the endpoint's name, the status code, the response class,
the response model (its JSON schema, hashed), the request body (its JSON schema, hashed), the path / query / header /
cookie parameters, every dependency (qualified name, in order), the role / scope gate and whether a signed-in caller
is needed. The module an endpoint lives in is left out on purpose: that is what a move changes.

Regenerate the fixture (only when a route change is intended):
    python -m tests.route_table_snapshot > tests/fixtures/route_table.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.datastructures import DefaultPlaceholder
from pydantic import TypeAdapter

from tests.route_inventory import Route, _api_routes, authenticates, dependency_calls, gate_of


def _schema_hash(annotation: Any) -> str | None:
    if annotation is None:
        return None
    try:
        schema: Any = TypeAdapter(annotation).json_schema()
    except Exception:  # noqa: BLE001 - a type pydantic cannot describe: its plain name stands in
        schema = getattr(annotation, "__name__", str(annotation))
    text = json.dumps(schema, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _call_name(call: Any) -> str:
    if hasattr(call, "__qualname__"):
        return f"{call.__module__}.{call.__qualname__}"
    # A callable instance (e.g. FastAPI's HTTPBearer): its class and its plain settings, never its memory address.
    kind = type(call)
    plain = {k: v for k, v in sorted(vars(call).items()) if isinstance(v, (str, int, float, bool, type(None)))}
    return f"{kind.__module__}.{kind.__qualname__}{json.dumps(plain, sort_keys=True)}"


def _class_name(cls: Any) -> str:
    if isinstance(cls, DefaultPlaceholder):  # the app's default response class, not one the route chose
        return "default:" + getattr(cls.value, "__name__", str(cls.value))
    return getattr(cls, "__name__", str(cls))


def route_table(app: FastAPI) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for route in _api_routes(app.routes):
        methods = sorted(route.methods or ())
        dependant = route.dependant
        probe = Route(methods[0] if methods else "", route.path, route.endpoint, dependant)
        rows.append({
            "path": route.path,
            "methods": methods,
            "name": route.name,
            "status_code": route.status_code,
            "response_class": _class_name(route.response_class),
            "response_model": getattr(route.response_model, "__name__", None if route.response_model is None
                                      else str(route.response_model)),
            "response_schema": _schema_hash(route.response_model),
            "body": [[f.name, _schema_hash(f.field_info.annotation)] for f in dependant.body_params],
            "path_params": sorted(f.name for f in dependant.path_params),
            "query_params": sorted(f.name for f in dependant.query_params),
            "header_params": sorted(f.name for f in dependant.header_params),
            "cookie_params": sorted(f.name for f in dependant.cookie_params),
            "dependencies": [_call_name(c) for c in dependency_calls(dependant)],
            "gate": gate_of(probe),
            "authenticates": authenticates(probe),
            "include_in_schema": route.include_in_schema,
        })
    return rows


if __name__ == "__main__":
    from app.main import app as _app

    sys.stdout.write("[\n" + ",\n".join(json.dumps(row, sort_keys=True) for row in route_table(_app)) + "\n]\n")
