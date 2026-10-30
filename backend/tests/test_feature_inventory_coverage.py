"""M63 I-2: docs/FEATURE_INVENTORY.md covers every FastAPI router prefix and every legacy endpoint in app/main.py.

Read from the source (AST), not from the running app, so it does not depend on how a FastAPI version stores included
routers. Each prefix / path must appear in the inventory in backticks (`/api/x`), so `/api/admin` is not satisfied by
`/api/admin/alerts`. A PR that adds a router or a legacy endpoint adds its row to the inventory.
"""
import ast
import re
from pathlib import Path

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


def legacy_paths() -> set[str]:
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


def listed() -> set[str]:
    return set(re.findall(r"`(/[^`\s]*)`", INVENTORY.read_text(encoding="utf-8")))


def test_the_scan_finds_the_routers():
    prefixes = router_prefixes()
    assert len(prefixes) >= 100                        # a broken scan must not pass vacuously
    assert "/api/admin/controls" in prefixes and "/api/screener" in prefixes
    assert "/api/backtest" in legacy_paths() and "/api/system/health" in legacy_paths()


def test_every_router_prefix_is_in_the_inventory():
    have = listed()
    missing = sorted(f"{p} ({f})" for p, f in router_prefixes().items() if p not in have)
    assert not missing, "add these router prefixes to docs/FEATURE_INVENTORY.md (in backticks): " + ", ".join(missing)


def test_every_legacy_endpoint_is_in_the_inventory():
    have = listed()
    missing = sorted(p for p in legacy_paths() if p not in have)
    assert not missing, "add these app/main.py endpoints to docs/FEATURE_INVENTORY.md (in backticks): " + ", ".join(missing)
