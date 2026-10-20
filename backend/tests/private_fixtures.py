"""The private fixtures store: Trade-derived golden fixtures that stay out of this public repository (owner decision
PR-2; docs/PUBLIC_REPO_POLICY.md section 5).

The store is a directory named by the environment variable ATP_PRIVATE_FIXTURES. Each file keeps its repository path
under it, e.g. `$ATP_PRIVATE_FIXTURES/backend/tests/fixtures/zones/golden_trade_eafc872.json.gz`. The committed
manifest (tests/private_fixtures_manifest.json) holds only each file's path and sha256, never data. The store is filled
from git history with `python3 scripts/export_private_fixtures.py --to DIR`.

  * store not mounted (the variable unset): the test is skipped with a visible reason - the public CI; the parity tests
    run on the host or on a private runner that mounts the store read-only;
  * the variable set but the directory or the file missing: an error (PrivateFixtureMissing), never a skip, so a
    mistyped or empty store fails the run instead of passing green;
  * a file whose sha256 differs from the manifest: an error (PrivateFixtureMismatch), never a skip.

Nothing is read at import time: the loaders are called from tests and fixtures.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

ENV = "ATP_PRIVATE_FIXTURES"
REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().with_name("private_fixtures_manifest.json")
POLICY = "docs/PUBLIC_REPO_POLICY.md section 5"


class PrivateFixtureMismatch(RuntimeError):
    """A file in the store whose content is not the one the manifest pins."""


class PrivateFixtureMissing(RuntimeError):
    """ATP_PRIVATE_FIXTURES is set, but the store directory or a file in it is missing."""


def check_rel(rel: str) -> str:
    """A manifest path: relative, POSIX, no empty, '.' or '..' segment (it never leaves the store root)."""
    parts = rel.split("/")
    if not rel or "\\" in rel or PurePosixPath(rel).is_absolute() or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"{MANIFEST.name}: {rel!r} is not a plain relative path")
    return rel


@lru_cache(maxsize=1)
def manifest() -> Dict[str, Any]:
    with open(MANIFEST, encoding="utf-8") as fh:
        data = json.load(fh)
    for rel in data["files"]:
        check_rel(rel)
    return data


def files() -> Dict[str, str]:
    """Repository path -> sha256 of the file's bytes (as `sha256sum` prints it)."""
    return dict(manifest()["files"])


def store_root() -> Optional[Path]:
    root = os.environ.get(ENV, "").strip()
    return Path(root).expanduser() if root else None


def _pinned(rel: str) -> str:
    pinned = files().get(rel)
    if pinned is None:
        raise KeyError(f"{rel} is not in {MANIFEST.name} - a private fixture is listed there first")
    return pinned


def skip_reason(rel: str) -> str:
    return f"private fixture store not mounted ({ENV}): {rel} - runs on the host / private CI; see {POLICY}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def locate(rel: str) -> Optional[Path]:
    """The verified store file for a manifest path, or None when the store is not mounted (the variable unset). Raises
    PrivateFixtureMissing when the variable is set but the store or the file is missing, and PrivateFixtureMismatch
    when the file is there with other content."""
    pinned = _pinned(rel)
    root = store_root()
    if root is None:
        return None
    if not root.is_dir():
        raise PrivateFixtureMissing(f"{ENV}={root} is not a directory - mount or fill the store "
                                    f"(scripts/export_private_fixtures.py), or unset {ENV} to skip these tests")
    path = root / rel
    if not path.is_file():
        raise PrivateFixtureMissing(f"{path} is missing from the store named by {ENV} - refill it with "
                                    f"scripts/export_private_fixtures.py, or unset {ENV} to skip these tests")
    got = sha256_of(path)
    if got != pinned:
        raise PrivateFixtureMismatch(f"{path}: sha256 {got} but {MANIFEST.name} pins {pinned} - refill the store with "
                                     f"scripts/export_private_fixtures.py, or update the manifest in a reviewed change")
    return path


def path(rel: str) -> Path:
    """The verified store file; skips the calling test (with the reason) when the store is not mounted."""
    found = locate(rel)
    if found is None:
        import pytest
        pytest.skip(skip_reason(rel))
    return found


def read_json(file: Path) -> Any:
    """A fixture file: JSON, gzip-compressed when the name ends in .gz."""
    if file.suffix == ".gz":
        with gzip.open(file, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    with open(file, encoding="utf-8") as fh:
        return json.load(fh)


def load(rel: str) -> Any:
    """The fixture's JSON, verified; skips the calling test when the store is not mounted."""
    return read_json(path(rel))


def load_or_none(rel: str) -> Any:
    """The fixture's JSON, verified, or None when the store is not mounted (for module-level case lists); a set but
    broken store raises, as `locate` does."""
    found = locate(rel)
    return None if found is None else read_json(found)


def write_path(rel: str) -> Path:
    """Where a regeneration script writes a private fixture: its path in the store (the variable must be set). The new
    file's sha256 then replaces its line in the manifest, in a reviewed change."""
    _pinned(rel)
    root = store_root()
    if root is None:
        raise SystemExit(f"set {ENV} to the private fixtures store to write {rel} ({POLICY})")
    out = root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    return out
