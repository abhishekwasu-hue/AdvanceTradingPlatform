#!/usr/bin/env python3
"""Phase 0 acceptance flow on the CI staging stack (docs/specs/ATP_PHASE0_FREEZE.md section 5).

Runs in the stack's `ci-runner` container (deploy/ci/stack.sh acceptance), against the real edge (Caddy, HTTPS) of a
stack built from this commit, with the real Upstox and Dhan adapters talking to the fake broker:

  admin      the platform admin (SUPER_ADMIN_EMAILS) signs up, turns on two-factor authentication, sets the Phase 0
             flag posture (deploy/ci/ci_posture.py) through Admin -> Controls and issues two invite codes
  invite     sign-up without a code is refused; two users sign up with one code each; a used code is refused
  broker     user A links Upstox (the OAuth redirect flow, through the fake broker's login page), user B links Dhan
             (client id + access token); both read-only smoke tests pass
  screen     each user runs a screen on their own broker session's bars (Tier A)
  proposal   each user's plan-before-trade PAPER ticket becomes an ENTRY proposal (approval-only)
  approve    Approve PAPER: one PAPER position at the re-quoted live price
  exit       the price falls through the stop: the worker closes the position by itself (protective exit)
  journal    the closed trade is in the user's journal; the other user cannot read it
  no orders  a LIVE deployment is refused, and the fake broker saw no order-API call (place / modify / cancel) at all
  stale      with user B's broker feed an hour stale, an alert rule records nothing and sends nothing; with the feed
             current again, the same rule fires

Exit 0 when every step passed; the JSON report (--report) lists each step, its timing and the broker-call summary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ci_posture import POSTURE  # noqa: E402

BASE = os.environ.get("ATP_CI_BASE_URL", "https://atp.staging.internal")
CADDY_CA = os.environ.get("ATP_CI_CADDY_CA", "/state/caddy-root.crt")
FAKE = os.environ.get("ATP_CI_FAKE_CONTROL", "http://fake-broker:8099")
ADMIN_EMAIL = os.environ.get("ATP_CI_ADMIN_EMAIL") or "ci-admin@example.com"
PASSWORD = "Ci-Acceptance-" + os.urandom(6).hex() + "!"
SCREEN = "close > 0.5 * SMA(close, 20)"
SCREEN_SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"]
CHECKLIST = {k: True for k in ("trend_aligned", "level_identified", "pullback_confirmed", "commitment_candle", "no_event", "liquidity_ok")}
CYCLE_WAIT = float(os.environ.get("ATP_CI_EXIT_WAIT_SECONDS", "180"))
ALERT_WAIT = float(os.environ.get("ATP_CI_ALERT_WAIT_SECONDS", "300"))


class StepFailed(AssertionError):
    pass


def check(condition: Any, message: str) -> None:
    if not condition:
        raise StepFailed(message)


def ok(resp: httpx.Response, *expected: int) -> Any:
    expected = expected or (200,)
    if resp.status_code not in expected:
        raise StepFailed(f"{resp.request.method} {resp.request.url.path} -> {resp.status_code} (wanted {'/'.join(map(str, expected))}): {resp.text[:400]}")
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def wait_for(what: str, probe: Callable[[], Any], timeout: float, every: float = 3.0) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(every)
    raise StepFailed(f"timed out after {timeout:.0f} s waiting for {what} (last seen: {str(last)[:300]})")


class Api:
    """One person's HTTPS session against the stack's edge."""

    def __init__(self, token: Optional[str] = None) -> None:
        self.client = httpx.Client(base_url=BASE, verify=CADDY_CA, timeout=60.0)
        self.token = token

    def _h(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.client.get(path, headers=self._h(), **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> httpx.Response:
        return self.client.post(path, headers=self._h(), json=body, **kw)

    def put(self, path: str, body: Any = None) -> httpx.Response:
        return self.client.put(path, headers=self._h(), json=body)


fake = httpx.Client(base_url=FAKE, timeout=30.0)


def fake_state(**change: Any) -> Dict[str, Any]:
    return ok(fake.post("/__fake/state", json=change))


def fake_quote(symbol: str, broker: str) -> float:
    return float(ok(fake.get("/__fake/quote", params={"symbol": symbol, "broker": broker}))["ltp"])


def fake_calls() -> Dict[str, Any]:
    return ok(fake.get("/__fake/calls"))


class Flow:
    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []
        self.ctx: Dict[str, Any] = {}
        self.failed = False

    def run(self, name: str, fn: Callable[[], Optional[str]]) -> None:
        if self.failed:
            self.steps.append({"step": name, "status": "not run", "detail": "an earlier step failed"})
            print(f"  ----  {name}: not run")
            return
        started = time.monotonic()
        try:
            detail = fn() or ""
            status = "pass"
        except Exception as exc:  # noqa: BLE001 - every failure is reported with its step
            detail = f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, StepFailed):
                detail += "\n" + traceback.format_exc(limit=4)
            status = "FAIL"
            self.failed = True
        seconds = round(time.monotonic() - started, 1)
        self.steps.append({"step": name, "status": status, "detail": detail, "seconds": seconds})
        print(f"  {status:4}  {name} ({seconds} s){': ' + detail if detail else ''}", flush=True)
        if status == "FAIL" and os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=acceptance: {name}::{detail.splitlines()[0][:900]}")


# --- the steps ----------------------------------------------------------------------------------------------------------

def stack_ready(flow: Flow) -> str:
    anon = Api()
    deep = ok(anon.get("/api/system/health/deep"))
    check(deep.get("status") == "ok", f"deep health {deep.get('status')}: {deep.get('checks')}")
    status = ok(anon.get("/api/system/status"))
    nse = (status.get("sessions") or {}).get("NSE") or {}
    check(nse.get("is_open"), f"the NSE session is not open on the stack's clock ({nse.get('reason')}) - deploy/ci/ci_clock.py should have moved it into one")
    health = ok(fake.get("/__fake/health"))
    flow.ctx["clock"] = health.get("now_ist")
    return f"deep health ok; NSE open on the stack's clock ({health.get('now_ist')})"


OFFSET = int(os.environ.get("ATP_CI_OFFSET_SECONDS", "0") or 0)        # the API's clock shift (deploy/ci/ci_clock.py)


def _totp_code(secret: str, previous: Optional[str]) -> str:
    """The code the API expects now (on its clock), waiting for the next 30 s window when this one was already used
    (codes are single-use)."""
    totp = pyotp.TOTP(secret)
    code = totp.at(time.time() + OFFSET)
    while code == previous:
        time.sleep(1)
        code = totp.at(time.time() + OFFSET)
    return code


def admin_setup(flow: Flow) -> str:
    admin = Api()
    token = ok(admin.post("/api/auth/register", {"email": ADMIN_EMAIL, "password": PASSWORD}), 201)["access_token"]
    admin.token = token
    me = ok(admin.get("/api/auth/me"))
    check(me["role"] == "SUPER_ADMIN", f"{ADMIN_EMAIL} is {me['role']}, not SUPER_ADMIN - is SUPER_ADMIN_EMAILS passed to the API container?")
    secret = ok(admin.post("/api/auth/mfa/enrol"))["secret"]
    code = _totp_code(secret, None)
    ok(admin.post("/api/auth/mfa/confirm", {"code": code}))
    step_up = _totp_code(secret, code)
    ok(admin.post("/api/auth/mfa/step-up", {"code": step_up}), 204)
    flags = ok(admin.get("/api/admin/controls/flags"))
    defaults = {name: flags[name]["on"] for name in ("live_trading", "phase0_paper_only", "beta_cohort_gate")}
    flow.ctx["admin"], flow.ctx["defaults"] = admin, defaults
    return f"SUPER_ADMIN with an MFA session; flag defaults on a fresh host: {defaults}"


def set_posture(flow: Flow) -> str:
    admin: Api = flow.ctx["admin"]
    for name, on in POSTURE.items():
        ok(admin.put(f"/api/admin/controls/flags/{name}", {"on": on, "tenants": []}))
    flags = ok(admin.get("/api/admin/controls/flags"))
    wrong = {n: flags[n]["on"] for n, on in POSTURE.items() if flags[n]["on"] != on}
    check(not wrong, f"flags not as set: {wrong}")
    return "Phase 0 lock (live_trading off, phase0_paper_only on, beta_cohort_gate on) + " + ", ".join(n for n in POSTURE if n not in
                                                                                                         ("live_trading", "phase0_paper_only", "beta_cohort_gate"))


def invites(flow: Flow) -> str:
    admin: Api = flow.ctx["admin"]
    codes = [ok(admin.post("/api/admin/beta/invite-codes", {"max_uses": 1, "note": f"CI acceptance user {who}"}), 201)["code"] for who in "AB"]
    cohort = ok(admin.get("/api/admin/beta/cohort"))
    flow.ctx["codes"] = codes
    return f"2 single-use invite codes; cohort {json.dumps({k: cohort.get(k) for k in ('gate_on', 'cap', 'accounts', 'remaining') if k in cohort})}"


def signups(flow: Flow) -> str:
    anon = Api()
    refused = anon.post("/api/auth/register", {"email": "ci-no-invite@example.com", "password": PASSWORD})
    check(refused.status_code == 403 and "invite" in refused.text, f"sign-up without a code answered {refused.status_code}: {refused.text[:200]}")
    users = {}
    for who, code, broker in (("A", flow.ctx["codes"][0], "upstox"), ("B", flow.ctx["codes"][1], "dhan")):
        email = f"ci-user-{who.lower()}@example.com"
        token = ok(anon.post("/api/auth/register", {"email": email, "password": PASSWORD, "invite_code": code}), 201)["access_token"]
        api = Api(token)
        me = ok(api.get("/api/auth/me"))
        users[who] = {"api": api, "email": email, "broker": broker, "tenant_id": me["tenant_id"], "user_id": me["id"]}
    reused = anon.post("/api/auth/register", {"email": "ci-reuse@example.com", "password": PASSWORD, "invite_code": flow.ctx["codes"][0]})
    check(reused.status_code == 403, f"a used invite code answered {reused.status_code}")
    check(users["A"]["tenant_id"] != users["B"]["tenant_id"], "both users landed in one organisation")
    # a fresh login, as on the user's phone the next morning
    for u in users.values():
        u["api"].token = ok(Api().post("/api/auth/login", {"email": u["email"], "password": PASSWORD}))["access_token"]
    flow.ctx["users"] = users
    return "no code -> 403; A and B signed up with their codes (separate organisations); a used code -> 403; both logged in again"


def connect_upstox(flow: Flow) -> str:
    u = flow.ctx["users"]["A"]
    api: Api = u["api"]
    # Fake values in the formats the broker field map checks (app/brokers/field_specs.py, UI2-5).
    ok(api.post("/api/broker/upstox/credentials", {"api_key": "ci000000-0000-0000-0000-000000000001", "api_secret": "ci-upstox-secret"}), 204)
    url = ok(api.get("/api/broker/upstox/oauth/start"))["authorization_url"]
    check(urlsplit(url).hostname == "api.upstox.com", f"authorization URL {url}")
    # The browser: Upstox's login page (the fake broker inside the stack) sends it back to ATP's callback with a code.
    login_page = httpx.get(url, follow_redirects=False, timeout=30.0)
    check(login_page.status_code == 302, f"the login page answered {login_page.status_code}")
    callback = login_page.headers["location"]
    check(urlsplit(callback).path == "/api/broker/upstox/oauth/callback" and parse_qs(urlsplit(callback).query).get("code"), f"callback {callback}")
    back = api.client.get(callback, follow_redirects=False)
    check(back.status_code == 302 and "connected=1" in back.headers.get("location", ""), f"callback -> {back.status_code} {back.headers.get('location')}")
    status = ok(api.get("/api/broker/token-status"))
    upstox = next((s for s in status if s["broker_name"] == "upstox"), None)
    check(upstox and upstox["token_status"] == "VALID" and not upstox["needs_login"], f"token status {upstox}")
    return "Upstox OAuth: login page -> callback -> code exchanged -> token VALID"


def connect_dhan(flow: Flow) -> str:
    u = flow.ctx["users"]["B"]
    api: Api = u["api"]
    ok(api.post("/api/broker/dhan/credentials", {"client_id": "100001", "access_token": "ci-dhan.access.token"}), 204)
    profile = ok(api.post("/api/broker/dhan/authenticate"))
    check(profile.get("broker") == "dhan", f"profile {profile}")
    status = ok(api.get("/api/broker/token-status"))
    dhan = next((s for s in status if s["broker_name"] == "dhan"), None)
    check(dhan and dhan["token_status"] == "VALID", f"token status {dhan}")
    return f"Dhan: client id + access token stored, profile {profile.get('user_id')}, token VALID"


def smoke(flow: Flow) -> str:
    out = []
    for who in "AB":
        u = flow.ctx["users"][who]
        report = ok(u["api"].post(f"/api/broker/{u['broker']}/smoke-test"))
        steps = {s["name"]: s["status"] for s in report.get("steps", [])}
        needed = ("profile", "funds", "instruments", "quote", "positions", "orders")
        bad = {n: steps.get(n) for n in needed if steps.get(n) != "ok"}
        check(not bad, f"{u['broker']} smoke steps not ok: {bad} ({report.get('steps')})")
        out.append(f"{u['broker']} {sum(1 for v in steps.values() if v == 'ok')}/{len(steps)} ok")
    return "read-only smoke tests: " + "; ".join(out)


def screens(flow: Flow) -> str:
    out = []
    for who, symbol in (("A", "RELIANCE"), ("B", "INFY")):
        u = flow.ctx["users"][who]
        run = ok(u["api"].post("/api/screener/run", {"source": SCREEN, "base_tf": "1d", "symbols": SCREEN_SYMBOLS, "exchange": "NSE"}))
        check(run.get("run_id"), f"no stored run: {run}")
        check(str(run.get("data_source", "")).startswith(f"broker:{u['broker']}"), f"data source {run.get('data_source')}")
        check(symbol in run.get("matched", []), f"{symbol} not matched: {run.get('results')}")
        tier = run.get("data_tier") or {}
        u["symbol"] = symbol
        out.append(f"{who}: run #{run['run_id']} on {run['data_source']} ({tier.get('tier', '?')}), {len(run['matched'])}/{run['scanned']} matched")
    return "; ".join(out)


# M68 (owner order): the users' own Order defaults (Settings > Trade settings), saved before their first PAPER ticket.
ORDER_DEFAULTS = {"order_defaults.holding": "INTRADAY", "order_defaults.product": "MIS", "order_defaults.order_type": "MARKET",
                  # M68 PR-2 (TS1-019): no pre-chosen size either - his own Position size (one share a ticket here)
                  "position_size.mode": "FIXED_QUANTITY", "position_size.quantity": 1,
                  # M68 PR-7 (TS1-064): no pre-chosen paper capital - his own starting capital (the old start value here)
                  "paper_account.capital": 1_000_000}

def proposals(flow: Flow) -> str:
    out = []
    for who in "AB":
        u = flow.ctx["users"][who]
        # M68: ATP pre-chooses no order value - each user saves his own order defaults first, as a new trader does
        ok(u["api"].put("/api/me/trade-settings", {"values": ORDER_DEFAULTS}))
        ltp = fake_quote(u["symbol"], u["broker"])
        plan = {"symbol": u["symbol"], "direction": "LONG", "setup": "PULLBACK_AT_LEVEL", "entry": ltp, "stop_loss": round(ltp * 0.98, 1),
                "target": round(ltp * 1.05, 1), "checklist": CHECKLIST, "invalidation": "CI acceptance: close below the stop"}
        preview = ok(u["api"].post("/api/paper/ticket/preview", plan))
        check(not preview.get("errors") and not preview.get("refusals"), f"preview refused: {preview.get('errors')} {preview.get('refusals')}")
        placed = ok(u["api"].post("/api/paper/ticket", plan))
        check(placed.get("status") == "PROPOSED" and placed.get("proposal"), f"ticket -> {placed.get('status')}: {placed.get('reasons')}")
        pending = ok(u["api"].get("/api/ai/actions", params={"status": "PROPOSED"}))
        card = next((a for a in pending if a["id"] == placed["proposal"]["id"]), None)
        check(card is not None and card["action"] == "ENTRY", f"proposal card {card}")
        u["proposal_id"], u["plan"] = card["id"], plan
        out.append(f"{who}: plan #{placed['id']} {u['symbol']} at {ltp:g} -> proposal #{card['id']} ({card['action']})")
    return "; ".join(out)


def approvals(flow: Flow) -> str:
    out = []
    for who in "AB":
        u = flow.ctx["users"][who]
        decided = ok(u["api"].post(f"/api/ai/actions/{u['proposal_id']}/approve", {"note": "CI acceptance: approve PAPER"}))
        check(decided["status"] == "EXECUTED" and decided.get("trade_id"), f"approve -> {decided['status']}: {decided.get('result')}")
        positions = ok(u["api"].get("/api/positions"))
        pos = next((p for p in positions if p["id"] == decided["trade_id"]), None)
        check(pos and pos["mode"] == "PAPER", f"position {pos}")
        u["trade_id"], u["stop"] = pos["id"], float(pos["stop_loss"])
        out.append(f"{who}: PAPER position #{pos['id']} {pos['symbol']} qty {pos['quantity']:g} at {pos['entry_price']:g}, stop {pos['stop_loss']:g}")
    return "; ".join(out)


def protective_exits(flow: Flow) -> str:
    users = flow.ctx["users"]
    fake_state(ltp={users[w]["symbol"]: round(users[w]["stop"] * 0.99, 2) for w in "AB"})
    out = []
    try:
        for who in "AB":
            u = users[who]

            def closed(u: Dict[str, Any] = u) -> Optional[Dict[str, Any]]:
                trades = ok(u["api"].get("/api/trades"))
                t = next((x for x in trades if x["id"] == u["trade_id"]), None)
                return t if t and t.get("exit_time") else None
            trade = wait_for(f"the worker to close {who}'s position at its stop", closed, CYCLE_WAIT)
            check("stop" in (trade.get("exit_reason") or "").lower(), f"closed for {trade.get('exit_reason')!r}, not the stop")
            out.append(f"{who}: closed by the worker at {trade['exit_price']:g} ({trade['exit_reason']})")
    finally:
        fake_state(clear_ltp=[users[w]["symbol"] for w in "AB"])
    return "; ".join(out)


def journal(flow: Flow) -> str:
    users = flow.ctx["users"]
    out = []
    for who in "AB":
        u = users[who]
        entry = ok(u["api"].get(f"/api/journal/trades/{u['trade_id']}"))
        check(entry, "empty journal entry")
        week = ok(u["api"].get("/api/journal/week"))
        ids = {e.get("trade_id", e.get("id")) for e in week.get("entries", [])}
        check(u["trade_id"] in ids, f"trade #{u['trade_id']} not in this week's journal ({sorted(ids)})")
        out.append(f"{who}: trade #{u['trade_id']} in the journal (score {json.dumps(week.get('score'))[:80]})")
    other = users["A"]["api"].get(f"/api/journal/trades/{users['B']['trade_id']}")
    check(other.status_code == 404, f"A reading B's journal entry answered {other.status_code}")
    return "; ".join(out) + "; another user's entry -> 404"


def no_api_orders(flow: Flow) -> str:
    b = flow.ctx["users"]["B"]
    live = b["api"].post("/api/deployments", {"strategy_id": "ema_rsi_scalper_1m", "symbol": b["symbol"], "mode": "LIVE", "broker_name": "dhan"})
    check(live.status_code in (400, 402, 403, 409, 503), f"a LIVE deployment answered {live.status_code}: {live.text[:200]}")
    calls = fake_calls()
    check(calls["orders"] == [], f"order-API calls reached the fake broker: {calls['orders'][:5]}")
    hosts = {route.split(" ", 1)[1].split("/", 1)[0] for route in calls["by_route"]}
    check({"api.upstox.com", "api.dhan.co"} <= hosts, f"the brokers were not both used: {sorted(hosts)}")
    flow.ctx["calls"] = calls
    return (f"LIVE deployment refused ({live.status_code}); {calls['total']} broker calls, 0 to an order API "
            f"(hosts {', '.join(sorted(hosts))})")


def stale_alerts(flow: Flow) -> str:
    b = flow.ctx["users"]["B"]
    api: Api = b["api"]
    fake_state(stale_minutes={"dhan": 60})
    rule = ok(api.post("/api/alerts/rules", {"name": "CI stale-data guard", "kind": "instrument", "symbol": "TCS", "condition": "close > 0",
                                             "base_tf": "5m", "cooldown_minutes": 0, "mode": "instant"}), 201)
    rid = rule["id"]

    def evaluated_stale() -> Optional[Dict[str, Any]]:
        r = next((x for x in ok(api.get("/api/alerts/rules")) if x["id"] == rid), None)
        return r if r and r.get("last_problem") else None
    held = wait_for("the alert rule to be evaluated on the stale feed", evaluated_stale, ALERT_WAIT)
    events = ok(api.get(f"/api/alerts/rules/{rid}/events"))
    alerts = [n for n in ok(api.get("/api/notifications")) if n["event_type"] == "SCREEN_ALERT"]
    check(events == [] and alerts == [], f"stale data still produced {len(events)} event(s) and {len(alerts)} alert(s)")
    stale_reason = held["last_problem"]
    fake_state(stale_minutes={"dhan": 0})

    def fired() -> Optional[List[Dict[str, Any]]]:
        ev = ok(api.get(f"/api/alerts/rules/{rid}/events"))
        return ev or None
    events = wait_for("the same rule to fire on the current feed", fired, ALERT_WAIT)

    def notified() -> Optional[List[Dict[str, Any]]]:
        return [n for n in ok(api.get("/api/notifications")) if n["event_type"] == "SCREEN_ALERT"] or None
    alerts = wait_for("the screen alert notification", notified, 120)
    return (f"stale feed (60 min): no event, no alert - rule says {stale_reason!r}; current feed: {len(events)} event(s) "
            f"({events[0]['symbol']} bar {events[0]['bar_time']}), {len(alerts)} SCREEN_ALERT notification(s)")


def final_checks(flow: Flow) -> str:
    calls = fake_calls()
    check(calls["orders"] == [], f"order-API calls reached the fake broker: {calls['orders'][:5]}")
    flags = ok(flow.ctx["admin"].get("/api/admin/controls/flags"))
    check(flags["phase0_paper_only"]["on"] and not flags["live_trading"]["on"], "the Phase 0 lock changed during the flow")
    unknown = calls.get("unknown") or []
    flow.ctx["calls"] = calls
    return f"still 0 order-API calls after {calls['total']} broker calls; Phase 0 lock unchanged; {len(unknown)} unmodelled broker route(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0 acceptance flow on the CI staging stack")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    flow = Flow()
    print(f"Phase 0 acceptance flow against {BASE}")
    for name, fn in (("stack ready, NSE session open", stack_ready), ("admin: sign-up, MFA, Admin -> Controls", admin_setup),
                     ("admin: Phase 0 flag posture", set_posture), ("admin: invite codes", invites), ("invite -> sign up (A, B)", signups),
                     ("A links Upstox (OAuth)", connect_upstox), ("B links Dhan", connect_dhan), ("broker smoke tests (read-only)", smoke),
                     ("run a screen (own broker data)", screens), ("PAPER ticket -> ENTRY proposal", proposals), ("Approve PAPER", approvals),
                     ("protective exit by the worker", protective_exits), ("journal", journal), ("no API order reaches a broker", no_api_orders),
                     ("stale data suppresses alerts", stale_alerts), ("final: still no order, lock unchanged", final_checks)):
        flow.run(name, lambda fn=fn: fn(flow))
    try:
        calls = fake_calls()
    except Exception as exc:  # noqa: BLE001
        calls = {"error": str(exc)}
    passed = not flow.failed
    report = {"passed": passed, "base": BASE, "clock": flow.ctx.get("clock"), "flag_defaults": flow.ctx.get("defaults"),
              "steps": flow.steps, "broker_calls": {k: calls.get(k) for k in ("total", "orders", "by_route", "unknown")}}
    if args.report:
        with open(args.report, "w") as fh:
            json.dump(report, fh, indent=2)
    print(f"\nacceptance: {'PASS' if passed else 'FAIL'} - {sum(s['status'] == 'pass' for s in flow.steps)}/{len(flow.steps)} steps passed; "
          f"broker calls {calls.get('total')}, order-API calls {len(calls.get('orders') or [])}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
