#!/usr/bin/env python3
"""CI staging stack: a fake Upstox v2 + Dhan v2 broker (deploy/ci/compose.ci.yml, service `fake-broker`).

The application's real adapters (app/brokers/upstox.py, app/brokers/dhan.py) are used unchanged: inside the CI stack
the broker host names resolve to this container (compose `extra_hosts`) and the app trusts only the CA this script
writes (`SSL_CERT_FILE`), so a request can never reach a real broker from the stack, and every request that reaches a
"broker" is recorded here.

* 443 (TLS): api.upstox.com (/v2/...), assets.upstox.com (instrument master), api.dhan.co (/v2/...), images.dhan.co
  (scrip master), told apart by the Host header. One certificate carries all four names.
* 8099 (plain HTTP, compose network only): the same broker routes, plus the control API the CI scripts use:
    GET  /__fake/health                   ready + the fake clock
    GET  /__fake/calls                    every request so far; `orders` lists the order-API calls (place / modify /
                                          cancel) - the Phase 0 acceptance flow asserts it stays empty
    POST /__fake/calls/reset
    POST /__fake/state  {"stale_minutes": {"dhan": 60}, "ltp": {"RELIANCE": 2800.5}, "clear_ltp": ["RELIANCE"]}
    GET  /__fake/quote?symbol=RELIANCE&broker=dhan   the price a quote would show now

Order-API calls are answered with an error (400, never an authentication error: a token must not look expired) and never
"fill": nothing here models an order book.

Market data is synthetic and deterministic (a smooth function of the symbol and the minute, no randomness), on the
NSE clock (09:15-15:30 IST, Monday-Friday). "Now" is the wall clock plus FAKE_OFFSET_SECONDS - the same shift the app
containers get from libfaketime (deploy/ci/stack.sh), so the stack can run the acceptance flow inside a session at any
hour. `stale_minutes` freezes a broker's bars and quote timestamps that many minutes in the past.

Standard library only, plus `cryptography` for the throwaway CA (present in the backend image this runs in).
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import secrets
import ssl
import sys
import threading
import time
import zlib
from datetime import date, datetime, time as dtime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

IST = timezone(timedelta(hours=5, minutes=30))
OFFSET_SECONDS = int(os.environ.get("FAKE_OFFSET_SECONDS", "0") or 0)
TLS_DIR = os.environ.get("FAKE_TLS_DIR", "/tls")
TLS_PORT = int(os.environ.get("FAKE_TLS_PORT", "443"))
CONTROL_PORT = int(os.environ.get("FAKE_CONTROL_PORT", "8099"))
HOSTS = ("api.upstox.com", "assets.upstox.com", "api.dhan.co", "images.dhan.co", "fake-broker")
SESSION_OPEN, SESSION_CLOSE = dtime(9, 15), dtime(15, 30)
SESSION_MINUTES = 375

# symbol -> (ISIN, Dhan security id, base price)
EQUITIES: Dict[str, Tuple[str, str, float]] = {
    "RELIANCE": ("INE002A01018", "2885", 2900.0), "TCS": ("INE467B01029", "11536", 4100.0), "INFY": ("INE009A01021", "1594", 1800.0),
    "HDFCBANK": ("INE040A01034", "1333", 1650.0), "ICICIBANK": ("INE090A01021", "4963", 1250.0), "SBIN": ("INE062A01020", "3045", 820.0),
    "LT": ("INE018A01030", "11483", 3600.0), "ITC": ("INE154A01025", "1660", 470.0), "AXISBANK": ("INE238A01034", "5900", 1150.0),
    "KOTAKBANK": ("INE237A01028", "1922", 1800.0),
}
# symbol -> (Upstox instrument key, Upstox display name, Dhan security id, base price)
INDICES: Dict[str, Tuple[str, str, str, float]] = {
    "NIFTY": ("NSE_INDEX|Nifty 50", "Nifty 50", "13", 25000.0), "BANKNIFTY": ("NSE_INDEX|Nifty Bank", "Nifty Bank", "25", 52000.0),
}
UPSTOX_KEY = {s: f"NSE_EQ|{isin}" for s, (isin, _id, _p) in EQUITIES.items()} | {s: v[0] for s, v in INDICES.items()}
UPSTOX_SYMBOL = {v: k for k, v in UPSTOX_KEY.items()}
DHAN_ID = {("NSE_EQ", sec): s for s, (_i, sec, _p) in EQUITIES.items()} | {("IDX_I", v[2]): s for s, v in INDICES.items()}

LOCK = threading.Lock()
STATE: Dict[str, Any] = {"stale_minutes": {"upstox": 0, "dhan": 0}, "ltp": {}}
CALLS: List[Dict[str, Any]] = []
MAX_CALLS = 200_000


def now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=OFFSET_SECONDS)


# --- synthetic prices -------------------------------------------------------------------------------------------------

def base_price(symbol: str) -> float:
    if symbol in EQUITIES:
        return EQUITIES[symbol][2]
    if symbol in INDICES:
        return INDICES[symbol][3]
    if symbol.endswith(("CE", "PE")):
        return 120.0
    return 1000.0


def price_at(symbol: str, minute: int) -> float:
    """A smooth, positive, deterministic price for `symbol` at the epoch minute `minute`."""
    seed = zlib.crc32(symbol.encode())
    a, b, c = (seed % 997) / 997 * 2 * math.pi, (seed % 991) / 991 * 2 * math.pi, (seed % 983) / 983 * 2 * math.pi
    drift = 0.04 * math.sin(minute / (1440 * 9) * 2 * math.pi + a)          # a slow swing over days
    swing = 0.006 * math.sin(minute / 47.0 + b)                              # intraday waves
    ripple = 0.0015 * math.sin(minute / 5.3 + c)
    return round(base_price(symbol) * (1 + drift + swing + ripple), 2)


def is_session_day(day: date) -> bool:
    return day.weekday() < 5


def session_minutes(day: date) -> Iterator[datetime]:
    start = datetime.combine(day, SESSION_OPEN, IST)
    for i in range(SESSION_MINUTES):
        yield start + timedelta(minutes=i)


def interval_minutes(raw: str) -> Optional[int]:
    """Upstox ("1minute", "30minute", "5min", "day") and Dhan ("1", "5", "15", "25", "60") spellings -> minutes; None = daily."""
    text = (raw or "").strip().lower()
    if text in ("day", "1d", "d", "daily", "week", "month"):
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return max(1, int(digits)) if digits else 1


def data_until(broker: str) -> datetime:
    """The newest instant this broker's data reaches: now, or `stale_minutes` before it."""
    with LOCK:
        stale = int(STATE["stale_minutes"].get(broker, 0) or 0)
    return now() - timedelta(minutes=stale)


def bars(symbol: str, minutes: Optional[int], start_day: date, end_day: date, until: datetime) -> List[Tuple[datetime, float, float, float, float, float]]:
    """(start, open, high, low, close, volume) bars of `minutes` (None = daily) for session days in [start_day, end_day],
    only bars that have started by `until` (the bar still forming is included, as a broker sends it)."""
    out = []
    day = start_day
    while day <= end_day:
        if is_session_day(day):
            open_at = datetime.combine(day, SESSION_OPEN, IST)
            if minutes is None:
                if open_at <= until:
                    last = min(datetime.combine(day, SESSION_CLOSE, IST), until)
                    span = max(1, int((last - open_at).total_seconds() // 60))
                    samples = [price_at(symbol, int(open_at.timestamp() // 60) + k) for k in range(0, span + 1, 15)]
                    o, c = samples[0], price_at(symbol, int(last.timestamp() // 60))
                    out.append((datetime.combine(day, dtime(0, 0), IST), o, max(samples + [c]), min(samples + [c]), c, 100000.0 + span * 10))
            else:
                bucket = open_at
                close_at = datetime.combine(day, SESSION_CLOSE, IST)
                while bucket < close_at and bucket <= until:
                    end = min(bucket + timedelta(minutes=minutes), close_at)
                    first = int(bucket.timestamp() // 60)
                    last_minute = int(min(end, until + timedelta(minutes=1)).timestamp() // 60)
                    span = max(1, last_minute - first)
                    samples = [price_at(symbol, first + k) for k in range(0, span + 1, max(1, span // 8))]
                    o, c = price_at(symbol, first), price_at(symbol, first + span)
                    out.append((bucket, o, max(samples + [c]), min(samples + [c]), c, 1000.0 + (first % 97) * 10))
                    bucket = end
        day += timedelta(days=1)
    return out


def quote_price(symbol: str, broker: str) -> Tuple[float, datetime]:
    """(last price, exchange time) for a quote right now."""
    until = data_until(broker)
    with LOCK:
        override = STATE["ltp"].get(symbol)
    price = float(override) if override is not None else price_at(symbol, int(until.timestamp() // 60))
    return price, until - timedelta(seconds=1)


# --- instrument masters -----------------------------------------------------------------------------------------------

def next_expiry(today: date) -> date:
    """The next Tuesday on or after `today` (NSE weekly index options)."""
    return today + timedelta(days=(1 - today.weekday()) % 7)


def upstox_master() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for symbol, (isin, _sec, _p) in EQUITIES.items():
        rows.append({"instrument_key": f"NSE_EQ|{isin}", "exchange": "NSE", "segment": "NSE_EQ", "trading_symbol": symbol, "name": symbol,
                     "instrument_type": "EQ", "lot_size": 1, "tick_size": 0.05, "isin": isin})
    for symbol, (key, display, _sec, _p) in INDICES.items():
        rows.append({"instrument_key": key, "exchange": "NSE", "segment": "NSE_INDEX", "trading_symbol": symbol, "name": display,
                     "instrument_type": "INDEX", "lot_size": 1, "tick_size": 0.05})
    expiry = next_expiry(now().astimezone(IST).date())
    expiry_ms = int(datetime.combine(expiry, dtime(15, 30), IST).timestamp() * 1000)
    spot = round(price_at("NIFTY", int(now().timestamp() // 60)) / 50) * 50
    for n, strike in enumerate(range(spot - 200, spot + 250, 50)):
        for right in ("CE", "PE"):
            label = f"NIFTY {strike} {right} {expiry.strftime('%d %b %y').upper()}"
            rows.append({"instrument_key": f"NSE_FO|{70000 + n * 2 + (right == 'PE')}", "exchange": "NSE", "segment": "NSE_FO",
                         "trading_symbol": label, "underlying_symbol": "NIFTY", "name": "NIFTY", "instrument_type": right,
                         "lot_size": 75, "tick_size": 0.05, "expiry": expiry_ms, "strike_price": float(strike), "weekly": True})
    return rows


def dhan_master_csv() -> str:
    buf = io.StringIO()
    fields = ["SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SMST_SECURITY_ID", "SEM_INSTRUMENT_NAME", "SEM_EXPIRY_CODE", "SEM_TRADING_SYMBOL",
              "SEM_LOT_UNITS", "SEM_CUSTOM_SYMBOL", "SEM_EXPIRY_DATE", "SEM_STRIKE_PRICE", "SEM_OPTION_TYPE", "SEM_TICK_SIZE",
              "SEM_EXPIRY_FLAG", "SM_SYMBOL_NAME"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for symbol, (_isin, sec, _p) in EQUITIES.items():
        writer.writerow({"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "E", "SEM_SMST_SECURITY_ID": sec, "SEM_INSTRUMENT_NAME": "EQUITY",
                         "SEM_EXPIRY_CODE": "0", "SEM_TRADING_SYMBOL": symbol, "SEM_LOT_UNITS": "1", "SEM_CUSTOM_SYMBOL": symbol,
                         "SEM_EXPIRY_DATE": "", "SEM_STRIKE_PRICE": "0", "SEM_OPTION_TYPE": "XX", "SEM_TICK_SIZE": "0.05",
                         "SEM_EXPIRY_FLAG": "NA", "SM_SYMBOL_NAME": symbol})
    for symbol, (_key, display, sec, _p) in INDICES.items():
        writer.writerow({"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "I", "SEM_SMST_SECURITY_ID": sec, "SEM_INSTRUMENT_NAME": "INDEX",
                         "SEM_EXPIRY_CODE": "0", "SEM_TRADING_SYMBOL": symbol, "SEM_LOT_UNITS": "1", "SEM_CUSTOM_SYMBOL": display,
                         "SEM_EXPIRY_DATE": "", "SEM_STRIKE_PRICE": "0", "SEM_OPTION_TYPE": "XX", "SEM_TICK_SIZE": "0.05",
                         "SEM_EXPIRY_FLAG": "NA", "SM_SYMBOL_NAME": symbol})
    return buf.getvalue()


# --- request log ------------------------------------------------------------------------------------------------------

def classify(host: str, method: str, path: str) -> str:
    """order (would place / change / cancel an order) | auth | read | control | unknown (set by the router)."""
    if path.startswith("/__fake"):
        return "control"
    if method in ("POST", "PUT", "DELETE", "PATCH"):
        if host.startswith("api.upstox") and (path.startswith(("/v2/order", "/v3/order")) or "/gtt" in path):
            return "order"
        if host.startswith("api.dhan") and path.startswith(("/v2/orders", "/v2/super/orders", "/v2/forever/orders", "/v2/edis")):
            return "order"
    if "/login/" in path or path.endswith("/logout"):
        return "auth"
    return "read"


def record(host: str, method: str, path: str, kind: str, status: int) -> None:
    entry = {"at": now().isoformat(timespec="seconds"), "host": host, "method": method, "path": path, "kind": kind, "status": status}
    with LOCK:
        if len(CALLS) < MAX_CALLS:
            CALLS.append(entry)


def calls_summary() -> Dict[str, Any]:
    with LOCK:
        calls = list(CALLS)
    by_route: Dict[str, int] = {}
    for c in calls:
        if c["kind"] == "control":
            continue
        key = f"{c['method']} {c['host']}{_route_shape(c['path'])}"
        by_route[key] = by_route.get(key, 0) + 1
    return {"total": sum(1 for c in calls if c["kind"] != "control"), "orders": [c for c in calls if c["kind"] == "order"],
            "unknown": [c for c in calls if c["kind"] == "unknown"][:50], "by_route": dict(sorted(by_route.items())), "now": now().isoformat()}


def _route_shape(path: str) -> str:
    """/v2/historical-candle/intraday/NSE_EQ|INE.../1minute -> /v2/historical-candle/intraday/* (ids folded)."""
    parts = path.split("?")[0].split("/")
    keep = []
    for p in parts:
        keep.append("*" if any(ch.isdigit() for ch in p) and p not in ("v2", "v3") else p)
    return "/".join(keep)


# --- broker routes ----------------------------------------------------------------------------------------------------

class Reply:
    def __init__(self, status: int = 200, body: Any = None, *, content_type: str = "application/json", headers: Optional[Dict[str, str]] = None,
                 raw: Optional[bytes] = None, kind: Optional[str] = None) -> None:
        self.status, self.headers, self.kind = status, dict(headers or {}), kind
        self.content_type = content_type
        self.raw = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())


def upstox_ok(data: Any) -> Reply:
    return Reply(200, {"status": "success", "data": data})


def upstox_bars(symbol: str, minutes: Optional[int], start_day: date, end_day: date) -> List[List[Any]]:
    rows = bars(symbol, minutes, start_day, end_day, data_until("upstox"))
    return [[t.isoformat(), o, h, lo, c, v, 0] for (t, o, h, lo, c, v) in reversed(rows)]      # Upstox: newest first


def upstox_entry(symbol: str) -> Dict[str, Any]:
    price, at = quote_price(symbol, "upstox")
    return {"instrument_token": UPSTOX_KEY.get(symbol, symbol), "symbol": symbol, "last_price": price, "volume": 125000,
            "last_trade_time": int(at.timestamp() * 1000), "timestamp": at.astimezone(IST).isoformat(), "oi": 0,
            "ohlc": {"open": round(price * 0.995, 2), "high": round(price * 1.01, 2), "low": round(price * 0.99, 2), "close": round(price * 0.998, 2)},
            "depth": {"buy": [{"price": round(price - 0.05, 2), "quantity": 100}], "sell": [{"price": round(price + 0.05, 2), "quantity": 100}]}}


def upstox_response_key(symbol: str) -> str:
    if symbol in INDICES:
        return f"NSE_INDEX:{INDICES[symbol][1]}"
    return f"NSE_EQ:{symbol}"


def symbols_from_keys(raw: str) -> List[str]:
    return [UPSTOX_SYMBOL.get(k.strip(), k.strip()) for k in (raw or "").split(",") if k.strip()]


def route_upstox(method: str, path: str, query: Dict[str, List[str]], body: bytes) -> Reply:
    q = {k: v[0] for k, v in query.items()}
    if path == "/v2/login/authorization/dialog" and method == "GET":
        redirect, state = q.get("redirect_uri", ""), q.get("state", "")
        code = "ci-code-" + secrets.token_hex(6)
        sep = "&" if "?" in redirect else "?"
        return Reply(302, None, headers={"Location": f"{redirect}{sep}code={quote(code)}&state={quote(state)}"})
    if path == "/v2/login/authorization/token" and method == "POST":
        form = {k: v[0] for k, v in parse_qs(body.decode() or "").items()}
        if not form.get("code") or not form.get("client_secret"):
            return Reply(400, {"error_description": "code and client_secret are required"})
        return Reply(200, {"access_token": "ci-upstox-" + secrets.token_hex(12), "token_type": "Bearer", "user_id": "CIUPX1",
                           "user_name": "CI Upstox user", "email": "ci-upstox@example.com"})
    if path == "/v2/user/profile":
        return upstox_ok({"user_id": "CIUPX1", "user_name": "CI Upstox user", "email": "ci-upstox@example.com", "exchanges": ["NSE", "NFO"]})
    if path == "/v2/user/get-funds-and-margin":
        return upstox_ok({"equity": {"available_margin": 500000.0, "used_margin": 0.0, "total_margin": 500000.0}})
    if path in ("/v2/market-quote/ltp", "/v2/market-quote/quotes"):
        out = {}
        for symbol in symbols_from_keys(q.get("instrument_key", "")):
            entry = upstox_entry(symbol)
            out[upstox_response_key(symbol)] = entry if path.endswith("quotes") else {"instrument_token": entry["instrument_token"],
                                                                                      "last_price": entry["last_price"]}
        return upstox_ok(out)
    if path.startswith("/v2/historical-candle/intraday/"):
        _, _, _, _, key, interval = path.split("/", 5)
        symbol = UPSTOX_SYMBOL.get(key, key)
        today = data_until("upstox").astimezone(IST).date()
        return upstox_ok({"candles": upstox_bars(symbol, interval_minutes(interval), today, today)})
    if path.startswith("/v2/historical-candle/"):
        parts = path.split("/")
        # /v2/historical-candle/{key}/{interval}/{to}/{from}
        key, interval, to_day, from_day = parts[3], parts[4], parts[5], parts[6] if len(parts) > 6 else parts[5]
        symbol = UPSTOX_SYMBOL.get(key, key)
        return upstox_ok({"candles": upstox_bars(symbol, interval_minutes(interval), date.fromisoformat(from_day), date.fromisoformat(to_day))})
    if path in ("/v2/portfolio/short-term-positions", "/v2/portfolio/long-term-holdings", "/v2/order/retrieve-all",
                "/v2/order/trades/get-trades-for-day", "/v2/option/contract"):
        return upstox_ok([])
    if path == "/v2/option/chain":
        return upstox_ok([])
    if path == "/v2/logout":
        return upstox_ok({})
    if classify("api.upstox.com", method, path) == "order":
        return Reply(400, {"status": "error", "errors": [{"message": "fake broker: the CI stack accepts no orders"}]})
    return Reply(404, {"status": "error", "errors": [{"message": f"fake broker: no route {method} {path}"}]}, kind="unknown")


def route_upstox_assets(method: str, path: str) -> Reply:
    if path.startswith("/market-quote/instruments/exchange/") and path.endswith(".json.gz"):
        return Reply(200, raw=gzip.compress(json.dumps(upstox_master()).encode()), content_type="application/gzip")
    return Reply(404, {"error": f"no route {method} {path}"}, kind="unknown")


def dhan_quote_entry(symbol: str) -> Dict[str, Any]:
    price, at = quote_price(symbol, "dhan")
    return {"last_price": price, "volume": 125000, "oi": 0, "last_trade_time": at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "ohlc": {"open": round(price * 0.995, 2), "high": round(price * 1.01, 2), "low": round(price * 0.99, 2), "close": round(price * 0.998, 2)},
            "depth": {"buy": [{"price": round(price - 0.05, 2), "quantity": 100}], "sell": [{"price": round(price + 0.05, 2), "quantity": 100}]}}


def route_dhan(method: str, path: str, body: bytes, headers: Dict[str, str]) -> Reply:
    if not headers.get("access-token"):
        return Reply(401, {"errorType": "Invalid_Authentication", "errorCode": "DH-901", "errorMessage": "Client ID or user generated access token is invalid or expired."})
    payload: Any = {}
    if body:
        try:
            payload = json.loads(body.decode())
        except ValueError:
            return Reply(400, {"errorCode": "DH-905", "errorMessage": "bad JSON"})
    if path == "/v2/profile":
        return Reply(200, {"dhanClientId": headers.get("client-id") or "CIDHN1", "name": "CI Dhan user", "tokenValidity": "valid"})
    if path == "/v2/fundlimit":
        return Reply(200, {"dhanClientId": headers.get("client-id"), "availabelBalance": 500000.0, "sodLimit": 500000.0, "utilizedAmount": 0.0})
    if path in ("/v2/marketfeed/quote", "/v2/marketfeed/ltp", "/v2/marketfeed/ohlc") and method == "POST":
        data: Dict[str, Dict[str, Any]] = {}
        for segment, ids in (payload or {}).items():
            for sec in ids or []:
                symbol = DHAN_ID.get((segment, str(sec)))
                if symbol is not None:
                    data.setdefault(segment, {})[str(sec)] = dhan_quote_entry(symbol)
        return Reply(200, {"data": data, "status": "success"})
    if path in ("/v2/charts/intraday", "/v2/charts/historical") and method == "POST":
        symbol = DHAN_ID.get((str(payload.get("exchangeSegment")), str(payload.get("securityId"))))
        if symbol is None:
            return Reply(400, {"errorCode": "DH-905", "errorMessage": "unknown security"})
        minutes = None if path.endswith("historical") else interval_minutes(str(payload.get("interval") or "1"))
        rows = bars(symbol, minutes, date.fromisoformat(payload["fromDate"][:10]), date.fromisoformat(payload["toDate"][:10]), data_until("dhan"))
        return Reply(200, {"open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                           "volume": [r[5] for r in rows], "timestamp": [int(r[0].timestamp()) for r in rows]})
    if path in ("/v2/positions", "/v2/holdings", "/v2/trades") and method == "GET":
        return Reply(200, [])
    if path == "/v2/orders" and method == "GET":
        return Reply(200, [])
    if path.startswith("/v2/orders/") and method == "GET":
        return Reply(404, {"errorCode": "DH-906", "errorMessage": "no such order"})
    if path == "/v2/optionchain/expirylist" and method == "POST":
        return Reply(200, {"data": [next_expiry(now().astimezone(IST).date()).isoformat()], "status": "success"})
    if path == "/v2/optionchain" and method == "POST":
        return Reply(200, {"data": {"last_price": quote_price("NIFTY", "dhan")[0], "oc": {}}, "status": "success"})
    if classify("api.dhan.co", method, path) == "order":
        return Reply(400, {"errorType": "Order_Error", "errorCode": "DH-906", "errorMessage": "fake broker: the CI stack accepts no orders"})
    return Reply(404, {"errorCode": "DH-905", "errorMessage": f"fake broker: no route {method} {path}"}, kind="unknown")


def route_dhan_images(method: str, path: str) -> Reply:
    if path == "/api-data/api-scrip-master.csv":
        return Reply(200, raw=dhan_master_csv().encode(), content_type="text/csv")
    return Reply(404, {"error": f"no route {method} {path}"}, kind="unknown")


def route_control(method: str, path: str, query: Dict[str, List[str]], body: bytes) -> Reply:
    if path == "/__fake/health":
        return Reply(200, {"ok": True, "now": now().isoformat(), "now_ist": now().astimezone(IST).isoformat(), "offset_seconds": OFFSET_SECONDS})
    if path == "/__fake/calls" and method == "GET":
        return Reply(200, calls_summary())
    if path == "/__fake/calls/reset" and method == "POST":
        with LOCK:
            CALLS.clear()
        return Reply(200, {"ok": True})
    if path == "/__fake/state" and method == "POST":
        change = json.loads(body.decode() or "{}")
        with LOCK:
            for broker, minutes in (change.get("stale_minutes") or {}).items():
                STATE["stale_minutes"][str(broker)] = int(minutes)
            for symbol, ltp in (change.get("ltp") or {}).items():
                STATE["ltp"][str(symbol).upper()] = float(ltp)
            for symbol in change.get("clear_ltp") or []:
                STATE["ltp"].pop(str(symbol).upper(), None)
            snapshot = json.loads(json.dumps(STATE))
        return Reply(200, snapshot)
    if path == "/__fake/quote" and method == "GET":
        symbol = (query.get("symbol") or [""])[0].upper()
        broker = (query.get("broker") or ["upstox"])[0]
        price, at = quote_price(symbol, broker)
        return Reply(200, {"symbol": symbol, "broker": broker, "ltp": price, "exchange_time": at.isoformat()})
    return Reply(404, {"error": f"no control route {method} {path}"})


def dispatch(host: str, method: str, raw_path: str, body: bytes, headers: Dict[str, str]) -> Reply:
    split = urlsplit(raw_path)
    path, query = unquote(split.path), parse_qs(split.query)
    if path.startswith("/__fake"):
        return route_control(method, path, query, body)
    if host.startswith("api.upstox"):
        return route_upstox(method, path, query, body)
    if host.startswith("assets.upstox"):
        return route_upstox_assets(method, path)
    if host.startswith("api.dhan"):
        return route_dhan(method, path, body, headers)
    if host.startswith("images.dhan"):
        return route_dhan_images(method, path)
    return Reply(404, {"error": f"unknown host {host}"}, kind="unknown")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "atp-ci-fake-broker"

    def log_message(self, fmt: str, *args: Any) -> None:       # the request log is the call record, not stderr
        return

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            reply = dispatch(host, self.command, self.path, body, headers)
        except Exception as exc:  # noqa: BLE001 - a fake must answer, and say what broke
            reply = Reply(500, {"error": f"fake broker failed: {type(exc).__name__}: {exc}"})
        path = unquote(urlsplit(self.path).path)
        kind = reply.kind or classify(host, self.command, path)
        record(host, self.command, path, kind, reply.status)
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.content_type)
        self.send_header("Content-Length", str(len(reply.raw)))
        for key, value in reply.headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(reply.raw)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _handle


class TLSServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 1024          # the listen backlog: the default 5 drops SYNs under a load test (1 s, 3 s, 7 s... retries)
    context: ssl.SSLContext

    def finish_request(self, request: Any, client_address: Any) -> None:
        try:
            request = self.context.wrap_socket(request, server_side=True)     # the handshake runs in the request's own thread
        except (ssl.SSLError, OSError):
            return
        self.RequestHandlerClass(request, client_address, self)


class PlainServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 1024


# --- the throwaway CA -------------------------------------------------------------------------------------------------

def ensure_tls(directory: str) -> Tuple[str, str]:
    """ca.pem (what the app trusts) and broker.pem / broker.key (this server), written once per stack."""
    cert_path, key_path, ca_path = (os.path.join(directory, n) for n in ("broker.pem", "broker.key", "ca.pem"))
    if all(os.path.exists(p) for p in (cert_path, key_path, ca_path)):
        return cert_path, key_path
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    os.makedirs(directory, exist_ok=True)
    real_now = datetime.now(timezone.utc)
    start, end = real_now - timedelta(days=30), real_now + timedelta(days=30)      # covers the fake clock's shift either way
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ATP CI fake broker CA (throwaway)")])
    ca_cert = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
               .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
               .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                            key_encipherment=False, data_encipherment=False, key_agreement=False, encipher_only=False,
                                            decipher_only=False), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
               .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-broker")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(end)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(pem) + ca_cert.public_bytes(pem))
    tmp = ca_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(ca_cert.public_bytes(pem))
    for path in (cert_path, tmp):
        os.chmod(path, 0o644)                                    # the app containers run as another user
    os.chmod(key_path, 0o600)
    os.replace(tmp, ca_path)                                     # last: its presence is the health check
    return cert_path, key_path


def main() -> int:
    cert, key = ensure_tls(TLS_DIR)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    tls = TLSServer(("0.0.0.0", TLS_PORT), Handler)
    tls.context = context
    control = PlainServer(("0.0.0.0", CONTROL_PORT), Handler)
    for server in (tls, control):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"fake broker: TLS :{TLS_PORT} ({', '.join(HOSTS[:4])}), control :{CONTROL_PORT}, clock offset {OFFSET_SECONDS}s, "
          f"now {now().astimezone(IST).isoformat(timespec='seconds')}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
