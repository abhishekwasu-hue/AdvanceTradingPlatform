"""Handover 8d: the login error sentence, and `atp.sh reset-password` (backend/scripts/reset_password.py) - the new
password works, the old one and every session stop working, the rules apply, nothing echoes the password."""
import asyncio
import io
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from scripts import reset_password
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner

ROOT = Path(__file__).resolve().parents[2]
NEW = "Fresh-Owner-Pass-2026"


def _login(email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_wrong_password_says_so_plainly():
    email = f"reset-wrong-{uuid.uuid4().hex[:6]}@example.com"
    _owner(email)
    got = _login(email, "Not-The-Password-1")
    assert got.status_code == 401 and got.json()["detail"] == "Wrong email or password."
    unknown = _login(f"nobody-{uuid.uuid4().hex[:6]}@example.com", "Not-The-Password-1")
    assert unknown.status_code == 401 and unknown.json()["detail"] == "Wrong email or password."   # same: no account oracle


def test_reset_sets_the_password_and_signs_out_every_session():
    email = f"reset-ok-{uuid.uuid4().hex[:6]}@example.com"
    headers, _ = _owner(email)
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    code, line = asyncio.run(reset_password.reset(email.upper(), NEW, _session_factory))
    assert code == 0 and email in line and "session(s) signed out" in line and NEW not in line
    assert _login(email, NEW).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401        # the old session is gone


def test_reset_refuses_an_unknown_user_and_a_weak_password():
    code, line = asyncio.run(reset_password.reset(f"ghost-{uuid.uuid4().hex[:6]}@example.com", NEW, _session_factory))
    assert code == 3 and NEW not in line
    email = f"reset-weak-{uuid.uuid4().hex[:6]}@example.com"
    _owner(email)
    code, line = asyncio.run(reset_password.reset(email, "short", _session_factory))
    assert code == 4 and line.startswith("password not changed:") and "short" not in line.split(":", 1)[1].replace("too short", "")


def test_the_password_comes_only_on_stdin(monkeypatch, capsys):
    assert reset_password.main(["reset_password.py"]) == 2                         # no e-mail
    assert reset_password.main(["reset_password.py", "a@example.com", NEW]) == 2   # never as an argument
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert reset_password.main(["reset_password.py", "a@example.com"]) == 4        # nothing typed
    assert NEW not in capsys.readouterr().err


def test_atp_sh_wires_the_command_without_exposing_the_password():
    script = (ROOT / "deploy" / "atp.sh").read_text()
    body = script[script.index("cmd_reset_password() {"):script.index("\n}\n", script.index("cmd_reset_password() {"))]
    assert "read -r -s" in body                                  # typed without echo
    assert 'printf \'%s\\n\' "$pw" | dc exec -T backend python scripts/reset_password.py "$email"' in body   # stdin only
    assert not re.search(r"^\s*set -[a-z]*x|xtrace", script, re.M) and "echo \"$pw" not in body   # never traced
    assert "reset-password) cmd_reset_password" in script
    help_text = subprocess.run(["bash", str(ROOT / "deploy" / "atp.sh"), "help"], capture_output=True, text=True,
                               env={**os.environ, "ATP_ENV_FILE": "/nonexistent"}).stdout
    assert "reset-password EMAIL" in help_text and "--dry-run" in help_text
