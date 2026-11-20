"""TERM-CON C1 (plan 3.3, 6A): agrees / conflicts, the lean cue, the meter and the structure line; his own signals;
extra timeframes as their own readings. Counts only - no score, no weighting, no recommendation."""
from __future__ import annotations

import uuid

from app.conclusion import FLAGS
from app.conclusion import rules as R
from app.conclusion import sections as S
from app.signals import language as L
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import client
from tests.test_phase_l_ai import _owner


def _r(source: str, direction: str, tf: str | None = None, label: str | None = None) -> dict:
    return {"source": source, "direction": direction, "tf": tf, "label": label or source.capitalize(), "as_of": "t"}


CHART15 = _r("chart", "up", "15m")
CHART1H = _r("chart", "range", "1h")
OI_UP = _r("oi", "up", label="OI")
POS_DOWN = _r("positions", "down", label="Positions")
SIG_UP = _r("signals", "up", label="Your signals")


def test_agrees_and_conflicts_name_every_source_and_never_merge_timeframes():
    readings = [CHART15, CHART1H, OI_UP, SIG_UP, POS_DOWN]
    assert R.agrees(readings) == [{"direction": "up", "text": "15-minute structure up · OI up · Your signals up",
                                   "sources": ["chart", "oi", "signals"]}]
    assert R.conflicts(readings)[0]["text"] == "15-minute structure up · OI up · Your signals up · Positions down"
    assert R.conflicts([CHART15, OI_UP]) == [] and R.agrees([CHART15]) == []
    assert R.label(CHART1H) == "1-hour structure"


def test_the_meter_counts_and_the_lean_is_a_plain_majority_with_its_count():
    readings = [CHART15, CHART1H, OI_UP, SIG_UP, POS_DOWN, _r("x", "none"), _r("y", "mixed")]
    assert R.meter(readings) == {"up": 3, "down": 1, "range": 2, "none": 1, "total": 7}
    assert R.lean(readings) == {"side": "up", "count": 3, "of": 6, "words": "Lean up · 3 of 6"}
    assert R.lean([CHART15, POS_DOWN])["side"] == "sideways"                      # a tie
    assert R.lean([CHART15])["words"] == "Sideways · 1 up, 0 down of 1"           # fewer than two up / down readings
    assert R.lean([])["words"] == "No readings yet"


def test_the_line_is_in_the_signal_language_and_short():
    text = R.line([CHART15, CHART1H, OI_UP, POS_DOWN], "NIFTY")
    assert text == "15-minute structure up · 1-hour structure in a range · OI up · Positions down"
    assert L.check(text, "signal_own", own_symbols=["NIFTY"]) == []
    many = [_r(f"s{i}", "up", label=f"Source {chr(65 + i)}") for i in range(20)]
    assert 0 < len(R.line(many, "NIFTY")) <= 90
    assert R.line([_r("x", "up", label="Seller at 24200")], "NIFTY") == ""       # a number outside an area range


def test_no_advice_words_anywhere_in_the_glance():
    g = R.glance([CHART15, OI_UP, POS_DOWN], "NIFTY")
    texts = [g["lean"]["words"], g["line"], *(a["text"] for a in g["agrees"]), *(c["text"] for c in g["conflicts"])]
    for t in texts:
        assert L.call_words(t) == [] and "signal" not in t.lower().replace("your signals", "")


ITEMS = [{"symbol": "NIFTY25NOV25800CE", "direction": "SHORT", "status": "waiting", "at": "a",
          "source": {"kind": "autopilot", "name": "Range fade"}, "reasons": ["Seller area: 24,600–24,650", "rsi: 61.2"]},
         {"symbol": "NIFTY", "direction": "LONG", "status": "taken", "at": "b", "source": {"kind": "scan", "name": "Scan 7"}},
         {"symbol": "TCS", "direction": "LONG", "status": "taken", "at": "c", "source": {"kind": "scan", "name": "x"}}]


def test_his_signals_on_the_symbol_in_signal_words_only():
    sec = S.signals_section(ITEMS, "NIFTY", "t")
    texts = [x["text"] for x in sec["lines"]]
    assert texts == ["Range fade: a downside setup on NIFTY, waiting for you.", "Seller area: 24,600–24,650",
                     "Your strategy: an upside setup on NIFTY, taken."]                 # "Scan 7" has a digit; rsi line left out
    for x in sec["lines"]:
        assert L.check(x["text"], "signal_own", own_symbols=["NIFTY"]) == []
    assert S.signals_reading(ITEMS, "NIFTY")["direction"] == "down"
    assert S.signals_section([], "NIFTY", "t")["lines"][0]["text"] == "No signal on NIFTY today."


def test_the_read_carries_the_glance_and_extra_timeframes_are_separate_sections():
    headers, me = _owner(f"tc1-{uuid.uuid4().hex[:6]}@example.com")
    for flag in (*FLAGS, "read_chart"):
        _set_flag(flag, me["tenant_id"], on=True)
    got = client.get("/api/terminal/conclusion", params=[("symbol", "NIFTY"), ("tf", "15m"), ("also", "1h"), ("also", "15m")],
                     headers=headers)
    assert got.status_code == 200, got.text
    body = got.json()
    keys = [s["key"] for s in body["sections"]]
    assert keys.count("chart") == 1 and keys.count("chart-1h") <= 1 and "chart-15m" not in keys
    assert set(body["glance"]) == {"lean", "meter", "agrees", "conflicts", "line"}
    assert "signals" in keys


# --- C1b: risk and events --------------------------------------------------------------------------------------------

def test_risk_shows_the_kill_switch_and_only_what_his_own_settings_do():
    kill = {"engaged": True, "message": "New orders are stopped for your account. Exits still work."}
    guard = {"as_of": "g", "modes": {"PAPER": {"state": "paused", "cooldowns": [{"underlying": "NIFTY", "minutes_left": 12},
                                                                               {"underlying": "TCS", "minutes_left": 3}]},
                                     "LIVE": {"state": "normal", "cooldowns": []}}}
    texts = [x["text"] for x in S.risk_section(kill, guard, "NIFTY", "t")["lines"]]
    assert texts == ["New orders are stopped for your account. Exits still work.",
                     "PAPER: new entries paused by your drawdown settings.",
                     "PAPER: cool-down on NIFTY, 12 minutes left (your setting)."]
    calm = S.risk_section({"engaged": False, "message": "New orders are allowed."}, {"modes": {"PAPER": {"state": "normal"}}}, "NIFTY", "t")
    assert [x["text"] for x in calm["lines"]] == ["New orders are allowed."]          # nothing added when he set nothing


def test_events_for_every_symbol_or_this_one_only():
    events = [{"event_date": "2026-11-21", "underlying": None, "description": "RBI policy", "action": "BLOCK"},
              {"event_date": "2026-11-22", "underlying": "TCS", "description": "Results"},
              {"event_date": "2026-11-23", "underlying": "NIFTY", "start_time": "09:15", "description": "Index rebalance"}]
    texts = [x["text"] for x in S.events_section(events, "NIFTY", "t")["lines"]]
    assert texts == ["2026-11-21 · RBI policy · new entries refused in its window (your setting).",
                     "2026-11-23 09:15 · Index rebalance."]
    assert S.events_section([], "NIFTY", "t")["lines"][0]["text"] == "No market event in the next 7 days."


def test_needs_you_adds_the_kill_switch_and_an_expiry_today():
    pos = [{"symbol": "NIFTY25NOV25800CE", "underlying": "NIFTY", "expiry": "2026-11-20"},
           {"symbol": "TCS", "underlying": "TCS", "expiry": "2026-11-20"}]
    got = S.needs_you([], None, "NIFTY", kill={"engaged": True}, positions=pos, today="2026-11-20")
    assert [n["key"] for n in got] == ["kill_switch", "expiry"] and got[1]["text"] == "1 position expires today"
    assert S.needs_you([], None, "NIFTY", positions=pos, today="2026-11-21") == []


def test_the_read_has_risk_and_events_sections():
    headers, me = _owner(f"tc1b-{uuid.uuid4().hex[:6]}@example.com")
    for flag in FLAGS:
        _set_flag(flag, me["tenant_id"], on=True)
    body = client.get("/api/terminal/conclusion", params={"symbol": "NIFTY"}, headers=headers).json()
    keys = [s["key"] for s in body["sections"]]
    assert keys[-2:] == ["risk", "events"]
    risk = next(s for s in body["sections"] if s["key"] == "risk")
    assert risk["status"] == "ok" and risk["lines"][0]["text"] == "New orders are allowed."


# --- C3: the short cache ----------------------------------------------------------------------------------------------

def test_the_cache_reuses_one_read_per_key_and_a_new_change_key_reads_again(monkeypatch):
    from app.conclusion import gather as G
    calls = []

    async def fake(session, user, symbol, expiry, tf, now=None, also=()):
        calls.append((symbol, tf, also))
        return {"n": len(calls)}
    monkeypatch.setattr(G, "conclusion", fake)
    G._cache.clear()
    headers, me = _owner(f"tc3-{uuid.uuid4().hex[:6]}@example.com")
    for flag in FLAGS:
        _set_flag(flag, me["tenant_id"], on=True)
    url = "/api/terminal/conclusion"
    a = client.get(url, params={"symbol": "NIFTY", "v": "k1"}, headers=headers).json()
    b = client.get(url, params={"symbol": "nifty", "v": "k1"}, headers=headers).json()
    c = client.get(url, params={"symbol": "NIFTY", "v": "k2"}, headers=headers).json()
    assert a == b == {"n": 1} and c == {"n": 2} and len(calls) == 2
    assert G.cached(("x",), 0.0) is None
    G.remember(("x",), {"y": 1}, 0.0)
    assert G.cached(("x",), 1.0) == {"y": 1} and G.cached(("x",), G.CACHE_SECONDS + 1.0) is None
