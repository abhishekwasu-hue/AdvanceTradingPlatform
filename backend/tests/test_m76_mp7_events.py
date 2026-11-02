"""M76 MP-7 (S-3): event awareness - information only.

- his own event list lives in his prefs (`own_events`): empty by default, at most 30, strict types;
- the check card's event line (`/api/paper/fno/event-risk`) carries his own events, marked "own", only while
  `copilot_workspace` is on (off: the line is exactly as before);
- `GET /api/charts/workspace/events` (ui_v2 + copilot_workspace, 404 while off) gives the listed expiries up to the next
  monthly one (weekly / monthly from the master's own list), the calendar's events and his own, GET only, in the route
  table; no order path, worker or risk check reads the list, and the Guardian's event rules are untouched.

Made-up data only."""
import uuid
from datetime import date, timedelta
from pathlib import Path

from app.charts import workspace_events as we
from app.me import prefs
from app.paper import fno_insights
from tests.tenancy_route_table import ROUTE_TABLE
from tests.test_auth_api import client
from tests.test_fno8b4_risk_parts import _today
from tests.test_fno8b_insights import _org
from tests.test_trading_worker import _tenant
from tests.test_z5_p1d_risk_by_default import _flag

EVENTS = "/api/charts/workspace/events"
RISK = "/api/paper/fno/event-risk"


def _name() -> str:
    return "ZZMP" + uuid.uuid4().hex[:6].upper()


def _save(t, events) -> None:
    got = client.get("/api/me/prefs", headers=t["headers"]).json()
    put = client.put(f"/api/me/prefs/own_events?version={got['versions']['own_events']}", headers=t["headers"], json={"events": events})
    assert put.status_code == 200, put.text


def test_own_list_is_empty_by_default_strict_and_bounded():
    t = _org("mp7-prefs")
    assert client.get("/api/me/prefs", headers=t["headers"]).json()["prefs"]["own_events"] == {"events": []}
    day = (_today() + timedelta(days=2)).isoformat()
    _save(t, [{"date": day, "name": "Results of my stock", "underlying": "TCS"}, {"date": day, "name": "Holiday trip"}])
    got = client.get("/api/me/prefs", headers=t["headers"]).json()["prefs"]["own_events"]["events"]
    assert got == [{"date": day, "name": "Results of my stock", "underlying": "TCS"}, {"date": day, "name": "Holiday trip", "underlying": None}]
    many = [{"date": day, "name": f"E{i}"} for i in range(prefs.MAX_OWN_EVENTS + 1)]
    assert client.put("/api/me/prefs/own_events", headers=t["headers"], json={"events": many}).status_code == 422
    for bad in ({"date": "tomorrow", "name": "x"}, {"date": day, "name": ""}, {"date": day, "name": "x", "underlying": "a;b"},
                {"date": day, "name": "x", "extra": 1}):
        out = client.put("/api/me/prefs/own_events", headers=t["headers"], json={"events": [bad]})
        assert out.status_code == 422, bad


def test_own_rows_are_pure_and_scoped():
    start = date(2031, 3, 2)
    rows = fno_insights.own_event_rows([
        {"date": "2031-03-03", "name": "Mine", "underlying": None},
        {"date": "2031-03-04", "name": "Other symbol", "underlying": "TCS"},
        {"date": "2031-03-04", "name": "Index one", "underlying": "NIFTY 50"},
        {"date": "2031-03-01", "name": "Past"}, {"date": "2031-04-30", "name": "Too far"}, {"date": "bad", "name": "x"},
    ], "NIFTY", start, date(2031, 3, 20))
    assert [(r["date"], r["name"], r["scope"], r["source"], r["rule"]) for r in rows] == [
        ("2031-03-03", "Mine", "all symbols", "own", None), ("2031-03-04", "Index one", "NIFTY 50", "own", None)]


def test_event_line_carries_his_own_events_only_while_the_workspace_is_on():
    t = _org("mp7-line")
    name = _name()
    day = _today() + timedelta(days=2)
    _save(t, [{"date": day.isoformat(), "name": "My own review day", "underlying": name}])
    params = {"underlying": name, "until": (day + timedelta(days=3)).isoformat()}
    off = client.get(RISK, headers=t["headers"], params=params).json()
    assert [e for e in off["events"] if e["source"] == "own"] == []                      # flag off: as before
    _flag("copilot_workspace", t["tenant_id"])
    on = client.get(RISK, headers=t["headers"], params=params).json()
    own = [e for e in on["events"] if e["source"] == "own"]
    assert [(e["date"], e["name"], e["scope"], e["rule"]) for e in own] == [(day.isoformat(), "My own review day", name, None)]
    assert on["blocks"] is False and on["calendar_empty"] is False


def test_expiry_rows_weekly_then_the_monthly_from_the_list():
    listed = [date(2031, 3, 6), date(2031, 3, 13), date(2031, 3, 27), date(2031, 4, 3), date(2031, 4, 24)]
    assert we.expiry_rows(listed, date(2031, 3, 10)) == [
        {"date": "2031-03-13", "kind": "weekly", "name": "Weekly expiry"},
        {"date": "2031-03-27", "kind": "monthly", "name": "Monthly expiry"}]
    assert we.expiry_rows([date(2031, 3, 13)], date(2031, 3, 1)) == [{"date": "2031-03-13", "kind": "expiry", "name": "Expiry"}]
    assert we.expiry_rows([], date(2031, 3, 1)) == []


def test_workspace_events_route_flags_shape_and_read_only():
    t = _tenant(f"mp7-off-{uuid.uuid4().hex[:6]}@example.com")
    assert client.get(EVENTS, headers=t["headers"], params={"symbol": "NIFTY"}).status_code == 404
    _flag("ui_v2", t["tenant_id"])
    out = client.get(EVENTS, headers=t["headers"], params={"symbol": "NIFTY"})
    assert out.status_code == 404 and out.headers["X-Feature-Disabled"] == "copilot_workspace"
    _flag("copilot_workspace", t["tenant_id"])
    assert client.get(EVENTS, headers=t["headers"], params={"symbol": "drop;table"}).status_code == 422
    assert client.get(EVENTS, params={"symbol": "NIFTY"}).status_code == 401
    name = _name()
    day = _today() + timedelta(days=1)
    _save(t, [{"date": day.isoformat(), "name": "My event", "underlying": None}])
    body = client.get(EVENTS, headers=t["headers"], params={"symbol": name}).json()
    assert body["blocks"] is False and body["expiries"] == []                        # nothing listed for a made-up name: no guess
    assert [(e["name"], e["source"]) for e in body["events"] if e["source"] == "own"] == [("My event", "own")]
    assert {m for r in we.router.routes for m in getattr(r, "methods", set())} == {"GET"}
    assert ("GET", EVENTS) in ROUTE_TABLE


def test_no_order_path_reads_the_list():
    app = Path(__file__).resolve().parents[1] / "app"
    readers = sorted(p.relative_to(app).as_posix() for p in app.rglob("*.py")
                     if "OwnEventsPrefs" in p.read_text(encoding="utf-8") or "\"own_events\"" in p.read_text(encoding="utf-8"))
    assert readers == ["charts/workspace_events.py", "me/prefs.py", "paper/fno_insights_routes.py"]
    assert "own_events" not in (app / "risk_engine" / "guardian.py").read_text(encoding="utf-8")
