"""A1 (UI-H bug lane): deploy/atp.sh installs the chart analysis engine (the private package atp_private) from its
private source, and `health` reports it - against the docker-less fake host of test_z11_deploy_bundle.py.

The "private repository" here is a local bare git repository with made-up content (no network, no real key): the
deploy fetches the tag ATP_PRIVATE_REF with the deploy key into $ATP_DIR/private; without the key or the tag the
deploy goes on and says in one line what the owner adds. The key's content never appears in the output.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.test_z11_deploy_bundle import ROOT, Host, _git

KEY_TEXT = "FAKEFAKE-private-deploy-key-0b7e"


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def _private_origin(tmp: Path, tag: str = "v0.1.0") -> Path:
    origin, work = tmp / "atp-private.git", tmp / "private-work"
    _git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp, "clone", "-q", str(origin), str(work))
    pkg = work / "atp_private" / "chart_analyze"
    pkg.mkdir(parents=True)
    (work / "atp_private" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("# made-up engine for the A1 test\nVERSION = 'test'\n")
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "engine")
    _git(work, "tag", tag); _git(work, "push", "-q", "origin", "main", "--tags")
    return origin


def _key(host: Host) -> Path:
    ssh = host.tmp / ".ssh"                         # HOME is the test's tmp directory (Host.run)
    ssh.mkdir(exist_ok=True)
    key = ssh / "atp_private_ro"
    key.write_text(KEY_TEXT + "\n")
    key.chmod(0o600)
    return key


def _no_key_text(res, host: Host):
    assert KEY_TEXT not in res.all
    for f in (host.dir / ".deploy").rglob("*"):
        if f.is_file():
            assert KEY_TEXT not in f.read_text(errors="ignore"), f


def test_without_the_key_or_the_tag_the_deploy_goes_on_and_says_what_the_owner_adds(host):
    res = host.run("deploy")
    assert res.returncode == 0, res.all
    lines = [ln for ln in res.stdout.splitlines() if "chart analysis engine not installed" in ln]
    assert len(lines) == 1 and "~/.ssh/atp_private_ro" in lines[0] and "ATP_PRIVATE_REF" in lines[0], res.stdout
    assert "deployed " + host.sha_b[:12] in res.stdout
    assert (host.dir / "private").is_dir() and not (host.dir / "private" / ".git").exists()   # the mount source, empty
    backend_up = [c for c in host.state["calls"] if "backend" in c["argv"] and "up" in c["argv"]]
    assert backend_up and all(c["private"] == "none" for c in backend_up)


def test_only_the_key_or_only_the_tag_names_the_missing_one(host):
    _private_origin(host.tmp)
    host.write_env(extra={"ATP_PRIVATE_REF": "v0.1.0"})
    res = host.run("deploy")
    assert res.returncode == 0 and "the owner adds the read-only deploy key ~/.ssh/atp_private_ro" in res.stdout
    host.write_env()
    _key(host)
    res = host.run("deploy")
    assert res.returncode == 0 and "the owner adds ATP_PRIVATE_REF=<tag of atp-private> in .env" in res.stdout
    _no_key_text(res, host)


def test_with_the_key_and_the_tag_the_engine_is_checked_out_read_only_and_the_containers_follow_it(host):
    origin = _private_origin(host.tmp)
    _key(host)
    host.write_env(extra={"ATP_PRIVATE_REF": "v0.1.0", "ATP_PRIVATE_REPO": str(origin)})
    res = host.run("deploy")
    assert res.returncode == 0, res.all
    assert "chart analysis engine source at tag v0.1.0" in res.stdout and "not installed" not in res.stdout
    private = host.dir / "private"
    head = _git(private, "rev-parse", "HEAD")
    assert head == _git(host.tmp / "private-work", "rev-parse", "v0.1.0")
    init = private / "atp_private" / "chart_analyze" / "__init__.py"
    assert init.read_text().startswith("# made-up engine")
    assert init.stat().st_mode & stat.S_IROTH and private.stat().st_mode & stat.S_IXOTH   # the container's user reads it
    backend_up = [c for c in host.state["calls"] if c["argv"][-1:] == ["backend"] and "up" in c["argv"]]
    assert backend_up and backend_up[-1]["private"] == head[:12]          # a changed engine recreates the API
    _no_key_text(res, host)
    # ATP's own checkout is untouched by the private one nested in it
    assert subprocess.run(["git", "-C", str(host.dir), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() == host.sha_b


def test_a_tag_that_cannot_be_fetched_warns_and_the_deploy_goes_on(host):
    origin = _private_origin(host.tmp)
    _key(host)
    host.write_env(extra={"ATP_PRIVATE_REF": "v9.9.9", "ATP_PRIVATE_REPO": str(origin)})
    res = host.run("deploy")
    assert res.returncode == 0, res.all
    assert "could not fetch tag v9.9.9" in res.stdout and "deployed " + host.sha_b[:12] in res.stdout
    host.write_env(extra={"ATP_PRIVATE_REF": "--upload-pack=x", "ATP_PRIVATE_REPO": str(origin)})
    res = host.run("deploy")
    assert res.returncode == 0 and "ATP_PRIVATE_REF in .env is not a tag name" in res.stdout
    _no_key_text(res, host)


def test_a_full_commit_sha_is_fetched_and_checked_out_like_a_tag(host):
    """M79: ATP_PRIVATE_REF may pin a reviewed commit by its full sha while the private repository has no tag yet."""
    origin = _private_origin(host.tmp)
    work = host.tmp / "private-work"
    (work / "atp_private" / "chart_analyze" / "__init__.py").write_text("# made-up engine, untagged commit\n")
    _git(work, "commit", "-q", "-am", "untagged"); _git(work, "push", "-q", "origin", "main")
    sha = _git(work, "rev-parse", "HEAD")
    _key(host)
    host.write_env(extra={"ATP_PRIVATE_REF": sha, "ATP_PRIVATE_REPO": str(origin)})
    res = host.run("deploy")
    assert res.returncode == 0, res.all
    assert f"chart analysis engine source at commit {sha}" in res.stdout and "not installed" not in res.stdout
    private = host.dir / "private"
    assert _git(private, "rev-parse", "HEAD") == sha
    assert (private / "atp_private" / "chart_analyze" / "__init__.py").read_text().startswith("# made-up engine, untagged")
    missing = "0" * 40
    host.write_env(extra={"ATP_PRIVATE_REF": missing, "ATP_PRIVATE_REPO": str(origin)})
    res = host.run("deploy")
    assert res.returncode == 0 and f"could not fetch commit {missing}" in res.stdout, res.all
    assert _git(private, "rev-parse", "HEAD") == sha                 # the checkout stays at the last good commit
    _no_key_text(res, host)


@pytest.mark.parametrize("engine, line", [
    ({"installed": True, "name": "fake-engine", "version": "1.2.3"}, "ok    chart analysis engine: OK (fake-engine 1.2.3)"),
    ({"installed": False, "reason": "the private package is not installed on this server"},
     "WARN  chart analysis engine: MISSING (the private package is not installed on this server)"),
])
def test_health_reports_the_engine_ok_or_missing(host, engine, line):
    assert host.run("deploy").returncode == 0
    host.load()
    host.state["engine"] = engine
    host.save()
    res = host.run("health")
    assert line in res.stdout, res.stdout


def test_health_on_an_older_release_says_not_reported(host):
    assert host.run("deploy").returncode == 0
    res = host.run("health")
    assert "chart analysis engine: not reported by this release" in res.stdout


def test_compose_mounts_the_checkout_read_only_into_the_backend_and_the_worker_and_git_ignores_it():
    services = yaml.safe_load((ROOT / "docker-compose.hostinger.yml").read_text().replace("!override", "").replace("!reset", ""))["services"]
    for name in ("backend", "worker"):
        assert "./private:/opt/atp-private:ro" in services[name]["volumes"], name
        assert services[name]["environment"]["PYTHONPATH"] == "/opt/atp-private"
        assert services[name]["environment"]["ATP_PRIVATE_VERSION"] == "${ATP_PRIVATE_VERSION:-none}"
    assert "/private/" in (ROOT / ".gitignore").read_text().splitlines()
    assert "ATP_PRIVATE_REF" in (ROOT / "docs/deploy/HOSTINGER_RUNBOOK.md").read_text()
    assert os.access(ROOT / "deploy/atp.sh", os.X_OK)
