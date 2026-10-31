"""M63 I-2: docs/FEATURE_INVENTORY.md covers every FastAPI router prefix and every endpoint moved out of app/main.py.

Read from the source (AST), not from the running app, so it does not depend on how a FastAPI version stores included
routers. Each prefix / path must appear in the inventory in backticks (`/api/x`), so `/api/admin` is not satisfied by
`/api/admin/alerts`. A PR that adds a router adds its row to the inventory.

M55 Q1 moved the 18 endpoints that lived inline in app/main.py into module routers: main.py must stay free of inline
endpoints, and each moved path must be listed on an inventory row that names the router file that now serves it.
"""
import ast
import re
from pathlib import Path

from tests.route_table_snapshot import MOVED

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
INVENTORY = BACKEND.parent / "docs" / "FEATURE_INVENTORY.md"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def router_prefixes() -> dict[str, str]:
    """{prefix: file} for every APIRouter(prefix=...) and include_router(..., prefix=...) under app/."""
    found: dict[str, str] = {}
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _call_name(node) in ("APIRouter", "include_router"):
                for kw in node.keywords:
                    if kw.arg == "prefix":
                        assert isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str), \
                            f"{path.relative_to(BACKEND)}: a router prefix must be a string literal (the inventory test reads it)"
                        if kw.value.value:
                            found.setdefault(kw.value.value, str(path.relative_to(BACKEND)))
    return found


def main_inline_paths() -> set[str]:
    """Paths of the endpoints declared directly on `app` in app/main.py (@app.get("/api/...") and the like)."""
    out: set[str] = set()
    for node in ast.walk(ast.parse((APP / "main.py").read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in HTTP_METHODS
                        and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                        and dec.args and isinstance(dec.args[0], ast.Constant)):
                    out.add(dec.args[0].value)
    return out


def router_paths() -> dict[str, set[str]]:
    """{full path: {files}} for every `@<router>.<method>("...")` under app/, with its APIRouter's prefix in front."""
    found: dict[str, set[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prefix: dict[str, str] = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and _call_name(node.value) == "APIRouter"):
                value = next((kw.value.value for kw in node.value.keywords if kw.arg == "prefix"
                              and isinstance(kw.value, ast.Constant)), "")
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefix[target.id] = value
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                            and dec.func.attr in HTTP_METHODS and isinstance(dec.func.value, ast.Name)
                            and dec.func.value.id in prefix and dec.args and isinstance(dec.args[0], ast.Constant)):
                        full = prefix[dec.func.value.id] + dec.args[0].value
                        found.setdefault(full, set()).add(str(path.relative_to(BACKEND.parent)))
    return found


def listed() -> set[str]:
    return set(re.findall(r"`(/[^`\s]*)`", INVENTORY.read_text(encoding="utf-8")))


def moved_paths() -> list[str]:
    return list(dict.fromkeys(path for _, path in MOVED))


def test_the_scan_finds_the_routers():
    prefixes = router_prefixes()
    assert len(prefixes) >= 100                        # a broken scan must not pass vacuously
    assert "/api/admin/controls" in prefixes and "/api/screener" in prefixes
    paths = router_paths()
    assert paths["/api/backtest"] == {"backend/app/backtest/console_routes.py"}
    assert paths["/api/system/health"] == {"backend/app/platform/health_routes.py"}
    assert paths["/api/scanner/ai/plan"] == {"backend/app/screener/scanner/routes.py"}   # a prefixed router
    assert len(moved_paths()) == 18


def test_main_declares_no_inline_endpoint():
    """M55 Q1: app/main.py only includes routers; a new endpoint goes into its module's router."""
    assert not main_inline_paths(), "move these app/main.py endpoints into a module router: " + ", ".join(sorted(main_inline_paths()))


def test_every_router_prefix_is_in_the_inventory():
    have = listed()
    missing = sorted(f"{p} ({f})" for p, f in router_prefixes().items() if p not in have)
    assert not missing, "add these router prefixes to docs/FEATURE_INVENTORY.md (in backticks): " + ", ".join(missing)


def test_every_moved_endpoint_is_listed_under_the_router_that_serves_it():
    rows = INVENTORY.read_text(encoding="utf-8").splitlines()
    paths = router_paths()
    problems = []
    for path in moved_paths():
        files = paths.get(path, set())
        if len(files) != 1:
            problems.append(f"{path}: served by {sorted(files) or 'no router'} (expected exactly one router file)")
            continue
        (file,) = files
        if not any(f"`{path}`" in row and f"`{file}`" in row for row in rows):
            problems.append(f"{path}: no inventory row lists it together with `{file}`")
    assert not problems, "docs/FEATURE_INVENTORY.md section 3:\n" + "\n".join(problems)
