"""M62 JN-3 / JN-5: the journal list, its statistics (maths checked by hand on a fixture) and the CSV / PDF export -
the caller's own entries only (another organisation's trades never count), manual entries included and labelled by
default and left out with include_manual=false."""
import csv
import io
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import JournalTagRecord
from app.journal import export as journal_export
from app.journal import stats
from app.journal.stats import IST, Decision, Filters
from tests.test_auth_api import _session_factory, client
from tests.test_m62_journal_layer import _h, _run, _trade, flag_on, store  # noqa: F401  (pytest fixtures)
from tests.test_trading_api import _register_user_and_get_id

# --- the pure maths, on a fixture worked out by hand ------------------------------------------------------------------

MON = datetime(2026, 10, 26, 10, 0, tzinfo=IST)          # a Monday (IST)


def _fixture() -> list[Decision]:
    return [
        Decision("t:1", "S1", "5m", MON, 300.0, 100.0, mae_r=0.5, mfe_r=3.5, mae_amount=50.0, mfe_amount=350.0),
        Decision("t:2", "S1", "5m", MON + timedelta(hours=2), -100.0, 100.0, mae_r=1.0, mfe_r=0.2, mae_amount=100.0, mfe_amount=20.0),
        # 01:00 IST Tuesday is still Monday in UTC: the weekday is the IST one
        Decision("t:3", "S2", "15m", datetime(2026, 10, 26, 19, 30, tzinfo=UTC), 200.0, 0.0),
        Decision("t:4", "S2", "15m", MON + timedelta(days=2), -50.0, 50.0),
        Decision("t:5", "S2", "15m", MON + timedelta(days=3), None, 40.0),                       # open
        Decision("m:1", "S1", None, MON + timedelta(days=1), 0.0, 10.0, outside_atp=True),       # flat, outside ATP
    ]


def test_summary_maths_by_hand():
    t = stats.summary(_fixture())
    assert (t["trades"], t["closed"], t["open"], t["wins"], t["losses"], t["flat"], t["outside_atp"]) == (6, 5, 1, 2, 2, 1, 1)
    assert t["win_rate"] == 40.0                                    # 2 wins / 5 closed
    assert t["net_pnl"] == 350.0                                    # 300 - 100 + 200 - 50 + 0
    assert (t["gross_win"], t["gross_loss"]) == (500.0, -150.0)
    assert t["profit_factor"] == 3.33                               # 500 / 150
    assert t["expectancy"] == 70.0                                  # 350 / 5
    assert (t["avg_win"], t["avg_loss"]) == (250.0, -75.0)
    assert (t["avg_r"], t["r_trades"]) == (0.25, 4)                 # (3 - 1 - 1 + 0) / 4; t:3 has no stop, t:5 is open
    assert (t["avg_mae_r"], t["avg_mfe_r"], t["excursion_trades"]) == (0.75, 1.85, 2)
    assert (t["avg_mae"], t["avg_mfe"]) == (75.0, 185.0)


def test_breakdowns_by_setup_timeframe_weekday():
    out = stats.stats_payload(_fixture())
    s1, s2 = out["by_setup"]
    assert s1["setup"] == "S1" and (s1["trades"], s1["closed"], s1["win_rate"], s1["net_pnl"], s1["profit_factor"]) == (3, 3, 33.3, 200.0, 3.0)
    assert s1["expectancy"] == 66.67 and s1["avg_r"] == 0.67       # (3 - 1 + 0) / 3
    assert s2["setup"] == "S2" and (s2["trades"], s2["closed"], s2["win_rate"], s2["net_pnl"], s2["profit_factor"]) == (3, 2, 50.0, 150.0, 4.0)
    assert {g["timeframe"]: g["trades"] for g in out["by_timeframe"]} == {"15m": 3, "5m": 2, "-": 1}
    days = [(g["weekday"], g["trades"], g["net_pnl"]) for g in out["by_weekday"]]
    assert days == [("Monday", 2, 200.0), ("Tuesday", 2, 200.0), ("Wednesday", 1, -50.0), ("Thursday", 1, 0.0)]
    assert out["by_weekday"][-1]["win_rate"] is None                # only an open trade: no ratio


def test_no_trades_no_invented_numbers():
    t = stats.summary([])
    assert t["trades"] == 0 and t["win_rate"] is None and t["profit_factor"] is None and t["expectancy"] is None
    only_wins = stats.summary([Decision("t:1", "S", None, MON, 10.0, 5.0)])
    assert only_wins["profit_factor"] is None and only_wins["win_rate"] == 100.0      # no loss: no profit factor


def test_filters_bounds_are_ist_days():
    start, end = Filters(date_from=MON.date(), date_to=MON.date()).bounds()
    assert start == datetime(2026, 10, 25, 18, 30, tzinfo=UTC) and end == datetime(2026, 10, 26, 18, 30, tzinfo=UTC)


def test_indian_grouping_in_the_pdf():
    assert journal_export.indian(12345678.5) == "1,23,45,678.50"
    assert journal_export.rupees(-123456.0) == "-Rs 1,23,456.00"
    assert journal_export.rupees(700.0, signed=True) == "+Rs 700.00"
    assert journal_export.indian(-0.001) == "0.00"


# --- through the API: own organisation only, manual entries, filters, export -------------------------------------------

NOW = datetime.now(UTC)


def _tag(tenant_id, user_id, trade_id, tag, status="CONFIRMED"):
    async def go():
        async with _session_factory() as session:
            session.add(JournalTagRecord(tenant_id=tenant_id, user_id=user_id, trade_id=trade_id, tag=tag, source="AUTO", status=status))
            await session.commit()
    _run(go())


@pytest.fixture
def world(flag_on, store):  # noqa: F811
    run = uuid.uuid4().hex[:8]
    token_a, a_id, tenant_a = _run(_register_user_and_get_id(f"m62-stats-a-{run}@example.com"))
    token_b, b_id, tenant_b = _run(_register_user_and_get_id(f"m62-stats-b-{run}@example.com"))
    day = NOW - timedelta(days=1)
    # risk (100 - 98) x 10 = 20 each
    win = _trade(tenant_a, a_id, strategy_id="Buyer area", entry_time=day, exit_time=day + timedelta(minutes=30), exit_price=106.0, pnl=60.0)
    loss = _trade(tenant_a, a_id, strategy_id="Seller area", entry_time=day + timedelta(hours=1), exit_time=day + timedelta(hours=2),
                  exit_price=98.0, pnl=-20.0)
    meta = json.dumps({"risk_per_unit": 3, "quantity": 10})                # a structure's risk: 3 x 10 = 30
    leg1 = _trade(tenant_a, a_id, strategy_id="Buyer area", entry_time=day + timedelta(hours=3), exit_time=day + timedelta(hours=4),
                  exit_price=110.0, pnl=100.0, leg_group_id="G-M62", group_meta=meta)
    _trade(tenant_a, a_id, strategy_id="Buyer area", entry_time=day + timedelta(hours=3), exit_time=day + timedelta(hours=4),
           exit_price=96.0, pnl=-40.0, leg_group_id="G-M62", group_meta=meta)
    old = _trade(tenant_a, a_id, strategy_id="Buyer area", entry_time=NOW - timedelta(days=40), exit_time=NOW - timedelta(days=40),
                 exit_price=101.0, pnl=10.0)
    _trade(tenant_b, b_id, strategy_id="Buyer area", entry_time=day, exit_time=day, exit_price=150.0, pnl=99999.0)   # another organisation
    _tag(tenant_a, a_id, win, "early_exit")
    _tag(tenant_a, a_id, loss, "fomo_entry", status="DISMISSED")              # a dismissed tag never matches
    a = _h(token_a)
    r = client.post("/api/journal/manual", headers=a, json={
        "symbol": "SBIN", "direction": "LONG", "quantity": 5, "entry_time": (day + timedelta(hours=5)).isoformat(), "entry_price": 200.0,
        "stop_loss": 190.0, "exit_time": (day + timedelta(hours=6)).isoformat(), "exit_price": 210.0, "charges": 10, "setup": "Buyer area"})
    assert r.status_code == 201, r.text                                       # (210 - 200) x 5 - 10 = 40; risk 50 -> 0.8R
    return {"a": a, "b": _h(token_b), "win": win, "loss": loss, "leg1": leg1, "old": old, "manual": r.json()["id"],
            "from": (NOW - timedelta(days=7)).astimezone(IST).date().isoformat()}


def test_stats_own_org_manual_included_and_labelled(world):
    q = f"date_from={world['from']}"
    t = client.get(f"/api/journal/stats?{q}", headers=world["a"]).json()["totals"]
    # decisions: +60 (1R... 60/20 = 3R), -20 (-1R), the structure +100 - 40 = +60 (60/30 = 2R), manual +40 (0.8R)
    assert (t["trades"], t["closed"], t["wins"], t["losses"], t["outside_atp"]) == (4, 4, 3, 1, 1)
    assert t["net_pnl"] == 140.0 and t["win_rate"] == 75.0 and t["profit_factor"] == 8.0 and t["expectancy"] == 35.0
    assert t["avg_r"] == 1.2                                                    # (3 - 1 + 2 + 0.8) / 4
    assert (t["avg_win"], t["avg_loss"]) == (53.33, -20.0)
    atp = client.get(f"/api/journal/stats?{q}&include_manual=false", headers=world["a"]).json()["totals"]
    assert (atp["trades"], atp["net_pnl"], atp["win_rate"], atp["profit_factor"], atp["outside_atp"]) == (3, 100.0, 66.7, 6.0, 0)
    everything = client.get("/api/journal/stats", headers=world["a"]).json()["totals"]
    assert everything["trades"] == 5 and everything["net_pnl"] == 150.0      # the 40-day-old trade too; never tenant B's 99999
    other = client.get("/api/journal/stats", headers=world["b"]).json()["totals"]
    assert other["trades"] == 1 and other["net_pnl"] == 99999.0


def test_entries_list_filters(world):
    a, q = world["a"], f"date_from={world['from']}"
    body = client.get(f"/api/journal/entries?{q}", headers=a).json()
    assert body["total"] == 5                                                  # 4 trade rows (2 legs) + 1 manual
    manual = [e for e in body["entries"] if e["kind"] == "manual"]
    assert len(manual) == 1 and manual[0]["outside_atp"] and manual[0]["source"] == "OUTSIDE_ATP" and manual[0]["r_multiple"] == 0.8
    assert body["setups"] == ["Buyer area", "Seller area"] and "early_exit" in body["tags"] and "fomo_entry" not in body["tags"]
    first = body["entries"][0]
    assert first["chart"].startswith("/api/journal/") and set(first) >= {"symbol", "setup", "pnl", "r_multiple", "tags", "user_tags", "entry_time"}
    by_setup = client.get(f"/api/journal/entries?{q}&setup=Seller%20area", headers=a).json()
    assert [e["id"] for e in by_setup["entries"]] == [world["loss"]]
    by_tag = client.get(f"/api/journal/entries?{q}&tag=early_exit", headers=a).json()
    assert [e["id"] for e in by_tag["entries"]] == [world["win"]]
    assert client.get(f"/api/journal/entries?{q}&tag=fomo_entry", headers=a).json()["total"] == 0
    losses = client.get(f"/api/journal/entries?{q}&outcome=loss", headers=a).json()
    assert sorted(e["pnl"] for e in losses["entries"]) == [-40.0, -20.0]       # a losing leg is a losing row
    assert client.get(f"/api/journal/stats?{q}&outcome=loss", headers=a).json()["totals"]["trades"] == 1   # the structure won
    assert client.get("/api/journal/entries?outcome=maybe", headers=a).status_code == 422
    assert client.get("/api/journal/entries?date_from=2026-10-10&date_to=2026-10-01", headers=a).status_code == 422
    assert all(e["id"] != world["old"] for e in body["entries"])


def test_export_csv_and_pdf(world):
    a, q = world["a"], f"date_from={world['from']}"
    r = client.get(f"/api/journal/export?format=csv&{q}", headers=a)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 5 and r.headers["x-export-rows"] == "5"
    assert {row["source"] for row in rows} == {"ATP", "Outside ATP"}
    assert sum(float(row["net_pnl"]) for row in rows) == 140.0
    assert "99999" not in r.text
    pdf = client.get(f"/api/journal/export?format=pdf&{q}", headers=a)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    data = pdf.content
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    xref_at = int(re.search(rb"startxref\n(\d+)", data).group(1))
    assert data[xref_at:xref_at + 4] == b"xref"                                # the cross-reference table is where it says
    for n, off in enumerate(re.findall(rb"(\d{10}) 00000 n", data), start=1):
        assert data[int(off):].startswith(f"{n} 0 obj".encode())
    assert b"Trading journal" in data and b"Win rate: 75.0%" in data and b"+Rs 140.00" in data and b"Outside" in data
    assert client.get("/api/journal/export?format=xlsx", headers=a).status_code == 422


def test_flag_off_is_503(world):
    from tests.test_m62_journal_layer import _set_flag
    _set_flag(False)
    assert client.get("/api/journal/stats", headers=world["a"]).status_code == 503
    assert client.get("/api/journal/export", headers=world["a"]).status_code == 503
