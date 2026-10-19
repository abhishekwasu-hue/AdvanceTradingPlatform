"""FYERS API v3 client for 1-minute candles (user-owned account; analysis only — no order endpoints).

Facts used (from FYERS docs/community; verify against the current docs before production):
* Login: GET https://api-t1.fyers.in/api/v3/generate-authcode?client_id&redirect_uri&response_type=code&state
  -> redirect with `auth_code` & `state`; POST /api/v3/validate-authcode {grant_type, appIdHash=sha256("app_id:secret"), code}
  -> {"s":"ok","access_token":...}. Tokens expire daily; refresh tokens are not used (they require the user's PIN).
* Data: GET https://api-t1.fyers.in/data/history?symbol&resolution=1&date_format=1&range_from&range_to&cont_flag=1
  header `Authorization: <app_id>:<access_token>` -> {"s":"ok","candles":[[epoch_s, o, h, l, c, v], ...]}.
  Intraday requests cover at most 100 days.
* Rate limits: 10/s, 200/min, 100000/day. Repeated per-minute breaches can block the account for the day, so AMW
  defaults to 4/s and 150/min and stops after 2 breaches in a day.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlencode

import pandas as pd
import requests

from ..ai.redact import redact

API = "https://api-t1.fyers.in"
TZ = "Asia/Kolkata"
INDEX_MAP = {"^NSEI": "NSE:NIFTY50-INDEX", "^NSEBANK": "NSE:NIFTYBANK-INDEX", "^CNXIT": "NSE:NIFTYIT-INDEX"}
RESOLUTION = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1H": "60", "1D": "D"}
TOKEN_ERROR_CODES = {-8, -15, -16, -17, -300}   # invalid/expired token family (FYERS codes; -300 also seen for bad input)

# transport(method, url, params, json_body, headers, timeout) -> (status, json)
Transport = Callable[[str, str, dict | None, dict | None, dict, float], tuple[int, Any]]


def requests_transport(method, url, params, body, headers, timeout):
    r = requests.request(method, url, params=params, json=body, headers=headers, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


class FyersError(RuntimeError):
    """code: token_expired | invalid_credentials | rate_limited | rate_limit_guard | bad_request | unavailable |
    network | timeout | unknown"""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def to_fyers_symbol(ticker: str) -> str:
    t = ticker.strip().upper()
    if t in INDEX_MAP:
        return INDEX_MAP[t]
    if t.startswith("NSE:") or t.startswith("BSE:"):
        return t
    base = t[:-3] if t.endswith(".NS") else t
    return f"NSE:{base}-EQ"


def from_fyers_symbol(sym: str) -> str:
    for k, v in INDEX_MAP.items():
        if v == sym:
            return k
    s = sym.split(":", 1)[-1]
    return (s[:-3] if s.endswith("-EQ") else s) + ".NS"


def app_id_hash(app_id: str, secret: str) -> str:
    return hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()


def next_expiry(issued: datetime, hour: int = 6) -> datetime:
    """Conservative token expiry: next 06:00 IST after issue (naive UTC returned)."""
    loc = pd.Timestamp(issued).tz_localize("UTC").tz_convert(TZ)
    exp = loc.normalize() + pd.Timedelta(hours=hour)
    if exp <= loc:
        exp += pd.Timedelta(days=1)
    return exp.tz_convert("UTC").tz_localize(None).to_pydatetime()


class RateLimiter:
    """Sliding-window limiter shared by every call made with one user's FYERS app in this process (collector + scans).
    `breach_store=(get(day) -> int, put(day, n))` persists the daily breach count so every AMW process honours the
    same guard (FYERS may block an account for the day after repeated per-minute breaches)."""

    MAX_BREACHES = 2

    def __init__(self, per_second: int = 4, per_minute: int = 150, clock=time.monotonic, sleeper=time.sleep,
                 breach_store: tuple[Callable[[str], int], Callable[[str, int], None]] | None = None,
                 shared_minute: Callable[[int], bool] | None = None, wall=time.time):
        """`shared_minute(limit) -> bool` takes one unit of a per-minute budget shared by ALL processes (database
        counter); when it is exhausted the call waits for the next minute."""
        self.shared_minute, self.wall = shared_minute, wall
        self.ps, self.pm = per_second, per_minute
        self.clock, self.sleep = clock, sleeper
        self.sec, self.min = deque(), deque()
        self.lock = threading.Lock()
        self.breaches_today, self.breach_day = 0, None
        self.breach_store = breach_store

    def acquire(self, max_wait: float = 65.0) -> None:
        deadline = self.clock() + max_wait
        while True:
            with self.lock:
                now = self.clock()
                while self.sec and now - self.sec[0] >= 1.0:
                    self.sec.popleft()
                while self.min and now - self.min[0] >= 60.0:
                    self.min.popleft()
                if len(self.sec) < self.ps and len(self.min) < self.pm:
                    if self.shared_minute is None or self.shared_minute(self.pm):
                        self.sec.append(now)
                        self.min.append(now)
                        return
                    wait = 60.0 - (self.wall() % 60.0) + 0.05      # shared budget used up: next minute window
                else:
                    wait = max(1.0 - (now - self.sec[0]) if len(self.sec) >= self.ps else 0,
                               60.0 - (now - self.min[0]) if len(self.min) >= self.pm else 0, 0.01)
            if self.clock() + wait > deadline:
                raise FyersError("rate_limit_guard", "local FYERS rate budget exhausted; try fewer symbols", True)
            self.sleep(wait)

    def breaches(self, today: str) -> int:
        if self.breach_store:
            return int(self.breach_store[0](today) or 0)
        return self.breaches_today if self.breach_day == today else 0

    def check_guard(self, today: str, max_breaches: int | None = None) -> None:
        n = self.breaches(today)
        if n >= (max_breaches or self.MAX_BREACHES):
            raise FyersError("rate_limit_guard", f"FYERS rate limit hit {n}x today; FYERS calls paused until the next "
                             "day to protect your account from a daily block", False)

    def record_breach(self, today: str, max_breaches: int | None = None) -> None:
        with self.lock:
            n = self.breaches(today) + 1
            if self.breach_store:
                self.breach_store[1](today, n)
            else:
                self.breach_day, self.breaches_today = today, n
        self.check_guard(today, max_breaches)


class FyersClient:
    def __init__(self, app_id: str, access_token: str | None = None, transport: Transport | None = None,
                 limiter: RateLimiter | None = None, timeout: float = 15):
        self.app_id = app_id
        self._token = access_token
        self.transport = transport or requests_transport
        self.limiter = limiter or RateLimiter()
        self.timeout = timeout

    def __repr__(self) -> str:
        return "<FyersClient token=[REDACTED]>"

    # ---------------- login
    @staticmethod
    def new_state() -> str:
        return secrets.token_urlsafe(24)

    def login_url(self, redirect_uri: str, state: str) -> str:
        return f"{API}/api/v3/generate-authcode?" + urlencode({"client_id": self.app_id, "redirect_uri": redirect_uri,
                                                               "response_type": "code", "state": state})

    def exchange_code(self, secret: str, auth_code: str) -> str:
        st, js = self._raw("POST", f"{API}/api/v3/validate-authcode", None,
                           {"grant_type": "authorization_code", "appIdHash": app_id_hash(self.app_id, secret), "code": auth_code},
                           {"Content-Type": "application/json"}, secrets_=(secret, auth_code))
        if st == 200 and isinstance(js, dict) and js.get("s") == "ok" and js.get("access_token"):
            self._token = js["access_token"]
            return self._token
        raise FyersError("invalid_credentials", "FYERS rejected the login code or app credentials: "
                         + redact(str((js or {}).get("message", f"HTTP {st}")), secret, auth_code))

    # ---------------- data
    def _auth(self) -> dict:
        if not self._token:
            raise FyersError("token_expired", "Not logged in to FYERS today - log in again")
        return {"Authorization": f"{self.app_id}:{self._token}"}

    def _raw(self, method, url, params, body, headers, secrets_=()):
        try:
            return self.transport(method, url, params, body, headers, self.timeout)
        except requests.Timeout as e:
            raise FyersError("timeout", "FYERS did not respond in time", True) from e
        except requests.RequestException as e:
            raise FyersError("network", redact(f"network error contacting FYERS ({type(e).__name__})", self._token, *secrets_), True) from e

    def history(self, symbol: str, resolution: str, day_from: str, day_to: str) -> list[list]:
        self.limiter.acquire()
        st, js = self._raw("GET", f"{API}/data/history", {"symbol": symbol, "resolution": resolution, "date_format": "1",
                                                         "range_from": day_from, "range_to": day_to, "cont_flag": "1"},
                           None, self._auth())
        msg = redact(str((js or {}).get("message", "")) if isinstance(js, dict) else "", self._token)
        if st == 429:
            raise FyersError("rate_limited", f"FYERS rate limit (HTTP 429) {msg}", True)
        if st == 401 or (isinstance(js, dict) and js.get("s") != "ok" and js.get("code") in TOKEN_ERROR_CODES
                         and "token" in msg.lower()):
            raise FyersError("token_expired", f"FYERS session expired or invalid - log in again ({msg or st})")
        if st >= 500:
            raise FyersError("unavailable", f"FYERS unavailable (HTTP {st})", True)
        if st != 200 or not isinstance(js, dict) or js.get("s") not in ("ok", "no_data"):
            raise FyersError("bad_request", f"FYERS history error for {symbol}: {msg or st}")
        return js.get("candles") or []


def candles_to_bars(symbol: str, candles: list[list], now_utc: datetime, grace_s: float = 5.0, source: str = "fyers") -> tuple[list[dict], list[str]]:
    """Convert FYERS candles to validated, COMPLETED 1-minute bar dicts. Returns (bars, rejection reasons)."""
    from .bars import Bar, is_complete, validate_bar
    out, rejected = [], []
    for c in candles:
        try:
            ep, o, h, lo, cl = int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])
            v = None if len(c) < 6 or c[5] is None else float(c[5])
        except (TypeError, ValueError, IndexError):
            rejected.append(f"malformed candle {str(c)[:60]}")
            continue
        ts = pd.Timestamp(ep, unit="s", tz="UTC").tz_convert(TZ)
        if not is_complete(ts, now_utc, grace_s):
            continue                                     # forming bar: never stored or broadcast
        b = Bar(symbol, ts.isoformat(), o, h, lo, cl, v, source)
        why = validate_bar(b)
        if why:
            rejected.append(f"{ts.strftime('%H:%M')} {why}")
            continue
        out.append(b.to_dict())
    return out, rejected


class FyersHistoryProvider:
    """DataProvider over a user's FYERS account (for scans on that user's broker data). Daily/intraday OHLCV."""
    name = "FYERS (your broker account)"
    data_kind = "broker (user-licensed)"
    cache = None

    def __init__(self, client: FyersClient, clock=None):
        from ..storage.db import utcnow
        self.client, self.clock = client, clock or utcnow

    def fetch_many(self, tickers: list[str], tf: str) -> dict[str, pd.DataFrame | None]:
        res = RESOLUTION.get("1D" if tf == "1W" else tf)
        days = 365 if res == "D" else 59
        today = pd.Timestamp(self.clock()).tz_localize("UTC").tz_convert(TZ).date()
        self.client.limiter.check_guard(str(today))
        out = {}
        for t in tickers:
            try:
                cs = self.client.history(to_fyers_symbol(t), res, str(today - timedelta(days=days)), str(today))
            except FyersError as e:
                if e.code == "rate_limited":
                    self.client.limiter.record_breach(str(today))     # raises rate_limit_guard at the daily maximum
                if e.code in ("token_expired", "rate_limit_guard"):
                    raise
                out[t] = None
                continue
            if not cs:
                out[t] = None
                continue
            df = pd.DataFrame(cs, columns=["ts", "Open", "High", "Low", "Close", "Volume"][:len(cs[0])])
            idx = pd.to_datetime(df.pop("ts"), unit="s", utc=True).dt.tz_convert(TZ)
            df.index = idx.dt.tz_localize(None).dt.normalize() if res == "D" else idx
            if tf == "1W":
                df = df.resample("W-MON", label="left", closed="left").agg(
                    {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
            out[t] = df
        return out

    def health(self) -> dict:
        return {"provider": self.name, "data_kind": self.data_kind}
