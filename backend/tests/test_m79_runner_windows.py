"""M79: the signal engine runner's per-timeframe data windows and the stored history (synthetic bars only).

Covered: the owner default (15m: 90 days blocking + history up to 1825 days; Daily: 400 blocking + history up to 3650;
other timeframes on intraday_days / daily_days as before); the `windows` setting validated (plain-words problems);
`windows: {}` = the old behaviour; a gap in the history part is listed apart and never blocks, a gap in the window
still blocks; the history read once per day and joined to the same source's newer bars gives exactly the bars of one
full read; the broker is never asked for the history; the calendar handed in runs to the end of the next year.
"""
import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.engine_provider import frames as F
from app.engine_provider import sources as S
from app.workers import engine_runner as R

IST = F.IST
D1, D2, D3 = date(2030, 1, 7), date(2030, 1, 8), date(2030, 1, 9)        # made-up Monday .. Wednesday
KEY = "NSE_INDEX:TESTINDEX"
M = timedelta(minutes=1)


def at(day, hh, mm):
    return datetime.combine(day, time(hh, mm), tzinfo=IST)


def cal():
    return F.market_calendar("NSE", (), date(2030, 1, 1), date(2030, 1, 31))


def minute_rows(day, skip=()):
    rows, t, i = [], at(day, 9, 15), 0
    while t < at(day, 15, 30):
        if t not in skip:
            p = 100.0 + (i % 7) * 0.5
            rows.append(((t + M).astimezone(UTC), 1, p, p + 1.0, p - 1.0, p + 0.25, 0))
        t += M
        i += 1
    return rows


def minutes(rows, c, source="stored"):
    return F.from_lake_rows(rows, "1m", c, source=source, own_volume=False)


def run(coro):
    return asyncio.run(coro)


# --- settings ----------------------------------------------------------------------------------------------------------

def test_owner_default_windows():
    cfg = R.parse_settings(1, {"user_id": 5, "symbols": []})
    assert cfg.windows == R.DEFAULT_WINDOWS
    assert cfg.window_days("15m") == (90, 1825)
    assert cfg.window_days("1d") == (400, 3650)
    assert cfg.window_days("5m") == (R.DEFAULTS["intraday_days"],) * 2
    assert cfg.window_days("1m") == (R.DEFAULTS["intraday_days"],) * 2


def test_empty_windows_keep_the_old_days():
    cfg = R.parse_settings(1, {"user_id": 5, "symbols": [], "windows": {}, "intraday_days": 7, "daily_days": 30})
    assert cfg.window_days("15m") == (7, 7) and cfg.window_days("1d") == (30, 30)


@pytest.mark.parametrize("windows, words", [
    ([], "not a set of values"),
    ({"2m": {"days": 5}}, "not one ATP builds"),
    ({"15m": {"days": 5, "back": 9}}, "only days and history_days"),
    ({"15m": {"days": True}}, "whole number"),
    ({"15m": {"days": 0}}, "whole number"),
    ({"15m": {"history_days": 3651}}, "whole number"),
    ({"15m": {"days": 10.5}}, "whole number"),
    ({"15m": {"days": 90, "history_days": 30}}, "at least its days"),
])
def test_bad_windows_are_refused_in_plain_words(windows, words):
    with pytest.raises(R.SettingsProblem, match=words):
        R.parse_settings(1, {"user_id": 5, "symbols": [], "windows": windows})


def test_history_starts_reach_back_and_window_starts_do_not():
    c = F.market_calendar("NSE", (), date(2024, 1, 1), date(2031, 12, 31))
    cfg = R.parse_settings(1, {"user_id": 5, "symbols": []})
    as_of = at(D3, 10, 0)
    req = R.window_starts(c, ["5m", "15m", "1d"], as_of, cfg)
    back = R.history_starts(c, ["5m", "15m", "1d"], as_of, cfg)
    assert back["5m"] == req["5m"]
    assert back["15m"] < req["15m"] and back["1d"] < req["1d"]
    assert (D3 - req["15m"].date()).days >= 90 and (D3 - back["15m"].date()).days >= 1825


def test_calendar_runs_to_the_end_of_next_year():
    assert R.calendar_last(datetime(2030, 3, 4, 5, tzinfo=UTC)) == date(2031, 12, 31)


# --- frames: the history part never blocks -------------------------------------------------------------------------

def test_history_gap_is_listed_apart_and_never_blocks():
    c = cal()
    rows = minute_rows(D1, skip={at(D1, 10, 0)}) + minute_rows(D2) + minute_rows(D3)
    d = F.SymbolData(symbol=KEY, exchange="NSE", minutes=minutes(rows, c))
    starts = {"15m": at(D1, 9, 15)}
    built = F.build_request(d, ["15m"], "15m", at(D3, 15, 30), starts, c, required={"15m": at(D2, 9, 15)})
    assert built.request is not None and built.data_problems == ()
    assert [g.kind for g in built.history_gaps] == [F.INCOMPLETE_BARS]
    assert built.request.frames["15m"].bars[0].start == at(D1, 9, 15)       # the history is handed in as it is
    # the same gap inside the required window blocks, as before
    blocked = F.build_request(d, ["15m"], "15m", at(D3, 15, 30), starts, c)
    assert blocked.data_problems == ("incomplete_bars:15m",) and blocked.history_gaps == ()


def test_missing_history_day_is_a_history_gap_and_a_window_gap_still_blocks():
    c = cal()
    rows = minute_rows(D2) + minute_rows(D3, skip={at(D3, 11, m) for m in range(15, 30)})
    d = F.SymbolData(symbol=KEY, exchange="NSE", minutes=minutes(rows, c))
    built = F.build_request(d, ["15m"], "15m", at(D3, 15, 30), {"15m": at(D1, 9, 15)}, c, required={"15m": at(D2, 9, 15)})
    assert [g.kind for g in built.history_gaps] == [F.MISSING_BARS]
    assert built.history_gaps[0].first == at(D1, 9, 15) and built.history_gaps[0].count == 25
    assert built.data_problems == ("missing_bars:15m",)


def test_history_joined_to_newer_bars_equals_one_full_read():
    c = cal()
    rows = minute_rows(D1) + minute_rows(D2) + minute_rows(D3, skip={at(D3, 9, 40)})
    full = F.SymbolData(symbol=KEY, exchange="NSE", minutes=minutes(rows, c))
    cut = at(D3, 9, 15)
    hist = F.history_of(minutes([r for r in rows if r[0] <= cut.astimezone(UTC)], c), "15m", cut, c)
    today = minutes([r for r in rows if r[0] > cut.astimezone(UTC)], c)
    split = F.SymbolData(symbol=KEY, exchange="NSE", minutes=today, history={"15m": hist})
    a = F.build_request(full, ["15m"], "15m", at(D3, 12, 0), {"15m": at(D1, 9, 15)}, c, required={"15m": at(D2, 9, 15)})
    b = F.build_request(split, ["15m"], "15m", at(D3, 12, 0), {"15m": at(D1, 9, 15)}, c, required={"15m": at(D2, 9, 15)})
    assert a.request.frames == b.request.frames and a.gaps == b.gaps and a.history_gaps == b.history_gaps
    assert b.data_problems == ("incomplete_bars:15m",)


def test_history_of_another_source_is_never_joined():
    c = cal()
    rows = minute_rows(D1) + minute_rows(D2) + minute_rows(D3)
    cut = at(D3, 9, 15)
    hist = F.history_of(minutes([r for r in rows if r[0] <= cut.astimezone(UTC)], c, source="A"), "15m", cut, c)
    today = minutes([r for r in rows if r[0] > cut.astimezone(UTC)], c, source="B")
    d = F.SymbolData(symbol=KEY, exchange="NSE", minutes=today, history={"15m": hist})
    built = F.build_request(d, ["15m"], "15m", at(D3, 12, 0), {"15m": at(D1, 9, 15)}, c, required={"15m": at(D3, 9, 15)})
    assert built.request.frames["15m"].bars[0].start == at(D3, 9, 15)
    assert built.data_problems == () and {g.kind for g in built.history_gaps} == {F.MISSING_BARS}


# --- sources: the history once a day, never from the broker ----------------------------------------------------------

def test_collect_reads_the_history_once_and_never_asks_the_broker_for_it(monkeypatch):
    c = cal()
    rows = minute_rows(D1) + minute_rows(D2) + minute_rows(D3)
    lake_calls, broker_calls = [], []

    async def fake_lake(session, key, timeframe, start, end, cal_, *, scope=None):
        lake_calls.append((timeframe, start, end))
        got = [r for r in rows if start.astimezone(UTC) < r[0] <= end.astimezone(UTC)]
        return (minutes(got, cal_) if got else None), frozenset(), []

    async def fake_broker(broker, symbol, exchange, timeframe, start, end, cal_, *, own_volume=True):
        broker_calls.append((timeframe, start, end))
        return None, []

    monkeypatch.setattr(S, "lake_bars", fake_lake)
    monkeypatch.setattr(S, "broker_bars", fake_broker)
    S._HISTORY.clear()
    starts = {"5m": at(D3, 9, 15), "15m": at(D1, 9, 15)}
    required = {"15m": at(D2, 9, 15)}
    for now in (at(D3, 11, 0), at(D3, 11, 5)):
        data, _ = run(S.collect(None, key=KEY, exchange="NSE", timeframes=["5m", "15m"], starts=starts, now=now, cal=c,
                                broker=object(), broker_symbol="TESTINDEX", required=required))
        assert set(data.history) == {"15m"} and data.history["15m"].cut == at(D3, 9, 15)
    history_reads = [x for x in lake_calls if x[2] == at(D3, 9, 15)]
    assert len(history_reads) == 1 and history_reads[0][1] == at(D1, 9, 15)
    assert broker_calls and all(start >= at(D2, 9, 15) for _, start, _ in broker_calls)   # the broker: from the window, never the history
    built = F.build_request(data, ["5m", "15m"], "5m", at(D3, 11, 5), starts, c, required=required)
    assert built.request.frames["15m"].bars[0].start == at(D1, 9, 15)
    assert built.history_gaps == ()
    S._HISTORY.clear()
