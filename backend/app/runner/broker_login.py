"""Runner S5: the daily broker login on the user's own runner (docs/design/USER_RUNNER.md section 5; RUN-Q3 Angel One).

The login is the EXISTING Angel One adapter's own login (`AngelOneBroker.authenticate`: `loginByPassword` with the client
code, the PIN and the TOTP code of the moment from `app.brokers.totp`), the same call ATP's server-side automatic login
runs (`app/brokers/auto_login.py` through `routes.run_authentication`). What the runner adds is where the details and the
session live (the local keystore, never ATP) and where the call leaves from (this machine).

- When: on an NSE trading day (weekends; the holiday list once ATP syncs it, S4), from `broker_login_minutes_before_open`
  before the open until the close - the same window as the server's (`auto_login.in_window`). The minutes are an
  operational setting in the runner's `runner.json` (the S2 settings file; RunnerSettings ignores keys it does not
  own); without it, the rule set's `auto_login_minutes_before_open` (rule IN-SEBI.login.daily, today 45).
- Idempotent: a session already stored for today is left alone (the Angel One session lasts until midnight,
  docs/brokers/EASIEST_LOGIN.md section 5), so calling `run_due` every loop cycle logs in once.
- Retries with backoff: `broker_login_retries` (default: the rule set's `auto_login_retries`) more attempts, the first
  `broker_login_retry_minutes` (default: the rule set's `auto_login_retry_minutes`) after the failure, then doubling.
  After the last one the day's login is given up: state "failed" with ONE plain sentence; `python -m app.runner.keys
  login angel_one` retries at once (and clears the given-up state).
- Status for the heartbeat (S4): `<home>/broker_status.json` holds {broker, state, reason, day, attempts, last_try,
  next_try} and `status_line()` gives the one line the heartbeat reports ("broker session valid" / "login failed:
  <plain reason>"). Each attempt also appends one line to the local log `<home>/broker_login.log`. Neither ever holds a
  token, a PIN, a key or a TOTP value, nor the broker's own error text: errors are reduced to fixed sentences and the
  logger gets only exception class names.
- Egress: the adapter's client is built here with `trust_env=False`, so no HTTP(S)_PROXY variable on the machine can
  route the broker calls elsewhere; every Angel One call (login, orders, quotes, candles) goes straight from this
  machine - from the static IP he registered (SmartAPI's static-IP rule from 1 Apr 2026). Nothing is proxied via ATP:
  the adapter only knows Angel One's own hosts (`BROKER_HOSTS`, checked by a test).

A login only: no order, no deployment change, no LIVE switch, no kill switch (the kill switch is the user's alone).
Exits are never blocked by a failed login (ADR-0004): the loop's close path keeps running; a failed login only means the
broker cannot be reached with a session, which the status reports.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from app.brokers.angel_one import BASE_URL as ANGEL_BASE_URL
from app.brokers.angel_one import AngelOneBroker
from app.brokers.exceptions import BrokerAPIError, BrokerAuthenticationError
from app.brokers.models import BrokerCredentials
from app.compliance.rules import load as load_ruleset
from app.market_data.calendar import IST, MARKET_CLOSE, MARKET_OPEN, is_trading_day, known_holidays
from app.runner.keystore import Keystore, KeystoreError

logger = logging.getLogger(__name__)

RULE = "IN-SEBI.login.daily"                 # the server's automatic-login rule (auto_login.py / login_reminder.RULE)
STATUS_FILE = "broker_status.json"
LOG_FILE = "broker_login.log"
SETTINGS_FILE = "runner.json"
BROKERS: tuple[str, ...] = ("angel_one",)    # RUN-Q3: Angel One first
NAMES = {"angel_one": "Angel One"}
# Every host the Angel One adapter talks to: its API and its public scrip master. None is ATP's.
BROKER_HOSTS: frozenset[str] = frozenset({"apiconnect.angelone.in", "margincalculator.angelbroking.com"})

LOGGED_IN, FAILED, RETRYING, NOT_SET_UP = "logged_in", "failed", "retrying", "not_set_up"


# --- settings ------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class LoginSettings:
    minutes_before_open: int
    retries: int
    retry_minutes: int

    @classmethod
    def load(cls, home: Path) -> LoginSettings:
        ruleset = load_ruleset()
        values = {
            "minutes_before_open": int(ruleset.param(RULE, "auto_login_minutes_before_open", 45)),
            "retries": int(ruleset.param(RULE, "auto_login_retries", 1)),
            "retry_minutes": int(ruleset.param(RULE, "auto_login_retry_minutes", 10)),
        }
        config = home / SETTINGS_FILE
        if config.exists():
            raw = json.loads(config.read_text() or "{}")
            for key in values:
                if f"broker_login_{key}" in raw:
                    values[key] = max(0, int(raw[f"broker_login_{key}"]))
        return cls(**values)

    @property
    def max_attempts(self) -> int:
        return 1 + max(0, self.retries)

    def login_time(self, day: date) -> datetime:
        return datetime.combine(day, MARKET_OPEN, tzinfo=IST) - timedelta(minutes=self.minutes_before_open)

    def in_window(self, now: datetime) -> bool:
        now_ist = now.astimezone(IST)
        return (is_trading_day(now_ist.date(), known_holidays("NSE"))
                and self.login_time(now_ist.date()) <= now_ist and now_ist.time() < MARKET_CLOSE)

    def backoff(self, attempts_made: int) -> timedelta:
        """The wait after the `attempts_made`-th failure: retry_minutes, then doubling."""
        return timedelta(minutes=self.retry_minutes * (2 ** max(0, attempts_made - 1)))


# --- plain reasons (never the broker's text, never a value) ---------------------------------------------------------
def plain_reason(broker: str, exc: BaseException) -> str:
    name = NAMES.get(broker, broker)
    if isinstance(exc, KeystoreError):
        return str(exc)                                   # KeystoreError sentences are fixed and carry no value
    if isinstance(exc, (httpx.TransportError, TimeoutError, OSError)):
        return f"{name} could not be reached from this machine (network)"
    if isinstance(exc, BrokerAuthenticationError):
        return (f"{name} refused the login - check the client code, PIN and TOTP secret "
                f"(python -m app.runner.keys rotate {broker})")
    if isinstance(exc, BrokerAPIError):
        return f"{name} did not answer as expected"
    return f"{name} login stopped on an unexpected error"


# --- the login -----------------------------------------------------------------------------------------------------
def _today(now: datetime) -> str:
    return now.astimezone(IST).date().isoformat()


def broker_client(broker: str) -> httpx.AsyncClient:
    """The adapter's HTTP client on the runner: straight to the broker from this machine (no proxy from the environment)."""
    if broker != "angel_one":
        raise KeystoreError(f"The runner's first broker is Angel One; {broker} comes later")
    return httpx.AsyncClient(base_url=ANGEL_BASE_URL, timeout=15.0, trust_env=False)


def credentials(keystore: Keystore, broker: str, now: datetime) -> BrokerCredentials:
    """The adapter's credentials from the keystore, with today's session token when one is stored (in memory only)."""
    fields = keystore.broker_fields(broker)
    stored = keystore.session(broker, _today(now))
    return BrokerCredentials.model_validate({**fields, "access_token": (stored or {}).get("access_token")})


def build_adapter(keystore: Keystore, broker: str, now: datetime, client: Optional[httpx.AsyncClient] = None) -> Any:
    """The broker adapter the runner trades and reads market data through, on today's session (S2's feed can use it in
    place of its interim broker.json)."""
    return AngelOneBroker(credentials(keystore, broker, now), client=client or broker_client(broker))


class DailyLogin:
    """The runner's daily broker login. `client_factory` (tests: an httpx.MockTransport) builds the adapter's client."""

    def __init__(self, home: str | os.PathLike[str], keystore: Keystore, broker: str = "angel_one",
                 client_factory: Callable[[str], httpx.AsyncClient] = broker_client) -> None:
        if broker not in BROKERS:
            raise KeystoreError(f"The runner's first broker is Angel One; {broker} comes later")
        self.home = Path(home).expanduser()
        self.keystore = keystore
        self.broker = broker
        self.client_factory = client_factory
        self.settings = LoginSettings.load(self.home)

    # status ---------------------------------------------------------------------------------------------------------
    @property
    def status_path(self) -> Path:
        return self.home / STATUS_FILE

    def status(self) -> dict[str, Any]:
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return {}

    def _today_state(self, now: datetime) -> dict[str, Any]:
        state = self.status()
        if state.get("broker") == self.broker and state.get("day") == _today(now):
            return state
        return {"broker": self.broker, "day": _today(now), "attempts": 0}

    def _save(self, state: dict[str, Any]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_name(f".{STATUS_FILE}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps(state, sort_keys=True))
        os.replace(tmp, self.status_path)

    def _log(self, now: datetime, line: str) -> None:
        with open(self.home / LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{now.astimezone(timezone.utc).isoformat(timespec='seconds')} {self.broker} {line}\n")

    def status_line(self, now: datetime) -> str:
        """The one line the heartbeat (S4) reports - never a value."""
        state = self.status()
        if state.get("broker") != self.broker or state.get("day") != _today(now):
            return "broker not logged in today"
        if state.get("state") == LOGGED_IN:
            return "broker session valid"
        if state.get("state") in (FAILED, NOT_SET_UP):
            return f"login failed: {state.get('reason')}"
        if state.get("state") == RETRYING:
            return f"login failed: {state.get('reason')} (trying again)"
        return "broker not logged in today"

    # login ----------------------------------------------------------------------------------------------------------
    def due(self, now: datetime) -> bool:
        if not self.settings.in_window(now):
            return False
        try:
            if self.keystore.session(self.broker, _today(now)):
                return False
        except KeystoreError:
            pass                                    # attempted once, so the status says why (locked / missing / damaged)
        state = self._today_state(now)
        if state.get("state") in (FAILED, NOT_SET_UP, LOGGED_IN) or int(state.get("attempts") or 0) >= self.settings.max_attempts:
            return False
        next_try = state.get("next_try")
        return not next_try or now >= datetime.fromisoformat(next_try)

    async def run_due(self, now: Optional[datetime] = None) -> Optional[bool]:
        """Called by the loop every cycle: True logged in now, False this attempt failed, None nothing due."""
        now = now or datetime.now(timezone.utc)
        if not self.due(now):
            return None
        return await self.login(now)

    async def login(self, now: Optional[datetime] = None, *, manual: bool = False) -> bool:
        """One login attempt. `manual` (the CLI): logs in even when today's was given up, and starts the count again."""
        now = now or datetime.now(timezone.utc)
        state: dict[str, Any] = {"broker": self.broker, "day": _today(now), "attempts": 0} if manual else self._today_state(now)
        attempts = int(state.get("attempts") or 0) + 1
        client: Optional[httpx.AsyncClient] = None
        try:
            fields = self.keystore.broker_fields(self.broker)
            client = self.client_factory(self.broker)
            adapter = AngelOneBroker(BrokerCredentials.model_validate(fields), client=client)
            await adapter.authenticate()
            tokens = {"access_token": getattr(adapter, "_access_token", None),
                      "refresh_token": getattr(adapter, "_refresh_token", None),
                      "feed_token": getattr(adapter, "_feed_token", None)}
            self.keystore.put_session(self.broker, _today(now), tokens, now.astimezone(timezone.utc).isoformat())
        except Exception as exc:  # noqa: BLE001 - every failure becomes a plain status; nothing may escape with a value
            reason = plain_reason(self.broker, exc)
            not_set_up = isinstance(exc, KeystoreError)
            give_up = not_set_up or attempts >= self.settings.max_attempts
            state = {**state, "attempts": attempts, "last_try": now.isoformat(), "reason": reason,
                     "state": (NOT_SET_UP if not_set_up else FAILED) if give_up else RETRYING,
                     "next_try": None if give_up else (now + self.settings.backoff(attempts)).isoformat()}
            self._save(state)
            self._log(now, f"login attempt {attempts} failed: {reason}" + ("" if give_up else f"; next try {state['next_try']}"))
            logger.warning("Runner %s login attempt %d failed: %s", self.broker, attempts, type(exc).__name__)
            return False
        finally:
            if client is not None:
                await client.aclose()
        self._save({"broker": self.broker, "day": _today(now), "attempts": attempts, "last_try": now.isoformat(),
                    "state": LOGGED_IN, "reason": None, "next_try": None})
        self._log(now, f"login attempt {attempts} succeeded")
        logger.info("Runner %s login succeeded (attempt %d)", self.broker, attempts)
        return True


__all__ = ["DailyLogin", "LoginSettings", "build_adapter", "broker_client", "credentials", "plain_reason", "BROKER_HOSTS"]
