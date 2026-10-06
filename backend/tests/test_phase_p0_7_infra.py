"""P0.7 (ATP_PRO_GRADE_UPGRADE_PLAN, infra): the deploy script's rollback restarts the previous image, every
container's logs rotate, the CI workflow carries the static gates (ruff, mypy, bandit, gitleaks) and every
third-party action is pinned to a commit SHA."""

import re
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


class _Compose(yaml.SafeLoader):
    """Compose's `!override` / `!reset` merge tags (P0.1 / S4) are not YAML core; read them as their value."""


_Compose.add_constructor("!override", lambda loader, node: loader.construct_sequence(node))
_Compose.add_constructor("!reset", lambda loader, node: None)


def _compose(name: str) -> dict:
    return yaml.load((ROOT / name).read_text(), Loader=_Compose)


def _steps(workflow: dict):
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            yield job, step


# --- deploy.sh rollback ------------------------------------------------------------------------------------------------
def test_deploy_rollback_restarts_the_api_from_the_previous_image():
    script = (ROOT / "scripts" / "deploy.sh").read_text()
    if shutil.which("sh"):   # a Windows dev box has no sh; the body checks below still run there
        assert subprocess.run(["sh", "-n", str(ROOT / "scripts" / "deploy.sh")], capture_output=True).returncode == 0
    # `images -q` is the only compose form that prints the image id (`--format` takes table|json only); the old
    # `--format '{{.ID}}'` failed silently and left nothing to roll back to.
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "$COMPOSE images -q backend" in code and "images backend --format" not in code
    assert "$COMPOSE build --pull backend worker frontend" in script
    body = script[script.index("rollback() {"):]
    body = body[:body.index("\n}\n")]
    # The previous image is kept under a stable tag before the build re-points :latest, and the rollback retags it
    # back and recreates the API without rebuilding; it then waits for the deep health check again.
    assert 'docker tag "$PREVIOUS" "$IMAGE:previous"' in script
    assert 'docker tag "$IMAGE:previous" "$IMAGE:latest"' in body
    assert "$COMPOSE up -d --no-deps --no-build backend" in body
    assert "wait_healthy" in body and "exit 4" in body
    # Nothing but the API is recreated by the rollback (worker/frontend were not restarted yet), and no `docker tag
    # ... :rollback` leftover that restarted nothing.
    assert ":rollback" not in script
    assert "up -d --no-deps --no-build backend" in body and "worker" not in body.split("up -d")[1].split("\n")[0]


# --- log rotation ------------------------------------------------------------------------------------------------------
def test_every_compose_service_rotates_its_logs():
    base = _compose("docker-compose.yml")
    anchor = base["x-logging"]
    assert anchor["driver"] == "json-file" and anchor["options"]["max-size"] == "20m" and anchor["options"]["max-file"] == "5"
    for name, service in base["services"].items():
        assert service.get("logging") == anchor, f"{name} has no log rotation"
    prod = _compose("docker-compose.prod.yml")
    for name in ("caddy", "offsite"):
        assert prod["services"][name].get("logging") == anchor, f"{name} has no log rotation"
    # The overlays only add services or override ports; they inherit the base file's logging for shared services.
    for overlay in ("docker-compose.local.yml", "docker-compose.staging.yml"):
        for name, service in _compose(overlay)["services"].items():
            assert name in base["services"] and "logging" not in service, f"{overlay}:{name} overrides logging"


# --- CI gates and pinned actions -----------------------------------------------------------------------------------------
def test_ci_runs_the_static_gates_and_pins_every_action_to_a_sha():
    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    staging_text = (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text()
    ci, staging = yaml.safe_load(ci_text), yaml.safe_load(staging_text)
    for workflow in (ci, staging):
        for _job, step in _steps(workflow):
            if "uses" in step:
                assert SHA_PIN.match(step["uses"].strip()), f"action not SHA-pinned: {step['uses']}"
    # The tag each SHA stands for is kept next to it (YAML drops the comment, so check the text).
    for line in (ci_text + staging_text).splitlines():
        if "uses:" in line:
            assert re.search(r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d", line), f"pinned action without the tag comment: {line.strip()}"
    lint = ci["jobs"]["lint"]
    runs = " ".join(step.get("run", "") for step in lint["steps"])
    uses = " ".join(step.get("uses", "") for step in lint["steps"])
    assert "ruff check app tests" in runs and re.search(r"(^|\s)mypy(\s|$)", runs) and "bandit -q -r app -ll -ii" in runs
    assert "gitleaks/gitleaks-action@" in uses
    assert lint["permissions"] == {"contents": "read", "pull-requests": "read"}
    # The gates are a separate job: the test job is untouched and both run in parallel.
    assert "needs" not in lint and "needs" not in ci["jobs"]["backend"]
    assert ci["jobs"]["container-scan"]["needs"] == ["backend", "frontend"]


def test_lint_configs_exist_and_say_what_they_gate():
    ruff = (ROOT / "backend" / "ruff.toml").read_text()
    assert 'select = ["E4", "E7", "E9", "F", "B006", "S102", "S307", "T10", "PLE"]' in ruff
    mypy = (ROOT / "backend" / "mypy.ini").read_text()
    assert "follow_imports = silent" in mypy and "app/kill_switch" in mypy and "app/db" in mypy
    gitleaks = (ROOT / ".gitleaks.toml").read_text()
    assert "useDefault = true" in gitleaks and "JWT_ACCEPT_LEGACY" in gitleaks


def test_feed_parser_uses_defusedxml():
    source = (ROOT / "backend" / "app" / "news_feed" / "sources.py").read_text()
    assert "import defusedxml.ElementTree as ET" in source and "import xml.etree" not in source
    assert "defusedxml==" in (ROOT / "backend" / "requirements.txt").read_text()
