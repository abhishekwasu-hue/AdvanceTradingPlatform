"""N3 S8a (spec §2.4 category E): ownership and events in ScreenQL - Holding, HoldingChange, DaysToEvent - point in time
(flag screener_ownership, off by default).

- when a row becomes known (the day after its cited publication, else when stored; a correction from its own time);
- the series on hand-built rows: the latest pattern known by each bar's close, corrections, staleness, the change over N
  patterns, days to the next known event;
- no look-ahead: a series on bars cut at any point equals the full series up to there, and rows known after a bar's
  close never reach it (with the Z10-a causal cases in app/quality/causal_cases.py);
- the store read (shared reference data), the run route, and the refusals (flag off, history, alerts)."""
import asyncio
import json
import types
import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.core.models import OHLCVBar
from app.db.models import CompanyRecord, EarningsCalendarEventRecord, MdCorporateActionRecord, ShareholdingSnapshotRecord
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.screener import compile_screen
from app.screener import history as H
from app.screener import ownership as O
from app.screener.runtime import SymbolData, evaluate
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant

UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"n3s8a-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _days(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _frame(days):
    c = 100 + np.arange(len(days), dtype=float)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1000.0}, index=H.bar_index(days))


def _ev(text, data):
    from app.screener.validator import NOT_A_CONDITION
    ast, v = compile_screen(text, base_tf="1d")
    assert not [p for p in v.problems if p.message != NOT_A_CONDITION], v.problems      # a number is fine here
    return evaluate(ast, data, base_tf="1d")


def _at(day, hh=3, mm=45):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=UTC)


STAKES = {"promoter": 50.0, "pledge": 4.0, "fii": 20.0, "dii": 12.0, "public": 18.0}


# --- when a row is known -------------------------------------------------------------------------------------------------------

def test_the_flag_is_off_by_default():
    assert O.FLAG in DEFAULT_OFF_FLAGS


def test_known_from_the_day_after_the_cited_publication_else_when_stored():
    stored = datetime(2025, 5, 3, 12, 0, tzinfo=UTC)
    assert O.known_from(publication_date=date(2025, 4, 20), stored_at=stored, not_before=date(2025, 3, 31)) == datetime(2025, 4, 21, 3, 45, tzinfo=UTC)
    assert O.known_from(publication_date=date(2025, 5, 3), stored_at=stored) == stored                      # next open is after the store
    assert O.known_from(publication_date=date(2025, 3, 1), stored_at=stored, not_before=date(2025, 3, 31)) == stored   # before the quarter end
    assert O.known_from(publication_date=None, stored_at=stored.replace(tzinfo=None)) == stored


# --- the series ------------------------------------------------------------------------------------------------------------------

def test_holding_is_the_latest_pattern_known_by_each_bar_s_close():
    days = _days(date(2025, 4, 1), 120)
    q1, q2 = date(2025, 3, 31), date(2025, 6, 30)
    patterns = [O.Pattern(q1, _at(date(2025, 4, 22)), dict(STAKES)),
                O.Pattern(q2, _at(date(2025, 7, 21)), {**STAKES, "promoter": 53.0, "fii": 18.5}),
                O.Pattern(q1, _at(date(2025, 5, 15)), {**STAKES, "promoter": 49.0})]          # a correction of Q1, from its own time
    data = SymbolData("S", {"1d": _frame(days)}, ownership=O.Ownership(patterns, []))
    held = pd.Series(_ev('Holding("promoter")', data).to_numpy(), index=days)
    assert np.isnan(held[date(2025, 4, 21)])                         # the 21st's bar closes at 00:00 IST on the 22nd, before 09:15
    assert held[date(2025, 4, 22)] == 50.0 and held[date(2025, 5, 14)] == 50.0
    assert held[date(2025, 5, 15)] == 49.0 and held[date(2025, 7, 18)] == 49.0                  # the correction never reaches back
    assert held[date(2025, 7, 21)] == 53.0
    change = pd.Series(_ev('HoldingChange("promoter", 1)', data).to_numpy(), index=days)
    assert np.isnan(change[date(2025, 7, 18)]) and change[date(2025, 7, 21)] == pytest.approx(4.0)   # 53 - 49 (Q1 as corrected)
    fii = pd.Series(_ev('HoldingChange("fii")', data).to_numpy(), index=days)
    assert fii[date(2025, 7, 21)] == pytest.approx(-1.5)
    stale = O.Ownership([O.Pattern(date(2024, 9, 30), _at(date(2024, 10, 21)), dict(STAKES))], [])
    late = pd.Series(_ev('Holding("pledge")', SymbolData("S", {"1d": _frame(days)}, ownership=stale)).to_numpy(), index=days)
    cut = date(2024, 9, 30) + timedelta(days=O.STALE_DAYS)
    assert late[[d for d in days if d < cut][-1]] == 4.0 and np.isnan(late[[d for d in days if d >= cut][0]])


def test_days_to_event_counts_to_the_next_known_event():
    days = _days(date(2025, 4, 1), 40)
    events = [O.Event("results", date(2025, 4, 25), _at(date(2025, 4, 10))),
              O.Event("ex_date", date(2025, 4, 16), _at(date(2025, 4, 2))),
              O.Event("results", date(2025, 5, 20), _at(date(2025, 5, 2)))]
    data = SymbolData("S", {"1d": _frame(days)}, ownership=O.Ownership([], events))
    res = pd.Series(_ev('DaysToEvent("results")', data).to_numpy(), index=days)
    assert np.isnan(res[date(2025, 4, 9)])                            # announced on the 10th: not before
    assert res[date(2025, 4, 10)] == 15 and res[date(2025, 4, 25)] == 0
    assert np.isnan(res[date(2025, 4, 28)]) and res[date(2025, 5, 2)] == 18
    anyk = pd.Series(_ev('DaysToEvent("any")', data).to_numpy(), index=days)
    assert anyk[date(2025, 4, 2)] == 14 and anyk[date(2025, 4, 14)] == 2                         # the ex-date first
    week = pd.Series(_ev('DaysToEvent("results") <= 7', data).to_numpy(), index=days)
    assert not week[date(2025, 4, 17)] and week[date(2025, 4, 18)]


def test_not_loaded_is_an_error_and_unknown_choices_are_refused():
    data = SymbolData("S", {"1d": _frame(_days(date(2025, 4, 1), 5))})
    with pytest.raises(Exception, match="not loaded"):
        _ev('Holding("promoter")', data)
    for bad in ('Holding("mf")', 'DaysToEvent("ipo")', 'HoldingChange("fii", 9)'):
        assert not compile_screen(bad, base_tf="1d")[1].ok, bad


# --- no look-ahead ---------------------------------------------------------------------------------------------------------------

def test_a_cut_series_equals_the_full_one_and_later_rows_change_nothing():
    days = _days(date(2025, 1, 1), 220)
    f = _frame(days)
    patterns = [O.Pattern(date(2024, 12, 31), _at(date(2025, 1, 21)), dict(STAKES)),
                O.Pattern(date(2025, 3, 31), _at(date(2025, 4, 22)), {**STAKES, "promoter": 51.0}),
                O.Pattern(date(2025, 6, 30), _at(date(2025, 7, 22)), {**STAKES, "promoter": 47.0})]
    events = [O.Event("results", date(2025, 2, 10), _at(date(2025, 1, 30))), O.Event("board_meeting", date(2025, 8, 1), _at(date(2025, 7, 1)))]
    own = O.Ownership(patterns, events)
    for text in ('Holding("promoter")', 'HoldingChange("promoter", 2)', 'DaysToEvent("any")'):
        full = _ev(text, SymbolData("S", {"1d": f}, ownership=own)).to_numpy()
        for n in (30, 75, 140, 200):
            known = O.Ownership([p for p in patterns if p.known_from <= H.bar_index([days[n - 1]])[0] + pd.Timedelta(days=1)],
                                [e for e in events if e.known_from <= H.bar_index([days[n - 1]])[0] + pd.Timedelta(days=1)])
            cut = _ev(text, SymbolData("S", {"1d": f.iloc[:n]}, ownership=known)).to_numpy()
            np.testing.assert_array_equal(cut, full[:n], err_msg=f"{text} at {n}")
        later = O.Ownership(patterns + [O.Pattern(date(2025, 6, 30), _at(date(2025, 9, 1)), {**STAKES, "promoter": 90.0})],
                            events + [O.Event("results", date(2025, 6, 2), _at(date(2025, 9, 1)))])
        again = _ev(text, SymbolData("S", {"1d": f}, ownership=later)).to_numpy()
        upto = sum(1 for d in days if d < date(2025, 9, 1))
        np.testing.assert_array_equal(again[:upto], full[:upto], err_msg=text)


# --- the store and the routes ----------------------------------------------------------------------------------------------------

def _seed(*rows):
    async def go():
        async with _session_factory() as session:
            for r in rows:
                session.add(r)
                await session.flush()
            await session.commit()
    _run(go())


def _company(symbol):
    async def go():
        async with _session_factory() as session:
            row = CompanyRecord(symbol=symbol, name=symbol, sector="IT", industry="Software")
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _cite(day):
    return json.dumps({"source": "NSE shareholding pattern", "publication_date": day.isoformat(), "retrieved_date": day.isoformat()})


def test_the_store_read_knows_each_row_from_its_time():
    sym = f"OWN{uuid.uuid4().hex[:5].upper()}"
    cid = _company(sym)
    stored = datetime(2025, 8, 1, 6, 0, tzinfo=UTC)
    _seed(ShareholdingSnapshotRecord(company_id=cid, as_of_date=date(2025, 3, 31), promoter_pct=50.0, promoter_pledge_pct=2.0, fii_pct=20.0,
                                     source_json=_cite(date(2025, 4, 20)), created_at=stored),
          ShareholdingSnapshotRecord(company_id=cid, as_of_date=date(2025, 6, 30), promoter_pct=52.0, promoter_pledge_pct=1.0, created_at=stored),
          EarningsCalendarEventRecord(company_id=cid, event_type="RESULTS", event_date=date(2025, 8, 12), created_at=stored),
          EarningsCalendarEventRecord(company_id=cid, event_type="PRODUCT_LAUNCH", event_date=date(2025, 8, 20), created_at=stored),
          MdCorporateActionRecord(symbol=sym, exchange="NSE", ex_date=date(2025, 8, 18), action="DIVIDEND", amount=5.0, source="t",
                                  ingested_at=datetime(2025, 8, 5, tzinfo=UTC)))

    async def go(until):
        async with _session_factory() as session:
            return (await O.load(session, [sym.lower()], "NSE", until=until))[sym]
    everything = _run(go(None))
    assert [p.known_from for p in everything.patterns] == [datetime(2025, 4, 21, 3, 45, tzinfo=UTC), stored]
    assert sorted((e.kind, e.day) for e in everything.events) == [("ex_date", date(2025, 8, 18)), ("results", date(2025, 8, 12))]
    early = _run(go(datetime(2025, 6, 1, tzinfo=UTC)))
    assert [p.quarter_end for p in early.patterns] == [date(2025, 3, 31)] and early.events == []


def _org(tag, on=True):
    t = _tenant(_email(tag), with_credentials=False)
    for name in ("screener_v2",) + ((O.FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def _broker(monkeypatch, symbols):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        if symbol not in symbols:
            raise RuntimeError("unknown symbol")
        idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=2), periods=60, freq="1D")
        return [OHLCVBar(timestamp=t.to_pydatetime(), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0) for t in idx]
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


def test_a_run_reads_the_patterns_known_now_and_the_flag_gates_it(monkeypatch):
    hi, lo = (f"OWH{uuid.uuid4().hex[:5].upper()}", f"OWL{uuid.uuid4().hex[:5].upper()}")
    long_ago = datetime.now(UTC) - timedelta(days=40)
    quarter = (datetime.now(UTC) - timedelta(days=60)).date()
    for sym, pct in ((hi, 61.0), (lo, 35.0)):
        cid = _company(sym)
        _seed(ShareholdingSnapshotRecord(company_id=cid, as_of_date=quarter, promoter_pct=pct, promoter_pledge_pct=0.0, created_at=long_ago))
    _broker(monkeypatch, {hi, lo})
    source = 'Holding("promoter") > 50 AND Holding("pledge") < 1'
    off = _org("off", on=False)
    refused = client.post("/api/screener/validate", headers=off["headers"], json={"source": source}).json()
    assert not refused["ok"] and O.FLAG in json.dumps(refused["problems"])
    assert client.post("/api/screener/run", headers=off["headers"], json={"source": source, "symbols": [hi]}).status_code == 422
    t = _org("on")
    r = client.post("/api/screener/run", headers=t["headers"], json={"source": source, "symbols": [hi, lo]})
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == [hi]
    entries = client.get("/api/screener/registry", headers=off["headers"]).json()["entries"]
    assert "Holding" not in entries and "Holding" in client.get("/api/screener/registry", headers=t["headers"]).json()["entries"]


def test_history_and_alerts_refuse_ownership_functions():
    t = _org("hist")
    _flag(H.FLAG, t["tenant_id"])
    r = client.post("/api/screener/history/asof", headers=t["headers"],
                    json={"source": 'DaysToEvent("results") <= 7', "date": "2025-12-01", "universe": {"symbols": ["X"]}})
    assert r.status_code == 422 and "screen history runs do not read ownership" in r.text
    from fastapi import HTTPException

    from app.alerts.rules_routes import _ownership_check
    ast, _ = compile_screen('HoldingChange("fii") > 1')
    with pytest.raises(HTTPException, match="422"):
        _ownership_check(ast)


# --- review of S8 ----------------------------------------------------------------------------------------------------------------

def test_a_correction_never_reaches_back_and_a_backfill_is_as_published():
    """MED-1: rows in stored order; a later row for a quarter is known from when it was stored even when it cites an
    earlier publication; a quarter loaded late (a backfill) is known from its cited publication - "as published" - and
    fills in bars that had no pattern for it. Ties are broken by id."""
    sym = f"OWC{uuid.uuid4().hex[:5].upper()}"
    cid = _company(sym)
    _seed(ShareholdingSnapshotRecord(company_id=cid, as_of_date=date(2025, 3, 31), promoter_pct=50.0, promoter_pledge_pct=0.0,
                                     source_json=_cite(date(2025, 4, 20)), created_at=datetime(2025, 4, 25, tzinfo=UTC)),
          ShareholdingSnapshotRecord(company_id=cid, as_of_date=date(2025, 3, 31), promoter_pct=40.0, promoter_pledge_pct=0.0,
                                     source_json=_cite(date(2025, 4, 20)), created_at=datetime(2025, 8, 1, tzinfo=UTC)),       # a correction
          ShareholdingSnapshotRecord(company_id=cid, as_of_date=date(2024, 12, 31), promoter_pct=45.0, promoter_pledge_pct=0.0,
                                     source_json=_cite(date(2025, 1, 20)), created_at=datetime(2025, 9, 1, tzinfo=UTC)))       # a backfill

    async def go(until):
        async with _session_factory() as session:
            return (await O.load(session, [sym], "NSE", until=until))[sym]
    own = _run(go(None))
    by_value = {p.stakes["promoter"]: p for p in own.patterns}
    assert by_value[50.0].known_from == datetime(2025, 4, 21, 3, 45, tzinfo=UTC)
    assert by_value[40.0].known_from == datetime(2025, 8, 1, tzinfo=UTC)                         # not 21 Apr
    assert by_value[45.0].known_from == datetime(2025, 1, 21, 3, 45, tzinfo=UTC)                 # as published
    days = _days(date(2025, 1, 1), 200)
    held = pd.Series(_ev('Holding("promoter")', SymbolData(sym, {"1d": _frame(days)}, ownership=own)).to_numpy(), index=days)
    assert held[date(2025, 5, 2)] == 50.0 and held[date(2025, 8, 1)] == 40.0 and held[date(2025, 2, 3)] == 45.0
    early = _run(go(datetime(2025, 7, 1, tzinfo=UTC)))                                             # what was stored by then
    held_then = pd.Series(_ev('Holding("promoter")', SymbolData(sym, {"1d": _frame(days)}, ownership=early)).to_numpy(), index=days)
    assert held_then[date(2025, 5, 2)] == held[date(2025, 5, 2)]                                  # the correction never reaches back
    tie = [O.Pattern(date(2025, 3, 31), datetime(2025, 4, 21, tzinfo=UTC), {"promoter": v}, datetime(2025, 4, 21, tzinfo=UTC), i)
           for i, v in ((7, 30.0), (3, 20.0))]
    assert O._known_patterns(tie, pd.Timestamp("2025-05-01", tz="UTC").value)[0].stakes["promoter"] == 30.0   # the higher id wins


def test_days_to_event_vectorised_matches_the_bar_by_bar_rule():
    days = _days(date(2025, 3, 1), 80)
    rng = np.random.default_rng(5)
    events = [O.Event("results", date(2025, 3, 1) + timedelta(days=int(d)), _at(date(2025, 3, 1) + timedelta(days=int(k))))
              for d, k in zip(rng.integers(0, 120, 25), rng.integers(0, 100, 25))]
    f = _frame(days)
    got = O.days_to_event_series(SymbolData("S", {"1d": f}, ownership=O.Ownership([], events)), f, "1d", "results").to_numpy()
    from app.screener.zones import _closes_ns
    closes = _closes_ns(f.index, "1d")
    for i, d in enumerate(days):
        ahead = [e.day for e in events if pd.Timestamp(e.known_from).value <= closes[i] and e.day >= d]
        expect = float((min(ahead) - d).days) if ahead else np.nan
        assert (np.isnan(got[i]) and np.isnan(expect)) or got[i] == expect, (d, got[i], expect)
