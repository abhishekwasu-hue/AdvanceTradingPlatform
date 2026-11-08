"""M88: the Journal imports trades done outside ATP from a file (CSV / Excel) - one synthetic file per broker layout,
the FIFO pairing, problems per row, duplicates (same file, same trade), the caller's own rows only, the analysis that
reads them (Stats, the week, the weekly review) and each trade on its own chart. Every file here is made up."""
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.ai.chart_png import is_png
from app.journal import importer
from app.journal.importer import ImportFileError, pair_fifo, read_file
from tests.test_auth_api import client
from tests.test_m62_journal_layer import _h, _run, flag_on, store  # noqa: F401  (pytest fixtures)
from tests.test_trading_api import _register_user_and_get_id

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def _xlsx(rows: list[list[object]], *, date_cols: tuple[int, ...] = ()) -> bytes:
    """A minimal .xlsx (inline strings; numbers; a date style on `date_cols` as Excel serial days)."""
    def ref(r: int, c: int) -> str:
        return f"{chr(65 + c)}{r + 1}"
    cells = []
    for r, row in enumerate(rows):
        parts = []
        for c, v in enumerate(row):
            if isinstance(v, datetime):
                serial = (v - datetime(1899, 12, 30)).total_seconds() / 86400
                parts.append(f'<c r="{ref(r, c)}" s="1"><v>{serial}</v></c>')
            elif isinstance(v, (int, float)):
                parts.append(f'<c r="{ref(r, c)}"><v>{v}</v></c>')
            else:
                parts.append(f'<c r="{ref(r, c)}" t="inlineStr"><is><t>{v}</t></is></c>')
        cells.append(f'<row r="{r + 1}">{"".join(parts)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                                      '<sheets><sheet name="Trades" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                 '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="ws"/></Relationships>')
        z.writestr("xl/styles.xml", f'<styleSheet {ns}><numFmts count="1"><numFmt numFmtId="164" formatCode="dd-mm-yyyy hh:mm:ss"/></numFmts>'
                                    '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(cells)}</sheetData></worksheet>')
    return buf.getvalue()


# --- one synthetic file per layout ------------------------------------------------------------------------------------

ZERODHA = _csv(
    "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time",
    "AAAA,INE000A00001,2026-10-05,NSE,EQ,EQ,buy,false,10,100.00,T1,O1,2026-10-05T09:20:00",
    "AAAA,INE000A00001,2026-10-05,NSE,EQ,EQ,buy,false,10,110.00,T2,O2,2026-10-05T10:00:00",
    "AAAA,INE000A00001,2026-10-06,NSE,EQ,EQ,sell,false,20,120.00,T3,O3,2026-10-06T11:00:00",
    "BBBB,INE000B00001,2026-10-07,NSE,EQ,EQ,sell,false,5,200.00,T4,O4,2026-10-07T09:30:00",
    "BBBB,INE000B00001,2026-10-07,NSE,EQ,EQ,buy,false,5,190.00,T5,O5,2026-10-07T14:30:00",
)


def test_zerodha_tradebook_is_detected_and_paired_fifo():
    r = read_file("tradebook.csv", ZERODHA, now=NOW)
    assert r.layout == "zerodha" and r.shape == "fills" and not r.problems
    assert r.mapping["time"] == "order_execution_time" and r.mapping["side"] == "trade_type"
    a, b = r.trades
    assert (a.symbol, a.direction, a.quantity, a.entry_price, a.exit_price) == ("AAAA", "LONG", 20.0, 105.0, 120.0)   # average of 100 and 110
    assert a.entry_time == datetime(2026, 10, 5, 9, 20, tzinfo=IST) and a.exit_time == datetime(2026, 10, 6, 11, 0, tzinfo=IST)
    assert a.rows == [2, 3, 4]
    assert (b.symbol, b.direction, b.entry_price, b.exit_price) == ("BBBB", "SHORT", 200.0, 190.0)      # a sell first opens a short


def test_upstox_xlsx_date_and_time_columns():
    data = _xlsx([
        ["Trade report"], [],
        ["Date", "Company", "Amount", "Exchange", "Segment", "Scrip Code", "Instrument Type", "Trade Num", "Trade Time", "Side", "Quantity", "Price"],
        ["06-10-2026", "CCCC", 5000, "NSE", "EQ", 500001, "EQ", "U1", "09:45:10", "Buy", 50, 100],
        ["06-10-2026", "CCCC", 5250, "NSE", "EQ", 500001, "EQ", "U2", "15:05:00", "Sell", 50, 105],
    ])
    r = read_file("upstox.xlsx", data, now=NOW)
    assert r.layout == "upstox" and not r.problems
    (t,) = r.trades
    assert (t.direction, t.quantity, t.entry_price, t.exit_price) == ("LONG", 50.0, 100.0, 105.0)
    assert t.entry_time == datetime(2026, 10, 6, 9, 45, 10, tzinfo=IST) and t.exit_time == datetime(2026, 10, 6, 15, 5, tzinfo=IST)


def test_angel_tradebook_duplicate_fill_is_a_row_problem():
    data = _csv(
        "Trade Date,Trade Time,Symbol,Exchange,Segment,Buy/Sell,Quantity,Trade Price,Order No,Trade No",
        "07/10/2026,10:00:00,DDDD,NSE,EQ,B,4,250.5,A1,N1",
        "07/10/2026,10:00:00,DDDD,NSE,EQ,B,4,250.5,A1,N1",
        "08/10/2026,13:15:00,DDDD,NSE,EQ,S,4,260.5,A2,N2",
    )
    r = read_file("angel.csv", data, now=NOW)
    assert r.layout == "angel"
    assert [(p.row, "row 2" in p.reason) for p in r.problems] == [(3, True)]
    (t,) = r.trades
    assert (t.quantity, t.entry_price, t.exit_price) == (4.0, 250.5, 260.5)


def test_dhan_tradebook_skips_rows_that_were_not_filled():
    data = _csv(
        "Date,Time,Name,Buy/Sell,Order,Exchange,Segment,Quantity/Lot,Trade Price,Trade Value,Status",
        "09-10-2026,09:16:00,EEEE,BUY,MARKET,NSE,EQ,10,50,500,Traded",
        "09-10-2026,09:17:00,EEEE,BUY,LIMIT,NSE,EQ,10,49,490,Rejected",
        "09-10-2026,11:40:00,EEEE,SELL,MARKET,NSE,EQ,10,48,480,Traded",
    )
    r = read_file("dhan.csv", data, now=NOW)
    assert r.layout == "dhan" and r.mapping["quantity"] == "Quantity/Lot"
    assert len(r.problems) == 1 and r.problems[0].row == 3 and "Rejected" in r.problems[0].reason
    (t,) = r.trades
    assert (t.quantity, t.entry_price, t.exit_price) == (10.0, 50.0, 48.0)


def test_groww_value_without_price_and_12_hour_times():
    data = _csv(
        "Stock name,Symbol,ISIN,Type,Quantity,Value,Exchange,Exchange Order Id,Execution date and time,Order status",
        "Ffff Ltd,FFFF,INE000F00001,BUY,8,800,NSE,G1,\"12 Oct 2026, 09:20 AM\",Executed",
        "Ffff Ltd,FFFF,INE000F00001,SELL,8,880,NSE,G2,\"13 Oct 2026, 02:05 PM\",Executed",
    )
    r = read_file("groww.csv", data, now=NOW)
    assert r.layout == "groww" and not r.problems
    (t,) = r.trades
    assert (t.symbol, t.entry_price, t.exit_price) == ("FFFF", 100.0, 110.0)          # value / quantity, his own numbers
    assert t.exit_time == datetime(2026, 10, 13, 14, 5, tzinfo=IST)


def test_fyers_symbol_prefix_and_suffix_cleaned():
    data = _csv(
        "Symbol,Date,Time,Transaction Type,Qty,Trade Price,Trade Value,Order ID,Trade ID,Segment",
        "NSE:GGGG-EQ,14-10-2026,10:00:00,BUY,3,1000,3000,F1,X1,CM",
        "NSE:GGGG-EQ,14-10-2026,12:00:00,SELL,3,990,2970,F2,X2,CM",
    )
    r = read_file("fyers.csv", data, now=NOW)
    assert r.layout == "fyers"
    (t,) = r.trades
    assert (t.symbol, t.exchange, t.kind, t.exit_price) == ("GGGG", "NSE", "UNDERLYING", 990.0)


def test_plain_sheet_of_whole_trades_with_his_own_stop():
    data = _csv(
        "Symbol,Side,Qty,Entry time,Entry price,Exit time,Exit price,Charges,Stop loss,Setup",
        "HHHH,Long,10,15-10-2026 09:30,100,15-10-2026 13:00,106,20,98,Buyer area",
        "IIII,Short,5,16-10-2026 10:00,500,,,0,,",
        "JJJJ,Long,2,17-10-2026 10:00,50,17-10-2026 11:00,,0,,",
    )
    r = read_file("mine.csv", data, now=NOW)
    assert r.shape == "trades" and r.layout == "trades"
    a, b = r.trades
    assert (a.direction, a.stop_loss, a.charges, a.setup) == ("LONG", 98.0, 20.0, "Buyer area")
    assert (b.direction, b.exit_time, b.exit_price, b.stop_loss) == ("SHORT", None, None, None)       # open; no stop assumed
    assert [p.row for p in r.problems] == [4] and "both" in r.problems[0].reason


def test_a_fill_that_crosses_zero_closes_and_opens_the_other_side():
    t0 = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)
    fills = [importer.Fill(2, "KKKK", None, "UNDERLYING", "BUY", 10, 100, t0, 10.0, True),
             importer.Fill(3, "KKKK", None, "UNDERLYING", "SELL", 15, 110, t0 + timedelta(hours=1), 15.0, True)]
    long_, short = pair_fifo(fills)
    assert (long_.direction, long_.quantity, long_.exit_price, round(long_.charges, 2)) == ("LONG", 10, 110, 20.0)
    assert (short.direction, short.quantity, short.entry_price, short.exit_time, round(short.charges, 2)) == ("SHORT", 5, 110, None, 5.0)


def test_future_rows_and_unreadable_rows_are_problems_not_trades():
    data = _csv("Symbol,Side,Qty,Price,Time", "LLLL,buy,1,10,2099-01-01 10:00", "LLLL,hold,1,10,2026-10-01 10:00",
                "LLLL,buy,,10,2026-10-01 10:00", "LLLL,buy,1,10,someday")
    r = read_file("x.csv", data, now=NOW)
    assert not r.trades
    reasons = {p.row: p.reason for p in r.problems}
    assert "future" in reasons[2] and "not readable" in reasons[3] and reasons[4] == "No quantity." and "date" in reasons[5]


def test_old_binary_xls_gets_a_plain_sentence_and_html_xls_is_read():
    with pytest.raises(ImportFileError, match="save it as .xlsx"):
        read_file("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, now=NOW)
    html = (b"<html><body><table><tr><th>Symbol</th><th>Buy/Sell</th><th>Quantity</th><th>Trade Price</th><th>Trade Date</th></tr>"
            b"<tr><td>MMMM</td><td>Buy</td><td>1</td><td>10</td><td>01-10-2026</td></tr></table></body></html>")
    r = read_file("backoffice.xls", html, now=NOW)
    (t,) = r.trades
    assert (t.symbol, t.timed, t.exit_time) == ("MMMM", False, None)
    with pytest.raises(ImportFileError, match=".csv, .xlsx or .xls"):
        read_file("notes.txt", b"a,b", now=NOW)


def test_he_can_change_the_mapping():
    data = _csv("Ticker,Way,Units,Fill,When", "NNNN,B,2,10,2026-10-01 10:00", "NNNN,S,2,12,2026-10-02 10:00")
    with pytest.raises(ImportFileError, match="symbol"):
        read_file("x.csv", data, now=NOW)
    mapping = {"symbol": "Ticker", "side": "Way", "quantity": "Units", "price": "Fill", "time": "When"}
    (t,) = read_file("x.csv", data, mapping, now=NOW).trades
    assert (t.symbol, t.exit_price) == ("NNNN", 12.0)
    with pytest.raises(ImportFileError, match="not in this file"):
        read_file("x.csv", data, {**mapping, "price": "Nope"}, now=NOW)


def test_excel_date_cells_are_read_as_india_time():
    data = _xlsx([["Symbol", "Buy/Sell", "Quantity", "Trade Price", "Trade Date"],
                  ["OOOO", "Buy", 1, 10, datetime(2026, 10, 1, 10, 30)]], date_cols=(4,))
    (t,) = read_file("d.xlsx", data, now=NOW).trades
    assert t.entry_time == datetime(2026, 10, 1, 10, 30, tzinfo=IST)


# --- through the API --------------------------------------------------------------------------------------------------

@pytest.fixture
def two(flag_on, store):  # noqa: F811
    run = uuid.uuid4().hex[:8]
    token_a, a_id, _ = _run(_register_user_and_get_id(f"m88-a-{run}@example.com"))
    token_b, b_id, _ = _run(_register_user_and_get_id(f"m88-b-{run}@example.com"))
    return {"a": _h(token_a), "b": _h(token_b)}


def _post(path, headers, name, data, mapping=None):
    files = {"file": (name, data, "text/csv")}
    return client.post(path, headers=headers, files=files, data={"mapping": json.dumps(mapping)} if mapping else None)


def _week_of(day: datetime) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def test_preview_stores_nothing_import_stores_and_duplicates_are_skipped(two):
    a = two["a"]
    p = _post("/api/journal/import/preview", a, "tradebook.csv", ZERODHA)
    assert p.status_code == 200, p.text
    body = p.json()
    assert body["layout"]["id"] == "zerodha" and body["pairing"] and body["counts"]["trades"] == 2 and body["counts"]["to_import"] == 2
    assert client.get("/api/journal/manual", headers=a).json()["entries"] == []             # nothing saved by a preview
    r = _post("/api/journal/import", a, "tradebook.csv", ZERODHA)
    assert r.status_code == 200 and r.json()["imported"] == 2, r.text
    listed = client.get("/api/journal/manual", headers=a).json()["entries"]
    assert {e["symbol"] for e in listed} == {"AAAA", "BBBB"}
    assert all(e["source"] == "OUTSIDE_ATP" and e["imported"] and e["source_label"].startswith("Imported") for e in listed)
    assert all(e["r_multiple"] is None and e["plan_view"]["invalidation"] is None for e in listed)        # no stop in the file: no R
    again = _post("/api/journal/import", a, "tradebook.csv", ZERODHA).json()                              # the same file
    assert again["already_imported"] and again["imported"] == 0
    assert _post("/api/journal/import/preview", a, "tradebook.csv", ZERODHA).json()["already_imported"]["trades_left"] == 2
    renamed = ZERODHA + b"\n"                                                                              # another file, same trades
    second = _post("/api/journal/import/preview", a, "copy.csv", renamed).json()
    assert second["already_imported"] is None and second["counts"]["duplicates"] == 2 and second["counts"]["to_import"] == 0
    assert _post("/api/journal/import", a, "copy.csv", renamed).json()["imported"] == 0
    assert len(client.get("/api/journal/manual", headers=a).json()["entries"]) == 2
    assert client.get("/api/journal/manual", headers=two["b"]).json()["entries"] == []                    # never another user's
    other = client.get(f"/api/journal/manual/{listed[0]['id']}", headers=two["b"])
    assert other.status_code == 404


def test_removed_imports_may_come_in_again_and_bad_files_are_refused(two):
    a = two["a"]
    assert _post("/api/journal/import", a, "tradebook.csv", ZERODHA).json()["imported"] == 2
    for e in client.get("/api/journal/manual", headers=a).json()["entries"]:
        assert client.delete(f"/api/journal/manual/{e['id']}", headers=a).status_code == 200
    assert _post("/api/journal/import", a, "tradebook.csv", ZERODHA).json()["imported"] == 2
    bad = _post("/api/journal/import/preview", a, "x.csv", b"just,some\nwords,here\n")
    assert bad.status_code == 422
    big = _post("/api/journal/import/preview", a, "x.csv", b"a" * (importer.IMPORT_MAX_BYTES + 1))
    assert big.status_code == 413
    assert _post("/api/journal/import/preview", a, "x.csv", ZERODHA, {"symbol": "nope"}).status_code == 422


def test_an_imported_file_changes_stats_week_and_the_weekly_review(two):
    a = two["a"]
    day = datetime(2026, 10, 5, tzinfo=IST)
    before = client.get("/api/journal/stats", headers=a).json()["totals"]
    week_before = client.get(f"/api/journal/week?week={_week_of(day)}", headers=a).json()
    review_before = client.get(f"/api/journal/review?week={_week_of(day)}&history=0", headers=a).json()
    assert before["trades"] == 0 and week_before["manual_entries"] == [] and review_before["totals"]["trades"] == 0
    assert _post("/api/journal/import", a, "tradebook.csv", ZERODHA).json()["imported"] == 2
    after = client.get("/api/journal/stats", headers=a).json()["totals"]
    # AAAA long: (120 - 105) x 20 = 300; BBBB short: (200 - 190) x 5 = 50; no charges in the file
    assert (after["trades"], after["closed"], after["wins"], after["outside_atp"], after["net_pnl"]) == (2, 2, 2, 2, 350.0)
    assert after["avg_r"] is None                                                       # no stop in the file: no R
    week = client.get(f"/api/journal/week?week={_week_of(day)}", headers=a).json()
    assert sorted(m["symbol"] for m in week["manual_entries"]) == ["AAAA", "BBBB"]
    review = client.get(f"/api/journal/review?week={_week_of(day)}&history=0", headers=a).json()
    t = review["totals"]
    assert (t["trades"], t["outside_atp"], t["net_pnl"], t["win_rate"]) == (2, 2, 350.0, 100.0)
    assert any("done outside ATP" in line for line in review["lines"])
    assert client.get("/api/journal/stats", headers=two["b"]).json()["totals"]["trades"] == 0


def test_his_stop_in_the_file_gives_r(two):
    a = two["a"]
    data = _csv("Symbol,Side,Qty,Entry time,Entry price,Exit time,Exit price,Stop loss",
                "HHHH,Long,10,15-10-2026 09:30,100,15-10-2026 13:00,106,98")
    assert _post("/api/journal/import", a, "mine.csv", data).json()["imported"] == 1
    (e,) = client.get("/api/journal/manual", headers=a).json()["entries"]
    assert e["r_multiple"] == 3.0 and e["plan_view"]["invalidation"] == 98.0             # (106 - 100) x 10 / (2 x 10)


def test_trade_on_its_chart_and_save_image(two, monkeypatch):
    a = two["a"]
    assert _post("/api/journal/import", a, "tradebook.csv", ZERODHA).json()["imported"] == 2
    aaaa = next(e for e in client.get("/api/journal/manual", headers=a).json()["entries"] if e["symbol"] == "AAAA")
    asked = []

    async def fake_daily(session, symbol, start, end):
        asked.append(symbol)
        out = []
        for i in range(30):
            day = datetime(2026, 9, 20, 18, 30, tzinfo=UTC) + timedelta(days=i)    # each bar's END, as the lake stores Daily bars
            out.append(SimpleNamespace(ts=day, open=100 + i, high=103 + i, low=98 + i, close=101 + i, volume=1000))
        return [b for b in out if start <= b.ts <= end]
    import app.market_data.eod_routes as eod
    monkeypatch.setattr(eod, "daily_bars", fake_daily)
    assert aaaa["charts"]["trade"].endswith(f"/manual/{aaaa['id']}/trade-chart")
    r = client.get(f"/api/journal/manual/{aaaa['id']}/trade-chart", headers=a)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["timeframe"] == "day" and c["timeframe_label"] == "Daily candles" and "intraday" in c["note"] and c["candles"]
    entry, exit_ = c["marks"]
    assert (entry["kind"], entry["price"], entry["side"]) == ("entry", 105.0, "BUY")
    assert (exit_["kind"], exit_["price"], exit_["side"]) == ("exit", 120.0, "SELL")
    stamps = [k["timestamp"] for k in c["candles"]]
    assert entry["bar"] in stamps and exit_["bar"] in stamps and entry["bar"] < exit_["bar"]
    assert entry["bar"].startswith("2026-10-04T18:30") or entry["bar"].startswith("2026-10-05")   # the candle of 5 Oct (IST)
    png = client.get(f"/api/journal/manual/{aaaa['id']}/trade-chart.png", headers=a)
    assert png.status_code == 200 and is_png(png.content) and "attachment" in png.headers["content-disposition"]
    assert asked and set(asked) == {"AAAA"}
    assert client.get(f"/api/journal/manual/{aaaa['id']}/trade-chart", headers=two["b"]).status_code == 404
    assert client.get(f"/api/journal/manual/{aaaa['id']}/trade-chart.png", headers=two["b"]).status_code == 404


def test_no_candles_is_said_plainly(two, monkeypatch):
    a = two["a"]
    assert _post("/api/journal/import", a, "tradebook.csv", ZERODHA).json()["imported"] == 2
    e = client.get("/api/journal/manual", headers=a).json()["entries"][0]

    async def none(session, symbol, start, end):
        return []
    import app.market_data.eod_routes as eod
    monkeypatch.setattr(eod, "daily_bars", none)
    c = client.get(f"/api/journal/manual/{e['id']}/trade-chart", headers=a).json()
    assert c["missing"] and c["candles"] == [] and "No daily candles" in c["missing_text"]
    assert is_png(client.get(f"/api/journal/manual/{e['id']}/trade-chart.png", headers=a).content)
