"""HB1: the daily NSE bhavcopy backfill and the nightly EOD job - shared lake, flag `history_backfill` (off by default).

Nothing here touches the network: the archive is a fake fetcher serving fixture files by name, and the polite HTTP
fetcher is driven through `httpx.MockTransport` with a fake clock and sleep. Symbols are made up (HBQ...), and each
test uses its own dates, because the per-day state is shared by the in-memory test database.
Covered: every file format over the years (legacy cash with and without ISIN, the full bhavcopy, the July-2024 UDiFF
cash file, legacy and UDiFF F&O, the all-indices file); refusals; idempotent re-run; resume after an interruption;
rate-limit backoff and a blocked file uploaded by hand; split / bonus / rights adjustment from the exchange's
corporate-action export; symbol change stitched by ISIN and a delisted name kept; holidays loader and gap reporting;
quality gates; the flag; the nightly job; the admin endpoints.
"""
import asyncio
import io
import zipfile
from datetime import date, datetime, time, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import (DataQualityEventRecord, HistoryBackfillFileRecord, IndexEodRecord, MarketHolidayRecord,
                           MdCandleRecord, MdCorporateActionRecord, MdIsinSymbolRecord, User)
from app.market_data.calendar import IST
from app.market_lake import adjust, bhavcopy, bhavcopy_backfill as hb1, by_isin, corporate_actions
from app.market_lake.bhavcopy import BhavcopyRefused
from app.market_lake.nse_fetch import FetchResult, PoliteFetcher
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client
from tests.utils import enable_mfa

F = HistoryBackfillFileRecord


def _run(coro):
    return asyncio.run(coro)


def _count(model, *where):
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)
    return _run(go())


def _flag(on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = current.get("flags") or {}
            flags[hb1.FLAG] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _state(segment, day):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(F).where(F.segment == segment, F.trade_date == day))
    return _run(go())


def _weekdays(start: date, n: int):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# --- fixture files in every format ----------------------------------------------------------------------------------
def _row(symbol, close, *, series="EQ", isin=None, volume=1000, open_=None, high=None, low=None):
    o = close if open_ is None else open_
    return {"symbol": symbol, "series": series, "isin": isin or "", "volume": volume, "open": o,
            "high": max(o, close) * 1.01 if high is None else high, "low": min(o, close) * 0.99 if low is None else low, "close": close}


def cm_legacy(day, rows, *, with_isin=True):
    head = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP," + ("TOTALTRADES,ISIN," if with_isin else "")
    lines = [head]
    for r in rows:
        line = (f"{r['symbol']},{r['series']},{r['open']},{r['high']:.2f},{r['low']:.2f},{r['close']},{r['close']},{r['open']},"
                f"{r['volume']},{r['volume'] * r['close']:.2f},{day:%d-%b-%Y}".upper() + ",")
        lines.append(line + (f"12,{r['isin']}," if with_isin else ""))
    return "\n".join(lines) + "\n"


def cm_full(day, rows):
    head = (" SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE,"
            " TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER")
    lines = [head] + [f"{r['symbol']}, {r['series']}, {day:%d-%b-%Y}, {r['open']}, {r['open']}, {r['high']:.2f}, {r['low']:.2f},"
                      f" {r['close']}, {r['close']}, {r['close']}, {r['volume']}, 1.00, 10, -, -" for r in rows]
    return "\n".join(lines) + "\n"


UDIFF_HEAD = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,"
              "FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,"
              "TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")


def cm_udiff(day, rows):
    lines = [UDIFF_HEAD] + [f"{day},{day},CM,NSE,STK,1,{r['isin']},{r['symbol']},{r['series']},,,,,{r['symbol']} LIMITED,{r['open']},"
                            f"{r['high']:.2f},{r['low']:.2f},{r['close']},{r['close']},{r['open']},,,,,{r['volume']},1.0,10,F1,1,,,,,"
                            for r in rows]
    return "\n".join(lines) + "\n"


def fo_legacy(day, contracts):
    lines = ["INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,"]
    for inst, sym, exp, strike, right, close, vol, oi in contracts:
        o = h = lo = close
        lines.append(f"{inst},{sym},{exp:%d-%b-%Y},{strike},{right},{o},{h},{lo},{close},{close or 1.05},{vol},1,{oi},0,{day:%d-%b-%Y},")
    return "\n".join(lines) + "\n"


def fo_udiff(day, contracts):
    lines = [UDIFF_HEAD]
    for tp, sym, exp, strike, right, close, vol, oi in contracts:
        lines.append(f"{day},{day},FO,NSE,{tp},1,,{sym},,{exp},{exp},{strike},{right},{sym},{close},{close},{close},{close},{close},"
                     f"{close},,{close},{oi},0,{vol},1.0,5,F1,25,,,,,")
    return "\n".join(lines) + "\n"


def index_file(day, rows):
    lines = ["Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),"
             "Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield"]
    for name, o, h, lo, c in rows:
        cells = [name, f"{day:%d-%m-%Y}", o, h, lo, c, "1", "0.1", "100", "1", "20.5", "3.1", "1.2"]
        lines.append(",".join("-" if v is None else str(v) for v in cells))
    return "\n".join(lines) + "\n"


def zipped(inner: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(inner, text)
    return buf.getvalue()


class Archive:
    """The NSE archive as a fake fetcher: file name -> bytes; a name can be blocked (403) or made to fail."""

    def __init__(self):
        self.files, self.blocked, self.calls = {}, set(), []
        self.cancel_on = None

    def put(self, segment, day, text):
        name = bhavcopy.candidate_files(segment, day)[0][1]
        self.files[name] = zipped(name[:-4], text) if name.endswith(".zip") else text.encode()
        return name

    async def get(self, url):
        self.calls.append(url)
        name = url.rsplit("/", 1)[-1]
        if self.cancel_on and self.cancel_on in name:
            raise asyncio.CancelledError()              # the process dies mid-run (not an ordinary error)
        if name in self.blocked:
            return FetchResult(url, "blocked", http_status=403, attempts=4, detail="HTTP 403 after 4 attempt(s)")
        if name in self.files:
            return FetchResult(url, "ok", self.files[name], 200, 1)
        return FetchResult(url, "not_found", http_status=404, attempts=1)

    async def aclose(self):
        pass


def _backfill(archive, start, end, **kw):
    return _run(hb1.run_backfill(_session_factory, start=start, end=end, fetcher=archive, **kw))


def _closes(key, start, end, adjusted=True, symbol=None):
    async def go():
        async with _session_factory() as session:
            bars = await adjust.candles(session, key, "day", hb1.bar_end(start) - timedelta(days=1), hb1.bar_end(end),
                                        symbol=symbol or key.split(":")[1], exchange="NSE", adjusted=adjusted,
                                        sources=[hb1.SOURCE["cm"]])
            return [round(b.close, 4) for b in bars], bars
    return _run(go())


# --- parsing: every format over the years ---------------------------------------------------------------------------
def test_cash_formats_across_the_years_parse_to_the_same_rows():
    day = date(2016, 1, 1)
    rows = [_row("HBQP1", 104, isin="INE0HBQ00011"), _row("HBQP2", 55.5, series="BE", isin="INE0HBQ00029"),
            _row("HBQBOND", 1000, series="N1", isin="INE0HBQ00037"), _row("HBQSME", 20, series="SM", isin="INE0HBQ00045")]
    legacy = bhavcopy.parse_cm(cm_legacy(day, rows))
    assert legacy.file_format == "cm_legacy" and legacy.trade_date == day
    assert [(r.instrument_key, r.series, r.isin, r.close) for r in legacy.rows] == [
        ("NSE:HBQP1", "EQ", "INE0HBQ00011", 104.0), ("NSE:HBQP2", "BE", "INE0HBQ00029", 55.5), ("NSE:HBQSME", "SM", "INE0HBQ00045", 20.0)]
    assert legacy.skipped == {"other_series": 1}

    older = bhavcopy.parse_cm(cm_legacy(date(2009, 6, 1), rows, with_isin=False))     # before NSE added TOTALTRADES / ISIN
    assert older.file_format == "cm_legacy" and {r.isin for r in older.rows} == {None} and "without ISIN" in older.notes[0]
    full = bhavcopy.parse_cm(cm_full(date(2020, 1, 1), rows))
    assert full.file_format == "cm_full" and full.trade_date == date(2020, 1, 1)
    udiff = bhavcopy.parse_cm(cm_udiff(date(2024, 7, 8), rows))                        # NSE's July-2024 UDiFF cash file
    assert udiff.file_format == "cm_udiff" and udiff.trade_date == date(2024, 7, 8)
    assert udiff.rows[0].name == "HBQP1 LIMITED"
    same = [(r.instrument_key, r.series, r.open, r.close, r.volume) for r in legacy.rows]
    for other in (older, full, udiff):
        assert [(r.instrument_key, r.series, r.open, r.close, r.volume) for r in other.rows] == same
    # A zipped file reads the same as its CSV.
    assert bhavcopy.parse("cm", zipped("x.csv", cm_legacy(day, rows)))[0].rows == legacy.rows


def test_fo_and_index_formats_and_refusals():
    day, exp = date(2016, 1, 1), date(2016, 1, 28)
    contracts = [("FUTIDX", "HBQIDX", exp, 0, "XX", 7930, 1000, 50000), ("OPTIDX", "HBQIDX", exp, 8000, "CE", 55.5, 200, 3000),
                 ("OPTIDX", "HBQIDX", exp, 9000, "CE", 0, 0, 0), ("FUTSTK", "HBQSTK", exp, 0, "XX", 101.25, 50, 700)]
    legacy = bhavcopy.parse_fo(fo_legacy(day, contracts))
    assert [r.instrument_key for r in legacy.rows] == ["NFO:HBQIDX:FUT:2016-01-28", "NFO:HBQSTK:FUT:2016-01-28"]
    assert legacy.rows[0].oi == 50000 and legacy.skipped == {"options_not_loaded": 2}
    with_opts = bhavcopy.parse_fo(fo_legacy(day, contracts), with_options=True)
    assert "NFO:HBQIDX:CE:2016-01-28:8000" in {r.instrument_key for r in with_opts.rows} and with_opts.skipped == {"no_trade": 1}
    uday, uexp = date(2024, 7, 8), date(2024, 7, 25)
    udiff = bhavcopy.parse_fo(fo_udiff(uday, [("IDF", "HBQIDX", uexp, "", "", 24400.5, 900, 12000),
                                              ("IDO", "HBQIDX", uexp, "24500.00", "PE", 120.0, 300, 900)]), with_options=True)
    assert udiff.file_format == "fo_udiff" and [r.instrument_key for r in udiff.rows] == [
        "NFO:HBQIDX:FUT:2024-07-25", "NFO:HBQIDX:PE:2024-07-25:24500"]

    parsed, closes = bhavcopy.parse("index", index_file(uday, [("HBQ Test Index", 1000, 1010, 995, 1005),
                                                               ("HBQ Close Only", None, None, None, 500)]).encode())
    assert [r.instrument_key for r in parsed.rows] == ["NSE_INDEX:HBQTESTINDEX"] and parsed.skipped == {"close_only": 1}
    assert len(closes) == 2                                # both closes still go to U1-c's index_eod

    with pytest.raises(BhavcopyRefused, match="not a cash-market"):
        bhavcopy.parse_cm(fo_legacy(day, contracts))
    with pytest.raises(BhavcopyRefused, match="segment FO"):
        bhavcopy.parse_cm(fo_udiff(uday, [("IDF", "HBQIDX", uexp, "", "", 1, 1, 1)]))
    mixed = cm_legacy(day, [_row("HBQM1", 10)]) + cm_legacy(date(2016, 1, 4), [_row("HBQM2", 10)]).split("\n", 1)[1]
    with pytest.raises(BhavcopyRefused, match="mixes trade dates"):
        bhavcopy.parse_cm(mixed)
    with pytest.raises(BhavcopyRefused, match="two different rows"):
        bhavcopy.parse_cm(cm_legacy(day, [_row("HBQD1", 10), _row("HBQD1", 11)]))
    dup = bhavcopy.parse_cm(cm_legacy(day, [_row("HBQD2", 10), _row("HBQD2", 10), _row("HBQD3", 5, series="BE"),
                                            _row("HBQD3", 5.5, series="EQ")]))
    assert dup.duplicates == ["NSE:HBQD2"] and [r.series for r in dup.rows if r.symbol == "HBQD3"] == ["EQ"]
    no_price = cm_legacy(day, [_row("HBQN1", 10)]).replace(",10,10.10,", ",,10.10,")
    assert bhavcopy.parse_cm(no_price).invalid == ["NSE:HBQN1"]
    assert [n for _, n in bhavcopy.candidate_files("cm", date(2024, 7, 5))] == ["cm05JUL2024bhav.csv.zip", "BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv.zip"]
    assert [n for _, n in bhavcopy.candidate_files("cm", date(2024, 7, 8))][0] == "BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
    assert bhavcopy.candidate_files("fo", date(2016, 1, 1))[0][1] == "fo01JAN2016bhav.csv.zip"


# --- the job ------------------------------------------------------------------------------------------------------
def test_backfill_is_idempotent_and_resumes_after_an_interruption():
    _flag(True)
    days = _weekdays(date(2003, 3, 3), 5)
    archive = Archive()
    for i, d in enumerate(days):
        archive.put("cm", d, cm_legacy(d, [_row("HBQR1", 100 + i, isin="INE0HBQR0011"), _row("HBQR2", 50 + i, isin="INE0HBQR0029")]))
        archive.put("index", d, index_file(d, [("HBQ Resume Index", 900, 910, 890, 905 + i)]))
        archive.put("fo", d, fo_legacy(d, [("FUTSTK", "HBQR1", date(2003, 3, 27), 0, "XX", 100.5 + i, 10, 100)]))
    archive.cancel_on = f"cm{days[2]:%d%b%Y}".upper().replace("CM", "cm", 1)     # the process is killed on day 3
    with pytest.raises(asyncio.CancelledError):
        _backfill(archive, days[0], days[-1])
    assert _state("cm", days[2]).status == "running"                             # nothing of it was written
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NSE:HBQR1") == 2

    archive.cancel_on, archive.calls = None, []
    second = _backfill(archive, days[0], days[-1])
    assert (second.done, second.skipped, second.refused, second.failed) == (9, 6, 0, 0)
    assert not any(f"{days[0]:%d%b%Y}".upper() in c for c in archive.calls)     # done days are never fetched again
    state = _state("cm", days[2])
    assert state.status == "done" and state.attempts == 2 and len(state.sha256) == 64 and state.file_format == "cm_legacy"
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NSE:HBQR1", MdCandleRecord.source == "nse_bhav:cm") == 5
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NSE_INDEX:HBQRESUMEINDEX") == 5
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NFO:HBQR1:FUT:2003-03-27") == 5
    assert _count(IndexEodRecord, IndexEodRecord.index_code == "HBQRESUMEINDEX") == 5        # U1-c table filled too
    end = hb1.bar_end(days[0])
    assert end.astimezone(IST).time() == time(15, 30)

    archive.calls = []
    third = _backfill(archive, days[0], days[-1])                                 # idempotent: nothing new, no requests
    assert (third.done, third.skipped, third.files) == (0, 15, 0) and archive.calls == []
    # A corrected file uploaded for a done day: only the changed bar becomes a new version.
    fixed = cm_legacy(days[1], [_row("HBQR1", 101.5, isin="INE0HBQR0011"), _row("HBQR2", 51, isin="INE0HBQR0029")])
    result = _run(hb1.ingest_upload(_session_factory, "cm", days[1], fixed.encode(), "fixed.csv"))
    assert result["status"] == "done" and result["corrected"] == 1 and result["skipped"] == 1 and result["origin"] == "upload"
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NSE:HBQR1", MdCandleRecord.version == 2) == 1


def test_format_switch_in_july_2024_keeps_one_series_and_isin():
    _flag(True)
    days = [date(2024, 7, 4), date(2024, 7, 5), date(2024, 7, 8), date(2024, 7, 9)]
    archive = Archive()
    for i, d in enumerate(days):
        rows = [_row("HBQU1", 200 + i, isin="INE0HBQU0013")]
        archive.put("cm", d, cm_legacy(d, rows) if d < bhavcopy.UDIFF_FROM else cm_udiff(d, rows))
        exp = date(2024, 7, 25)
        archive.put("fo", d, fo_legacy(d, [("FUTSTK", "HBQU1", exp, 0, "XX", 201 + i, 5, 50)]) if d < bhavcopy.UDIFF_FROM
                    else fo_udiff(d, [("STF", "HBQU1", exp, "", "", 201 + i, 5, 50)]))
    summary = _backfill(archive, days[0], days[-1], segments=["cm", "fo"])
    assert (summary.done, summary.refused, summary.failed) == (8, 0, 0)
    assert [_state("cm", d).file_format for d in days] == ["cm_legacy", "cm_legacy", "cm_udiff", "cm_udiff"]
    assert any("BhavCopy_NSE_CM_0_0_0_20240708" in c for c in archive.calls) and any("cm05JUL2024bhav" in c for c in archive.calls)
    closes, _ = _closes("NSE:HBQU1", days[0], days[-1], adjusted=False)
    assert closes == [200, 201, 202, 203]
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NFO:HBQU1:FUT:2024-07-25") == 4

    async def ranges():
        async with _session_factory() as session:
            return [(r.symbol, r.first_date, r.last_date) for r in await by_isin.symbol_ranges(session, "INE0HBQU0013")]
    assert _run(ranges()) == [("HBQU1", days[0], days[-1])]


def test_split_bonus_and_rights_adjust_from_the_exchanges_corporate_action_file():
    _flag(True)
    days = _weekdays(date(2005, 6, 6), 8)
    # Unadjusted closes as the exchange printed them: a 1:5 split (face value 10 -> 2) on day 3, a 1:1 bonus on day 6.
    unadjusted = [1000, 1010, 202, 204, 206, 103, 104, 105]
    archive = Archive()
    for d, c in zip(days, unadjusted):
        archive.put("cm", d, cm_legacy(d, [_row("HBQS1", c, isin="INE0HBQS0016", volume=100)]))
    _backfill(archive, days[0], days[-1], segments=["cm"])
    spikes = _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:HBQS1", DataQualityEventRecord.kind == "spike")
    assert spikes == 2                                         # the raw series jumps on both ex-dates: reported, not "fixed"

    nse_export = ("SYMBOL,COMPANY NAME,SERIES,PURPOSE,FACE VALUE,EX-DATE,RECORD DATE\n"
                  f"HBQS1,HBQ S ONE LTD,EQ,Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share,10,{days[2]:%d-%b-%Y},-\n"
                  f"HBQS1,HBQ S ONE LTD,EQ,Bonus 1:1,2,{days[5]:%d-%b-%Y},-\n"
                  f"HBQS1,HBQ S ONE LTD,EQ,Annual General Meeting,2,{days[6]:%d-%b-%Y},-\n"
                  f"HBQS1,HBQ S ONE LTD,EQ,Demerger,2,{days[7]:%d-%b-%Y},-\n").encode()

    async def load(content):
        async with _session_factory() as session:
            return await corporate_actions.load_csv(session, content)
    report = _run(load(nse_export))
    assert (report.inserted, report.ignored) == (2, 1) and "demerger" in report.unreadable[0]
    assert _run(load(nse_export)).unchanged == 2                                   # idempotent

    adjusted, bars = _closes("NSE:HBQS1", days[0], days[-1])
    assert adjusted == [100.0, 101.0, 101.0, 102.0, 103.0, 103.0, 104.0, 105.0]     # factor 0.2 then 0.5 before the ex-dates
    assert bars[0].volume == 1000                                                  # volume x10 (x5 split, x2 bonus)
    raw, _ = _closes("NSE:HBQS1", days[0], days[-1], adjusted=False)
    assert raw == unadjusted                                                       # unadjusted kept

    # Rights 1:4 at 80 with the cum-rights close 105 (ATP's own CSV layout): TERP factor (4*105 + 80) / (5*105).
    rights = f"symbol,ex_date,action,ratio_new,ratio_old,amount\nHBQS1,{days[7] + timedelta(days=1)},RIGHTS,1,4,80\n".encode()
    assert _run(load(rights)).inserted == 1
    adjusted_r, _ = _closes("NSE:HBQS1", days[0], days[-1])
    factor = (4 * 105 + 80) / (5 * 105)
    assert adjusted_r[-1] == round(105 * factor, 4) and adjusted_r[0] == round(100 * factor, 4)


def test_symbol_change_is_stitched_by_isin_and_a_delisted_name_stays_in_its_days():
    _flag(True)
    days = _weekdays(date(2006, 9, 4), 4)
    archive = Archive()
    for i, d in enumerate(days):
        sym = "HBQOLD" if i < 2 else "HBQNEW"                                       # renamed on day 3, same ISIN
        rows = [_row(sym, 300 + i, isin="INE0HBQC0018")]
        if i == 0:
            rows.append(_row("HBQGONE", 12, isin="INE0HBQG0010"))                  # delisted after day 1
        archive.put("cm", d, cm_legacy(d, rows))
    _backfill(archive, days[0], days[-1], segments=["cm"])

    async def read():
        async with _session_factory() as session:
            series = await by_isin.isin_candles(session, "INE0HBQC0018", days[0], days[-1], adjusted=False)
            return ([(b.instrument_key, b.close) for b in series], await by_isin.universe_on(session, days[0]),
                    await by_isin.universe_on(session, days[-1]))
    series, first_day, last_day = _run(read())
    assert series == [("NSE:HBQOLD", 300), ("NSE:HBQOLD", 301), ("NSE:HBQNEW", 302), ("NSE:HBQNEW", 303)]
    assert "NSE:HBQGONE" in first_day and "NSE:HBQGONE" not in last_day and "NSE:HBQNEW" in last_day
    assert _count(MdIsinSymbolRecord, MdIsinSymbolRecord.isin == "INE0HBQC0018") == 2


def test_holidays_loader_gaps_reported_never_filled_and_skipped_without_a_calendar():
    _flag(True)
    holiday, missing = date(2007, 10, 2), date(2007, 10, 4)                       # Tue: holiday; Thu: the file never came
    csv_text = ("Sr. No,Date,Day,Description\n1,02-Oct-2007,Tuesday,Gandhi Jayanti (test)\n"
                "2,26-Jan-2007,Friday,Republic Day (test)\n3,not a date,,\n").encode()

    async def load_holidays():
        from app.market_data.holiday_import import import_holidays
        async with _session_factory() as session:
            return await import_holidays(session, csv_text)
    first = _run(load_holidays())
    assert (first.added, first.years, len(first.unreadable)) == (2, [2007], 1)
    assert _run(load_holidays()).existing == 2                                     # idempotent
    days = _weekdays(date(2007, 10, 1), 5)
    archive = Archive()
    for i, d in enumerate(days):
        if d not in (holiday, missing):
            archive.put("cm", d, cm_legacy(d, [_row("HBQH1", 40 + i), _row("HBQH2", 70 + i)] if d != date(2007, 10, 3)
                                           else [_row("HBQH1", 42)]))                 # HBQH2 did not trade on the 3rd
    summary = _backfill(archive, days[0], days[-1], segments=["cm"])
    assert summary.gaps == ["cm 2007-10-04"] and summary.no_file == 2
    assert _state("cm", holiday).note == "exchange holiday" and "gap" in _state("cm", missing).note
    assert _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE_BHAV:CM", DataQualityEventRecord.kind == "gap",
                  DataQualityEventRecord.ts == hb1.bar_end(missing)) == 1
    assert _count(MdCandleRecord, MdCandleRecord.instrument_key == "NSE:HBQH1", MdCandleRecord.ts == hb1.bar_end(missing)) == 0

    async def gaps():
        async with _session_factory() as session:
            return await hb1.gap_report(session, days[0], days[-1], symbols=["HBQH1", "HBQH2"])
    report = _run(gaps())
    assert report["missing_files"] == ["2007-10-04"] and report["gap_events"] == 1      # HBQH2 on the 3rd only
    assert _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:HBQH2", DataQualityEventRecord.kind == "gap") == 1

    # 2008 has no holiday calendar: a missing weekday is not called a holiday or a gap.
    lone = date(2008, 3, 3)
    assert _backfill(Archive(), lone, lone, segments=["cm"]).gaps == []
    assert "gap check skipped" in _state("cm", lone).note


def test_quality_gates_short_file_invalid_rows_and_force():
    _flag(True)
    days = _weekdays(date(2009, 2, 2), 5)
    archive = Archive()
    many = [_row(f"HBQG{i}", 10 + i) for i in range(10)]
    for d in days[:4]:
        archive.put("cm", d, cm_legacy(d, many))
    short = cm_legacy(days[4], many[:3]).replace(",10,10.10,9.90,10,", ",10,10.10,9.90,,")      # 3 rows; one without a close
    archive.files[bhavcopy.candidate_files("cm", days[4])[0][1]] = zipped("x.csv", short)
    summary = _backfill(archive, days[0], days[-1], segments=["cm"])
    assert summary.done == 4 and summary.refused == 1
    state = _state("cm", days[4])
    assert state.status == "refused" and "short file" in state.error
    assert _count(MdCandleRecord, MdCandleRecord.ts == hb1.bar_end(days[4]), MdCandleRecord.instrument_key.like("NSE:HBQG%")) == 0
    # The owner checks the file and uploads it with force: loaded, the row without a close reported (not filled).
    result = _run(hb1.ingest_upload(_session_factory, "cm", days[4], short.encode(), "short.csv", force=True))
    assert result["status"] == "done" and result["rows_loaded"] == 2
    assert _count(DataQualityEventRecord, DataQualityEventRecord.instrument_key == "NSE:HBQG0", DataQualityEventRecord.kind == "invalid") == 1
    wrong_day = _run(hb1.ingest_upload(_session_factory, "cm", days[3], short.encode(), "x.csv", force=True))
    assert wrong_day["status"] == "refused" and "not 2009-02-05" in wrong_day["error"]
    assert wrong_day["day_status"] == "done" and _state("cm", days[3]).rows_loaded == 10      # the loaded day is kept


def test_rate_limit_backoff_pacing_and_a_blocked_file_uploaded_by_hand():
    seen = []
    script = {"a": [429, 503, 200], "b": [404], "c": [403, 403, 403]}

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        seen.append(name)
        code = script[name].pop(0)
        headers = {"Retry-After": "7"} if code == 429 else {}
        return httpx.Response(code, content=b"data" if code == 200 else b"", headers=headers)

    clock = [0.0]
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    async def go():
        fetcher = PoliteFetcher(transport=httpx.MockTransport(handler), pause_seconds=1.5, retries=3, backoff_base=2,
                                max_backoff=60, sleep=sleep, clock=lambda: clock[0])
        try:
            return [await fetcher.get(f"https://archive.invalid/{n}") for n in ("a", "b", "c")], fetcher, dict(fetcher._client.headers)
        finally:
            await fetcher.aclose()
    (a, b, c), fetcher, headers = _run(go())
    assert (a.status, a.content, a.attempts) == ("ok", b"data", 3)
    assert (b.status, b.attempts) == ("not_found", 1)                    # a 404 is an answer, never retried
    assert (c.status, c.http_status) == ("blocked", 403)
    assert seen == ["a", "a", "a", "b", "c", "c", "c"] and fetcher.requests == 7
    # Retry-After honoured (7 s), then 2 * 2^1; a fresh request waits out the 1.5 s pace; 403s back off 2, 4 s.
    assert sleeps == [7.0, 4, 1.5, 1.5, 2, 4] and fetcher.waits == [7.0, 4, 1.5, 1.5, 2, 4]
    assert "Mozilla" in headers["user-agent"] and headers["referer"].startswith("https://www.nseindia.com")

    _flag(True)
    d = date(2010, 5, 3)
    archive = Archive()
    name = archive.put("cm", d, cm_legacy(d, [_row("HBQB1", 77)]))
    archive.blocked.add(name)
    archive.blocked.add(bhavcopy.candidate_files("cm", d)[1][1])
    summary = _backfill(archive, d, d, segments=["cm"])
    assert summary.blocked == 1 and "upload the file" in _state("cm", d).error
    result = _run(hb1.ingest_upload(_session_factory, "cm", d, archive.files[name], name))
    assert result["status"] == "done" and result["origin"] == "upload" and _state("cm", d).attempts == 2


def test_flag_off_refuses_everything_and_nothing_is_fetched():
    _flag(False)
    assert hb1.FLAG in controls.FEATURE_FLAGS and hb1.FLAG in controls.DEFAULT_OFF_FLAGS
    archive = Archive()
    with pytest.raises(hb1.BackfillConfigError, match="flag is off"):
        _backfill(archive, date(2011, 1, 3), date(2011, 1, 7))
    with pytest.raises(hb1.BackfillConfigError, match="flag is off"):
        _run(hb1.ingest_upload(_session_factory, "cm", date(2011, 1, 3), b"x", "x.csv"))
    assert archive.calls == [] and _state("cm", date(2011, 1, 3)) is None
    _flag(True)


def test_nightly_job_fetches_today_and_catches_up_and_the_worker_gates_it(monkeypatch):
    from app.core import config
    from app.workers.trading_worker import TradingWorker

    monkeypatch.setattr(config, "HISTORY_BACKFILL_CATCHUP_DAYS", 3)
    now = datetime(2012, 3, 7, 19, 30, tzinfo=IST)                          # Wednesday evening
    assert hb1.nightly_due(now.replace(hour=18), None) is None
    assert hb1.nightly_due(now, None) == date(2012, 3, 7) and hb1.nightly_due(now, date(2012, 3, 7)) is None
    _flag(True)
    archive = Archive()
    for d in (date(2012, 3, 5), date(2012, 3, 7)):                          # Tuesday's file is late
        archive.put("cm", d, cm_legacy(d, [_row("HBQE1", 10 + d.day)]))
    summary = _run(hb1.run_nightly(_session_factory, archive, now=now))
    assert summary.done == 2 and summary.days == 4                          # Sun 4th .. Wed 7th, weekends asked too
    assert _state("cm", date(2012, 3, 6)).status == "no_file"
    archive.put("cm", date(2012, 3, 6), cm_legacy(date(2012, 3, 6), [_row("HBQE1", 16)]))
    again = _run(hb1.run_nightly(_session_factory, archive, now=now + timedelta(days=1)))
    assert _state("cm", date(2012, 3, 6)).status == "done" and again.skipped >= 2   # the late file is picked up next night

    started = []

    async def fake_nightly(factory, fetcher, *, now=None):
        started.append(now)
        return hb1.BackfillSummary()

    class FakeFetcher:
        async def aclose(self):
            pass
    monkeypatch.setattr(hb1, "run_nightly", fake_nightly)
    monkeypatch.setattr("app.market_lake.nse_fetch.PoliteFetcher", FakeFetcher)

    def worker_turn(flag_on, when, last=None):
        _flag(flag_on)

        async def go():
            worker = TradingWorker(_session_factory)
            worker._last_history_eod_day = last
            async with _session_factory() as session:
                ok = await worker._maybe_start_history_eod(session, when)
            if worker._history_eod_task is not None:
                await worker._history_eod_task
            return ok, worker._last_history_eod_day
        return _run(go())
    assert worker_turn(False, now) == (False, None) and started == []
    assert worker_turn(True, now.replace(hour=17)) == (False, None)
    assert worker_turn(True, now) == (True, date(2012, 3, 7)) and len(started) == 1
    assert worker_turn(True, now, last=date(2012, 3, 7))[0] is False and len(started) == 1


# --- admin --------------------------------------------------------------------------------------------------------
def _admin(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()

    async def promote():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            user.role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    enable_mfa(headers)
    return headers


def test_admin_progress_and_uploads_are_super_admin_only_and_flag_gated():
    plain = {"Authorization": f"Bearer {_register('hb1-plain@example.com')}"}
    assert client.get("/api/admin/history-backfill/progress", headers=plain).status_code == 403
    headers = _admin("hb1-admin@example.com")
    d = date(2013, 4, 1)
    files = {"file": ("cm.csv", cm_legacy(d, [_row("HBQA1", 33, isin="INE0HBQA0012")]).encode(), "text/csv")}
    _flag(False)
    assert client.post("/api/admin/history-backfill/upload", headers=headers, data={"segment": "cm", "trade_date": d.isoformat()},
                       files=files).status_code == 409
    _flag(True)
    up = client.post("/api/admin/history-backfill/upload", headers=headers, data={"segment": "cm", "trade_date": d.isoformat()},
                     files=files)
    assert up.status_code == 200, up.text
    assert up.json()["status"] == "done" and up.json()["rows_loaded"] == 1
    assert client.post("/api/admin/history-backfill/upload", headers=headers, data={"segment": "xx", "trade_date": d.isoformat()},
                       files=files).status_code == 422
    ca = client.post("/api/admin/history-backfill/corporate-actions", headers=headers,
                     files={"file": ("ca.csv", b"isin,ex_date,action,ratio_new,ratio_old,amount\nINE0HBQA0012,2013-04-01,DIVIDEND,,,2.5\n", "text/csv")})
    assert ca.status_code == 200 and ca.json()["inserted"] == 1
    assert _count(MdCorporateActionRecord, MdCorporateActionRecord.symbol == "HBQA1", MdCorporateActionRecord.action == "DIVIDEND") == 1
    hol = client.post("/api/admin/history-backfill/holidays", headers=headers, data={"exchange": "NSE"},
                      files={"file": ("h.csv", b"date,description\n2014-10-02,Gandhi Jayanti (test)\n", "text/csv")})
    assert hol.status_code == 200 and hol.json()["added"] == 1
    assert _count(MarketHolidayRecord, MarketHolidayRecord.holiday_date == date(2014, 10, 2)) == 1
    body = client.get("/api/admin/history-backfill/progress", headers=headers).json()
    assert body["flag_on"] is True and body["segments"]["cm"]["by_status"]["done"] >= 1
    assert "candles" not in str(body)
    # Daily bars are shared (Tier C): any signed-in user reads them through the history API.
    hist = client.get("/api/market-data/history", headers=plain, params={
        "instrument_key": "NSE:HBQA1", "timeframe": "day", "start": datetime(2013, 3, 31, tzinfo=timezone.utc).isoformat(),
        "end": datetime(2013, 4, 2, tzinfo=timezone.utc).isoformat(), "adjusted": "false"})
    assert hist.status_code == 200 and [c["close"] for c in hist.json()["candles"]] == [33.0]


def test_corporate_action_purpose_parser():
    parse = corporate_actions.parse_purpose
    assert parse("Bonus 1:2", 10) == ([("BONUS", 3.0, 2.0, None)], None)
    assert parse("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", 10) == ([("SPLIT", 10.0, 1.0, None)], None)
    assert parse("Rights 1:5 @ Premium Rs 90/-", 10) == ([("RIGHTS", 1.0, 5.0, 100.0)], None)
    assert parse("Rights 1:5 @ Premium Rs 90/-", None)[1] is not None             # needs the face value: reported
    assert parse("Interim Dividend - Rs 2 Per Share / Special Dividend - Rs 3.50 Per Share", 1) == ([("DIVIDEND", None, None, 5.5)], None)
    assert parse("Dividend - 50%", 2) == ([("DIVIDEND", None, None, 1.0)], None)
    assert parse("Annual General Meeting", 1) == ([], None)
    assert "demerger" in parse("Demerger", 1)[1]


# --- review fixes -------------------------------------------------------------------------------------------------
def _load_ca(content):
    async def go():
        async with _session_factory() as session:
            return await corporate_actions.load_csv(session, content)
    return _run(go())


def _isin_closes(isin, start, end, adjusted=True):
    async def go():
        async with _session_factory() as session:
            return [(b.instrument_key, round(b.close, 4)) for b in await by_isin.isin_candles(session, isin, start, end, adjusted=adjusted)]
    return _run(go())


def test_a_reused_symbols_later_actions_never_reach_an_older_company():
    _flag(True)
    old_days, new_days = _weekdays(date(2014, 1, 6), 3), _weekdays(date(2014, 9, 1), 3)
    archive = Archive()
    for i, d in enumerate(old_days):
        archive.put("cm", d, cm_legacy(d, [_row("HBQXYZ", 100 + i, isin="INE0HBQA1018")]))      # company A, delisted later
    for i, d in enumerate(new_days):
        archive.put("cm", d, cm_legacy(d, [_row("HBQXYZ", 10 + i, isin="INE0HBQB1016")]))       # company B reuses the symbol
    _backfill(archive, old_days[0], new_days[-1], segments=["cm"], include_weekends=False)
    _load_ca(f"symbol,ex_date,action,ratio_new,ratio_old,amount\nHBQXYZ,{new_days[1]},SPLIT,10,1,\n".encode())
    assert [c for _, c in _isin_closes("INE0HBQA1018", old_days[0], new_days[-1])] == [100, 101, 102]   # not 10, 10.1, 10.2
    assert [c for _, c in _isin_closes("INE0HBQB1016", old_days[0], new_days[-1])] == [1.0, 11, 12]     # B's own split applies


def test_a_new_isin_at_a_split_and_a_rename_back_are_stitched_as_one_company():
    _flag(True)
    days = _weekdays(date(2015, 3, 2), 8)              # Mon 2 .. Wed 11 (a weekend in between)
    plan = [("HBQHD", "INE0HBQD1012", 2000), ("HBQHD", "INE0HBQD1012", 2010),         # the old ISIN
            ("HBQHD", "INE0HBQD2010", 1005), ("HBQHD", "INE0HBQD2010", 1010),         # split 1:2 -> new ISIN, same symbol
            ("HBQHDX", "INE0HBQD2010", 1015), ("HBQHDX", "INE0HBQD2010", 1020),       # renamed ...
            ("HBQHD", "INE0HBQD2010", 1025), ("HBQHD", "INE0HBQD2010", 1030)]         # ... and back
    archive = Archive()
    for d, (sym, isin, close) in zip(days, plan):
        archive.put("cm", d, cm_legacy(d, [_row(sym, close, isin=isin)]))
    _backfill(archive, days[0], days[-1], segments=["cm"], include_weekends=False)
    _load_ca(f"symbol,ex_date,action,ratio_new,ratio_old,amount\nHBQHD,{days[2]},SPLIT,2,1,\n".encode())

    async def runs():
        async with _session_factory() as session:
            return [(r.isin[-5:], r.symbol, r.first_date, r.last_date) for r in await by_isin.company_runs(session, "INE0HBQD1012")]
    assert _run(runs()) == [("D1012", "HBQHD", days[0], days[1]), ("D2010", "HBQHD", days[2], days[3]),
                            ("D2010", "HBQHDX", days[4], days[5]), ("D2010", "HBQHD", days[6], days[7])]
    closes = _isin_closes("INE0HBQD1012", days[0], days[-1])
    assert [c for _, c in closes] == [1000, 1005, 1005, 1010, 1015, 1020, 1025, 1030]       # adjusted across the ISIN change
    assert _isin_closes("INE0HBQD2010", days[0], days[-1]) == closes                         # the same company either way

    # Out of order: a day uploaded between two runs of the same symbol joins them into one run.
    gap_days = _weekdays(date(2015, 4, 6), 3)
    for d in (gap_days[0], gap_days[2], gap_days[1]):
        _run(hb1.ingest_upload(_session_factory, "cm", d, cm_legacy(d, [_row("HBQJN", 50, isin="INE0HBQJ1019")]).encode(), "j.csv"))
    assert _count(MdIsinSymbolRecord, MdIsinSymbolRecord.isin == "INE0HBQJ1019") == 1


def test_past_holiday_uploads_cannot_close_live_days_or_switch_exchange_and_can_be_undone():
    from app.market_data.holiday_import import import_holidays, remove_holidays
    content = (b"date,description,exchange\n2016-01-26,Republic Day (test),NSE\n2016-03-07,Mahashivratri (test),MCX\n"
               b"2099-01-26,Far future (test),NSE\n")

    async def go(fn, **kw):
        async with _session_factory() as session:
            return await fn(session, content, exchange="NSE", **kw)
    report = _run(go(import_holidays, today=date(2026, 10, 11)))
    assert report.added == 1 and len(report.unreadable) == 2
    assert any("MCX" in u for u in report.unreadable) and any("2099-01-26" in u for u in report.unreadable)
    assert _count(MarketHolidayRecord, MarketHolidayRecord.holiday_date == date(2099, 1, 26)) == 0
    assert _count(MarketHolidayRecord, MarketHolidayRecord.holiday_date == date(2016, 3, 7)) == 0
    assert _run(go(import_holidays, today=date(2026, 10, 11), allow_future=True)).added == 1          # explicit only
    undone = _run(go(remove_holidays))
    assert undone.removed == 2 and _count(MarketHolidayRecord, MarketHolidayRecord.holiday_date.in_([date(2016, 1, 26), date(2099, 1, 26)])) == 0


def test_uploads_wait_for_a_running_job_and_a_state_row_race_is_not_an_error(monkeypatch):
    _flag(True)
    d = date(2016, 6, 6)

    async def busy(session, take):
        return False
    monkeypatch.setattr(hb1, "_lock", busy)
    with pytest.raises(hb1.BackfillConfigError, match="in progress"):
        _run(hb1.ingest_upload(_session_factory, "cm", d, b"x", "x.csv"))
    monkeypatch.undo()

    async def race():
        async with _session_factory() as session:
            session.add(F(segment="cm", trade_date=d, status="running", attempts=1, inserted=0, corrected=0, skipped=0, quality_events=0))
            await session.commit()

        class Blind:                                    # a writer that did not see the other's row yet
            def __init__(self):
                self.inner = _session_factory()

            async def __aenter__(self):
                session = await self.inner.__aenter__()

                async def scalar(*_a, **_k):
                    return None
                session.scalar = scalar
                return session

            async def __aexit__(self, *exc):
                return await self.inner.__aexit__(*exc)
        return await hb1._start(lambda: Blind(), "cm", d, skip_done=True, retry_no_file=False, today=date(2026, 1, 1))
    assert _run(race()) == (None, "running")


def test_fo_volume_is_withheld_until_the_unit_is_settled(monkeypatch):
    from app.core import config
    _flag(True)
    d1, d2 = date(2016, 8, 1), date(2016, 8, 2)
    archive = Archive()
    archive.put("fo", d1, fo_legacy(d1, [("FUTSTK", "HBQV1", date(2016, 8, 25), 0, "XX", 100, 77, 900)]))
    archive.put("fo", d2, fo_legacy(d2, [("FUTSTK", "HBQV1", date(2016, 8, 25), 0, "XX", 101, 88, 950)]))
    _backfill(archive, d1, d1, segments=["fo"])
    assert _state("fo", d1).volume_unit == "withheld" and "withheld" in _state("fo", d1).note
    monkeypatch.setattr(config, "HISTORY_BACKFILL_FO_VOLUME", "as_printed")
    _backfill(archive, d2, d2, segments=["fo"])
    assert _state("fo", d2).volume_unit == "contracts"

    async def vols():
        async with _session_factory() as session:
            rows = await session.scalars(select(MdCandleRecord).where(MdCandleRecord.instrument_key == "NFO:HBQV1:FUT:2016-08-25"))
            return sorted((r.ts, r.volume, r.oi) for r in rows)
    assert [(v, oi) for _, v, oi in _run(vols())] == [(0, 900), (88, 950)]
    assert bhavcopy.parse_fo(fo_udiff(date(2024, 7, 8), [("STF", "HBQV1", date(2024, 7, 25), "", "", 1, 1, 1)])).volume_unit == "unconfirmed"


def test_a_zip_that_would_unpack_too_large_is_refused_before_inflating(monkeypatch):
    monkeypatch.setattr(bhavcopy, "MAX_CSV_BYTES", 1000)
    big = zipped("cm.csv", cm_legacy(date(2016, 1, 1), [_row(f"HBQZ{i}", 10) for i in range(100)]))
    with pytest.raises(BhavcopyRefused, match="unpack"):
        bhavcopy.decode(big)
    assert bhavcopy.decode(zipped("cm.csv", "a,b\n1,2\n")) == "a,b\n1,2\n"


def test_weekends_are_probed_with_the_cash_file_only_and_a_special_session_loads():
    _flag(True)
    budget_saturday, sunday = date(2020, 2, 1), date(2020, 2, 2)
    archive = Archive()
    archive.put("cm", budget_saturday, cm_legacy(budget_saturday, [_row("HBQSAT", 300)]))
    archive.put("index", budget_saturday, index_file(budget_saturday, [("HBQ Saturday Index", 1, 2, 0.5, 1.5)]))
    summary = _backfill(archive, budget_saturday, sunday)
    assert _state("cm", budget_saturday).status == "done" and _state("index", budget_saturday).status == "done"
    assert _state("cm", sunday).status == "no_file" and _state("cm", sunday).note.startswith("weekend")
    assert _state("index", sunday) is None and _state("fo", sunday) is None             # nothing else asked on a closed day
    sunday_calls = [c.rsplit("/", 1)[-1] for c in archive.calls if "02022020" in c or "20200202" in c or "02FEB2020" in c]
    assert sunday_calls and all(n.startswith(("cm", "BhavCopy_NSE_CM")) for n in sunday_calls)       # cash only
    assert summary.gaps == []
    skipped = _backfill(Archive(), date(2020, 2, 8), date(2020, 2, 9), include_weekends=False)
    assert skipped.days == 0 and _state("cm", date(2020, 2, 8)) is None


def test_stopping_the_worker_cancels_the_nightly_job():
    from app.workers.trading_worker import TradingWorker

    async def go():
        worker = TradingWorker(_session_factory)
        worker._history_eod_task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        worker.stop()
        with pytest.raises(asyncio.CancelledError):
            await worker._history_eod_task
        return worker._history_eod_task.cancelled()
    assert _run(go()) is True
