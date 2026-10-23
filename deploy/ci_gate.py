#!/usr/bin/env python3
"""A3 (review R3): the deploy gate. `atp.sh deploy` runs this before it changes anything.

    python3 deploy/ci_gate.py check SHA

It asks the PUBLIC GitHub API (no token, nothing secret on the server; the repository is public) whether the push
runs on main for SHA passed: every REQUIRED job concluded success, and every other job of those workflows either
passed, was skipped, or is on the KNOWN-RED list. Settings (atp.sh passes them from .env, defaults below):

    CI_GATE_REPO       owner/name on GitHub (atp.sh derives it from the clone's origin URL)
    CI_GATE_REQUIRED   workflow-file:job pairs that must pass, space or comma separated
    CI_GATE_KNOWN_RED  workflow-file:job pairs that are known to fail on main and do not block (documented in
                       docs/deploy/HOSTINGER_RUNBOOK.md section 8a; today: the 500-user load test, LD-9)
    CI_GATE_API        API base (default https://api.github.com; tests point the fake curl at it)

Every call goes through `curl` on PATH (the host already needs it). Prints one JSON line:

    {"verdict": "pass" | "pending" | "fail" | "unreachable" | "unconfigured", "sha": ..., "detail": "plain sentence",
     "newest_passed": sha or null, "known_red": [...]}

Exit: 0 pass | 1 pending or fail | 3 GitHub could not be reached | 2 usage or settings. Standard library only.
"""
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_REQUIRED = ("ci.yml:lint ci.yml:backend ci.yml:backend-lookahead ci.yml:frontend "
                    "staging-stack.yml:acceptance staging-stack.yml:rollback staging-stack.yml:offsite")
DEFAULT_KNOWN_RED = "staging-stack.yml:load-500"
WORKFLOW_LABEL = {"ci.yml": "CI", "staging-stack.yml": "staging stack"}
BAD = {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
CANDIDATES = 3          # main commits looked at for "the newest one that passed" (each costs 1-3 API calls)


class Unreachable(Exception):
    pass


def pairs(text: str) -> List[Tuple[str, str]]:
    out = []
    for item in re.split(r"[\s,]+", text.strip()):
        if not item:
            continue
        wf, sep, job = item.partition(":")
        if not sep or not wf or not job:
            raise ValueError(f"'{item}' is not workflow-file:job")
        out.append((wf, job))
    return out


def label(wf: str) -> str:
    return WORKFLOW_LABEL.get(wf, wf)


class Api:
    def __init__(self, base: str, repo: str):
        self.base = base.rstrip("/")
        self.repo = repo
        self.calls = 0

    def get(self, path: str) -> dict:
        self.calls += 1
        url = f"{self.base}/repos/{self.repo}/{path}"
        try:
            res = subprocess.run(["curl", "-fsSL", "--max-time", "20", "-H", "Accept: application/vnd.github+json",
                                  "-H", "X-GitHub-Api-Version: 2022-11-28", url],
                                 capture_output=True, text=True, timeout=40)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Unreachable(str(exc)) from exc
        if res.returncode != 0:
            lines = (res.stderr or "").strip().splitlines()
            raise Unreachable(lines[-1][:200] if lines else f"curl exit {res.returncode}")
        try:
            data = json.loads(res.stdout)
        except ValueError as exc:
            raise Unreachable("the answer was not JSON") from exc
        if not isinstance(data, dict):
            raise Unreachable("unexpected answer")
        return data


def latest_runs(api: Api, sha: str, workflows: List[str]) -> Dict[str, dict]:
    data = api.get(f"actions/runs?head_sha={sha}&branch=main&event=push&per_page=100")
    best: Dict[str, dict] = {}
    for run in data.get("workflow_runs") or []:
        if run.get("head_sha") != sha:
            continue
        wf = os.path.basename(str(run.get("path") or "").split("@", 1)[0])
        if wf not in workflows:
            continue
        if wf not in best or int(run.get("id") or 0) > int(best[wf].get("id") or 0):
            best[wf] = run
    return best


def evaluate(api: Api, sha: str, required: List[Tuple[str, str]], known_red: List[Tuple[str, str]]) -> Tuple[str, str, List[str]]:
    """(verdict, plain detail, known-red jobs that failed) for one commit."""
    workflows = sorted({wf for wf, _ in required})
    runs = latest_runs(api, sha, workflows)
    short = sha[:12]
    missing = [wf for wf in workflows if wf not in runs]
    if missing:
        return ("fail", f"{short} has no {', '.join(label(w) for w in missing)} result on main (the run was cancelled "
                        "before it started, or never ran)", [])
    running = [wf for wf in workflows if runs[wf].get("status") != "completed"]
    problems: List[str] = []
    red_seen: List[str] = []
    pending: List[str] = [f"{label(wf)} still running" for wf in running]
    for wf in workflows:
        run = runs[wf]
        jobs = api.get(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100").get("jobs") or []
        by_name = {str(j.get("name")): j for j in jobs}
        for rwf, job in required:
            if rwf != wf:
                continue
            j = by_name.get(job)
            if j is None:
                if wf not in running:
                    problems.append(f"{label(wf)} job '{job}' did not run")
            elif j.get("status") != "completed":
                if wf not in running:
                    pending.append(f"{label(wf)} job '{job}' still running")
            elif j.get("conclusion") != "success":
                problems.append(f"{label(wf)} job '{job}' {j.get('conclusion') or 'did not finish'}")
        required_here = {job for rwf, job in required if rwf == wf}
        allowed = {job for kwf, job in known_red if kwf == wf}
        for name, j in by_name.items():
            if name in required_here:
                continue
            if j.get("status") == "completed" and j.get("conclusion") in BAD:
                if name in allowed:
                    red_seen.append(f"{label(wf)} '{name}'")
                else:
                    problems.append(f"{label(wf)} job '{name}' {j.get('conclusion')}")
            elif j.get("status") != "completed" and name not in allowed and wf not in running:
                pending.append(f"{label(wf)} job '{name}' still running")
    if problems:
        return "fail", f"{short} did not pass its checks: " + "; ".join(problems), red_seen
    if pending:
        return "pending", f"the checks for {short} are still running ({'; '.join(pending)})", red_seen
    return "pass", f"{short} passed every required check on main", red_seen


def newest_passed(api: Api, skip: str, required: List[Tuple[str, str]], known_red: List[Tuple[str, str]]) -> Optional[str]:
    first = sorted({wf for wf, _ in required})[0] if required else "ci.yml"
    data = api.get(f"actions/workflows/{first}/runs?branch=main&event=push&status=success&per_page=10")
    seen: List[str] = []
    for run in data.get("workflow_runs") or []:
        sha = str(run.get("head_sha") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or sha == skip or sha in seen:
            continue
        seen.append(sha)
        if len(seen) > CANDIDATES:
            break
        if evaluate(api, sha, required, known_red)[0] == "pass":
            return sha
    return None


def main(argv: List[str]) -> int:
    if len(argv) != 3 or argv[1] != "check" or not re.fullmatch(r"[0-9a-f]{40}", argv[2]):
        print("usage: ci_gate.py check <full 40-character commit sha>", file=sys.stderr)
        return 2
    sha = argv[2]
    repo = os.environ.get("CI_GATE_REPO", "").strip()
    out: Dict[str, Any] = {"verdict": "unconfigured", "sha": sha, "detail": "", "newest_passed": None, "known_red": [], "known_red_text": ""}
    try:
        required = pairs(os.environ.get("CI_GATE_REQUIRED") or DEFAULT_REQUIRED)
        known_red = pairs(os.environ.get("CI_GATE_KNOWN_RED", DEFAULT_KNOWN_RED))
    except ValueError as exc:
        out["detail"] = f"the gate settings are wrong: {exc}"
        print(json.dumps(out))
        return 2
    clash = sorted(f"{w}:{j}" for w, j in set(required) & set(known_red))
    if clash:
        out["detail"] = f"a required check cannot also be on the known-red list: {', '.join(clash)}"
        print(json.dumps(out))
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        out["detail"] = "cannot tell which GitHub repository to check - set CI_GATE_REPO=owner/name in .env"
        print(json.dumps(out))
        return 2
    api = Api(os.environ.get("CI_GATE_API") or "https://api.github.com", repo)
    try:
        verdict, detail, red = evaluate(api, sha, required, known_red)
        out.update(verdict=verdict, detail=detail, known_red=red, known_red_text="; ".join(red))
        if verdict != "pass":
            try:
                out["newest_passed"] = newest_passed(api, sha, required, known_red)
            except Unreachable:
                pass
    except Unreachable as exc:
        out.update(verdict="unreachable", detail=f"GitHub could not be reached to read the checks of {sha[:12]} ({exc}); "
                                                 "the free API allows 60 questions an hour from one server")
    print(json.dumps(out))
    return {"pass": 0, "pending": 1, "fail": 1, "unreachable": 3}.get(out["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
