"""Production readiness A2: the outside uptime check (.github/workflows/uptime.yml + scripts/monitoring/uptime_check.py).

Pinned: two failed rounds in a row = down (one blip pages nobody); down opens one issue (labelled, with the marker),
later failures edit it, recovery comments and closes it; Telegram only when both optional secrets exist and its token
never reaches the output; the workflow holds no secret value, pins its action to a SHA, runs only on the owner's
repository with `issues: write` and passes actionlint when it is installed.
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
spec = importlib.util.spec_from_file_location("uptime_check", ROOT / "scripts" / "monitoring" / "uptime_check.py")
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
    """The site (a list of answers per round) and GitHub's issues API."""
    def __init__(self, rounds, issues=None):
        self.rounds = list(rounds)          # each: (home_status, health_body or exception)
        self.current = None
        self.issues = issues or []
        self.calls: list = []
        self.telegram: list = []

    def opener(self, request, timeout=None):
        url = request.full_url
        if url.startswith("https://api.telegram.org/"):
            self.telegram.append(json.loads(request.data))
            return _Resp(200, b"{}")
        if url.startswith("https://api.github.com/"):
            body = json.loads(request.data) if request.data else None
            self.calls.append((request.get_method(), url.split("/repos/o/r", 1)[1], body))
            if request.get_method() == "GET":
                return _Resp(200, json.dumps(self.issues).encode())
            return _Resp(201, b"{}")
        if url.endswith("/api/system/health"):
            answer = self.current[1]
            if isinstance(answer, Exception):
                raise answer
            return _Resp(200, answer)
        if self.current is None or url.endswith("/"):
            self.current = self.rounds.pop(0) if self.rounds else self.current
        status = self.current[0]
        if status != 200:
            raise urllib.error.HTTPError(url, status, "x", {}, io.BytesIO())
        return _Resp(200, b"<html>")


UP = (200, b'{"status": "ok"}')
DOWN = (502, TimeoutError("timed out"))


def _run(world, monkeypatch, telegram=False):
    if telegram:
        monkeypatch.setenv("UPTIME_TELEGRAM_BOT_TOKEN", "999:FAKE-uptime-bot-token")
        monkeypatch.setenv("UPTIME_TELEGRAM_CHAT_ID", "555")
    else:
        monkeypatch.delenv("UPTIME_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("UPTIME_TELEGRAM_CHAT_ID", raising=False)
    gh = uptime.GitHub("o/r", "ghs_fake", opener=world.opener)
    return uptime.run(base="https://aialgodesk.tech", retry_seconds=0, timeout=1, github=gh, sleep=lambda _s: None, opener=world.opener)


def test_one_blip_pages_nobody(monkeypatch):
    world = World([DOWN, UP])
    out = _run(world, monkeypatch, telegram=True)
    assert out["down"] is False and out["action"] == "none"
    assert [c[0] for c in world.calls] == ["GET"] and world.telegram == []


def test_two_failures_open_one_issue_and_telegram_when_set_up(monkeypatch):
    world = World([DOWN, DOWN])
    out = _run(world, monkeypatch, telegram=True)
    assert out["down"] is True and out["action"] == "open"
    method, path, body = world.calls[-1]
    assert (method, path) == ("POST", "/issues") and body["labels"] == ["uptime"] and uptime.MARKER in body["body"]
    assert "https://aialgodesk.tech/api/system/health" in body["body"]
    assert world.telegram and world.telegram[0]["chat_id"] == "555" and "DOWN" in world.telegram[0]["text"]
    assert "FAKE-uptime-bot-token" not in json.dumps(out)


def test_an_open_issue_is_edited_while_down_and_closed_on_recovery(monkeypatch):
    issue = [{"number": 7, "body": uptime.MARKER + " x"}, {"number": 8, "body": "someone else's", "pull_request": {}}]
    world = World([DOWN, DOWN], issues=issue)
    assert _run(world, monkeypatch)["action"] == "update"
    assert world.calls[-1][:2] == ("PATCH", "/issues/7") and "state" not in world.calls[-1][2]
    world = World([UP], issues=issue)
    out = _run(world, monkeypatch, telegram=True)
    assert out["action"] == "close"
    assert [c[:2] for c in world.calls[1:]] == [("POST", "/issues/7/comments"), ("PATCH", "/issues/7")]
    assert world.calls[-1][2] == {"state": "closed", "state_reason": "completed"} and "back up" in world.telegram[0]["text"]


def test_health_must_say_ok():
    probe = uptime.probe("https://x/api/system/health", timeout=1, expect_health=True,
                         opener=lambda r, timeout=None: _Resp(200, b'{"status": "degraded"}'))
    assert probe.ok is False
    probe = uptime.probe("https://x/api/system/health", timeout=1, expect_health=True, opener=lambda r, timeout=None: _Resp(200, b"<html>"))
    assert probe.ok is False and "not JSON" in probe.detail
    assert uptime.decide(False, None) == "none" and uptime.decide(True, None) == "open"


def test_without_telegram_secrets_nothing_is_sent_there(monkeypatch):
    world = World([DOWN, DOWN])
    out = _run(world, monkeypatch, telegram=False)
    assert out["telegram"] is None and world.telegram == []


def test_the_workflow_has_no_secret_values_and_minimal_rights():
    text = WORKFLOW.read_text()
    wf = yaml.safe_load(text)
    on = wf.get("on") or wf.get(True)
    assert on["schedule"][0]["cron"] == "7-59/10 * * * *" and "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "read", "issues": "write"}
    job = wf["jobs"]["probe"]
    assert job["if"] == "github.repository == 'abhishekwasu-hue/AdvanceTradingPlatform'" and job["timeout-minutes"] <= 10
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@") and len(checkout["uses"].split("@")[1]) == 40
    assert checkout["with"]["persist-credentials"] is False
    env = job["steps"][1]["env"]
    assert env["UPTIME_TELEGRAM_BOT_TOKEN"] == "${{ secrets.UPTIME_TELEGRAM_BOT_TOKEN }}"
    assert env["UPTIME_TELEGRAM_CHAT_ID"] == "${{ secrets.UPTIME_TELEGRAM_CHAT_ID }}"
    for value in env.values():
        assert value.startswith("${{ ") and value.endswith(" }}"), value       # names only, no value in git


def test_actionlint_passes():
    if not shutil.which("actionlint"):
        pytest.skip("actionlint not installed")
    subprocess.run(["actionlint", str(WORKFLOW)], check=True)
