"""Runner S4: the runner's side of the link to ATP (docs/design/USER_RUNNER.md section 4).

Outbound HTTPS only: the runner calls ATP; nothing on the user's machine listens. The design's WSS channel is a later
transport; S4 uses the HTTPS polling route the design names as its fallback (one signed heartbeat per period).

- Pairing (`pair`, CLI `python -m app.runner pair <code> --atp <address>`): the runner makes its own Ed25519 key HERE
  (`device.key`, mode 600, never sent), presents the user's one-time code with the public key and a proof signature
  (S3 `POST /api/runner/pair`), and keeps the device id, the user it is paired to and ATP's command key (`link.json`).
- Every later request is signed with the device key over the same canonical bytes ATP checks
  (app/runner_link/signing.py, ONE module both sides import), with a timestamp newer than the last one sent.
- Heartbeat (every `heartbeat_seconds`): run state, version, the kill switch state, the daily broker login line (S5
  `status_line`), open positions. The answer carries the user's mirror choice (rule R-8), the user's own commands and
  the etag of his saved rules.
- Commands (rule R-1): applied only when signed with the pinned ATP command key, for THIS device and the user it was
  paired to, with the one origin "user_signed" and an id newer than the last applied. Anything else is refused and
  logged. Today the only command is his kill switch, which stops new entries only (exits always run, ADR-0004).
- Rules sync (rules R-2 / R-3): when the etag changes the runner pulls his saved strategies, deployments and settings
  into its local store (`sync.apply_rules`); reference data (F&O ban list, contracts) once per day.
- Mirror (rule R-8): only while his mirror choice is ON; idempotent on ATP; paged.
- Offline: exits, stops and square-offs never wait for ATP. New entries pause once ATP has not answered for
  `link_grace_minutes` (a plain line in `link.log` and the audit log) and resume when it answers again.
Nothing here places, changes or cancels an order.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import socket
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, load_pem_private_key

from app.runner_link import signing

logger = logging.getLogger("app.runner.link")

RUNNER_VERSION = "0.4.0"
PAIR_PATH = "/api/runner/pair"
HEARTBEAT_PATH = "/api/runner/device/heartbeat"
MIRROR_PATH = "/api/runner/device/mirror"
RULES_PATH = "/api/runner/device/rules"
REFERENCE_PATH = "/api/runner/device/reference"
HTTP_TIMEOUT_SECONDS = 15.0          # one request to ATP (operational); exits never wait for it
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_IST = ZoneInfo("Asia/Kolkata")         # the reference data's day (operational)

NOT_REACHED = "ATP has not answered since {since} - new entries pause until the link returns; exits continue"
NEVER_REACHED = "ATP has not answered since this runner started - new entries pause until the link returns; exits continue"


class LinkError(Exception):
    """A plain reason for the user (never a key, a signature or ATP's raw error text)."""


# --- files ---------------------------------------------------------------------------------------------------------

def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def _private_ok(path: Path) -> None:
    if os.stat(path).st_mode & 0o077:
        raise LinkError(f"{path} must be readable by you only (chmod 600 {path})")


def load_key(path: Path) -> Ed25519PrivateKey:
    _private_ok(path)
    key = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise LinkError(f"{path} is not this runner's device key - pair the runner again")
    return key


def new_key(path: Path) -> Ed25519PrivateKey:
    """A new device key, made on this machine and written mode 600 (a re-pairing replaces the old one)."""
    key = Ed25519PrivateKey.generate()
    _write_private(path, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return key


@dataclass(frozen=True)
class Pairing:
    atp_url: str
    device_id: str
    user_ref: int
    command_key: str          # ATP's command key (public, 64 hex), pinned at pairing
    name: str

    @classmethod
    def load(cls, path: Path) -> Pairing | None:
        if not path.exists():
            return None
        _private_ok(path)
        raw = json.loads(path.read_text())
        return cls(atp_url=str(raw["atp_url"]), device_id=str(raw["device_id"]), user_ref=int(raw["user_ref"]),
                   command_key=str(raw["command_key"]), name=str(raw["name"]))

    def save(self, path: Path) -> None:
        _write_private(path, json.dumps(asdict(self), sort_keys=True).encode())


def check_atp_url(url: str) -> str:
    """ATP's address: https, or http only to this machine (a local test server)."""
    parts = urlsplit((url or "").strip())
    if parts.scheme == "https" and parts.hostname:
        return f"https://{parts.netloc}"
    if parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS:
        return f"http://{parts.netloc}"
    raise LinkError("ATP's address must start with https:// (as shown on My runner)")


def default_name() -> str:
    name = re.sub(r"[^A-Za-z0-9 ._()-]", "-", socket.gethostname() or "runner").strip("-. ") or "runner"
    return ("Runner " + name)[:60] if not name[0].isalnum() else name[:60]


# --- the signed client -----------------------------------------------------------------------------------------------

class LinkClient:
    """Signed requests from this device. `http` is an httpx.Client with ATP's address as base_url (tests: ATP's
    TestClient)."""

    def __init__(self, http: httpx.Client, key: Ed25519PrivateKey, device_id: str) -> None:
        self.http = http
        self.key = key
        self.device_id = device_id
        self._last_ms = 0

    def _stamp(self) -> int:
        now_ms = int(time.time() * 1000)
        self._last_ms = max(now_ms, self._last_ms + 1)          # ATP accepts only a newer request than the last
        return self._last_ms

    def request(self, method: str, path: str, payload: Any = None) -> httpx.Response:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":"), default=str).encode()
        headers = signing.request_headers(self.key, self.device_id, method, path, body, self._stamp())
        if body:
            headers["Content-Type"] = "application/json"
        return self.http.request(method, path, content=body or None, headers=headers)


def pair(home: Path, code: str, *, atp_url: str, name: str | None = None, http: httpx.Client | None = None,
         key_path: Path | None = None, link_path: Path | None = None) -> Pairing:
    """Pairs this runner with the user's one-time code from My runner. The private key never leaves this machine."""
    base = check_atp_url(atp_url) if http is None else atp_url
    key_file = key_path or home / "device.key"
    link_file = link_path or home / "link.json"
    key = new_key(key_file)
    public = signing.public_hex(key)
    normal = re.sub(r"[\s-]", "", code or "").upper()
    body = {"code": code, "public_key": public, "proof": signing.sign(key, signing.pair_message(normal, public)),
            "name": (name or default_name())[:60], "version": RUNNER_VERSION}
    client = http or httpx.Client(base_url=base, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        resp = client.post(PAIR_PATH, json=body)
    except httpx.HTTPError as exc:
        raise LinkError(f"ATP did not answer at {base} ({type(exc).__name__}) - check the address and this machine's network") from exc
    finally:
        if http is None:
            client.close()
    if resp.status_code != 201:
        raise LinkError(_plain_error(resp, "ATP refused the pairing"))
    answer = resp.json()
    command_key = str(answer.get("command_key") or "")
    if signing.load_public_key(command_key) is None:
        raise LinkError("ATP's answer is missing its command key - update the runner and pair again")
    pairing = Pairing(atp_url=base, device_id=str(answer["device_id"]), user_ref=int(answer["user_ref"]),
                      command_key=command_key.lower(), name=str(answer.get("name") or body["name"]))
    pairing.save(link_file)
    return pairing


def _plain_error(resp: httpx.Response, fallback: str) -> str:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return detail["message"][:200]
    return f"{fallback} (HTTP {resp.status_code})"


# --- commands (rule R-1) --------------------------------------------------------------------------------------------

def verify_command(command: Any, pairing: Pairing, last_applied: int) -> tuple[dict[str, Any] | None, str | None]:
    """(payload, None) when the command may be applied; (None, reason) when it is refused. A command already applied
    (its id is not newer) returns (None, "already applied")."""
    if not isinstance(command, dict) or not isinstance(command.get("payload"), dict):
        return None, "not a command"
    payload: dict[str, Any] = command["payload"]
    if not signing.verify(pairing.command_key, str(command.get("signature") or ""), signing.command_message(payload)):
        return None, "signature does not match ATP's key pinned at pairing (pair the runner again if ATP changed it)"
    if payload.get("v") != 1 or payload.get("kind") not in signing.COMMAND_KINDS:
        return None, "unknown command"
    if payload.get("device_id") != pairing.device_id:
        return None, "the command is for another runner"
    if payload.get("user_ref") != pairing.user_ref:
        return None, "the command is not from the user this runner is paired to"
    if payload.get("origin") != signing.COMMAND_ORIGIN:
        return None, "the command is not the user's own action"
    command_id = payload.get("command_id")
    if not isinstance(command_id, int) or command_id != command.get("id"):
        return None, "not a command"
    if command_id <= last_applied:
        return None, "already applied"
    if not isinstance(payload.get("on"), bool):
        return None, "not a command"
    return payload, None


# --- the loop's link ----------------------------------------------------------------------------------------------------

@dataclass
class LinkState:
    last_ok_at: str | None = None          # when ATP last answered a signed request
    last_command_id: int = 0               # the newest command applied (replay guard)
    rules_etag: str | None = None          # the etag of the rules last applied to the local store
    mirror_since: str | None = None        # the newest order / position time mirrored
    mirror_audit_seq: int = 0              # the last audit row mirrored

    @classmethod
    def load(cls, path: Path) -> LinkState:
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        return cls(**known)

    def save(self, path: Path) -> None:
        _write_private(path, json.dumps(asdict(self), sort_keys=True).encode())


class Link:
    """One runner's link to ATP, driven by the loop once per cycle (`tick`). Network calls run in a worker thread with a
    timeout; any failure leaves the runner running on its own (exits never wait)."""

    def __init__(self, settings: Any, pairing: Pairing, client: LinkClient, sessions: Callable[[], Any], *,
                 audit: Any = None, login_status: Callable[[datetime], str] | None = None) -> None:
        self.settings = settings
        self.pairing = pairing
        self.client = client
        self.sessions = sessions
        self.audit = audit
        self.login_status = login_status
        self.state = LinkState.load(settings.link_state_path)
        self.mirror_upload = True                     # the user's choice as the last answer said (R-8 default ON)
        self.reference_day: str | None = None         # in memory: a restart pulls the day's reference data again
        self._last_beat: datetime | None = None
        self._acks: list[int] = []
        self._refused: set[int] = set()
        self._paused_reason: str | None = None
        self._revoked = False

    # --- plain lines ---------------------------------------------------------------------------------------------
    def _log(self, now: datetime, line: str) -> None:
        try:
            self.settings.home.mkdir(parents=True, exist_ok=True)
            with open(self.settings.link_log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{now.astimezone(UTC).isoformat(timespec='seconds')} {line}\n")
        except OSError:
            pass
        logger.info("Runner link: %s", line)

    def _save(self) -> None:
        try:
            self.state.save(self.settings.link_state_path)
        except OSError as exc:
            logger.warning("Runner link: state not saved (%s)", type(exc).__name__)

    # --- offline: entries only --------------------------------------------------------------------------------------
    def entries_paused(self, now: datetime) -> str | None:
        """Why new entries wait for the link now, or None. Exits never read this."""
        last = datetime.fromisoformat(self.state.last_ok_at) if self.state.last_ok_at else None
        grace = timedelta(minutes=max(0, int(self.settings.link_grace_minutes)))
        if self._revoked:
            reason: str | None = "This runner was removed from your account in ATP - pair it again; exits continue"
        elif last is None:
            reason = NEVER_REACHED
        elif now - last > grace:
            reason = NOT_REACHED.format(since=last.astimezone(UTC).strftime("%d %b %H:%M UTC"))
        else:
            reason = None
        if reason != self._paused_reason:
            self._log(now, reason or "ATP answers again - new entries resume")
            if self.audit is not None:
                self.audit.write("link_entries", paused=reason is not None, reason=reason)
            self._paused_reason = reason
        return reason

    # --- one tick ------------------------------------------------------------------------------------------------------
    async def _call(self, method: str, path: str, payload: Any = None) -> httpx.Response | None:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self.client.request, method, path, payload),
                                          timeout=HTTP_TIMEOUT_SECONDS + 5)
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            logger.info("Runner link: %s %s not answered (%s)", method, path, type(exc).__name__)
            return None

    def _ok(self, now: datetime) -> None:
        self.state.last_ok_at = now.astimezone(UTC).isoformat()
        self._revoked = False

    async def tick(self, now: datetime, *, killed: bool, open_positions: int) -> None:
        """Heartbeat when due, then the commands, the rules, the reference data and the mirror. Never raises for a
        network problem; the next tick tries again."""
        if self._last_beat is not None and (now - self._last_beat).total_seconds() + 1 < int(self.settings.heartbeat_seconds):
            return
        self._last_beat = now
        login = self.login_status(now) if self.login_status is not None else None
        beat: dict[str, Any] = {"run_state": "running", "version": RUNNER_VERSION, "kill_switch_on": bool(killed),
                                "open_positions": max(0, int(open_positions)), "broker_session": _session_word(login),
                                "broker_login": login[:200] if login else None, "acked_commands": self._acks[:20]}
        resp = await self._call("POST", HEARTBEAT_PATH, beat)
        if resp is None:
            return
        if resp.status_code == 401 and "removed" in _plain_error(resp, ""):
            if not self._revoked:
                self._log(now, "ATP says this runner was removed from your account - pair it again; exits continue")
            self._revoked = True
            return
        if resp.status_code != 200:
            self._log(now, f"heartbeat not accepted: {_plain_error(resp, 'ATP refused it')}")
            return
        answer = resp.json()
        self._ok(now)
        self.mirror_upload = bool(answer.get("mirror_upload", True))
        self._apply_commands(now, answer.get("commands") or [])
        etag = answer.get("rules_etag")
        if etag and etag != self.state.rules_etag:
            await self._pull_rules(now)
        day = now.astimezone(_IST).date().isoformat()
        if self.reference_day != day:
            await self._pull_reference(now, day)
        if self.mirror_upload:
            await self._mirror(now)
        self._save()

    def _apply_commands(self, now: datetime, commands: list[Any]) -> None:
        acks: list[int] = []
        for command in commands:
            payload, refusal = verify_command(command, self.pairing, self.state.last_command_id)
            cid = command.get("id") if isinstance(command, dict) else None
            if refusal == "already applied" and isinstance(cid, int):
                acks.append(cid)                                     # ATP missed the ack: say it again, apply nothing
                continue
            if payload is None:
                if isinstance(cid, int) and cid in self._refused:
                    continue
                if isinstance(cid, int):
                    self._refused.add(cid)
                self._log(now, f"command refused: {refusal}")
                if self.audit is not None:
                    self.audit.write("command_refused", command_id=cid, reason=refusal)
                continue
            on = bool(payload["on"])
            self.settings.set_killed(on, origin=signing.COMMAND_ORIGIN)   # his own action, relayed and signed for him
            self.state.last_command_id = int(payload["command_id"])
            acks.append(int(payload["command_id"]))
            self._log(now, "kill switch " + ("ON from your ATP button: no new entries; exits continue" if on
                                             else "OFF from your ATP button"))
            if self.audit is not None:
                self.audit.write("kill_switch_command", command_id=payload["command_id"], on=on, origin=payload["origin"])
        self._acks = acks
        self._save()

    async def _pull_rules(self, now: datetime) -> None:
        from app.runner import sync
        resp = await self._call("GET", RULES_PATH)
        if resp is None or resp.status_code != 200:
            if resp is not None:
                self._log(now, f"rules not pulled: {_plain_error(resp, 'ATP refused it')}")
            return
        snapshot = resp.json()
        if snapshot.get("user_ref") != self.pairing.user_ref:
            self._log(now, "rules refused: they are not for the user this runner is paired to")
            return
        async with self.sessions() as session:
            counts = await sync.apply_rules(session, snapshot)
        self.state.rules_etag = snapshot.get("etag")
        self._log(now, "your saved rules synced from ATP: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        if self.audit is not None:
            self.audit.write("rules_synced", etag=self.state.rules_etag, **counts)

    async def _pull_reference(self, now: datetime, day: str) -> None:
        from app.runner import sync
        resp = await self._call("GET", REFERENCE_PATH)
        if resp is None or resp.status_code != 200:
            return
        async with self.sessions() as session:
            counts = await sync.apply_reference(session, resp.json())
        self.reference_day = day
        self._log(now, "reference data from ATP: " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    async def _mirror(self, now: datetime) -> None:
        from app.runner import sync
        async with self.sessions() as session:
            upload, since, seq = await sync.mirror_page(session, self.settings.audit_path, self.state.mirror_since,
                                                        self.state.mirror_audit_seq)
        if not (upload["orders"] or upload["positions"] or upload["audit"]):
            return
        resp = await self._call("POST", MIRROR_PATH, upload)
        if resp is None:
            return
        if resp.status_code == 409:
            self.mirror_upload = False                               # his choice is OFF: nothing is sent
            return
        if resp.status_code != 200:
            self._log(now, f"mirror not accepted: {_plain_error(resp, 'ATP refused it')}")
            return
        self.state.mirror_since, self.state.mirror_audit_seq = since, seq


def _session_word(line: str | None) -> str:
    """S3's broker_session word from S5's status line."""
    if not line:
        return "none"
    if line == "broker session valid":
        return "valid"
    return "none"


__all__ = ["Link", "LinkClient", "LinkError", "LinkState", "Pairing", "pair", "verify_command", "check_atp_url",
           "RUNNER_VERSION", "load_key", "new_key"]
