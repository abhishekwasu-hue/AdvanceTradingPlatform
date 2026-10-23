#!/usr/bin/env python3
"""Production readiness A2: the outside uptime check, run by .github/workflows/uptime.yml every 10 minutes.

From a GitHub runner (outside the Hostinger host) it asks the site and the API's liveness endpoint:

    UPTIME_BASE_URL (default https://aialgodesk.tech)  ->  GET /  and  GET /api/system/health

A round fails when either answer is not HTTP 200 (or the health body is not {"status": "ok"}) within the timeout.
Two rounds, UPTIME_RETRY_SECONDS apart (default 90): only **two failures in a row** count as down, so one dropped
packet never pages anyone. Then, on the repository's own issues (the workflow's GITHUB_TOKEN, `issues: write`):

* down, no open uptime issue  -> a new issue (label `uptime`); GitHub e-mails the owner;
* down, the issue is open      -> its body is updated with the latest failure (an edit sends no e-mail);
* up again, the issue is open  -> a "back up" comment (e-mailed) and the issue is closed;
* up, no issue                 -> nothing.

Optional Telegram: when the repository secrets UPTIME_TELEGRAM_BOT_TOKEN and UPTIME_TELEGRAM_CHAT_ID exist, the same
"down" and "back up" lines go there too. Their values are never printed, and nothing secret lives in git.
Standard library only. Exit 0 unless the script itself could not work (bad settings, GitHub refused the issue call).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

LABEL = "uptime"
TITLE = "Uptime: the site is not answering"
MARKER = "<!-- atp-uptime-check -->"
USER_AGENT = "atp-uptime-check (GitHub Actions)"


@dataclass
class Probe:
    url: str
    ok: bool
    detail: str


def probe(url: str, *, timeout: float, expect_health: bool, opener: Callable = urllib.request.urlopen) -> Probe:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read(4096)
    except urllib.error.HTTPError as exc:
        return Probe(url, False, f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 - DNS, TLS, timeout, refused: all are "down" from outside
        return Probe(url, False, type(exc).__name__)
    if status != 200:
        return Probe(url, False, f"HTTP {status}")
    if expect_health:
        try:
            if json.loads(body.decode("utf-8", "replace")).get("status") != "ok":
                return Probe(url, False, "health answered without status ok")
        except (ValueError, AttributeError):
            return Probe(url, False, "health answered with a body that is not JSON")
    return Probe(url, True, "HTTP 200")


def targets(base: str) -> List[Tuple[str, bool]]:
    base = base.rstrip("/")
    return [(base + "/", False), (base + "/api/system/health", True)]


def round_ok(results: List[Probe]) -> bool:
    return all(p.ok for p in results)


def decide(down: bool, open_issue: Optional[int]) -> str:
    """open | update | close | none."""
    if down:
        return "update" if open_issue else "open"
    return "close" if open_issue else "none"


def report(results: List[Probe], when: str) -> str:
    lines = [MARKER, f"Last checked from outside (GitHub Actions): {when}", "",
             "Two checks in a row failed:", ""]
    lines += [f"- `{p.url}`: {'ok' if p.ok else 'FAILED'} ({p.detail})" for p in results]
    lines += ["", "What to do: open the runbook, section 10 (health) - run `atp.sh health` on the server.",
              "This issue closes itself when the site answers again."]
    return "\n".join(lines)


class GitHub:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com",
                 opener: Callable = urllib.request.urlopen) -> None:
        self.repo, self.token, self.api, self.opener = repo, token, api.rstrip("/"), opener

    def call(self, method: str, path: str, payload: Optional[Dict] = None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(f"{self.api}/repos/{self.repo}{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28", **({"Content-Type": "application/json"} if data else {})})
        with self.opener(request, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def open_issue(self) -> Optional[int]:
        items = self.call("GET", f"/issues?state=open&labels={LABEL}&per_page=20") or []
        for item in items:
            if "pull_request" not in item and MARKER in (item.get("body") or ""):
                return int(item["number"])
        return None


def telegram(text: str, *, opener: Callable = urllib.request.urlopen) -> Optional[bool]:
    token = os.environ.get("UPTIME_TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("UPTIME_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return None
    data = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=20) as response:
            return getattr(response, "status", 200) == 200
    except Exception:  # noqa: BLE001 - the issue is the alert of record; never print the URL (it holds the token)
        return False


def run(*, base: str, retry_seconds: float, timeout: float, github: Optional[GitHub], sleep: Callable = time.sleep,
        opener: Callable = urllib.request.urlopen, now: Optional[datetime] = None) -> Dict:
    first = [probe(url, timeout=timeout, expect_health=health, opener=opener) for url, health in targets(base)]
    results = first
    down = False
    if not round_ok(first):
        sleep(retry_seconds)
        results = [probe(url, timeout=timeout, expect_health=health, opener=opener) for url, health in targets(base)]
        down = not round_ok(results)
    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    issue = github.open_issue() if github else None
    action = decide(down, issue)
    sent = None
    if github is not None:
        if action == "open":
            github.call("POST", "/issues", {"title": TITLE, "body": report(results, when), "labels": [LABEL]})
            sent = telegram(f"AiAlgoDesk is DOWN (checked from outside at {when}): "
                            + "; ".join(f"{p.url} {p.detail}" for p in results if not p.ok), opener=opener)
        elif action == "update":
            github.call("PATCH", f"/issues/{issue}", {"body": report(results, when)})
        elif action == "close":
            github.call("POST", f"/issues/{issue}/comments", {"body": f"Back up: both checks answered at {when}."})
            github.call("PATCH", f"/issues/{issue}", {"state": "closed", "state_reason": "completed"})
            sent = telegram(f"AiAlgoDesk is back up (checked from outside at {when}).", opener=opener)
    return {"down": down, "action": action, "issue": issue, "telegram": sent,
            "results": [{"url": p.url, "ok": p.ok, "detail": p.detail} for p in results]}


def main() -> int:
    base = os.environ.get("UPTIME_BASE_URL", "").strip() or "https://aialgodesk.tech"
    if not base.startswith("https://"):
        print("UPTIME_BASE_URL must start with https://")
        return 2
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    github = GitHub(repo, token) if repo and token else None
    try:
        result = run(base=base, retry_seconds=float(os.environ.get("UPTIME_RETRY_SECONDS", "90") or 90),
                     timeout=float(os.environ.get("UPTIME_TIMEOUT_SECONDS", "20") or 20), github=github)
    except urllib.error.HTTPError as exc:
        print(f"GitHub refused the issue call: HTTP {exc.code}")
        return 1
    print(json.dumps(result))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"Uptime: {'DOWN' if result['down'] else 'up'} - action: {result['action']}\n\n")
            fh.writelines(f"- {r['url']}: {'ok' if r['ok'] else 'FAILED'} ({r['detail']})\n" for r in result["results"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
