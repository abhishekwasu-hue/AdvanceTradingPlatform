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
