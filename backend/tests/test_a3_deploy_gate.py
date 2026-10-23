"""A3 (review R3, RDY-9): the deploy gate's helper (deploy/ci_gate.py) against a fake `curl` serving a local JSON file
in place of the public GitHub API, and the audit row for an untested deploy (backend/scripts/deploy_audit.py).
atp.sh's own use of the gate is pinned in test_z11_deploy_bundle.py (the fake host). Nothing here reaches the network.
"""
import asyncio
import io
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from scripts import deploy_audit
from tests.test_auth_api import _session_factory

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "deploy" / "ci_gate.py"
SHA = "a" * 40
OLD = "b" * 40

# The fake curl: answers from $FAKE_GH (a JSON file {"runs": {sha: [run, ...]}, "down": bool}) and logs every URL.
FAKE_CURL = r'''
import json, os, sys
st = json.load(open(os.environ["FAKE_GH"]))
url = sys.argv[-1]
with open(os.environ["FAKE_GH"] + ".log", "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
if st.get("down"):
    print("curl: (28) Operation timed out", file=sys.stderr); sys.exit(28)
path, _, query = url.split("/repos/example/atp/", 1)[1].partition("?")
q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
runs = [dict(r, head_sha=s) for s, rs in st["runs"].items() for r in rs]
if path == "actions/runs":
    print(json.dumps({"workflow_runs": [r for r in runs if r["head_sha"] == q["head_sha"]]}))
elif path.startswith("actions/runs/"):
    run = [r for r in runs if r["id"] == int(path.split("/")[2])][0]
    print(json.dumps({"jobs": [{"name": n, "status": "completed" if c != "in_progress" else c, "conclusion": None if c == "in_progress" else c}
                               for n, c in run["jobs"].items()]}))
elif path.startswith("actions/workflows/"):
    wf = path.split("/")[2]
    print(json.dumps({"workflow_runs": [r for r in reversed(runs) if r["path"].endswith(wf) and r["conclusion"] == "success"]}))
else:
    sys.exit(22)
'''


def run(rid, wf, jobs, status="completed"):
    bad = [c for c in jobs.values() if c not in ("success", "skipped", "in_progress")]
    return {"id": rid, "path": f".github/workflows/{wf}", "status": status,
            "conclusion": None if status != "completed" else ("failure" if bad else "success"), "jobs": jobs}


def green(base, **over):
    ci = {"lint": "success", "backend": "success", "backend-lookahead": "success", "perf": "success", "frontend": "success",
          "container-scan": "success"}
    stack = {"gate": "success", "acceptance": "success", "load-500": "success", "rollback": "success", "offsite": "success"}
    for k, v in over.items():
        (ci if k.replace("_", "-") in ci else stack)[k.replace("_", "-")] = v
    return [run(base, "ci.yml", ci), run(base + 1, "staging-stack.yml", stack)]


@pytest.fixture
def gh(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(f"#!{sys.executable} -S\n{FAKE_CURL}")
    (bin_dir / "curl").chmod(0o755)
    state = tmp_path / "gh.json"

    def check(runs, sha=SHA, down=False, **env):
        state.write_text(json.dumps({"runs": runs, "down": down}))
        e = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_GH": str(state), "CI_GATE_REPO": "example/atp", **env}
        for k in ("CI_GATE_REQUIRED", "CI_GATE_KNOWN_RED", "CI_GATE_API"):
            if k not in env:
                e.pop(k, None)
        res = subprocess.run([sys.executable, str(GATE), "check", sha], env=e, capture_output=True, text=True, timeout=60)
        res.json = json.loads(res.stdout) if res.stdout.strip() else {}
        log = Path(str(state) + ".log")
        res.calls = log.read_text().splitlines() if log.exists() else []
        if log.exists():
            log.unlink()
        return res
    return check


def test_a_commit_whose_required_jobs_passed_passes_without_any_token(gh):
    res = gh({SHA: green(10)})
    assert res.returncode == 0 and res.json["verdict"] == "pass" and res.json["newest_passed"] is None
    assert len(res.calls) == 3                                         # runs of the sha + the jobs of its two runs
    assert all("authorization" not in c.lower() and "token" not in c.lower() for c in res.calls)
    assert all("https://api.github.com/repos/example/atp/actions/" in c for c in res.calls)
    assert "branch=main" in res.calls[0] and "event=push" in res.calls[0] and f"head_sha={SHA}" in res.calls[0]


def test_a_failed_required_job_fails_and_names_the_newest_commit_that_passed(gh):
    res = gh({OLD: green(10), SHA: green(20, backend="failure")})
    assert res.returncode == 1 and res.json["verdict"] == "fail"
    assert "CI job 'backend' failure" in res.json["detail"] and res.json["newest_passed"] == OLD


def test_any_other_failed_job_blocks_unless_it_is_on_the_known_red_list(gh):
    res = gh({SHA: green(10, perf="failure")})
    assert res.json["verdict"] == "fail" and "CI job 'perf' failure" in res.json["detail"]
    res = gh({SHA: green(10, load_500="failure")})                    # LD-9: known red by default
    assert res.returncode == 0 and res.json["verdict"] == "pass" and res.json["known_red_text"] == "staging stack 'load-500'"
    res = gh({SHA: green(10, load_500="failure")}, CI_GATE_KNOWN_RED="")   # the owner emptied the list
    assert res.json["verdict"] == "fail" and "'load-500' failure" in res.json["detail"]


def test_a_skipped_or_missing_required_job_is_not_a_pass(gh):
    res = gh({SHA: green(10, rollback="skipped")})
    assert res.json["verdict"] == "fail" and "'rollback' skipped" in res.json["detail"]
    runs = green(10)
    del runs[1]["jobs"]["offsite"]
    assert "'offsite' did not run" in gh({SHA: runs}).json["detail"]
    res = gh({SHA: green(10)[:1]})                                      # the staging stack never ran (or was cancelled unstarted)
    assert res.json["verdict"] == "fail" and "no staging stack result on main" in res.json["detail"]


def test_a_cancelled_run_fails_and_a_running_one_is_pending(gh):
    res = gh({SHA: green(10, frontend="cancelled")})
    assert res.json["verdict"] == "fail" and "'frontend' cancelled" in res.json["detail"]
    runs = green(10)
    runs[1] = run(11, "staging-stack.yml", {"gate": "success", "acceptance": "in_progress"}, status="in_progress")
    res = gh({OLD: green(30), SHA: runs})
    assert res.returncode == 1 and res.json["verdict"] == "pending" and "staging stack still running" in res.json["detail"]
    assert res.json["newest_passed"] == OLD
    runs[1] = run(11, "staging-stack.yml", {"gate": "success", "acceptance": "failure", "rollback": "in_progress"}, status="in_progress")
    assert gh({SHA: runs}).json["verdict"] == "fail"                     # a required job already failed: no need to wait


def test_the_newest_run_of_a_workflow_counts(gh):
    runs = green(10, backend="failure") + green(40)                    # re-run as a new run: the higher id wins
    assert gh({SHA: runs}).json["verdict"] == "pass"


def test_github_unreachable_is_its_own_answer_and_settings_are_checked(gh):
    res = gh({SHA: green(10)}, down=True)
    assert res.returncode == 3 and res.json["verdict"] == "unreachable" and "could not be reached" in res.json["detail"]
    res = gh({SHA: green(10)}, CI_GATE_REPO="")
    assert res.returncode == 2 and "CI_GATE_REPO" in res.json["detail"] and res.calls == []
    res = gh({SHA: green(10)}, CI_GATE_KNOWN_RED="ci.yml:backend")
    assert res.returncode == 2 and "cannot also be on the known-red list" in res.json["detail"]
    res = gh({SHA: green(10)}, CI_GATE_REQUIRED="ci.yml")
    assert res.returncode == 2 and "workflow-file:job" in res.json["detail"]
    res = gh({SHA: green(10)}, CI_GATE_REQUIRED="ci.yml:lint ci.yml:backend")   # a narrower list from .env
    assert res.json["verdict"] == "pass" and len(res.calls) == 2
    assert gh({}, sha="not-a-sha").returncode == 2


def test_the_audit_row_for_an_untested_deploy_keeps_the_chain_and_takes_the_reason_on_stdin(monkeypatch, capsys):
    from app.audit.log import verify_audit_chain
    from app.db.models import AuditLogRecord

    reason = f"hotfix for the login page {uuid.uuid4().hex[:6]}"
    detail = asyncio.run(deploy_audit.record("deploy_untested", SHA, deploy_audit.clean_reason(reason + "\x00\x07\n"), _session_factory))
    assert detail == f"{SHA[:12]} - {reason}"

    async def check():
        async with _session_factory() as session:
            row = await session.scalar(select(AuditLogRecord).where(AuditLogRecord.detail == detail))
            ok, _ = await verify_audit_chain(session)
            return row, ok
    row, ok = asyncio.run(check())
    assert row is not None and row.event == "deploy_untested" and row.tenant_id is None and row.user_id is None and ok
    assert deploy_audit.main(["deploy_audit.py", "deploy_untested"]) == 2
    assert deploy_audit.main(["deploy_audit.py", "something_else", SHA]) == 2
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert deploy_audit.main(["deploy_audit.py", "deploy_untested", SHA]) == 2        # no reason: nothing written
    assert len(deploy_audit.clean_reason("x" * 500)) == 200
