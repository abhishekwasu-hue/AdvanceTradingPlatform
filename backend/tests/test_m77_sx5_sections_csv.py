"""M77 SX-5: sections 5 (AI explanation card) and 6 (option context), and the CSV tables.

- AI card: copied from his own finished workspace card (grounded, no model call); flags off / none -> "Not available";
- option context: numbers from the Option chain page's own view; not F&O / no chain -> "Not available";
- PDF prints both sections; no advice wording in what it prints;
- CSV: plain numbers (no grouping), same study = same bytes, one table or a ZIP of all, a formula-like cell is quoted;
- the route: 404 while off, his own study only, an unknown table -> 404 with a plain sentence.
"""
import csv
import io
import re
import zipfile
from datetime import UTC, datetime

import pytest

from app.study import csv_export as CSV
from app.study import pages as P
from app.study import sections as SEC
from app.study import snapshot as S
from tests.test_m77_sx2_studies import BODY, _flags, _inputs, _member, client, served  # noqa: F401 - the app client and the served fixture

ADVICE = re.compile(r"\b(buy|sell|target|stop[- ]loss|entry|recommend\w*|profit)\b", re.I)
ANSWER = {"status": "ready", "message": None, "as_of": "2026-03-24T04:00:00+00:00", "notes": ["Read from ATP's stored candles."],
          "disclaimer": "Educational only.", "card": {
              "story": "Price is in a range under the seller area.", "trend": {"text": "Down on the 15-minute chart."},
              "current_state": "Testing the area.", "rsi": "No divergence.",
              "scenarios": {"primary": "A rejection from the area.", "alternative": "A close above the area."},
              "what_to_wait_for": {"condition": "A commitment candle at the area."},
              "evidence": [{"name": "Trend", "present": True}, {"name": "RSI divergence", "present": False}]}}
VIEW = {"underlying": "BANKNIFTY", "expiry": "2026-03-26", "data_as_of": "2026-03-24T03:55:00+00:00",
        "summary": {"spot": 46585.26, "atm_strike": 46600.0, "pcr_oi": 0.87, "max_pain": 46500.0, "atm_iv": 14.2, "days_to_expiry": 2,
                    "spot_source": "live"},
        "rows": [{"strike": k, "call": {"oi": c, "iv": 14.0, "delta": 0.5, "gamma": 0.0004, "theta": -12.0, "vega": 20.0},
                  "put": {"oi": p, "iv": 15.0, "delta": -0.5, "gamma": 0.0004, "theta": -11.0, "vega": 19.0}}
                 for k, c, p in ((46400.0, 100.0, 900.0), (46500.0, 300.0, 1200.0), (46600.0, 800.0, 700.0),
                                 (46700.0, 1500.0, 200.0), (46800.0, 1100.0, 50.0))]}


def _snap(**over):
    base = {"ai_card": SEC.ai_card_part(ANSWER, "15m", datetime(2026, 3, 24, 4, 5, tzinfo=UTC)), "option_context": SEC.option_context_part(VIEW)}
    base.update(over)
    return S.assemble(_inputs(**base))


def _text(snap) -> str:
    return " ".join(P.all_text(P.render(snap, S.fingerprint(S.canonical(snap)))))


def test_ai_card_part_keeps_the_card_and_how_it_was_worked_out():
    part = SEC.ai_card_part(ANSWER, "15m", datetime(2026, 3, 24, 4, 5, tzinfo=UTC))
    names = [line["name"] for line in part["lines"]]
    assert names == ["Story", "Trend", "Current state", "RSI", "First scenario", "Other scenario", "What to watch"]
    assert part["how"]["made_at"] == "2026-03-24T04:05:00+00:00" and part["how"]["reading_as_of"] == ANSWER["as_of"]
    assert part["evidence"] == [{"name": "Trend", "present": True}, {"name": "RSI divergence", "present": False}]
    assert SEC.ai_card_part({"status": "code_only", "message": "The AI part is not available.", "card": None}, "15m", None) == {
        "reason": "The AI part is not available"}


def test_pick_card_takes_the_smallest_timeframe_first_and_never_another_symbol():
    rows = [{"pick": {"symbol": "NIFTY", "timeframe": "15m"}, "row": "nifty"}, {"pick": {"symbol": "BANKNIFTY", "timeframe": "1h"}, "row": "bank-1h"},
            {"pick": {"symbol": "BANKNIFTY", "timeframe": "15m"}, "row": "bank-15m"}]
    picks = [{"symbol": "BANKNIFTY", "timeframe": "15m"}, {"symbol": "BANKNIFTY", "timeframe": "1h"}]
    assert SEC.pick_card(rows, picks) == ("bank-15m", "15m")
    assert SEC.pick_card(rows[:1], picks) is None


def test_option_context_part_numbers_and_walls_near_spot():
    oc = SEC.option_context_part(VIEW)
    assert oc["pcr_oi"] == 0.87 and oc["atm_strike"] == 46600.0 and oc["days_to_expiry"] == 2
    calls = [w for w in oc["walls"] if w["side"] == "call"]
    puts = [w for w in oc["walls"] if w["side"] == "put"]
    assert [w["strike"] for w in calls] == [46700.0, 46800.0, 46600.0]            # at / above spot, highest call OI first
    assert [w["strike"] for w in puts] == [46500.0, 46400.0]                       # below spot, highest put OI first
    assert oc["atm_legs"]["call"]["oi"] == 800.0 and oc["chosen_strikes"] is None


def test_sections_printed_and_not_available_reasons():
    text = _text(_snap())
    assert "What the card says" in text and "How I worked this out" in text and "Price is in a range under the seller area." in text
    assert "Put / call ratio (OI)" in text and "0.87" in text and "46,700.00" in text and "At the money strike" in text
    assert "Not available: the AI explanation card" not in text
    off = _text(_snap(ai_card={"reason": SEC.AI_OFF}, option_context={"reason": SEC.NOT_FNO}))
    assert f"Not available: {SEC.AI_OFF}." in off and f"Not available: {SEC.NOT_FNO}." in off
    unticked = _snap(sections=["cover", "notes"])
    assert "ai_card" not in unticked["data"] and "option_context" not in unticked["data"]


def test_no_advice_wording_printed_outside_his_notes():
    snap = _snap(notes=None, checklist_rules=[])
    lines = [t for t in P.all_text(P.render(snap, "f" * 64)) if not t.startswith("Data sources")]
    assert not [t for t in lines if ADVICE.search(t)]


def test_csv_tables_plain_numbers_same_bytes():
    snap = _snap(checklist_rules=["=HYPERLINK(1)", "Area is fresh"])
    t = CSV.tables(snap)
    assert list(t) == ["structure", "areas", "option_context", "ai_card", "checklist", "bars-1h", "bars-15m"]
    assert t == CSV.tables(snap)
    areas = list(csv.reader(io.StringIO(t["areas"].decode())))
    assert areas[0][:5] == ["label", "timeframe", "role", "low", "high"]
    assert all("," not in v for row in areas[1:] for v in row[3:5]) and areas[1][2] in ("seller", "buyer")   # plain numbers
    oc = t["option_context"].decode()
    assert "summary,pcr_oi,0.87" in oc and "oi_wall,call 46700,1500" in oc
    rules = list(csv.reader(io.StringIO(t["checklist"].decode())))
    assert rules[1][0] == "'=HYPERLINK(1)"                                        # never run as a formula
    bars = list(csv.reader(io.StringIO(t["bars-15m"].decode())))
    assert bars[1][0].endswith("+05:30") and len(bars) - 1 == len(snap["data"]["charts"][1]["bars"])
    z = CSV.zip_all(snap, "study-x")
    assert z == CSV.zip_all(snap, "study-x")
    names = zipfile.ZipFile(io.BytesIO(z)).namelist()
    assert names == [f"study-x-{n}.csv" for n in t]
    with pytest.raises(CSV.NoSuchTable):
        CSV.table_csv(snap, "trade_plan")


def test_reads_say_not_available(monkeypatch):
    import asyncio

    from app.platform import controls

    async def off(session, name, tenant_id):
        return False

    monkeypatch.setattr(controls, "flag_enabled", off)

    class U:
        id, tenant_id = 10**9, 10**9

    a = asyncio.run(SEC.option_context_read(None, U(), "NFO", "BANKNIFTY26MARFUT"))      # a contract chart: no chain read
    b = asyncio.run(SEC.ai_card_read(None, U(), "NIFTY BANK", ["15m"], datetime.now(UTC)))
    assert a == {"reason": SEC.NOT_FNO} and b == {"reason": SEC.AI_OFF}


def test_csv_route_and_sections_read_on_save(served, monkeypatch):  # noqa: F811 - the fixture
    async def card(session, user, symbol, tfs, when):
        return SEC.ai_card_part(ANSWER, "15m", when)

    async def chain(session, user, exchange, symbol):
        return SEC.option_context_part(VIEW)

    monkeypatch.setattr(SEC, "ai_card_read", card)
    monkeypatch.setattr(SEC, "option_context_read", chain)
    headers, _, _ = _member("csv")
    _flags(ui_v2=True, study_export=True)
    sid = client.post("/api/studies", json=BODY, headers=headers).json()["id"]
    snap = client.get(f"/api/studies/{sid}", headers=headers).json()["snapshot"]
    assert snap["data"]["option_context"]["pcr_oi"] == 0.87 and snap["data"]["ai_card"]["lines"]
    assert "ai_card" not in snap["not_available"] and "option_context" not in snap["not_available"]
    z = client.get(f"/api/studies/{sid}/csv", headers=headers)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert client.get(f"/api/studies/{sid}/csv", headers=headers).content == z.content
    one = client.get(f"/api/studies/{sid}/csv?table=areas", headers=headers)
    assert one.status_code == 200 and one.headers["content-type"].startswith("text/csv") and one.text.startswith("label,")
    assert client.get(f"/api/studies/{sid}/csv?table=trade_plan", headers=headers).status_code == 404
    other, _, _ = _member("csv-other")
    assert client.get(f"/api/studies/{sid}/csv", headers=other).status_code == 404
    _flags(ui_v2=True, study_export=False)
    assert client.get(f"/api/studies/{sid}/csv", headers=headers).status_code == 404
