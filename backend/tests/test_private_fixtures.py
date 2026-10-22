"""The private fixtures resolver (tests/private_fixtures.py) and the store export (scripts/export_private_fixtures.py),
on made-up files in tmp_path - never the real fixtures (owner decision PR-2, docs/PUBLIC_REPO_POLICY.md section 5).

Unset store: skip with the reason. Set store with the directory or the file missing: an error. Other content than the
manifest pins: an error. Manifest paths stay inside the store. The export refuses a target inside the repository and a
file whose content is not the pinned one."""
import gzip
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from tests import private_fixtures as PF

REPO = Path(__file__).resolve().parents[2]
REL = "backend/tests/fixtures/made_up/golden.json"
BODY = b'{"cases": [1, 2, 3]}\n'
GZ_REL = "backend/tests/fixtures/made_up/golden.json.gz"
GZ_BODY = gzip.compress(BODY, mtime=0)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"source_commit": "HEAD", "files": {REL: _sha(BODY), GZ_REL: _sha(GZ_BODY)}}), encoding="utf-8")
    monkeypatch.setattr(PF, "MANIFEST", path)
    monkeypatch.delenv(PF.ENV, raising=False)
    PF.manifest.cache_clear()
    yield path
    PF.manifest.cache_clear()


def _store(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


def test_unset_store_skips_with_the_reason(manifest):
    assert PF.locate(REL) is None and PF.load_or_none(REL) is None
    with pytest.raises(pytest.skip.Exception) as skipped:
        PF.load(REL)
    assert str(skipped.value) == (f"private fixture store not mounted (ATP_PRIVATE_FIXTURES): {REL} - runs on the host / "
                                  f"private CI; see docs/PUBLIC_REPO_POLICY.md section 5")


def test_a_mounted_store_is_read_and_verified(manifest, tmp_path, monkeypatch):
    store = _store(tmp_path / "store", {REL: BODY, GZ_REL: GZ_BODY})
    monkeypatch.setenv(PF.ENV, str(store))
    assert PF.path(REL) == store / REL
    assert PF.load(REL) == PF.load(GZ_REL) == PF.load_or_none(REL) == {"cases": [1, 2, 3]}


def test_other_content_is_an_error_not_a_skip(manifest, tmp_path, monkeypatch):
    store = _store(tmp_path / "store", {REL: BODY + b" "})
    monkeypatch.setenv(PF.ENV, str(store))
    with pytest.raises(PF.PrivateFixtureMismatch):
        PF.load(REL)
    with pytest.raises(PF.PrivateFixtureMismatch):
        PF.load_or_none(REL)


def test_a_set_store_without_the_directory_or_the_file_is_an_error(manifest, tmp_path, monkeypatch):
    monkeypatch.setenv(PF.ENV, str(tmp_path / "mistyped"))
    with pytest.raises(PF.PrivateFixtureMissing):
        PF.load(REL)
    store = _store(tmp_path / "store", {GZ_REL: GZ_BODY})
    monkeypatch.setenv(PF.ENV, str(store))
    with pytest.raises(PF.PrivateFixtureMissing):
        PF.load(REL)
    with pytest.raises(PF.PrivateFixtureMissing):
        PF.load_or_none(REL)


def test_only_manifest_paths_are_resolved_or_written(manifest, tmp_path, monkeypatch):
    with pytest.raises(KeyError):
        PF.locate("backend/tests/fixtures/made_up/other.json")
    with pytest.raises(SystemExit):
        PF.write_path(REL)                                   # the store is not set
    monkeypatch.setenv(PF.ENV, str(tmp_path / "store"))
    with pytest.raises(KeyError):
        PF.write_path("backend/tests/fixtures/made_up/other.json")
    assert PF.write_path(REL) == tmp_path / "store" / REL and (tmp_path / "store" / REL).parent.is_dir()


@pytest.mark.parametrize("bad", ["/abs/golden.json", "../golden.json", "backend/../../golden.json", "a//b.json", "./a.json"])
def test_a_manifest_path_that_leaves_the_store_is_refused(manifest, bad):
    manifest.write_text(json.dumps({"files": {bad: "0" * 64}}), encoding="utf-8")
    PF.manifest.cache_clear()
    with pytest.raises(ValueError):
        PF.files()


# --- scripts/export_private_fixtures.py -----------------------------------------------------------------------------------

def _export():
    spec = importlib.util.spec_from_file_location("export_private_fixtures", REPO / "scripts" / "export_private_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_repo(root: Path, pinned: str) -> Path:
    _store(root, {REL: BODY})
    manifest = root / "backend" / "tests" / "private_fixtures_manifest.json"
    manifest.write_text(json.dumps({"source_commit": "HEAD", "files": {REL: pinned}}), encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "fixtures"], check=True)
    return root


def test_export_writes_and_verifies(tmp_path):
    repo = _git_repo(tmp_path / "repo", _sha(BODY))
    export = _export()
    assert export.main(["--list", "--repo", str(repo)]) == 0
    out = tmp_path / "store"
    assert export.main(["--to", str(out), "--repo", str(repo)]) == 0
    assert (out / REL).read_bytes() == BODY
    assert export.main(["--to", str(out), "--repo", str(repo)]) == 0               # already in place: kept


def test_export_refuses_a_target_inside_the_repository_and_a_bad_hash(tmp_path):
    export = _export()
    good = _git_repo(tmp_path / "good", _sha(BODY))
    with pytest.raises(SystemExit, match="inside the repository"):
        export.main(["--to", str(good / "store"), "--repo", str(good)])
    bad = _git_repo(tmp_path / "bad", "0" * 64)
    with pytest.raises(SystemExit, match="the manifest pins"):
        export.main(["--to", str(tmp_path / "store"), "--repo", str(bad)])
    assert export.main(["--list", "--repo", str(bad)]) == 1
    assert not (tmp_path / "store" / REL).exists()
