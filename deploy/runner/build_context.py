"""Runner S6: the runner image's build context, generated from the runner manifest (backend/app/runner/manifest.py).

The runner image never sees the backend folder. This script copies into a fresh folder ONLY:
  * the runner's module set: `app.runner.*` and the manifest's `RUNNER_MODULES`, with everything they import
    (module-level and inside functions, followed to any depth) EXCEPT the forbidden packages, which are never copied -
    the private package, its loaders, AI, admin, marketplace, Lab, the server's worker / app, billing and the lake
    (`FORBIDDEN_PREFIXES`). An import of a forbidden module is not followed; in the image it is physically absent, so
    a code path that reaches one gets an ImportError (the admin flag list reads that as "unavailable", S1 open item);
  * the data files of the packages that ship (rules as data, instrument lists, ...);
  * the runner's dependency lock (`deploy/runner/requirements.lock`: a subset of backend/requirements.lock, same pins
    and hashes, only the packages the runner needs) and the Dockerfile.

    python deploy/runner/build_context.py --out /tmp/runner-ctx     # then: docker build /tmp/runner-ctx
    python deploy/runner/build_context.py --check                   # the runner lock is current (CI)
    python deploy/runner/build_context.py --write-lock              # regenerate the runner lock from the backend lock

The tests (backend/tests/test_runner_s6_image.py) build the context and assert that no forbidden package, no
`atp_private`, no `.env` / key file is in it, and that the Dockerfile, the lock and the manifest agree.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from app.runner.manifest import FORBIDDEN_PREFIXES, RUNNER_MODULES, is_forbidden  # noqa: E402

BACKEND_LOCK = BACKEND / "requirements.lock"
RUNNER_LOCK = HERE / "requirements.lock"

# The runner's direct third-party dependencies (distribution names as in backend/requirements.txt). Their own
# dependencies come from the backend lock's "# via" lines. Not here on purpose: the web server (uvicorn), the server's
# database driver (asyncpg, the runner uses its own SQLite file), AI SDKs, image / PDF / Parquet / S3 libraries,
# the error tracker and tracing exporters, test tools. A module-level import of anything else in the shipped set fails
# the consistency test.
RUNNER_REQUIREMENTS: tuple[str, ...] = (
    "aiosqlite", "bcrypt", "cffi", "cryptography", "fastapi", "httpx", "numpy", "pandas", "pydantic",
    "pyjwt", "pyotp", "python-dotenv", "python-multipart", "prometheus-client", "sqlalchemy", "websockets",
    "redis", "defusedxml",   # module-level imports of shipped modules (the cache client answers "no Redis" at once)
    "email-validator", "greenlet",   # extras the backend asks for: pydantic[email], sqlalchemy[asyncio]
)

# import name -> distribution name, where they differ (for the consistency test)
IMPORT_TO_DIST: dict[str, str] = {
    "jwt": "pyjwt", "dotenv": "python-dotenv", "multipart": "python-multipart", "python_multipart": "python-multipart",
    "prometheus_client": "prometheus-client", "email_validator": "email-validator", "dateutil": "python-dateutil",
    "yaml": "pyyaml", "PIL": "pillow", "google": "protobuf", "typing_extensions": "typing-extensions",
}

# never in the context, whatever the closure says (defence in depth on top of the forbidden list)
NEVER_NAMES = (".env", "id_rsa", "id_ed25519", "atp_private", "__pycache__")
NEVER_SUFFIXES = (".pem", ".key", ".pyc", ".db", ".sqlite", ".env")


def path_of(module: str) -> Path | None:
    base = BACKEND.joinpath(*module.split("."))
    if (base / "__init__.py").exists():
        return base / "__init__.py"
    return base.with_suffix(".py") if base.with_suffix(".py").exists() else None


def imports_of(module: str) -> set[str]:
    """Every module `module` imports (module level and inside functions; relative imports resolved; `from a import b`
    also yields `a.b` when b is a submodule; `import_module("app.x")` with a literal name)."""
    path = path_of(module)
    assert path is not None, module
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            found.add(base)
            found.update(f"{base}.{a.name}" for a in node.names if path_of(f"{base}.{a.name}"))
        elif (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "import_module"
              and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
    return found


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return handler.type is None or any(getattr(n, "id", "") in {"ImportError", "ModuleNotFoundError", "Exception"}
                                       for n in names)


def top_level_imports_of(module: str) -> set[str]:
    """Third-party / stdlib top names imported at module level (outside any function) by `module`."""
    tree = ast.parse(path_of(module).read_text())  # type: ignore[union-attr]
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Try) and any(_catches_import_error(h) for h in child.handlers):
                # an optional import (`try: import x` / `except ImportError`) is not required
                for stmt in child.orelse + child.finalbody:
                    visit(stmt)
                continue
            if isinstance(child, ast.Import):
                names.update(a.name.split(".")[0] for a in child.names)
            elif isinstance(child, ast.ImportFrom) and not child.level and child.module:
                names.add(child.module.split(".")[0])
            visit(child)

    visit(tree)
    return names


def runner_roots() -> list[str]:
    runner = BACKEND / "app" / "runner"
    return sorted({"app.runner." + p.stem for p in runner.glob("*.py") if p.stem != "__init__"} | {"app.runner"}) + list(
        RUNNER_MODULES)


def closure() -> tuple[set[str], set[tuple[str, str]]]:
    """(modules that ship, (importer, forbidden module) edges that are NOT followed)."""
    seen: set[str] = set()
    cut: set[tuple[str, str]] = set()
    stack = runner_roots()
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        parent = module.rsplit(".", 1)[0] if "." in module else None
        if parent and parent not in seen:   # a package's __init__ runs before its submodules
            stack.append(parent)
        for name in imports_of(module):
            if not (name == "app" or name.startswith("app.")):
                continue
            if is_forbidden(name):
                cut.add((module, name))
            elif path_of(name):
                stack.append(name)
    assert not any(is_forbidden(m) for m in seen)
    return seen, cut


def _allowed_file(path: Path) -> bool:
    return not (path.name in NEVER_NAMES or path.suffix in NEVER_SUFFIXES or any(p in NEVER_NAMES for p in path.parts))


def shipped_files(modules: set[str]) -> list[Path]:
    """Backend-relative files: each module's .py and the data files of each shipped package (its folder's non-Python
    files and its data sub-folders, i.e. sub-folders that are not packages)."""
    files: set[Path] = set()
    for module in modules:
        path = path_of(module)
        assert path is not None, module
        files.add(path)
        if path.name != "__init__.py":
            continue
        folder = path.parent
        for item in folder.iterdir():
            if item.is_file() and item.suffix != ".py":
                files.add(item)
            elif item.is_dir() and not (item / "__init__.py").exists() and item.name != "__pycache__":
                files.update(f for f in item.rglob("*") if f.is_file())
    out = sorted(f.relative_to(BACKEND) for f in files if _allowed_file(f.relative_to(BACKEND)))
    assert not any(is_forbidden(".".join(f.with_suffix("").parts)) for f in out)
    return out


# --- the runner lock: a subset of the backend lock ---------------------------------------------------------------------

_HEAD = re.compile(r"^([A-Za-z0-9_.\-]+)==")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text: str) -> dict[str, tuple[str, set[str]]]:
    """{distribution: (its whole block of lines, the packages it is 'via')} from a uv lock."""
    blocks: dict[str, tuple[str, set[str]]] = {}
    current: str | None = None
    lines: list[str] = []
    via: set[str] = set()

    def flush() -> None:
        if current:
            blocks[current] = ("\n".join(lines).rstrip() + "\n", via)

    for raw in text.splitlines():
        head = _HEAD.match(raw)
        if head:
            flush()
            current, lines, via = _norm(head.group(1)), [raw], set()
            continue
        if current is None:
            continue
        lines.append(raw)
        stripped = raw.strip().lstrip("#").strip()
        if raw.strip().startswith("#"):
            stripped = stripped.removeprefix("via").strip()
            if stripped:
                via.add("-r" if stripped.startswith("-r") else _norm(stripped.split()[0]))
    flush()
    return blocks


def runner_lock_text() -> str:
    blocks = parse_lock(BACKEND_LOCK.read_text())
    keep = {_norm(n) for n in RUNNER_REQUIREMENTS}
    missing = keep - set(blocks)
    assert not missing, f"not in backend/requirements.lock: {sorted(missing)}"
    changed = True
    while changed:   # every dependency of a kept package (the lock's "# via <kept>")
        changed = False
        for name, (_block, via) in blocks.items():
            if name not in keep and via & keep:
                keep.add(name)
                changed = True
    header = ("# Runner S6: generated by `python deploy/runner/build_context.py --write-lock` from backend/requirements.lock\n"
              "# (the same pins and hashes; only the runner's packages). Do not edit by hand.\n")
    return header + "".join(blocks[n][0] for n in sorted(keep))


def stage(out: Path) -> dict:
    """Write the build context into `out` (which must be empty or absent)."""
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    modules, cut = closure()
    files = shipped_files(modules)
    for rel in files:
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BACKEND / rel, dest)
    shutil.copy2(HERE / "Dockerfile", out / "Dockerfile")
    shutil.copy2(RUNNER_LOCK, out / "requirements.lock")
    (out / "FORBIDDEN.txt").write_text("\n".join(FORBIDDEN_PREFIXES) + "\n")   # the image re-checks itself at build
    (out / "MODULES.txt").write_text("\n".join(sorted(modules)) + "\n")
    return {"modules": len(modules), "files": len(files), "cut_edges": sorted(cut)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--check", action="store_true", help="fail when deploy/runner/requirements.lock is not current")
    ap.add_argument("--write-lock", action="store_true")
    args = ap.parse_args(argv)
    if args.write_lock:
        RUNNER_LOCK.write_text(runner_lock_text())
        print(f"wrote {RUNNER_LOCK.relative_to(REPO)}")
    if args.check:
        if not RUNNER_LOCK.exists() or RUNNER_LOCK.read_text() != runner_lock_text():
            print("deploy/runner/requirements.lock is out of date: python deploy/runner/build_context.py --write-lock")
            return 1
        print("runner lock current")
    if args.out:
        report = stage(args.out)
        print(f"runner build context: {report['modules']} modules, {report['files']} files -> {args.out}")
        cut: dict[str, set[str]] = {}
        for importer, target in report["cut_edges"]:
            prefix = next(p for p in FORBIDDEN_PREFIXES if target == p or target.startswith(p + "."))
            cut.setdefault(prefix, set()).add(importer)
        for prefix, importers in sorted(cut.items()):
            print(f"  not shipped: {prefix} (imported by {len(importers)} shipped modules; absent in the image)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
