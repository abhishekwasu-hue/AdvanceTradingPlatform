"""M66 EN-2: the engine data adapter (app/engine_provider/frames.py, sources.py) on synthetic bars only.

Covered: closed bars only (no bar after the read time, a running bucket never handed in), India-time START / END from
ATP's END-labelled lake rows and the broker's START-labelled bars, 15m / 1h buckets from the session open (cut short at
the close), gaps listed and never filled (missing bars, incomplete buckets, missing source), holidays and early closes,
the futures volume (missing = None + a gap, a real zero stays zero, no contract of that day = a gap, auction bars
skipped), closing-auction and auction-extended Daily marks, the data-problem tokens, the admin's gap list, the sources'
broker / futures / probe paths with fakes, and deterministic output. All bars are made up (prices around 100).
"""
import asyncio
import random
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from app.engine_provider import frames as F
from app.engine_provider import sources as S
from app.engine_provider.contract import EarlyClose, EngineRequest, EngineResult

IST = F.IST
D1 = date(2030, 1, 7)          # a made-up Monday
D2 = date(2030, 1, 8)
D3 = date(2030, 1, 9)
KEY = "NSE_INDEX:TESTINDEX"
STOCK = "NSE:TESTSTOCK"
M = timedelta(minutes=1)


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=IST)


def cal(holidays=(), early=()):
    return F.market_calendar("NSE", holidays, date(2030, 1, 1), date(2030, 1, 31), early)


def minute_rows(day: date, frm=(9, 15), to=(15, 30), skip=(), base=100.0, volume=0):
    """END-labelled lake rows (UTC, version 1) of one day's 1-minute bars [frm, to)."""
    rows, t, end = [], at(day, *frm), at(day, *to)
    i = 0
    while t < end:
        if t not in skip:
            p = base + (i % 7) * 0.5
            rows.append(((t + M).astimezone(UTC), 1, p, p + 1.0, p - 1.0, p + 0.25, volume))
        t += M
        i += 1
    return rows


def minutes_of(rows, c, key=KEY, source="stored"):
    return F.from_lake_rows(rows, "1m", c, source=source, own_volume=not F.is_index_key(key))


def fut_bars(day: date, frm=(9, 15), to=(15, 30), skip=(), volume=10.0, none_at=()):
    out, t = [], at(day, *frm)
    while t < at(day, *to):
        if t not in skip:
            out.append(F.RawBar(t, t + M, 100.0, 101.0, 99.0, 100.5, None if t in none_at else volume))
        t += M
    return tuple(out)


def data(rows, c, *, key=KEY, futures=(), expected=True, daily=None, extended=frozenset()):
    return F.SymbolData(symbol=key, exchange="NSE", minutes=minutes_of(rows, c, key), daily=daily, extended_days=extended,
                        futures=tuple(futures), futures_expected=expected and F.is_index_key(key))


STARTS = {"1m": at(D1, 9, 15), "5m": at(D1, 9, 15), "15m": at(D1, 9, 15), "1h": at(D1, 9, 15), "1d": at(D1, 9, 15)}


# --- closed bars only ------------------------------------------------------------------------------------------------

def test_only_closed_bars_and_as_of_is_the_signal_bars_close():
    c = cal()
    rows = minute_rows(D1, to=(10, 7))                                     # the 10:07 minute is still running
    contract = F.FuturesContract("TESTFUT", date(2030, 1, 31), fut_bars(D1, to=(10, 7)))
    built = F.build_request(data(rows, c, futures=[contract]), ["5m", "15m", "1h"], "5m", at(D1, 10, 7), STARTS, c)
    req = built.request
    assert isinstance(req, EngineRequest)
    assert req.as_of == at(D1, 10, 5)
    for frame in req.frames.values():
        assert all(b.end <= req.as_of for b in frame.bars)
    assert req.frames["5m"].bars[-1].start == at(D1, 10, 0)
    assert req.frames["15m"].bars[-1].end == at(D1, 10, 0)                # 10:00-10:15 has not closed
    assert req.frames["1h"].bars == ()                                    # 09:15-10:15 has not closed
    assert built.gaps == ()                                               # nothing missing in the closed window


def test_a_running_broker_bar_is_never_handed_in():
    c = cal()
    bars = [SimpleNamespace(timestamp=at(D1, 9, 15) + i * timedelta(minutes=5), open=100, high=101, low=99, close=100.5, volume=5)
            for i in range(4)]                                            # the last one 09:30-09:35 is running at 09:33
    native = F.from_broker_bars(bars, "5m", c, source="your broker")
    d = F.SymbolData(symbol=STOCK, exchange="NSE", native={"5m": native})
    built = F.build_request(d, ["5m"], "5m", at(D1, 9, 33), STARTS, c)
    assert [b.start for b in built.request.frames["5m"].bars] == [at(D1, 9, 15), at(D1, 9, 20), at(D1, 9, 25)]
    assert built.request.as_of == at(D1, 9, 30)


# --- India-time boundaries -------------------------------------------------------------------------------------------

def test_lake_rows_become_ist_start_and_end_and_buckets_count_from_the_open():
    c = cal()
    rows = minute_rows(D1)
    d = data(rows, c, expected=False)
    built = F.build_request(d, ["5m", "15m", "1h"], "5m", at(D1, 16, 0), STARTS, c)
    one_m = minutes_of(rows, c).bars[0]
    assert one_m.start == at(D1, 9, 15) and one_m.end == at(D1, 9, 16) and one_m.start.utcoffset() == timedelta(hours=5, minutes=30)
    h1 = built.request.frames["1h"].bars
    assert len(h1) == 7 and h1[0].start == at(D1, 9, 15) and h1[-1].start == at(D1, 15, 15) and h1[-1].end == at(D1, 15, 30)
    assert len(built.request.frames["15m"].bars) == 25
    assert len(built.request.frames["5m"].bars) == 75
    assert all(b.closing_auction is False for b in built.request.frames["5m"].bars)
    assert built.gaps == ()


def test_daily_bar_spans_the_session_and_a_broker_daily_uses_the_early_close():
    c = cal(early=[EarlyClose(D2, time(13, 0))])
    lake = F.from_lake_rows([(at(D1, 15, 30).astimezone(UTC), 1, 100.0, 102.0, 98.0, 101.0, 0)], "1d", c, source="s", own_volume=False)
    assert lake.bars[0].start == at(D1, 9, 15) and lake.bars[0].end == at(D1, 15, 30) and lake.bars[0].volume is None
    broker = F.from_broker_bars([SimpleNamespace(timestamp=datetime.combine(D2, time(0, 0), tzinfo=IST), open=1, high=2, low=0.5,
                                                 close=1.5, volume=7)], "1d", c, source="b")
    assert broker.bars[0].start == at(D2, 9, 15) and broker.bars[0].end == at(D2, 13, 0) and broker.bars[0].volume == 7.0


# --- gaps: reported, never filled ------------------------------------------------------------------------------------

def test_missing_minutes_are_gaps_and_nothing_is_filled():
    c = cal()
    hole = {at(D1, 10, 0) + i * M for i in range(5)}                      # 10:00-10:04 absent
    rows = minute_rows(D1, to=(11, 0), skip=hole)
    built = F.build_request(data(rows, c, expected=False), ["5m", "15m"], "5m", at(D1, 11, 0), STARTS, c)
    five = built.request.frames["5m"].bars
    assert at(D1, 10, 0) not in {b.start for b in five} and len(five) == 20
    by = {(g.kind, g.timeframe): g for g in built.gaps}
    miss = by[(F.MISSING_BARS, "5m")]
    assert (miss.count, miss.first, miss.last) == (1, at(D1, 10, 0), at(D1, 10, 0))
    inc = by[(F.INCOMPLETE_BARS, "15m")]
    assert inc.first == at(D1, 10, 0) and "10 of 15 minutes" in inc.text
    assert set(built.data_problems) == {"missing_bars:5m", "incomplete_bars:15m"}


def test_a_run_of_missing_days_and_a_missing_source():
    c = cal()
    rows = minute_rows(D1) + minute_rows(D3)                              # D2 absent in the store
    d = data(rows, c, expected=False)
    built = F.build_request(d, ["1h", "1d"], "1h", at(D3, 16, 0), STARTS, c)
    gap = [g for g in built.gaps if g.kind == F.MISSING_BARS and g.timeframe == "1h"]
    assert len(gap) == 1 and gap[0].count == 7 and gap[0].first == at(D2, 9, 15) and gap[0].last == at(D2, 15, 15)
    src = [g for g in built.gaps if g.kind == F.MISSING_SOURCE]
    assert [g.timeframe for g in src] == ["1d"] and built.request.frames["1d"].bars == ()
    assert "no source holds these bars" in src[0].text


def test_no_signal_bar_means_no_request_but_every_gap_is_listed():
    c = cal()
    d = F.SymbolData(symbol=KEY, exchange="NSE", futures_expected=True, sources_tried=("your broker",))
    built = F.build_request(d, ["5m", "1h"], "5m", at(D1, 11, 0), STARTS, c)
    assert built.request is None
    assert {g.token for g in built.gaps} == {"missing_source:5m", "missing_source:1h"}
    assert "looked at: your broker" in built.gaps[0].text


def test_an_invalid_stored_bar_is_left_out_and_reported():
    c = cal()
    rows = minute_rows(D1, to=(9, 25))
    rows[2] = (rows[2][0], 1, 100.0, 99.0, 101.0, 100.0, 0)                # low above high
    d = F.SymbolData(symbol=KEY, exchange="NSE", native={"1m": minutes_of(rows, c)})
    built = F.build_request(d, ["1m"], "1m", at(D1, 9, 25), STARTS, c)
    assert len(built.request.frames["1m"].bars) == 9
    assert {g.token for g in built.gaps} == {"invalid_bars:1m", "missing_bars:1m"}


# --- holidays and early closes ---------------------------------------------------------------------------------------

def test_a_holiday_expects_no_bars_and_an_early_close_cuts_the_day():
    c = cal(holidays=[D2], early=[EarlyClose(D3, time(13, 0))])
    rows = minute_rows(D1) + minute_rows(D3, to=(13, 10))                 # 13:00-13:09 after the early close
    built = F.build_request(data(rows, c, expected=False), ["1h", "5m"], "5m", at(D3, 16, 0), STARTS, c)
    assert built.gaps == ()
    h1 = [b for b in built.request.frames["1h"].bars if b.start.date() == D3]
    cont = [b for b in h1 if not b.closing_auction]
    assert cont[-1].start == at(D3, 12, 15) and cont[-1].end == at(D3, 13, 0)
    auction5 = [b for b in built.request.frames["5m"].bars if b.closing_auction]
    assert [b.start for b in auction5] == [at(D3, 13, 0), at(D3, 13, 5)]
    assert built.request.calendar.holidays == (D2,) and built.request.calendar.early_closes[0].close == time(13, 0)
    assert F.expected_starts("1d", at(D1, 9, 15), at(D3, 16, 0), c) == (at(D1, 9, 15), at(D3, 9, 15))


def test_calendar_keeps_only_the_window_sorted():
    c = F.market_calendar("NSE", [date(2030, 2, 1), D3, D2], D1, D3)
    assert c.holidays == (D2, D3) and c.timezone == "Asia/Kolkata" and c.regular_open == time(9, 15)


# --- futures volume --------------------------------------------------------------------------------------------------

def test_futures_volume_missing_is_none_and_a_gap_and_a_zero_stays_zero():
    c = cal()
    rows = minute_rows(D1, to=(10, 0))
    hole = {at(D1, 9, 20) + i * M for i in range(2)}                      # part of the 09:20 bar absent
    fut = fut_bars(D1, to=(10, 0), skip=hole, none_at={at(D1, 9, 30)})
    fut = tuple(F.RawBar(f.start, f.end, f.open, f.high, f.low, f.close, 0.0) if at(D1, 9, 40) <= f.start < at(D1, 9, 45) else f for f in fut)
    contract = F.FuturesContract("TESTFUT", date(2030, 1, 31), fut)
    built = F.build_request(data(rows, c, futures=[contract]), ["5m"], "5m", at(D1, 10, 0), STARTS, c)
    frame = built.request.frames["5m"]
    vols = dict(zip([b.start for b in frame.bars], frame.futures_volume))
    assert vols[at(D1, 9, 15)] == 50.0
    assert vols[at(D1, 9, 20)] is None and vols[at(D1, 9, 30)] is None
    assert vols[at(D1, 9, 40)] == 0.0                                     # a real zero is not a gap
    assert all(b.volume is None for b in frame.bars)                       # an index has no volume of its own
    gaps = [g for g in built.gaps if g.kind == F.MISSING_FUTURES_VOLUME]
    assert [(g.first, g.count) for g in gaps] == [(at(D1, 9, 20), 1), (at(D1, 9, 30), 1)]
    assert "only part of the bar" in gaps[0].text and "no volume" in gaps[1].text
    assert "missing_futures_volume:5m" in built.data_problems


def test_no_futures_fetched_or_no_contract_of_that_day_is_a_gap_never_a_guess():
    c = cal()
    rows = minute_rows(D1, to=(9, 30))
    none = F.build_request(data(rows, c), ["5m"], "5m", at(D1, 9, 30), STARTS, c)
    assert none.request.frames["5m"].futures_volume == (None, None, None)
    assert none.gaps[0].count == 3 and "no futures bars were fetched" in none.gaps[0].text
    later = F.FuturesContract("TESTFUT", date(2030, 2, 26), fut_bars(D1, to=(9, 30)))   # its near-month stretch is unproven on D1
    built = F.build_request(data(rows, c, futures=[later]), ["5m"], "5m", at(D1, 9, 30), STARTS, c)
    assert built.request.frames["5m"].futures_volume == (None, None, None)
    assert "no near-month contract" in built.gaps[0].text
    known = dataclasses_replace(later, valid_from=D1)
    ok = F.build_request(data(rows, c, futures=[known]), ["5m"], "5m", at(D1, 9, 30), STARTS, c)
    assert ok.request.frames["5m"].futures_volume == (50.0, 50.0, 50.0) and ok.gaps == ()


def dataclasses_replace(obj, **kw):
    import dataclasses
    return dataclasses.replace(obj, **kw)


def test_two_contracts_are_never_summed():
    c = cal()
    rows = minute_rows(D1, to=(9, 20))
    near = F.FuturesContract("NEAR", date(2030, 1, 31), fut_bars(D1, to=(9, 20), volume=1.0))
    nxt = F.FuturesContract("NEXT", date(2030, 2, 28), fut_bars(D1, to=(9, 20), volume=1000.0), valid_from=date(2030, 2, 1))
    built = F.build_request(data(rows, c, futures=[nxt, near]), ["5m"], "5m", at(D1, 9, 20), STARTS, c)
    assert built.request.frames["5m"].futures_volume == (5.0,)


def test_auction_bars_carry_the_mark_and_no_futures_gap():
    c = cal()
    rows = minute_rows(D1, frm=(15, 0), to=(15, 40))
    contract = F.FuturesContract("TESTFUT", date(2030, 1, 31), fut_bars(D1, frm=(15, 0), to=(15, 30)))
    starts = {**STARTS, "5m": at(D1, 15, 0)}
    built = F.build_request(data(rows, c, futures=[contract]), ["5m"], "5m", at(D1, 16, 0), starts, c)
    frame = built.request.frames["5m"]
    marks = [(b.start.time(), b.closing_auction) for b in frame.bars]
    assert marks[-2:] == [(time(15, 30), True), (time(15, 35), True)] and marks[0] == (time(15, 0), False)
    assert frame.futures_volume[-2:] == (None, None) and built.gaps == ()


def test_a_flagged_post_close_minute_is_marked_not_dropped():
    c = cal()
    rows = minute_rows(D1, to=(9, 17))
    flagged = F.from_lake_rows(rows, "1m", c, source="s", post_close=[rows[1][0]])
    assert [b.closing_auction for b in flagged.bars] == [False, True]


# --- auction-extended Daily ------------------------------------------------------------------------------------------

def test_daily_extended_session_mark():
    c = cal()
    rows = minute_rows(D1) + minute_rows(D2) + minute_rows(D3, to=(12, 0))
    hi = max(r[3] for r in rows)
    daily_rows = [(at(D1, 15, 30).astimezone(UTC), 1, 100.0, hi + 5.0, 99.0, 100.0, 0),      # high beyond the continuous minutes
                  (at(D2, 15, 30).astimezone(UTC), 1, 100.0, hi, 99.0, 100.0, 0),            # inside them
                  (at(D3, 15, 30).astimezone(UTC), 1, 100.0, hi, 99.0, 100.0, 0)]            # minutes incomplete
    daily = F.from_lake_rows(daily_rows, "1d", c, source="s", own_volume=False)
    built = F.build_request(data(rows, c, expected=False, daily=daily), ["1d", "1h"], "1d", at(D3, 16, 0), STARTS, c)
    marks = [b.extended_session for b in built.request.frames["1d"].bars]
    assert marks == [True, False, None]
    flagged = F.build_request(data(rows, c, expected=False, daily=daily, extended=frozenset({D2})), ["1d"], "1d", at(D3, 16, 0), STARTS, c)
    assert [b.extended_session for b in flagged.request.frames["1d"].bars] == [True, True, None]
    assert all(b.closing_auction is None for b in flagged.request.frames["1d"].bars)


# --- data problems, the gap list, determinism ------------------------------------------------------------------------

def test_merge_problems_and_gap_report_in_plain_words():
    c = cal()
    rows = minute_rows(D1, to=(10, 0), skip={at(D1, 9, 30)})
    built = F.build_request(data(rows, c), ["5m"], "5m", at(D1, 10, 0), STARTS, c)
    result = F.merge_problems(EngineResult(interface=1, version="x", data_problems=("engine_token",)), built.gaps)
    assert result.data_problems == ("engine_token", "incomplete_bars:5m", "missing_futures_volume:5m")
    report = F.gap_report(built.gaps)
    lines = report[KEY]["5m"]
    assert lines[0] == f"{KEY} 5m: 1 bar built from incomplete 1-minute bars, at 07 Jan 2030 09:30 (India time) - 4 of 5 minutes there."
    assert all("None" not in line for line in lines)


def test_output_is_deterministic_whatever_the_input_order():
    c = cal()
    rows = minute_rows(D1, to=(11, 0), skip={at(D1, 10, 3)})
    contract = F.FuturesContract("TESTFUT", date(2030, 1, 31), fut_bars(D1, to=(11, 0), skip={at(D1, 9, 50)}))
    first = F.build_request(data(rows, c, futures=[contract]), ["5m", "15m", "1h"], "5m", at(D1, 11, 0), STARTS, c)
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    shuffled.sort(key=lambda r: r[0])                                     # the lake readers' order: by END
    again = F.build_request(data(shuffled, c, futures=[contract]), ["5m", "15m", "1h"], "5m", at(D1, 11, 0), STARTS, c)
    assert first == again
    assert F.gap_report(first.gaps) == F.gap_report(again.gaps)


def test_the_adapter_never_uses_the_zero_filled_chart_volume():
    import inspect
    for mod in (F, S):
        assert "replace_volume" not in inspect.getsource(mod).replace("`app.market_data.index_volume.replace_volume` is not used", "")


# --- sources (fakes; no network, no database) ------------------------------------------------------------------------

class FakeBroker:
    def __init__(self, bars_by_symbol, fail=()):
        self.bars, self.fail, self.calls = bars_by_symbol, set(fail), []

    async def get_historical_data(self, symbol, exchange, interval, start, end):
        self.calls.append((symbol, exchange, interval))
        if symbol in self.fail:
            raise RuntimeError("broker down")
        return list(self.bars.get((symbol, interval), []))


def _broker_minutes(day, frm=(9, 15), to=(10, 0), volume=3.0):
    t, out = at(day, *frm), []
    while t < at(day, *to):
        out.append(SimpleNamespace(timestamp=t, open=100.0, high=101.0, low=99.0, close=100.5, volume=volume))
        t += M
    return out


def test_broker_failure_is_a_note_never_an_exception():
    c = cal()
    got, notes = asyncio.run(S.broker_bars(FakeBroker({}, fail={"X"}), "X", "NSE", "1m", at(D1, 9, 15), at(D1, 10, 0), c))
    assert got is None and "could not be loaded" in notes[0]


def test_collect_and_probe_with_a_fake_broker_and_store(monkeypatch):
    c = cal()
    lake_rows = minute_rows(D1, skip={at(D1, 11, 0)})

    async def fake_lake(session, key, timeframe, start, end, cal_, *, scope=None):
        if timeframe == "1m":
            return F.from_lake_rows(lake_rows, "1m", cal_, source=S.SHARED_INDEX, own_volume=False), frozenset(), []
        return None, frozenset(), []

    async def fake_near(session, symbol, exchange, on):
        return SimpleNamespace(symbol="TESTFUT", exchange="NFO", expiry=date(2030, 1, 31))

    async def fake_calendar(session, exchange, first, last, early_closes=()):
        return c

    from app.market_data import index_volume
    monkeypatch.setattr(S, "lake_bars", fake_lake)
    monkeypatch.setattr(S, "load_calendar", fake_calendar)
    monkeypatch.setattr(index_volume, "near_future", fake_near)
    monkeypatch.setattr(index_volume, "index_underlying", lambda symbol, exchange: "TESTINDEX")
    broker = FakeBroker({("TEST INDEX", "1min"): _broker_minutes(D1, volume=0.0),
                         ("TESTFUT", "1min"): _broker_minutes(D1, to=(9, 45))})
    starts = {"5m": at(D1, 9, 15), "15m": at(D1, 9, 15), "1d": at(D1, 9, 15)}
    d, notes = asyncio.run(S.collect(None, key=KEY, exchange="NSE", timeframes=["5m", "15m", "1d"], starts=starts, now=at(D1, 16, 0),
                                     cal=c, broker=broker, broker_symbol="TEST INDEX"))
    assert d.minutes.source == S.BROKER and d.minutes_by_timeframe["15m"].source == S.SHARED_INDEX
    assert d.futures_expected and d.futures[0].symbol == "TESTFUT" and d.daily is None
    assert all(b.volume is None for b in d.minutes.bars)                  # the index's broker zero is not a volume
    built = F.build_request(d, ["5m", "15m", "1d"], "5m", at(D1, 16, 0), starts, c)
    assert built.request.frames["5m"].bars[-1].start == at(D1, 9, 55)     # broker minutes end 10:00
    assert len(built.request.frames["15m"].bars) == 3                      # stored index history, cut at as_of 10:00
    out = asyncio.run(S.probe(None, [S.ProbeItem(KEY, "NSE", "TEST INDEX")], timeframes=["5m", "15m", "1d"], signal_timeframe="5m",
                              now=at(D1, 16, 0), starts=starts, broker=broker))
    lines = out["gaps"][KEY]
    assert any("bars missing, from 07 Jan 2030 10:00 to 07 Jan 2030 15:25" in line for line in lines["5m"])  # the broker stopped
    assert any("futures volume missing" in line for line in lines["5m"])  # the future's bars end 09:45
    assert "15m" not in lines                                             # the 11:00 hole is after as_of (10:00)
    assert any("no source holds these bars" in line for line in lines["1d"])
    assert "missing_source:1d" in out["problems"] and out["calendar"] == S.CALENDAR_NOTE


def test_futures_without_a_broker_is_reported(monkeypatch):
    from app.market_data import index_volume

    async def fake_near(session, symbol, exchange, on):
        return SimpleNamespace(symbol="TESTFUT", exchange="NFO", expiry=date(2030, 1, 31))
    monkeypatch.setattr(index_volume, "near_future", fake_near)
    monkeypatch.setattr(index_volume, "index_underlying", lambda symbol, exchange: "TESTINDEX")
    expected, contracts, notes = asyncio.run(S.futures_contracts(None, None, "TEST INDEX", "NSE", at(D1, 9, 15), at(D1, 10, 0), cal()))
    assert expected and contracts == () and "not connected" in notes[0]


@pytest.mark.parametrize("key,index", [(KEY, True), ("BSE_INDEX:X", True), (STOCK, False)])
def test_index_keys(key, index):
    assert F.is_index_key(key) is index


# --- the store, as stored (database) ---------------------------------------------------------------------------------

def test_lake_bars_read_the_owners_rows_as_stored_with_the_dh3_marks(monkeypatch):
    """The private store's rows reach the adapter as stored: a minute flagged post_close is marked, not dropped, and an
    auction-extended Daily is flagged, not clipped; the chart path (dh3 on) still drops / clips them."""
    import uuid

    from app.db.models import MdBarFlagRecord, MdTenantCandleRecord
    from app.market_lake import own_bars
    from tests.test_auth_api import _session_factory
    from tests.test_data_h_lake_backfill import Owner, _run

    owner = Owner(f"m66en2-{uuid.uuid4().hex[:8]}@example.com", monkeypatch, indices=("NIFTY",))
    key, src = "NSE_INDEX:NIFTY50", f"broker:upstox:u{owner.user.id}"
    rows = minute_rows(D1, frm=(15, 25), to=(15, 32))                     # 15:30 and 15:31 start after the close
    day_end = at(D1, 15, 30).astimezone(UTC)

    async def seed():
        async with _session_factory() as session:
            for r in rows:
                session.add(MdTenantCandleRecord(tenant_id=owner.tenant_id, instrument_key=key, timeframe="1m", ts=r[0], source=src,
                                                 version=1, open=r[2], high=r[3], low=r[4], close=r[5], volume=0, ingested_at=r[0]))
            session.add(MdTenantCandleRecord(tenant_id=owner.tenant_id, instrument_key=key, timeframe="day", ts=day_end, source=src,
                                             version=1, open=100, high=120, low=90, close=101, volume=0, ingested_at=day_end))
            for r in rows[-2:]:
                session.add(MdBarFlagRecord(tenant_id=owner.tenant_id, instrument_key=key, timeframe="1m", ts=r[0], kind="post_close",
                                            value=None, detail="test"))
            session.add(MdBarFlagRecord(tenant_id=owner.tenant_id, instrument_key=key, timeframe="day", ts=day_end, kind="auction_high",
                                        value=110, detail="test"))
            await session.commit()
    _run(seed())
    c = cal()
    lo, hi = at(D1, 9, 0), at(D1, 18, 0)

    async def read():
        async with _session_factory() as session:
            m, _, notes = await S.lake_bars(session, key, "1m", lo, hi, c, scope=owner.scope)
            d, ext, _ = await S.lake_bars(session, key, "1d", lo, hi, c, scope=owner.scope)
            chart = await own_bars.fetch_rows(session, owner.scope, key, "1m", lo.astimezone(UTC), hi.astimezone(UTC))
            chart_day = await own_bars.fetch_rows(session, owner.scope, key, "day", lo.astimezone(UTC), hi.astimezone(UTC))
            return m, d, ext, notes, chart, chart_day
    m, d, ext, notes, chart, chart_day = _run(read())
    assert m.source == S.OWN_LAKE and len(m.bars) == 7 and [b.closing_auction for b in m.bars][-3:] == [False, True, True]
    assert all(b.volume is None for b in m.bars)
    assert d.bars[0].high == 120.0 and ext == frozenset({D1})
    assert len(chart) == 5 and float(chart_day[0][3]) == 110.0            # the chart path is unchanged
