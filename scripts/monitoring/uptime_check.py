#!/usr/bin/env python3
"""Production readiness A2 (changed by RDY-21): the outside uptime check, run by .github/workflows/uptime.yml.

From a GitHub runner (outside the Hostinger host) it asks the site and the API's liveness endpoint every 10 minutes:

    UPTIME_BASE_URL (default https://aialgodesk.tech)  ->  GET /  and  GET /api/system/health

A round fails when either answer is not HTTP 200 (or the health body is not {"status": "ok"}) within the timeout.
Two rounds, UPTIME_RETRY_SECONDS apart (default 90): only **two failures in a row** count as down, so one dropped
packet never pages anyone.

The repository is PUBLIC, so an outage is never written to a GitHub issue or comment. The only alert goes privately to
the owner's Telegram chat, through the repository secrets UPTIME_TELEGRAM_BOT_TOKEN and UPTIME_TELEGRAM_CHAT_ID
(names only; their values are never printed):

* down, the owner was not told yet       -> one "DOWN" message;
* down, the owner was already told        -> nothing (no repeat every 10 minutes);
* up again after a "DOWN" message         -> one "back up" message;
* up, nothing was sent                    -> nothing.

What was sent is remembered in a small JSON file (UPTIME_STATE_FILE) that the workflow carries from run to run in the
Actions cache. Without it (the first run, or the cache was evicted) the check starts from "up, nothing sent". Without
the secrets the run logs a plain notice and sends nothing; the free outside uptime service (runbook 10b) is the
primary monitor either way. Standard library only. Exit 0 unless the settings are wrong: a down site or a failed
Telegram call is an alert, never a red workflow run.
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

USER_AGENT = "atp-uptime-check (GitHub Actions)"
DEFAULT_STATE: Dict = {"down": False, "down_alerted": False, "since": None}
NO_TELEGRAM_NOTICE = ("Notice: Telegram is not set up for the outside check (repository secrets "
                      "UPTIME_TELEGRAM_BOT_TOKEN and UPTIME_TELEGRAM_CHAT_ID), so no message was sent. "
                      "The outside uptime service is the primary monitor (runbook section 10b).")


@dataclass
class Probe:
    url: str
    ok: bool
    detail: str


def probe(url: str, *, timeout: float, expect_health: bool, opener: Optional[Callable] = None) -> Probe:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
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


def load_state(path: Optional[str]) -> Dict:
    """The last run's state; a missing or unreadable file is "up, nothing sent"."""
    state = dict(DEFAULT_STATE)
    if not path:
        return state
    try:
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return state
    if isinstance(stored, dict):
        state["down"] = stored.get("down") is True
        state["down_alerted"] = stored.get("down_alerted") is True
        state["since"] = stored.get("since") if isinstance(stored.get("since"), str) else None
    return state


def save_state(path: Optional[str], state: Dict) -> None:
    if not path:
        return
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def decide(previous: Dict, down: bool) -> str:
    """alert_down | alert_recovered | none. One DOWN message per outage, one recovery message after it."""
    if down:
        return "none" if previous.get("down_alerted") else "alert_down"
    return "alert_recovered" if previous.get("down_alerted") else "none"


def telegram_configured() -> bool:
    return bool(os.environ.get("UPTIME_TELEGRAM_BOT_TOKEN", "").strip()
                and os.environ.get("UPTIME_TELEGRAM_CHAT_ID", "").strip())


def telegram(text: str, *, opener: Optional[Callable] = None) -> Optional[bool]:
    """True sent, False refused / unreachable, None not set up. Never prints the URL: it holds the token."""
    token = os.environ.get("UPTIME_TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("UPTIME_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return None
    data = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            return getattr(response, "status", 200) == 200
    except Exception:  # noqa: BLE001 - a failed send is retried by the next run
        return False


def run(*, base: str, retry_seconds: float, timeout: float, previous: Optional[Dict] = None,
        sleep: Optional[Callable] = None, opener: Optional[Callable] = None,
        now: Optional[datetime] = None) -> Dict:
    prev = dict(DEFAULT_STATE, **(previous or {}))
    first = [probe(url, timeout=timeout, expect_health=health, opener=opener) for url, health in targets(base)]
    results = first
    down = False
    if not round_ok(first):
        (sleep or time.sleep)(retry_seconds)
        results = [probe(url, timeout=timeout, expect_health=health, opener=opener) for url, health in targets(base)]
        down = not round_ok(results)
    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    action = decide(prev, down)
    sent: Optional[bool] = None
    alerted = bool(prev["down_alerted"])
    if action == "alert_down":
        sent = telegram(f"AiAlgoDesk is DOWN (checked from outside at {when}): "
                        + "; ".join(f"{p.url} {p.detail}" for p in results if not p.ok), opener=opener)
        alerted = sent is True                      # not sent: the next run that still sees it down tries again
    elif action == "alert_recovered":
        since = f" It was down from {prev['since']}." if prev.get("since") else ""
        sent = telegram(f"AiAlgoDesk is back up (checked from outside at {when}).{since}", opener=opener)
        alerted = sent is not True                  # not sent: the next good run tries the recovery message again
    since = (prev["since"] if prev["down"] and prev.get("since") else when) if down else None
    state = {"down": down, "down_alerted": alerted, "since": since}
    return {"down": down, "action": action, "telegram": sent, "state": state,
            "changed": state != {k: prev.get(k) for k in DEFAULT_STATE},
            "results": [{"url": p.url, "ok": p.ok, "detail": p.detail} for p in results]}


def main() -> int:
    base = os.environ.get("UPTIME_BASE_URL", "").strip() or "https://aialgodesk.tech"
    if not base.startswith("https://"):
        print("UPTIME_BASE_URL must start with https://")
        return 2
    state_file = os.environ.get("UPTIME_STATE_FILE", "").strip() or None
    if not telegram_configured():
        print(NO_TELEGRAM_NOTICE)
    result = run(base=base, retry_seconds=float(os.environ.get("UPTIME_RETRY_SECONDS", "90") or 90),
                 timeout=float(os.environ.get("UPTIME_TIMEOUT_SECONDS", "20") or 20), previous=load_state(state_file))
    try:
        save_state(state_file, result["state"])
    except OSError as exc:
        print(f"Notice: the state file could not be written ({type(exc).__name__}); the next run starts fresh.")
    print(json.dumps({k: result[k] for k in ("down", "action", "telegram", "changed", "results")}))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if result['changed'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
