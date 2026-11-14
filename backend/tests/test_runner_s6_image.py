"""Runner S6: the runner image's build context, Dockerfile, lock and installer (docs/design/USER_RUNNER.md step S6).

- The build context generated from the runner manifest holds no forbidden package (the private package, its loaders,
  AI, admin, marketplace, Lab, the server's worker / app, billing, the lake), no key or `.env` file, and every runner
  module; the S2 loop runs from that context alone.
- The runner lock is current, a subset of the backend lock (same pins and hashes), and covers every module-level
  third-party import of what ships.
- The Dockerfile builds only from that context, as a non-root user, with the /runner volume, a healthcheck, no port.
- An absent loader package reads as "unavailable" in the admin flag list (S1 open item).
- The installer: bash -n, shellcheck (when installed; CI's lint job always runs it), and --dry-run changes nothing.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.runner.manifest import FORBIDDEN_PREFIXES, RUNNER_MODULES, RUNTIME_FORBIDDEN, is_forbidden

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
RUNNER_DIR = REPO / "deploy" / "runner"
INSTALLER = RUNNER_DIR / "install.sh"
WORKFLOW = REPO / ".github" / "workflows" / "runner-release.yml"


def _load_build_context():
    spec = importlib.util.spec_from_file_location("runner_build_context", RUNNER_DIR / "build_context.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bc = _load_build_context()


@pytest.fixture(scope="module")
def context(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("runner-ctx") / "ctx"
    bc.stage(out)
    return out


# --- the build context ---------------------------------------------------------------------------------------------

def test_the_build_context_holds_no_forbidden_package_key_or_env_file(context):
    files = [p.relative_to(context) for p in context.rglob("*") if p.is_file()]
    assert files
    for rel in files:
        dotted = ".".join(rel.with_suffix("").parts)
        assert not is_forbidden(dotted) and not any(is_forbidden(".".join(rel.parts[:i])) for i in range(1, len(rel.parts))), rel
        assert "atp_private" not in rel.parts and rel.name != ".env", rel
        assert rel.suffix not in (".pem", ".key", ".pyc", ".db", ".sqlite"), rel
        assert "__pycache__" not in rel.parts, rel
    for prefix in FORBIDDEN_PREFIXES:
        base = context.joinpath(*prefix.split("."))
        assert not base.exists() and not base.with_suffix(".py").exists(), prefix
    top = {p.name for p in context.iterdir()}
    assert top == {"app", "Dockerfile", "requirements.lock", "FORBIDDEN.txt", "MODULES.txt"}


def test_the_build_context_holds_every_runner_module_and_lists_the_forbidden_set(context):
    shipped = set((context / "MODULES.txt").read_text().split())
    runner = {"app.runner." + p.stem for p in (BACKEND / "app" / "runner").glob("*.py") if p.stem != "__init__"}
    assert set(RUNNER_MODULES) | runner | {"app", "app.runner"} <= shipped
    assert not any(is_forbidden(m) for m in shipped)
    for module in shipped:
        assert bc.path_of(module) is not None
        rel = bc.path_of(module).relative_to(BACKEND)
        assert (context / rel).read_bytes() == (BACKEND / rel).read_bytes(), rel
    assert (context / "FORBIDDEN.txt").read_text().split() == list(FORBIDDEN_PREFIXES)


def test_staging_refuses_a_folder_that_is_not_empty(tmp_path):
    (tmp_path / "x").write_text("x")
    with pytest.raises(SystemExit):
        bc.stage(tmp_path)


def test_the_runner_loop_runs_from_the_build_context_alone(context, tmp_path):
    """The S2 loop job with ONLY the build context on the path (not the backend folder): the shipped set is enough for
    a PAPER cycle, its entry and its stop exit, and the process loads no forbidden module."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DATABASE_URL", "MIGRATION_DATABASE_URL", "REDIS_URL", "ATP_RUNNER", "PYTHONPATH"))}
    env["PYTHONPATH"] = str(context)
    where = subprocess.run([sys.executable, "-c", "import app; print(app.__file__)"], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=60)
    assert where.returncode == 0 and where.stdout.strip().startswith(str(context)), where.stderr
    job = tmp_path / "runner_s2_loop_job.py"
    shutil.copy2(BACKEND / "tests" / "runner_s2_loop_job.py", job)
    run = subprocess.run([sys.executable, str(job), str(tmp_path / "home")], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    report = json.loads(next(x for x in run.stdout.splitlines() if x.startswith("RUNNER_S2 "))[len("RUNNER_S2 "):])
    assert not [m for m in report["loaded"] if is_forbidden(m, RUNTIME_FORBIDDEN)]
    assert set(report["loaded"]) <= set((context / "MODULES.txt").read_text().split())


# --- the runner lock -------------------------------------------------------------------------------------------------

def test_the_runner_lock_is_current_and_a_subset_of_the_backend_lock():
    committed = bc.RUNNER_LOCK.read_text()
    assert committed == bc.runner_lock_text(), "python deploy/runner/build_context.py --write-lock"
    backend_blocks = bc.parse_lock(bc.BACKEND_LOCK.read_text())
    runner_blocks = bc.parse_lock(committed)
    for name, (block, _via) in runner_blocks.items():
        assert backend_blocks[name][0] == block, name           # the same pin and the same hashes
    for server_only in ("asyncpg", "uvicorn", "anthropic", "pillow", "pyarrow", "boto3", "sentry-sdk", "alembic",
                        "pytest", "hypothesis", "opentelemetry-sdk"):
        assert server_only not in runner_blocks, server_only


def test_every_module_level_third_party_import_that_ships_is_in_the_runner_lock():
    modules, _cut = bc.closure()
    locked = set(bc.parse_lock(bc.RUNNER_LOCK.read_text()))
    missing: dict[str, str] = {}
    for module in modules:
        for name in bc.top_level_imports_of(module) - set(sys.stdlib_module_names) - {"app", "__future__"}:
            dist = bc._norm(bc.IMPORT_TO_DIST.get(name, name))
            if dist not in locked:
                missing[name] = module
    assert not missing, missing


# --- the Dockerfile ----------------------------------------------------------------------------------------------------

def _instructions(path: Path) -> list[tuple[str, str]]:
    text = re.sub(r"\\\n", " ", path.read_text())
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            word, _, rest = line.partition(" ")
            out.append((word.upper(), rest.strip()))
    return out


def test_the_dockerfile_matches_the_backend_base_and_builds_only_from_the_context(context):
    runner = _instructions(RUNNER_DIR / "Dockerfile")
    backend = _instructions(BACKEND / "Dockerfile")
    assert [r for w, r in runner if w == "FROM"] == [r for w, r in backend if w == "FROM"]   # the same Python base
    for word, rest in runner:
        if word in ("COPY", "ADD"):
            assert word == "COPY" and not rest.startswith("--from"), rest
            for source in rest.split()[:-1]:
                assert (context / source).exists(), source
    words = [w for w, _ in runner]
    assert "EXPOSE" not in words                                              # no inbound port
    users = [r for w, r in runner if w == "USER"]
    assert users and users[-1] not in ("root", "0")                          # runs as the non-root user
    assert ('VOLUME', '["/runner"]') in runner
    health = next(r for w, r in runner if w == "HEALTHCHECK")
    assert '"app.runner", "health"' in health
    assert ("ENTRYPOINT", '["python", "-m", "app.runner"]') in runner
    assert any(w == "RUN" and "--require-hashes -r requirements.lock" in r for w, r in runner)
    assert any(w == "RUN" and "FORBIDDEN.txt" in r for w, r in runner)       # the image re-checks itself


def test_the_dockerfile_self_check_passes_on_the_context_and_fails_with_a_forbidden_package(context, tmp_path):
    check = next(r for w, r in _instructions(RUNNER_DIR / "Dockerfile") if w == "RUN" and "FORBIDDEN.txt" in r)
    code = re.search(r'python -c "(.*?)" +&&', check).group(1)
    env = {**os.environ, "PYTHONPATH": str(context)}
    ok = subprocess.run([sys.executable, "-c", code], cwd=context, env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    bad = tmp_path / "bad"
    shutil.copytree(context, bad)
    (bad / "app" / "engine_provider").mkdir()
    (bad / "app" / "engine_provider" / "__init__.py").write_text("")
    fail = subprocess.run([sys.executable, "-c", code], cwd=bad, env={**os.environ, "PYTHONPATH": str(bad)},
                          capture_output=True, text=True)
    assert fail.returncode != 0 and "app.engine_provider" in fail.stderr


def test_the_health_command_reads_the_runner_process_and_never_takes_the_lock(tmp_path):
    from app.runner.__main__ import runner_alive
    from app.runner.settings import RunnerSettings
    settings = RunnerSettings.load(tmp_path)
    assert runner_alive(settings) is False                                    # no lock file
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "app.runner", "run"])
    try:
        settings.lock_path.write_text(str(proc.pid))
        assert runner_alive(settings) is True
        settings.lock_path.write_text(str(os.getpid()))                     # a process that is not the runner
        assert runner_alive(settings) is False
    finally:
        proc.kill()
        proc.wait()
    settings.lock_path.write_text(str(proc.pid))                             # a pid that is gone
    assert runner_alive(settings) is False


# --- S1 open item: an absent loader package is "unavailable" ---------------------------------------------------------

def test_the_admin_flag_list_reads_absent_engine_packages_as_unavailable(monkeypatch):
    from app.platform import controls, flag_state
    for name in ("app.engine_provider", "app.engine_provider.loader", "app.chart_reader", "app.chart_reader.loader",
                 "app.chart_analyze", "app.chart_analyze.provider"):
        monkeypatch.setitem(sys.modules, name, None)                          # import -> ImportError, as in the image
    missing = controls.engine_missing_flags()
    for flag in controls.ENGINE_FLAGS:
        assert missing[flag] == controls.ENGINE_MISSING_REASON
    for flag in controls.TRADE_ENGINE_FLAGS:
        assert missing[flag] == controls.TRADE_ENGINE_MISSING_REASON
    for flag in controls.CHART_READER_FLAGS:
        assert missing[flag] == controls.CHART_READER_MISSING_REASON
    assert asyncio.run(flag_state._chart_engine(None, None)) is False         # type: ignore[arg-type]
    assert asyncio.run(flag_state._signal_engine(None, None)) is False        # type: ignore[arg-type]


# --- the installer and the release workflow ----------------------------------------------------------------------------

def test_the_installer_parses_and_is_shellcheck_clean():
    assert subprocess.run(["bash", "-n", str(INSTALLER)]).returncode == 0
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed here (CI's lint job runs it on deploy/runner/install.sh)")
    run = subprocess.run(["shellcheck", "-x", str(INSTALLER)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout


def _stub_path(tmp_path: Path) -> tuple[dict, Path]:
    """PATH with stub docker / curl / apt-get / systemctl / cosign / sudo that only log: --dry-run must call none of
    them (the `command -v docker` check finds the stub and reads Docker as installed)."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    for name in ("docker", "curl", "apt-get", "systemctl", "cosign", "sudo", "chown", "rm", "mv"):
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 1\n')
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    return env, log


def test_the_installer_dry_run_prints_every_action_and_changes_nothing(tmp_path):
    env, log = _stub_path(tmp_path)
    home = tmp_path / "runner-folder"
    run = subprocess.run(["bash", str(INSTALLER), "install", "--dry-run", "--home", str(home), "--broker", "angel_one"],
                         env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    out = run.stdout
    assert not log.exists(), log.read_text()                                  # no command was really run
    assert not home.exists()
    assert "verify --certificate-identity-regexp" in out and "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in out
    pull = next(x for x in out.splitlines() if "docker pull" in x)
    assert "ghcr.io/abhishekwasu-hue/atp-runner@sha256:" in pull               # pulled by digest, never by tag
    assert f"chmod 0700 {home}" in out
    start = next(x for x in out.splitlines() if "docker run -d" in x)
    assert "--restart unless-stopped" in start and " -p " not in start and "--publish" not in start
    assert "--read-only" in start and "--cap-drop ALL" in start and "--user 10001:10001" in start
    assert start.endswith("run --home /runner --broker angel_one")
    assert out.index("verify --certificate-identity-regexp") < out.index("docker pull") < out.index("docker run -d")
    assert "Pairing:" in out and "Broker keys:" in out                        # S3-S5 not in this image: skipped, said so
    assert "public IPv4" in out


def test_the_installer_refuses_without_a_digest_and_on_unknown_options(tmp_path):
    env, log = _stub_path(tmp_path)
    bad = subprocess.run(["bash", str(INSTALLER), "--dry-run", "--nope"], env=env, capture_output=True, text=True)
    assert bad.returncode != 0 and "unknown option" in bad.stderr
    broker = subprocess.run(["bash", str(INSTALLER), "--dry-run", "--broker", "other"], env=env, capture_output=True, text=True)
    assert broker.returncode != 0 and "Angel One" in broker.stderr
    text = INSTALLER.read_text()
    assert 'RUNNER_DIGEST="@RUNNER_DIGEST@"' in text                          # only the release workflow writes it
    assert "this copy of the installer has no image digest" in text
    assert not log.exists()


def test_the_uninstall_dry_run_keeps_the_folder(tmp_path):
    env, log = _stub_path(tmp_path)
    run = subprocess.run(["bash", str(INSTALLER), "uninstall", "--dry-run", "--home", str(tmp_path / "f")], env=env,
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    assert "docker rm -f atp-runner" in run.stdout and "rm -rf" not in run.stdout and "Your folder is kept" in run.stdout
    assert not log.exists()


def test_the_release_workflow_signs_what_the_installer_verifies():
    flow = WORKFLOW.read_text()
    text = INSTALLER.read_text()
    assert re.search(r'^on:\n  push:\n    tags: \["runner-v\*"\]\n\n', flow, re.M)   # tags only, never main CI
    assert "pull_request" not in flow and "branches" not in flow
    assert "id-token: write" in flow and "cosign\" sign --yes" in flow
    assert "build_context.py --check --out" in flow
    identity = re.search(r"^SIGNER_IDENTITY_REGEXP='(.+)'$", text, re.M).group(1)
    assert identity.endswith(r"/\.github/workflows/runner-release\.yml@refs/tags/runner-v")
    assert "AdvanceTradingPlatform" in identity and WORKFLOW.name == "runner-release.yml"
    assert re.search(r'^RUNNER_IMAGE="ghcr\.io/abhishekwasu-hue/atp-runner"$', text, re.M)
    assert "IMAGE: ghcr.io/${{ github.repository_owner }}/atp-runner" in flow
    for key in ("COSIGN_VERSION", "COSIGN_SHA256"):
        flow_value = re.search(rf"^      {key}: (\S+)$", flow, re.M).group(1)
        name = "COSIGN_SHA256_AMD64" if key == "COSIGN_SHA256" else key
        assert f'{name}="{flow_value}"' in text, key
    assert "s|@RUNNER_DIGEST@|" in flow and "s|@RUNNER_VERSION@|" in flow
