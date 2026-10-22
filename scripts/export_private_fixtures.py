#!/usr/bin/env python3
"""Fill the private fixtures store from git history (owner decision PR-2; docs/PUBLIC_REPO_POLICY.md section 5).

The Trade-derived golden fixtures left the tree of this public repository; their content stays in its history (owner
decision PR-4: no history rewrite). This writes each file listed in backend/tests/private_fixtures_manifest.json, as it
is at a commit that still had it, into DIR with the same repository-relative layout, and checks every file against the
manifest's sha256. The tests then read them with ATP_PRIVATE_FIXTURES=DIR (backend/tests/private_fixtures.py).

    python3 scripts/export_private_fixtures.py --list                    # what would be written (dry run, no writes)
    python3 scripts/export_private_fixtures.py --to /srv/atp/private-fixtures
    python3 scripts/export_private_fixtures.py --to DIR --from-commit <sha>    # another commit with the same content

The default commit is the manifest's `source_commit` (a main commit that has the files in its tree). A shallow
clone may lack it: `git fetch --unshallow` (or `git fetch origin <sha>`) first. An existing file in DIR with the right
content is left as it is; one with other content is refused unless --overwrite is given. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
MANIFEST = "backend/tests/private_fixtures_manifest.json"


def check_rel(rel: str) -> str:
    """A manifest path: relative, POSIX, no empty, '.' or '..' segment (it never leaves the store root)."""
    if not rel or "\\" in rel or PurePosixPath(rel).is_absolute() or any(p in ("", ".", "..") for p in rel.split("/")):
        raise SystemExit(f"export_private_fixtures: {MANIFEST}: {rel!r} is not a plain relative path")
    return rel


def load_manifest(repo: Path) -> Dict[str, object]:
    with open(repo / MANIFEST, encoding="utf-8") as fh:
        data = json.load(fh)
    for rel in data["files"]:
        check_rel(rel)
    return data


def blob(repo: Path, commit: str, rel: str) -> bytes:
    """The file's bytes at the commit (git cat-file: no checkout, no filters)."""
    done = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{rel}"], capture_output=True)
    if done.returncode != 0:
        msg = done.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"export_private_fixtures: {rel} not readable at {commit} ({msg}) - fetch the commit "
                         f"(git fetch --unshallow) or pass --from-commit")
    return done.stdout


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--to", metavar="DIR", help="the private fixtures store to fill (created if missing)")
    mode.add_argument("--list", action="store_true", help="dry run: list each file, its sha256 and whether the commit has it")
    parser.add_argument("--from-commit", metavar="SHA", help="the commit to read (default: the manifest's source_commit)")
    parser.add_argument("--overwrite", action="store_true", help="replace a file in DIR whose content differs")
    parser.add_argument("--repo", default=str(REPO), help="the repository (default: this script's repository)")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    manifest = load_manifest(repo)
    files: Dict[str, str] = dict(manifest["files"])          # type: ignore[arg-type]
    commit = args.from_commit or str(manifest["source_commit"])

    if args.list:
        print(f"from {commit}, {len(files)} file(s):")
        bad = 0
        for rel, pinned in sorted(files.items()):
            got = hashlib.sha256(blob(repo, commit, rel)).hexdigest()
            state = "ok" if got == pinned else "SHA256 MISMATCH"
            bad += got != pinned
            print(f"  {rel}  {pinned}  {state}")
        return 1 if bad else 0

    out = Path(args.to).expanduser().resolve()
    if out == repo or repo in out.parents:
        raise SystemExit(f"export_private_fixtures: {out} is inside the repository - the store lives outside it")
    written = kept = 0
    for rel, pinned in sorted(files.items()):
        data = blob(repo, commit, rel)
        got = hashlib.sha256(data).hexdigest()
        if got != pinned:
            raise SystemExit(f"export_private_fixtures: {rel} at {commit} has sha256 {got}, the manifest pins {pinned}")
        dest = out / rel
        if dest.is_file():
            if hashlib.sha256(dest.read_bytes()).hexdigest() == pinned:
                kept += 1
                continue
            if not args.overwrite:
                raise SystemExit(f"export_private_fixtures: {dest} exists with other content (--overwrite replaces it)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != pinned:
            raise SystemExit(f"export_private_fixtures: {dest} does not read back with the pinned sha256")
        written += 1
    print(f"export_private_fixtures: {written} written, {kept} already in place, under {out} (from {commit}); "
          f"run the tests with ATP_PRIVATE_FIXTURES={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
