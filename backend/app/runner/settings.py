"""Runner S2: the runner's own folder and its local switches (docs/design/USER_RUNNER.md, step S2).

Everything the runner keeps lives in ONE folder on the user's machine (`home`):
- `runner.db`      the local SQLite store: his strategies, deployments, orders and positions (S1: the server's models);
- `audit.jsonl`    the local audit log - every order intent and result, hash-chained (`audit.py`);
- `runner.lock`    the process lock (one runner per folder, `lock.py`);
- `LIVE`           HIS OWN LIVE switch. LIVE orders need this file to read exactly "on" (the CLI `live on` writes it),
                   in addition to the deployment's own LIVE choice and ATP's own LIVE switches. Missing / anything else:
                   OFF. Nothing writes it except that command; no default, no deploy, no update turns it on;
- `KILL`           HIS OWN kill switch (owner rule, 14 Nov: the user's own command only; ATP / the platform admin never
                   stops, pauses or changes a user's runner): new entries stop while it exists; exits always run
                   (ADR-0004). Default OFF. Only `set_killed` writes it, and only for his own action - the CLI on his
                   runner, or (S4) his own button in ATP relayed as a command signed for his action;
- `runner.json`    optional operational settings (cycle length, history to read, the link's heartbeat and grace) - never
                   a trading rule;
- S4, the link to ATP (`link.py`): `device.key` (this runner's own Ed25519 private key, made here at pairing, mode 600,
  never sent), `link.json` (ATP's address, the device id, the user it is paired to and ATP's command key, pinned),
  `link_state.json` (when ATP last answered, the last command applied, what was mirrored) and `link.log` (plain lines).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LIVE_ON = "on"
# Who may flip the kill switch: the user himself, on the runner ("user_local") or through his own ATP button relayed as a
# command signed for his action ("user_signed", step S4). Nothing else - no admin, platform-wide or ATP-originated kill.
KILL_ORIGINS: tuple[str, ...] = ("user_local", "user_signed")
NOT_YOURS = "Only you can turn your runner's kill switch on or off"

# Operational settings only (how often to wake, how much history to read) - the trading settings are the user's own,
# in his deployments (CLAUDE.md section 3).
DEFAULTS: dict[str, Any] = {
    "cycle_seconds": 60,          # one cycle per base bar close, as the server worker (WORKER_CYCLE_SECONDS)
    "settle_seconds": 3.0,        # read the broker a moment after the bar closed, so the closed bar is there
    "history_days_intraday": 20,  # how far back the broker's candles are read for minute timeframes
    "history_days_daily": 400,    # ... and for day / week / month timeframes
    "ops_throttle": True,         # rule R-9: the orders-per-second limit (10/s) on his broker session, default ON
    # S4, the link to ATP (design section 4). The heartbeat period: reference 60 s = ATP's offline time of 3 minutes
    # (S3 `offline_after_minutes` default) is three missed heartbeats.
    "heartbeat_seconds": 60,
    # How long ATP may be unreachable before NEW entries pause (design section 4: "a runner setting"; exits, stops and
    # square-offs never pause). Reference for the default 3: ATP's own "runner offline" time (S3 default 3 minutes).
    "link_grace_minutes": 3,
}


@dataclass(frozen=True)
class RunnerSettings:
    home: Path
    cycle_seconds: int = DEFAULTS["cycle_seconds"]
    settle_seconds: float = DEFAULTS["settle_seconds"]
    history_days_intraday: int = DEFAULTS["history_days_intraday"]
    history_days_daily: int = DEFAULTS["history_days_daily"]
    ops_throttle: bool = DEFAULTS["ops_throttle"]
    heartbeat_seconds: int = DEFAULTS["heartbeat_seconds"]
    link_grace_minutes: int = DEFAULTS["link_grace_minutes"]

    @classmethod
    def load(cls, home: str | os.PathLike[str]) -> RunnerSettings:
        folder = Path(home).expanduser().resolve()
        values: dict[str, Any] = {}
        config = folder / "runner.json"
        if config.exists():
            raw = json.loads(config.read_text() or "{}")
            values = {k: (v is True if isinstance(DEFAULTS[k], bool) else type(DEFAULTS[k])(v)) for k, v in raw.items() if k in DEFAULTS}
        return cls(home=folder, **values)

    @property
    def db_path(self) -> Path:
        return self.home / "runner.db"

    @property
    def audit_path(self) -> Path:
        return self.home / "audit.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.home / "runner.lock"

    @property
    def live_switch_path(self) -> Path:
        return self.home / "LIVE"

    @property
    def kill_path(self) -> Path:
        return self.home / "KILL"

    @property
    def device_key_path(self) -> Path:
        return self.home / "device.key"

    @property
    def link_path(self) -> Path:
        return self.home / "link.json"

    @property
    def link_state_path(self) -> Path:
        return self.home / "link_state.json"

    @property
    def link_log_path(self) -> Path:
        return self.home / "link.log"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    def live_enabled(self) -> bool:
        """His own LIVE switch, read fresh every time (default OFF: no file, unreadable, or anything but "on")."""
        try:
            return self.live_switch_path.read_text().strip().lower() == LIVE_ON
        except OSError:
            return False

    def set_killed(self, on: bool, *, origin: str) -> None:
        """His own kill switch, ON or OFF. `origin` must be his own action (KILL_ORIGINS); anything else is refused."""
        if origin not in KILL_ORIGINS:
            raise PermissionError(NOT_YOURS)
        if on:
            self.home.mkdir(parents=True, exist_ok=True)
            self.kill_path.write_text("on\n")
        else:
            self.kill_path.unlink(missing_ok=True)

    def killed(self) -> bool:
        """The local kill switch (entries only; exits always run)."""
        return self.kill_path.exists()


__all__ = ["RunnerSettings", "DEFAULTS", "LIVE_ON", "KILL_ORIGINS", "NOT_YOURS"]
