"""Background 1-minute collector for one user's FYERS account, plus the per-process FeedManager.

Loop (exchange time, trading days only, 09:15–15:30 plus a short close margin):
  every `poll_every_min` minutes, ~5 s after the minute closes, fetch today's 1-minute candles for each watched symbol,
  keep only COMPLETED and VALID bars, upsert them (new / revised), then publish to the user's subscribers.
Because the whole day is requested each time, a missed poll is caught up with the provider's real candles on the next
poll (never forward-filled). Errors are isolated per symbol. Token expiry pauses the feed with status TOKEN_EXPIRED
until the user logs in again (in any AMW process); rate-limit protection pauses it with RATE_LIMIT_GUARD until the
next day.

Multi-process safety (the Streamlit UI and the API server are separate processes sharing one database):
* a database LEASE guarantees exactly one collector per user across all processes (no double polling of FYERS);
* the holder renews the lease every HEARTBEAT_S seconds and publishes its status there, so every process can show it;
* Stop from any process releases the lease; the holder notices at its next heartbeat and stops itself;
* the daily rate-limit guard is stored in the database and honoured by every process.
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from ..data.validation import is_trading_day
from ..storage.db import utcnow
from .fyers import FyersClient, FyersError, RateLimiter, candles_to_bars, from_fyers_symbol, next_expiry, to_fyers_symbol
from .hub import BarHub

log = logging.getLogger(__name__)
TZ = "Asia/Kolkata"
MAX_SYMBOLS = 100          # ~100 history calls per poll; stays under the 150/min local budget with room for scans
HEARTBEAT_S = 10.0         # lease renewal + status publication interval
LEASE_STALE_S = 90         # a holder silent for this long is considered dead and can be replaced
TOKEN_RECHECK_S = 15.0     # while TOKEN_EXPIRED, re-read the stored session (the login may happen in another process)


class FyersCollector:
    def __init__(self, store, hub: BarHub, user_id: int, symbols: list[str], market: dict, transport=None,
                 clock=utcnow, poll_every_min: int = 1, retention_days: int = 30, limiter: RateLimiter | None = None,
                 holder: str | None = None):
        if not symbols:
            raise ValueError("add at least one symbol")
        if len(symbols) > MAX_SYMBOLS:
            raise ValueError(f"at most {MAX_SYMBOLS} symbols per user feed (FYERS rate limits)")
        self.store, self.hub, self.user_id = store, hub, user_id
        self.symbols = [to_fyers_symbol(s) for s in dict.fromkeys(symbols)]
        self.market, self.transport, self.clock = market, transport, clock
        self.poll_every = max(1, int(poll_every_min))
        self.retention_days = retention_days
        self.limiter = limiter or RateLimiter()
        self.status = "STOPPED"
        self.detail = ""
        self.stats = {"polls": 0, "bars_new": 0, "bars_revised": 0, "rejected": 0, "errors": 0, "last_poll": None,
                      "last_bar": None, "last_error": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_slot = None
        self.holder = holder or f"{socket.gethostname()[:40]}:{os.getpid()}:{secrets.token_hex(4)}"
        self._last_beat = float("-inf")
        self._last_token_check = float("-inf")

    # ---------------- one poll (unit-testable, no threads)
    def _client(self) -> FyersClient:
        conn = self.store.broker_get(self.user_id, "fyers")
        if not conn:
            raise FyersError("invalid_credentials", "FYERS is not connected")
        if not conn["access_token"] or (conn["token_expires"] and conn["token_expires"] <= self.clock()):
            raise FyersError("token_expired", "FYERS login expired - log in again (tokens expire daily)")
        return FyersClient(conn["app_id"], conn["access_token"], self.transport, self.limiter)

    def in_session(self, now_utc: datetime) -> bool:
        loc = pd.Timestamp(now_utc).tz_localize("UTC").tz_convert(TZ)
        if not is_trading_day(loc, self.market.get("holidays", [])):
            return False
        hm = loc.strftime("%H:%M")
        return self.market.get("session_open", "09:15") < hm <= "15:35"

    def poll_once(self) -> dict:
        now = self.clock()
        today = str(pd.Timestamp(now).tz_localize("UTC").tz_convert(TZ).date())
        out = {"new": 0, "revised": 0, "rejected": 0, "errors": []}
        try:
            self.limiter.check_guard(today)              # shared daily guard (may have been tripped by another process)
            cl = self._client()
        except FyersError as e:
            self._set({"token_expired": "TOKEN_EXPIRED", "rate_limit_guard": "RATE_LIMIT_GUARD"}.get(e.code, "ERROR"), str(e))
            return out
        for sym in self.symbols:
            if self._stop.is_set():
                break
            try:
                cs = cl.history(sym, "1", today, today)
            except FyersError as e:
                out["errors"].append(f"{sym}: {e.code}")
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{sym}: {e}"
                if e.code == "token_expired":
                    self.store.broker_set_token(self.user_id, "fyers", None, None, None)
                    self._set("TOKEN_EXPIRED", str(e))
                    return out
                if e.code == "rate_limited":
                    try:
                        self.limiter.record_breach(today)
                    except FyersError as g:
                        self._set("RATE_LIMIT_GUARD", str(g))
                        return out
                if e.code == "rate_limit_guard":
                    self._set("RATE_LIMIT_GUARD", str(e))
                    return out
                continue
            ticker = from_fyers_symbol(sym)
            bars, rej = candles_to_bars(ticker, cs, now)
            out["rejected"] += len(rej)
            res = self.store.bars_upsert(self.user_id, bars)
            for b in res["new"]:
                self.hub.publish(self.user_id, {"type": "bar", **b})
            for b in res["revised"]:
                self.hub.publish(self.user_id, {"type": "revision", **b})
            out["new"] += len(res["new"])
            out["revised"] += len(res["revised"])
            if bars:
                self.stats["last_bar"] = max(self.stats["last_bar"] or "", bars[-1]["ts"])
        self.stats["polls"] += 1
        self.stats["last_poll"] = now
        self.stats["bars_new"] += out["new"]
        self.stats["bars_revised"] += out["revised"]
        self.stats["rejected"] += out["rejected"]
        self._set("RUNNING" if not out["errors"] else "DEGRADED",
                  f"{len(out['errors'])} symbol error(s)" if out["errors"] else "ok")
        self.hub.publish(self.user_id, {"type": "status", "status": self.status, "symbol": None, "new": out["new"],
                                        "errors": out["errors"][:5], "at": str(now)})
        return out

    def _set(self, status: str, detail: str):
        changed = status != self.status
        self.status, self.detail = status, detail
        if status in ("TOKEN_EXPIRED", "RATE_LIMIT_GUARD", "ERROR"):
            self.hub.publish(self.user_id, {"type": "status", "status": status, "symbol": None, "detail": detail})
            if changed:
                try:
                    from ..services.notify_events import notify
                    day = str(pd.Timestamp(self.clock()).tz_localize("UTC").tz_convert(TZ).date())
                    notify(self.store, self.user_id, "feed_failure", f"feed:{day}:{status}",
                           f"AMW live data (FYERS): {status} - {detail}")
                except Exception:  # noqa: BLE001 - never break the feed on a notification problem
                    log.exception("feed failure notification failed")

    def due(self, now: datetime) -> bool:
        loc = pd.Timestamp(now).tz_localize("UTC").tz_convert(TZ)
        if not self.in_session(now) or loc.second < 5:
            return False
        slot = loc.floor("min")
        mins = int((slot - slot.normalize()).total_seconds() // 60)
        if mins % self.poll_every:
            return False
        if self._last_slot == slot:
            return False
        self._last_slot = slot
        return True

    # ---------------- lease + recovery (database-coordinated; see module docstring)
    def heartbeat(self, force: bool = False) -> bool:
        """Renew the lease and publish status. False (and the collector stops) when the lease was released by a Stop
        in another process or taken over."""
        mono = time.monotonic()
        if not force and mono - self._last_beat < HEARTBEAT_S:
            return True
        self._last_beat = mono
        if self.store.feed_lease_renew(self.user_id, self.holder, self.clock(), self.runtime()):
            return True
        if not self._stop.is_set():
            self._stop.set()
            self.status, self.detail = "STOPPED", "stopped from another AMW session"
        return False

    def try_recover(self, now: datetime) -> None:
        """TOKEN_EXPIRED -> WAITING once a valid session is stored (login in any process);
        RATE_LIMIT_GUARD -> WAITING once the daily guard no longer applies (next day)."""
        if self.status == "TOKEN_EXPIRED":
            mono = time.monotonic()
            if mono - self._last_token_check >= TOKEN_RECHECK_S:
                self._last_token_check = mono
                pub = self.store.broker_public(self.user_id, "fyers", now=now)
                if pub and pub["token_valid"]:
                    self.status, self.detail = "WAITING", "logged in again"
        elif self.status == "RATE_LIMIT_GUARD":
            today = str(pd.Timestamp(now).tz_localize("UTC").tz_convert(TZ).date())
            if self.limiter.breaches(today) < self.limiter.MAX_BREACHES:
                self.status, self.detail = "WAITING", "rate-limit guard cleared (new day)"

    # ---------------- thread
    def start(self):
        """Run the loop. The caller (FeedManager.start) must hold the lease."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.status, self.detail = "WAITING", "waiting for the next completed minute"

        def run():
            purged_day = None
            while not self._stop.wait(1.0):
                now = self.clock()
                try:
                    if not self.heartbeat():
                        break
                    if self.status in ("TOKEN_EXPIRED", "RATE_LIMIT_GUARD"):
                        self.try_recover(now)
                        if self.status in ("TOKEN_EXPIRED", "RATE_LIMIT_GUARD"):
                            continue
                    if self.due(now):
                        self.poll_once()
                        self.heartbeat(force=True)
                    elif not self.in_session(now) and self.status not in ("ERROR",):
                        self.status, self.detail = "MARKET_CLOSED", "outside market hours / non-trading day"
                    day = now.date()
                    if purged_day != day:
                        cut = (pd.Timestamp(now) - pd.Timedelta(days=self.retention_days)).tz_localize("UTC").tz_convert(TZ).isoformat()
                        self.store.bars_purge(self.user_id, cut)
                        purged_day = day
                except Exception as e:  # noqa: BLE001 - keep the feed alive; record the error
                    log.exception("collector loop error")
                    self.stats["errors"] += 1
                    self.stats["last_error"] = type(e).__name__
        self._thread = threading.Thread(target=run, name=f"amw-fyers-{self.user_id}", daemon=True)
        self._thread.start()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def stop(self, release: bool = True):
        self._stop.set()
        self.status, self.detail = "STOPPED", "stopped by user"
        if release:
            self.store.feed_lease_release(self.user_id, self.holder, status=self.runtime())

    def runtime(self) -> dict:
        """Status as stored for other processes (no process-local fields)."""
        return {"status": self.status, "detail": self.detail, "symbols": [from_fyers_symbol(s) for s in self.symbols],
                "poll_every_min": self.poll_every, "heartbeat": str(self.clock()),
                **{k: (str(v) if isinstance(v, datetime) else v) for k, v in self.stats.items()}}

    def public(self) -> dict:
        return {**self.runtime(), "subscribers": self.hub.subscriber_count(self.user_id)}


class FeedManager:
    """Per-process manager. Credentials/tokens live in the encrypted broker_connections table; the feed lease, runtime
    status and rate-limit guard live in the database too, so the UI process and the API process stay consistent."""

    def __init__(self, svc, transport=None, clock=utcnow):
        self.svc, self.transport, self.clock = svc, transport, clock
        self.hub = BarHub()
        self.collectors: dict[int, FyersCollector] = {}
        self.limiters: dict[int, RateLimiter] = {}

    # ---- rate budget (per user; daily breach guard shared through the database)
    def _breach_store(self, user_id: int):
        store = self.svc.store

        def get(day: str) -> int:
            g = store.get_pref(user_id, "fyers_rate_guard", None) or {}
            return int(g.get("breaches", 0)) if g.get("day") == day else 0

        def put(day: str, n: int) -> None:
            store.set_pref(user_id, "fyers_rate_guard", {"day": day, "breaches": n})
        return get, put

    def _shared_minute(self, user_id: int):
        """Per-minute FYERS budget of one account shared by every AMW process (database fixed-window counter)."""
        store, key, seen = self.svc.store, f"fyers:{user_id}", {"n": 0}

        def take(limit: int) -> bool:
            now = time.time()
            win = time.strftime("%Y%m%d%H%M", time.gmtime(now))
            seen["n"] += 1
            if seen["n"] % 200 == 0:                              # housekeeping: keep only recent windows
                prev = time.strftime("%Y%m%d%H%M", time.gmtime(now - 60))
                store.rate_purge(key, {win, prev})
            return store.rate_take(key, win, limit)
        return take

    def limiter(self, user_id: int) -> RateLimiter:
        if user_id not in self.limiters:
            self.limiters[user_id] = RateLimiter(breach_store=self._breach_store(user_id),
                                                 shared_minute=self._shared_minute(user_id))
        return self.limiters[user_id]

    # ---- connection + daily login
    def connect(self, user_id: int, app_id: str, secret: str, redirect_uri: str) -> None:
        app_id, secret, redirect_uri = app_id.strip(), secret.strip(), redirect_uri.strip()
        if not (5 <= len(app_id) <= 40 and "-" in app_id):
            raise ValueError("FYERS App ID looks wrong (expected e.g. XXXXXXXXXX-100)")
        if not (6 <= len(secret) <= 64) or not redirect_uri.startswith(("http://", "https://")):
            raise ValueError("secret or redirect URI invalid")
        self.stop(user_id)                   # new credentials: any running feed must log in again
        self.svc.store.broker_put(user_id, "fyers", app_id, secret, redirect_uri)

    def login_url(self, user_id: int) -> str:
        conn = self.svc.store.broker_get(user_id, "fyers")
        if not conn:
            raise ValueError("connect your FYERS app first")
        state = FyersClient.new_state()
        self.svc.store.broker_set_state(user_id, "fyers", state, self.clock() + timedelta(minutes=15))
        return FyersClient(conn["app_id"]).login_url(conn["redirect_uri"], state)

    def complete_login(self, user_id: int, auth_code: str, state: str) -> datetime:
        if not self.svc.store.broker_consume_state(user_id, "fyers", state, now=self.clock()):
            raise FyersError("invalid_credentials", "login state invalid or expired - start the login again")
        conn = self.svc.store.broker_get(user_id, "fyers")
        tok = FyersClient(conn["app_id"], None, self.transport, self.limiter(user_id)).exchange_code(conn["secret"], auth_code)
        now = self.clock()
        exp = next_expiry(now)
        self.svc.store.broker_set_token(user_id, "fyers", tok, now, exp)
        c = self.collectors.get(user_id)
        if c and c.status == "TOKEN_EXPIRED":
            c.status, c.detail = "WAITING", "logged in again"    # a collector in another process re-checks by itself
        return exp

    def disconnect(self, user_id: int) -> bool:
        self.stop(user_id)
        return self.svc.store.broker_delete(user_id, "fyers")

    # ---- collector lifecycle (exactly one collector per user across processes)
    def start(self, user_id: int, symbols: list[str], poll_every_min: int = 1) -> FyersCollector:
        if not self.svc.store.broker_public(user_id, "fyers"):
            raise ValueError("connect your FYERS app first")
        old = self.collectors.pop(user_id, None)
        if old:
            old.stop()
        c = FyersCollector(self.svc.store, self.hub, user_id, symbols, self.svc.cfg(user_id)["market"], self.transport,
                           self.clock, poll_every_min, limiter=self.limiter(user_id))
        if not self.svc.store.feed_lease_acquire(user_id, c.holder, self.clock(), LEASE_STALE_S):
            raise ValueError("The FYERS feed for this account is already running in another AMW process (UI or API). "
                             "Press Stop first, then start again here.")
        self.collectors[user_id] = c
        self.svc.store.set_pref(user_id, "feed", {"symbols": symbols, "poll_every_min": poll_every_min, "desired": "running"})
        c.start()
        c.heartbeat(force=True)
        return c

    def stop(self, user_id: int) -> None:
        """Stops the user's feed wherever it runs: a local collector directly, one in another process via the lease."""
        c = self.collectors.pop(user_id, None)
        if c:
            c.stop()
        self.svc.store.feed_lease_release(user_id, None, status={"status": "STOPPED", "detail": "stopped by user",
                                                                 "at": str(self.clock())})
        saved = self.svc.store.get_pref(user_id, "feed", None)
        if saved and saved.get("desired") != "stopped":
            self.svc.store.set_pref(user_id, "feed", {**saved, "desired": "stopped"})

    def resume_all(self, users: list[int] | None = None, retry_after: float | None = LEASE_STALE_S + 5) -> list[int]:
        """Process start-up (AMW_FEED_AUTORESUME=1): restart the feeds users left running. A feed without today's login
        waits in TOKEN_EXPIRED and resumes by itself after the user logs in (in any process).
        After a crash/restart the previous holder's lease still looks alive for up to LEASE_STALE_S, so users whose
        lease is held are retried once after `retry_after` seconds (a feed genuinely running elsewhere stays there)."""
        started, held = [], []
        for uid in (users if users is not None else self.svc.store.feed_holders()):
            saved = self.svc.store.get_pref(uid, "feed", None) or {}
            if saved.get("desired") != "running" or not saved.get("symbols"):
                continue
            c = self.collectors.get(uid)
            if c and c.alive():
                continue
            try:
                self.start(uid, saved["symbols"], int(saved.get("poll_every_min") or 1))
                started.append(uid)
            except ValueError as e:
                log.info("feed auto-resume deferred for user %s: %s", uid, e)
                held.append(uid)
        if held and retry_after is not None:
            t = threading.Timer(retry_after, self.resume_all, kwargs={"users": held, "retry_after": None})
            t.daemon = True
            t.start()
        return started

    def collector_status(self, user_id: int) -> dict | None:
        c = self.collectors.get(user_id)
        if c and c.alive():
            return {**c.public(), "runner": "this process"}
        rt = self.svc.store.feed_runtime(user_id)
        if rt and rt["feed_holder"] and rt["feed_heartbeat"] and \
                rt["feed_heartbeat"] > self.clock() - timedelta(seconds=LEASE_STALE_S):
            return {**(rt["feed_status"] or {}), "runner": "another AMW process",
                    "subscribers": self.hub.subscriber_count(user_id)}
        if c:
            return {**c.public(), "runner": "this process"}
        return None

    def status(self, user_id: int) -> dict:
        conn = self.svc.store.broker_public(user_id, "fyers", now=self.clock())
        return {"connected": bool(conn), "connection": conn, "collector": self.collector_status(user_id),
                "saved": self.svc.store.get_pref(user_id, "feed", None)}

    def provider_for(self, user_id: int):
        """Scans on the user's FYERS data; they share the account's per-minute budget with the collector, wherever it runs."""
        from .fyers import FyersHistoryProvider
        conn = self.svc.store.broker_get(user_id, "fyers")
        if not conn or not conn["access_token"] or (conn["token_expires"] and conn["token_expires"] <= self.clock()):
            return None
        lim = self.limiter(user_id)          # per-minute budget is shared with the collector in ANY process (DB counter)
        return FyersHistoryProvider(FyersClient(conn["app_id"], conn["access_token"], self.transport, lim), clock=self.clock)
