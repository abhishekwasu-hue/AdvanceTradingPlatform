"""M55 Q1: moving the inline routes out of app/main.py and folding app/scanner into app/screener changed nothing a
caller meets. The fixture holds the 18 moved routes and every `/api/scanner*` route, captured from origin/main before
the move (tests/route_table_snapshot.py). They must still be served exactly as they were (path, methods, endpoint name,
status, response model and body schemas, parameters, every dependency, the gate, sign-in) and in the same order among
themselves. Every other route is free to change. Changing one of these on purpose means regenerating the fixture:
    python -m tests.route_table_snapshot > tests/fixtures/route_table.json
    python3 ../scripts/check_public_repo.py --hash backend/tests/fixtures/route_table.json   # its ALLOWED_TEST_DATA pin
"""
import importlib
import json
from pathlib import Path

from app.main import app
from tests.route_table_snapshot import MOVED, in_scope, route_table

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE = BACKEND / "tests" / "fixtures" / "route_table.json"


def _key(row: dict) -> str:
    return f"{','.join(row['methods'])} {row['path']}"


def test_moved_and_scanner_routes_are_served_as_before_in_the_same_order():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = [r for r in json.loads(json.dumps(route_table(app))) if in_scope(r)]   # the fixture's JSON round trip
    gone = sorted({_key(r) for r in expected} - {_key(r) for r in actual})
    new = sorted({_key(r) for r in actual} - {_key(r) for r in expected})
    assert not gone and not new, f"routes gone: {gone}; routes new: {new}"
    changed = [f"#{i} {_key(e)}: {sorted(k for k in e if e[k] != a.get(k)) or 'order'}"
               for i, (e, a) in enumerate(zip(expected, actual)) if e != a]
    assert not changed, "route(s) changed or moved in order:\n" + "\n".join(changed)


def test_the_fixture_holds_every_moved_route_in_its_old_order():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    moved = [(r["methods"][0], r["path"]) for r in expected if any((m, r["path"]) in MOVED for m in r["methods"])]
    assert moved == list(MOVED)


def test_main_defines_no_route_of_its_own():
    """M55 Q1: app/main.py only includes routers - no `@app.<method>` endpoint lives there any more."""
    source = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    for verb in ("get", "post", "put", "patch", "delete", "api_route", "websocket", "route"):
        assert f"@app.{verb}(" not in source, verb


def test_old_scanner_imports_are_the_moved_modules():
    """M55 Q1: app/scanner is folded into app/screener/scanner; every old import path is the same module object."""
    for name in ("ai", "engine", "models", "routes", "screenql"):
        assert importlib.import_module(f"app.scanner.{name}") is importlib.import_module(f"app.screener.scanner.{name}"), name
