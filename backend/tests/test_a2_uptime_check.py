"""Production readiness A2, changed by RDY-21: the outside uptime check (.github/workflows/uptime.yml +
scripts/monitoring/uptime_check.py).

Pinned: two failed rounds in a row = down (one blip pages nobody); the repository is public, so the check never calls
GitHub's API (no issue, no comment) and the workflow holds `contents: read` only; Telegram is called only when both
repository secrets are set, once per outage and once on recovery (state carried in a file between runs, retried when
a send failed); without the secrets a plain notice is logged and the run exits 0; the token never reaches the output;
the workflow holds no secret value, pins its actions to SHAs, runs only on the owner's repository and passes actionlint
when it is installed.
"""
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "uptime.yml"
SCRIPT = ROOT / "scripts" / "monitoring" / "uptime_check.py"
spec = importlib.util.spec_from_file_location("uptime_check", SCRIPT)
uptime = importlib.util.module_from_spec(spec)
sys.modules["uptime_check"] = uptime          # dataclasses look the module up by name
spec.loader.exec_module(uptime)


class _Resp:
    def __init__(self, status=200, body=b""):
        self.status, self._body = status, body

    def read(self, *_a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class World:
    """The site (a list of answers per round), Telegram, and a record of anything else that was called."""
    def __init__(self, rounds, telegram_fails=False):
        self.rounds = list(rounds)          # each: (home_status, health_body or exception)
        self.current = None
        self.telegram: list = []
        self.telegram_fails = telegram_fails
        self.other: list = []

    def opener(self, request, timeout=None):
        url = request.full_url
        if url.startswith("https://api.telegram.org/"):
            if self.telegram_fails:
                raise urllib.error.URLError("unreachable")
            self.telegram.append(json.loads(request.data))
            return _Resp(200, b"{}")
        if not url.startswith("https://aialgodesk.tech/"):
            self.other.append(url)
            raise AssertionError(f"unexpected call: {url}")
        if url.endswith("/api/system/health"):
            answer = self.current[1]
            if isinstance(answer, Exception):
                raise answer
            return _Resp(200, answer)
        self.current = self.rounds.pop(0) if self.rounds else self.current
        status = self.current[0]
        if status != 200:
            raise urllib.error.HTTPError(url, status, "x", {}, io.BytesIO())
        return _Resp(200, b"<html>")


UP = (200, b'{"status": "ok"}')
DOWN = (502, TimeoutError("timed out"))


def _secrets(monkeypatch, on):
    if on:
        monkeypatch.setenv("UPTIME_TELEGRAM_BOT_TOKEN", "999:FAKE-uptime-bot-token")
        monkeypatch.setenv("UPTIME_TELEGRAM_CHAT_ID", "555")
    else:
        monkeypatch.delenv("UPTIME_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("UPTIME_TELEGRAM_CHAT_ID", raising=False)


def _run(world, monkeypatch, telegram=True, previous=None):
    _secrets(monkeypatch, telegram)
    return uptime.run(base="https://aialgodesk.tech", retry_seconds=0, timeout=1, previous=previous,
                      sleep=lambda _s: None, opener=world.opener)


def test_one_blip_pages_nobody(monkeypatch):
    world = World([DOWN, UP])
    out = _run(world, monkeypatch)
    assert out["down"] is False and out["action"] == "none" and world.telegram == [] and out["changed"] is False


def test_two_failures_send_one_telegram_and_nothing_else(monkeypatch):
    world = World([DOWN, DOWN])
    out = _run(world, monkeypatch)
    assert out["down"] is True and out["action"] == "alert_down" and out["telegram"] is True
    assert len(world.telegram) == 1 and world.telegram[0]["chat_id"] == "555" and "DOWN" in world.telegram[0]["text"]
    assert "https://aialgodesk.tech/api/system/health" in world.telegram[0]["text"]
    assert out["state"]["down"] is True and out["state"]["down_alerted"] is True and out["changed"] is True
    assert world.other == []                                    # no GitHub API, nothing but the site and Telegram
    assert "FAKE-uptime-bot-token" not in json.dumps(out)


def test_a_long_outage_is_told_once_and_the_recovery_once(monkeypatch):
    world = World([DOWN, DOWN])
    first = _run(world, monkeypatch)
    world = World([DOWN, DOWN])
    second = _run(world, monkeypatch, previous=first["state"])
    assert second["action"] == "none" and world.telegram == [] and second["changed"] is False
    assert second["state"]["since"] == first["state"]["since"]
    world = World([UP])
    back = _run(world, monkeypatch, previous=second["state"])
    assert back["action"] == "alert_recovered" and len(world.telegram) == 1 and "back up" in world.telegram[0]["text"]
    assert back["state"] == {"down": False, "down_alerted": False, "since": None} and back["changed"] is True
    world = World([UP])
    after = _run(world, monkeypatch, previous=back["state"])
    assert after["action"] == "none" and world.telegram == [] and after["changed"] is False


def test_a_failed_send_is_tried_again_next_run(monkeypatch):
    world = World([DOWN, DOWN], telegram_fails=True)
    first = _run(world, monkeypatch)
    assert first["telegram"] is False and first["state"]["down_alerted"] is False
    world = World([DOWN, DOWN])
    second = _run(world, monkeypatch, previous=first["state"])
    assert second["action"] == "alert_down" and len(world.telegram) == 1
    world = World([UP], telegram_fails=True)
    back = _run(world, monkeypatch, previous=second["state"])
    assert back["action"] == "alert_recovered" and back["state"]["down_alerted"] is True    # recovery still owed
    world = World([UP])
    again = _run(world, monkeypatch, previous=back["state"])
    assert again["action"] == "alert_recovered" and len(world.telegram) == 1
    assert again["state"]["down_alerted"] is False


def test_recovery_without_an_earlier_down_message_sends_nothing(monkeypatch):
    world = World([UP])
    out = _run(world, monkeypatch, previous={"down": True, "down_alerted": False, "since": "x"})
    assert out["action"] == "none" and world.telegram == [] and out["changed"] is True


def test_without_telegram_secrets_nothing_is_sent(monkeypatch):
    world = World([DOWN, DOWN])
    out = _run(world, monkeypatch, telegram=False)
    assert out["telegram"] is None and world.telegram == [] and world.other == []
    assert out["state"]["down_alerted"] is False


def test_health_must_say_ok():
    probe = uptime.probe("https://x/api/system/health", timeout=1, expect_health=True,
                         opener=lambda r, timeout=None: _Resp(200, b'{"status": "degraded"}'))
    assert probe.ok is False
    probe = uptime.probe("https://x/api/system/health", timeout=1, expect_health=True, opener=lambda r, timeout=None: _Resp(200, b"<html>"))
    assert probe.ok is False and "not JSON" in probe.detail
    assert uptime.decide({"down_alerted": False}, False) == "none"
    assert uptime.decide({"down_alerted": False}, True) == "alert_down"
    assert uptime.decide({"down_alerted": True}, True) == "none"
    assert uptime.decide({"down_alerted": True}, False) == "alert_recovered"


def test_state_file_round_trip_and_bad_files(tmp_path):
    path = tmp_path / "s" / "state.json"
    assert uptime.load_state(str(path)) == uptime.DEFAULT_STATE
    uptime.save_state(str(path), {"down": True, "down_alerted": True, "since": "2026-10-23 10:00 UTC"})
    assert uptime.load_state(str(path)) == {"down": True, "down_alerted": True, "since": "2026-10-23 10:00 UTC"}
    path.write_text("not json")
    assert uptime.load_state(str(path)) == uptime.DEFAULT_STATE
    path.write_text('{"down": "yes", "down_alerted": 1, "since": 5}')
    assert uptime.load_state(str(path)) == uptime.DEFAULT_STATE
    assert uptime.load_state(None) == uptime.DEFAULT_STATE


def test_main_without_secrets_logs_a_plain_notice_and_exits_0(monkeypatch, tmp_path, capsys):
    _secrets(monkeypatch, False)
    world = World([DOWN, DOWN])
    monkeypatch.setattr(uptime.urllib.request, "urlopen", world.opener)
    monkeypatch.setattr(uptime.time, "sleep", lambda _s: None)
    monkeypatch.setenv("UPTIME_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.delenv("UPTIME_BASE_URL", raising=False)
    monkeypatch.setenv("UPTIME_RETRY_SECONDS", "0")
    assert uptime.main() == 0
    printed = capsys.readouterr().out
    assert "Telegram is not set up" in printed and "primary monitor" in printed
    assert (tmp_path / "out").read_text() == "changed=true\n"
    assert json.loads((tmp_path / "state.json").read_text())["down"] is True


def test_the_script_never_talks_to_github():
    text = SCRIPT.read_text()
    assert "api.github.com" not in text and "GITHUB_TOKEN" not in text and "/issues" not in text


def test_the_workflow_has_no_secret_values_and_read_only_rights():
    text = WORKFLOW.read_text()
    wf = yaml.safe_load(text)
    on = wf.get("on") or wf.get(True)
    assert on["schedule"][0]["cron"] == "7-59/10 * * * *" and "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "read"}
    assert "issues" not in text and "GITHUB_TOKEN" not in text
    job = wf["jobs"]["probe"]
    assert "permissions" not in job
    assert job["if"] == "github.repository == 'abhishekwasu-hue/AdvanceTradingPlatform'" and job["timeout-minutes"] <= 10
    steps = job["steps"]
    for step in steps:
        if "uses" in step:
            assert len(step["uses"].split("@")[1].split()[0]) == 40, step["uses"]        # pinned to a SHA
    assert steps[0]["with"]["persist-credentials"] is False
    assert steps[1]["uses"].startswith("actions/cache/restore@") and steps[1]["with"]["restore-keys"] == "uptime-state-"
    check = steps[2]
    env = check["env"]
    assert set(env) == {"UPTIME_BASE_URL", "UPTIME_TELEGRAM_BOT_TOKEN", "UPTIME_TELEGRAM_CHAT_ID"}
    assert env["UPTIME_TELEGRAM_BOT_TOKEN"] == "${{ secrets.UPTIME_TELEGRAM_BOT_TOKEN }}"
    assert env["UPTIME_TELEGRAM_CHAT_ID"] == "${{ secrets.UPTIME_TELEGRAM_CHAT_ID }}"
    for value in env.values():
        assert value.startswith("${{ ") and value.endswith(" }}"), value       # names only, no value in git
    save = steps[3]
    assert save["uses"].startswith("actions/cache/save@") and save["if"] == f"steps.{check['id']}.outputs.changed == 'true'"
    assert save["with"]["key"] == steps[1]["with"]["key"] and save["with"]["path"] == steps[1]["with"]["path"]


def test_actionlint_passes():
    if not shutil.which("actionlint"):
        pytest.skip("actionlint not installed")
    subprocess.run(["actionlint", str(WORKFLOW)], check=True)
