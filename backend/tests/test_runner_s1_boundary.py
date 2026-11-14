"""Runner S1 (docs/design/USER_RUNNER.md, step S1): the runner's module boundary and the local SQLite spike.

1. Boundary (static, AST): from `app.runner.manifest.RUNNER_MODULES`, every module reached by module-level imports (the
   closure that ships) is walked; every import in it - module level or inside a function - of a forbidden module must
   be one of the reviewed `KNOWN_EDGES` (and every known edge must still exist, so the list stays exact). No module in the
   closure names the private package in a dynamic import, and the runner package imports only the manifest's modules.
2. Spike (a separate process, tests/runner_s1_spike_job.py): the one entry pipeline - a DSL strategy's Signal ->
   execute_signal_for_user (PAPER) -> the position monitor's close on the stop - runs on a local SQLite FILE made from
   the models, with Redis at a closed port and no Postgres; the process never loads a `RUNTIME_FORBIDDEN` module.
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.runner.manifest import FORBIDDEN_PREFIXES, KNOWN_EDGES, RUNNER_MODULES, RUNTIME_FORBIDDEN, is_forbidden

BACKEND = Path(__file__).resolve().parents[1]


def _path_of(module: str) -> Path | None:
    base = BACKEND.joinpath(*module.split("."))
    if (base / "__init__.py").exists():
        return base / "__init__.py"
    return base.with_suffix(".py") if base.with_suffix(".py").exists() else None


def _imports(module: str) -> list[tuple[str, bool, ast.AST]]:
    """(imported module, lazy = inside a function, node) for every import statement of `module`, relative ones resolved;
    `from a import b` also yields `a.b` when b is a submodule."""
    path = _path_of(module)
    assert path is not None, module
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    found: list[tuple[str, bool, ast.AST]] = []

    def visit(node: ast.AST, lazy: bool) -> None:
        for child in ast.iter_child_nodes(node):
            inner = lazy or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(child, ast.Import):
                found.extend((alias.name, inner, child) for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                base = child.module or ""
                if child.level:
                    parts = package.split(".")[: len(package.split(".")) - (child.level - 1)]
                    base = ".".join(parts + ([child.module] if child.module else []))
                found.append((base, inner, child))
                found.extend((f"{base}.{alias.name}", inner, child) for alias in child.names if _path_of(f"{base}.{alias.name}"))
            visit(child, inner)

    visit(ast.parse(path.read_text()), False)
    return found


def _closure() -> tuple[set[str], set[tuple[str, str, str]]]:
    """(modules shipped: the module-level import closure of the manifest, forbidden edges found in them)."""
    seen: set[str] = set()
    edges: set[tuple[str, str, str]] = set()
    stack = list(RUNNER_MODULES)
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        for name, lazy, _node in _imports(module):
            if is_forbidden(name):
                edges.add((module, name, "lazy" if lazy else "top"))
            elif name.startswith("app.") and not lazy and _path_of(name):
                stack.append(name)
    return seen, edges


CLOSURE, EDGES = _closure()


def test_every_manifest_module_exists_and_none_is_forbidden():
    assert len(RUNNER_MODULES) == len(set(RUNNER_MODULES))
    for module in RUNNER_MODULES:
        assert _path_of(module) is not None, module
        assert not is_forbidden(module), module
    assert "app.brokers.angel_one" in RUNNER_MODULES                         # RUN-Q3: the first broker of the runner


def test_the_shipped_closure_reaches_no_forbidden_module_beyond_the_reviewed_edges():
    assert not any(is_forbidden(m) for m in CLOSURE)
    new = sorted(EDGES - set(KNOWN_EDGES))
    assert not new, f"new import of a forbidden module in the runner's closure (review it, then list it in KNOWN_EDGES): {new}"
    gone = sorted(set(KNOWN_EDGES) - EDGES)
    assert not gone, f"KNOWN_EDGES lists an import that no longer exists (remove it): {gone}"
    for (importer, imported, kind), why in KNOWN_EDGES.items():
        assert importer in CLOSURE and kind in ("top", "lazy") and why.strip(), (importer, imported)


def test_the_private_package_and_its_loaders_are_never_imported_at_module_level():
    private = ("atp_private", "app.engine_provider", "app.chart_reader", "app.chart_analyze", "app.ai", "app.admin",
               "app.marketplace", "app.lab", "app.workers", "app.main")
    top = [e for e in EDGES if e[2] == "top" and is_forbidden(e[1], private)]
    assert top == []


def test_no_module_in_the_closure_imports_the_private_package_by_name():
    for module in CLOSURE:
        tree = ast.parse(_path_of(module).read_text())   # type: ignore[union-attr]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        assert not arg.value.startswith("atp_private"), (module, node.lineno)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                assert not node.value.value.startswith("atp_private"), (module, node.lineno)


def test_the_runner_package_imports_only_its_manifest_and_the_server_never_imports_it():
    for path in (BACKEND / "app" / "runner").glob("*.py"):
        module = "app.runner" if path.name == "__init__.py" else f"app.runner.{path.stem}"
        for name, _lazy, _node in _imports(module):
            assert not name.startswith("app.") or name in RUNNER_MODULES or name.startswith("app.runner"), (path.name, name)
    for path in (BACKEND / "app").rglob("*.py"):
        if "runner" in path.relative_to(BACKEND / "app").parts[:1]:
            continue
        assert "app.runner" not in path.read_text(), path


def test_the_forbidden_lists_agree():
    assert set(RUNTIME_FORBIDDEN) <= set(FORBIDDEN_PREFIXES)


@pytest.fixture(scope="module")
def spike(tmp_path_factory) -> dict:
    db = tmp_path_factory.mktemp("runner") / "runner.db"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MIGRATION_DATABASE_URL", "REDIS_URL"))}
    env.update(DATABASE_URL=f"sqlite+aiosqlite:///{db}", REDIS_URL="redis://127.0.0.1:1/0", PYTHONPATH=str(BACKEND),
               ATP_RUNNER_MODE="1")   # S2: runner mode (billing meter off, the ban list from ATP)
    run = subprocess.run([sys.executable, "tests/runner_s1_spike_job.py"], cwd=BACKEND, env=env, capture_output=True,
                         text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    line = next(x for x in run.stdout.splitlines() if x.startswith("RUNNER_SPIKE "))
    report = json.loads(line[len("RUNNER_SPIKE "):])
    report["file"] = db
    return report


def test_the_entry_pipeline_runs_on_a_local_sqlite_file_without_postgres_or_redis(spike):
    assert spike["dialect"] == "sqlite" and Path(spike["database"]) == spike["file"] and spike["file"].stat().st_size > 0
    assert spike["signal"] == "LONG" and spike["stop"] < spike["entry"]
    assert spike["executed"] is True and spike["order_mode"] == "PAPER" and spike["order_status"] == "POSITION_OPEN"
    assert spike["trades_open"] == 1


def test_the_one_close_path_closes_the_position_on_its_stop(spike):
    assert spike["closed"] == 1 and spike["trades_closed"] == 1 and spike["exit_reason"] == "Stop Loss"


def test_the_pipeline_process_never_loads_a_forbidden_module(spike):
    loaded = [m for m in spike["loaded"] if is_forbidden(m, RUNTIME_FORBIDDEN)]
    assert loaded == []
    assert "app.execution.signal_execution" in spike["loaded"] and "app.trading.position_monitor" in spike["loaded"]
