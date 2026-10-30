"""M55 Q1: moving the inline routes out of app/main.py and folding app/scanner into app/screener changed nothing a
caller meets. The fixture is the route table captured from origin/main before the move (tests/route_table_snapshot.py).

Every route in it must still be served exactly as it was (path, methods, endpoint name, status, response model and
body schemas, parameters, every dependency, the gate, sign-in) and in the same match order. A route added later is
allowed; changing or removing one of these on purpose means regenerating the fixture in that PR:
    python -m tests.route_table_snapshot > tests/fixtures/route_table.json
    python3 ../scripts/check_public_repo.py --hash backend/tests/fixtures/route_table.json   # its ALLOWED_TEST_DATA pin
"""
import json
from pathlib import Path

from app.main import app
from tests.route_table_snapshot import route_table

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE = BACKEND / "tests" / "fixtures" / "route_table.json"

# The routes M55 Q1 moved out of app/main.py, in their old order (they stay last in the table, as before).
MOVED = [
    ("GET", "/api/strategies"), ("GET", "/api/strategies/{strategy_id}"), ("POST", "/api/strategies/{strategy_id}/signal"),
    ("POST", "/api/strategies/{strategy_id}/signal/enrich"), ("POST", "/api/strategies/{strategy_id}/paper-execute"),
    ("POST", "/api/backtest"), ("POST", "/api/price-action/structure"), ("POST", "/api/price-action/patterns"),
    ("POST", "/api/price-action/reversal"), ("POST", "/api/price-action/reversal-markers"),
    ("POST", "/api/support-resistance/zones"), ("POST", "/api/option-chain/analyze"), ("POST", "/api/option-chain/greeks"),
    ("POST", "/api/scanner/run"), ("GET", "/api/broker/available"), ("GET", "/api/instruments"),
    ("GET", "/api/instruments/{symbol}"), ("GET", "/api/system/health"),
]


def _key(row: dict) -> str:
    return f"{','.join(row['methods'])} {row['path']}"


def _rows() -> tuple:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(route_table(app)))   # the same JSON round trip the fixture went through
    return expected, actual


def test_every_route_is_served_as_before_in_the_same_order():
    expected, actual = _rows()
    by_key: dict = {}
    for i, row in enumerate(actual):
        by_key.setdefault(_key(row), []).append((i, row))
    problems, last = [], -1
    for row in expected:
        found = [(i, r) for i, r in by_key.get(_key(row), []) if r == row]
        if not found:
            near = by_key.get(_key(row))
            fields = sorted(k for k in row if near and row[k] != near[0][1].get(k)) if near else "gone"
            problems.append(f"{_key(row)}: {fields}")
            continue
        later = [i for i, _ in found if i > last]
        if not later:
            problems.append(f"{_key(row)}: now matched before a route it used to follow")
            continue
        last = later[0]
    assert not problems, "route(s) changed, gone or moved in match order:\n" + "\n".join(problems[:30])


def test_the_fixture_ends_with_the_moved_routes():
    """The fixture is the pre-move table: the 18 inline routes were matched last, in this order (the order test above
    keeps them so)."""
    expected, _ = _rows()
    assert [(r["methods"][0], r["path"]) for r in expected[-len(MOVED):]] == MOVED


def test_main_defines_no_route_of_its_own():
    """M55 Q1: app/main.py only includes routers - no `@app.<method>` endpoint lives there any more."""
    source = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    for verb in ("get", "post", "put", "patch", "delete", "api_route", "websocket", "route"):
        assert f"@app.{verb}(" not in source, verb


def test_old_scanner_imports_are_the_moved_modules():
    """M55 Q1: app/scanner is folded into app/screener/scanner; every old import path is the same module object."""
    import importlib

    for name in ("ai", "engine", "models", "routes", "screenql"):
        assert importlib.import_module(f"app.scanner.{name}") is importlib.import_module(f"app.screener.scanner.{name}"), name
