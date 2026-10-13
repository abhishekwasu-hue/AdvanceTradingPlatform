"""S5-C: F&O / open-interest screens (flag `screener_fno`, off by default).

* fno.py (pure): the four buildups and their thresholds; the per-date timeline from NSE's F&O bhavcopy rows (near month =
  the earliest live expiry, all-expiry OI, rollover, annualised carry, the near contract's own change across a roll).
* As of each bar's close, no look-ahead: an intraday bar of d (the 15:25 one too) sees d-1, the daily bar of d sees d;
  the ban list reads the bar's own trade date; appending or changing later dates never changes an earlier value.
* The loader: the shared lake only (`md_candles` futures / spot rows, `fo_ban_history`), the highest version ingested by
  the run time, nothing after it; options and look-alike symbols left out; index spots from NSE_INDEX.
* API and alerts: hidden and refused while the flag is off (registry, validate, run, presets, alert rules); a run reads
  the lake when on; alert rules pause while off and resume at the next bar close.
Synthetic rows only - no broker, no network.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.db.models import FoBanRecord, MdCandleRecord
from app.screener import compile_screen, fno, parse
from app.screener.runtime import ScreenRuntimeError, SymbolData, evaluate
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

IST = "Asia/Kolkata"
SYM, OTHER = "S5CFNO", "S5CNOFO"
MAR26, APR30 = date(2026, 3, 26), date(2026, 4, 30)
D1, D2, D3, D4 = date(2026, 3, 5), date(2026, 3, 6), date(2026, 3, 9), date(2026, 3, 10)


def _run(coro):
    return asyncio.run(coro)


def ist(d: date, hh: int, mm: int) -> datetime:
    return pd.Timestamp(datetime(d.year, d.month, d.day, hh, mm)).tz_localize(IST).tz_convert("UTC").to_pydatetime()


def _set(**flags):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def _clean_lake():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.like("%S5C%")))
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.like("NSE_INDEX:%S5C%")))
            await session.execute(delete(FoBanRecord).where(FoBanRecord.underlying.like("S5C%")))
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _clean():
    _clean_lake()
    yield
    _clean_lake()
    _set(screener_fno=False, screener_v2=False, screener_intrabar=False)


def _row(key, d, close, oi=None, version=1, ingested=None, source="nse_bhav:fo"):
    return MdCandleRecord(instrument_key=key, timeframe="day", ts=ist(d, 15, 30), source=source, version=version, open=close, high=close,
                          low=close, close=close, volume=1000, oi=oi, ingested_at=ingested or ist(d, 19, 0))


def _fut(sym, d, expiry, close, oi, **kw):
    return _row(f"NFO:{sym}:FUT:{expiry.isoformat()}", d, close, oi, **kw)


def _put(rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _ban(sym, d, fetched=None):
    return FoBanRecord(underlying=sym, trade_date=d, source="nse:fo_secban", fetched_at=fetched or ist(d, 0, 0) - timedelta(hours=5),
                       checksum="x" * 64)


def _day(d, near_close, near_oi, next_close=None, next_oi=100.0, spot=None, near=MAR26, nxt=APR30):
    contracts = [(f"NFO:S:FUT:{near.isoformat()}", near, near_close, near_oi)]
    if next_close is not None:
        contracts.append((f"NFO:S:FUT:{nxt.isoformat()}", nxt, next_close, next_oi))
    return fno.DayRow(d, contracts, spot)


def _frame(index, closes=None):
    c = np.full(len(index), 100.0) if closes is None else np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1000.0}, index=pd.DatetimeIndex(index))


def _intraday(days, minutes=5):
    idx = []
    for d in days:
        t = ist(d, 9, 15)
        while t < ist(d, 15, 30):
            idx.append(pd.Timestamp(t))
            t += timedelta(minutes=minutes)
    return _frame(idx)


def _ev(text, frame, timeline, tf):
    return evaluate(parse(text), SymbolData("S", {tf: frame}, fno=timeline), base_tf=tf)


# ---------------------------------------------------------------------------------------------------------- pure math
def test_buildups_thresholds_and_unknown():
    price = np.array([1.0, -1.0, 1.0, -1.0, 0.2, 1.0, np.nan])
    oi = np.array([3.0, 3.0, -3.0, -3.0, 3.0, 1.0, 5.0])
    assert fno.classify(price, oi, 0.0, 0.0).tolist() == ["LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING", "LONG_BUILDUP",
                                                          "LONG_BUILDUP", None]
    assert fno.classify(price, oi, 0.5, 2.0).tolist() == ["LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING", "", "", None]
    f = _frame(pd.date_range("2026-03-02 03:45", periods=4, freq="5min", tz="UTC"), [100, 101, 101, 99])
    f["oi"] = [100.0, 110.0, 110.0, np.nan]
    assert fno.oi_change(f, 1).round(6).tolist()[1:3] == [10.0, 0.0] and np.isnan(fno.oi_change(f, 1).iloc[3])
    assert fno.buildup(f, 1, 0, 0).tolist() == [None, "LONG_BUILDUP", "", None]


def test_registry_entries_flag_and_validation():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    from app.screener.registry import FNO_FLAG, FNO_FUNCTIONS, FUNCTIONS
    assert FNO_FLAG == fno.FLAG == "screener_fno" and FNO_FLAG in DEFAULT_OFF_FLAGS and "places no orders" in FEATURE_FLAGS[FNO_FLAG]
    assert set(FNO_FUNCTIONS) == {"OIChange", "Buildup", *fno.EOD_FUNCTIONS}
    assert all(FUNCTIONS[n].needs == ("fno_eod",) for n in fno.EOD_FUNCTIONS) and not FUNCTIONS["OIChange"].needs
    for text, ok in (('FutBuildup(1) == "LONG_BUILDUP"', True), ('FutBuildup(1) == "BULLISH"', False), ("FutBuildup(1, close, 2) == \"\"", False),
                     ("FutBuildup(1, 60) == \"\"", False), ("RolloverPct() > 50", True), ("InFnoBan() == FALSE", True), ("OIChange(3) > 5", True),
                     ("CostOfCarry() < 0", True)):
        assert compile_screen(text, base_tf="1d")[1].ok is ok, text
    # each gate sees only its own family
    from app.screener import zones as zone_gate
    from app.screener.breakouts import gate as breakout_gate
    ast = parse('NearZone() AND RangeBreakout("up") AND InFnoBan() == FALSE AND OIChange(2) > 1 AND RolloverPct() > 50')
    assert fno.flagged_calls(ast) == ["InFnoBan", "OIChange", "RolloverPct"] and fno.eod_calls(ast) == ["InFnoBan", "RolloverPct"]
    assert breakout_gate.flagged_calls(ast) == ["RangeBreakout"] and zone_gate.flagged_calls(ast) == ["NearZone"]


def test_timeline_near_month_rollover_carry_and_the_near_contracts_own_change():
    days = [_day(date(2026, 3, 25), 100.0, 900.0, 101.0, 100.0, spot=99.0),
            _day(MAR26, 104.0, 500.0, 105.0, 500.0, spot=104.0),                                   # expiry day: still the near month
            fno.DayRow(date(2026, 3, 27), [("NFO:S:FUT:2026-03-26", MAR26, 104.0, 0.0),           # an expired row is left out
                                           ("NFO:S:FUT:2026-04-30", APR30, 107.1, 1000.0)], 107.0)]
    tl = fno.build_timeline("S", days)
    assert tl.near_key == ["NFO:S:FUT:2026-03-26", "NFO:S:FUT:2026-03-26", "NFO:S:FUT:2026-04-30"]
    np.testing.assert_allclose(tl.total_oi, [1000.0, 1000.0, 1000.0])
    np.testing.assert_allclose(tl.rollover_pct, [10.0, 50.0, 0.0])
    np.testing.assert_allclose(tl.carry_pct, [(100 / 99 - 1) * 365 / 1 * 100, 0.0, (107.1 / 107 - 1) * 365 / 34 * 100])
    # the near contract against ITS OWN close: on the 27th the April contract (105 -> 107.1), not March's 104 -> 107.1
    np.testing.assert_allclose(fno.build_timeline("S", days).near_change(1)[1:], [4.0, 2.0])
    assert np.isnan(tl.near_change(1)[0]) and np.isnan(tl.near_change(5)).all()
    no_oi = fno.build_timeline("S", [_day(D1, 100.0, None, 101.0, 100.0)])
    assert np.isnan(no_oi.total_oi[0]) and np.isnan(no_oi.rollover_pct[0]) and np.isnan(no_oi.carry_pct[0])   # no spot: no carry


# ------------------------------------------------------------------------------------------------ as of, no look-ahead
def _timeline():
    return fno.build_timeline("S", [_day(D1, 100.0, 1000.0, 100.5, 100.0, spot=99.5), _day(D2, 102.0, 1200.0, 102.5, 100.0, spot=101.5),
                                    _day(D3, 101.0, 1300.0, 101.5, 200.0, spot=100.8)], [D3])


def test_intraday_bars_of_a_day_see_the_previous_trade_date_and_the_daily_bar_its_own():
    tl = _timeline()
    f = _intraday([D2, D3, D4])
    oi = _ev("FutOIChange(1)", f, tl, "5m")
    by_day = oi.groupby(pd.DatetimeIndex(f.index).tz_convert(IST).date)
    assert by_day.get_group(D2).isna().all()                                  # D2's bars see D1, which has no date before it
    assert by_day.get_group(D3).round(6).eq(round(20000 / 1100, 6)).all()     # every D3 bar, the 15:25 one too, sees D2 (vs D1)
    assert by_day.get_group(D4).round(6).eq(round(20000 / 1300, 6)).all()     # D4 sees D3: (1500 - 1300) / 1300
    assert _ev('FutBuildup(1) == "LONG_BUILDUP"', f, tl, "5m").groupby(by_day.ngroup()).all().tolist() == [False, True, False]
    ban = _ev("InFnoBan()", f, tl, "5m")
    assert ban.groupby(pd.DatetimeIndex(f.index).tz_convert(IST).date).all().to_dict() == {D2: False, D3: True, D4: False}
    assert ban.groupby(pd.DatetimeIndex(f.index).tz_convert(IST).date).any().to_dict() == {D2: False, D3: True, D4: False}
    # daily bars labelled at IST midnight, at the 09:15 open or at UTC midnight: the bar of d reads d
    for label in ((0, 0), (9, 15)):
        daily = _frame([pd.Timestamp(ist(d, *label)) for d in (D1, D2, D3)])
        assert _ev("FutOIChange(1)", daily, tl, "1d").round(6).tolist()[1:] == [round(20000 / 1100, 6), round(20000 / 1300, 6)]
        assert _ev("InFnoBan()", daily, tl, "1d").tolist() == [False, False, True]
        assert _ev("RolloverPct()", daily, tl, "1d").round(6).tolist() == [round(1000 / 110, 6), round(1000 / 130, 6), round(2000 / 150, 6)]
    utc_midnight = _frame([pd.Timestamp(datetime(d.year, d.month, d.day, tzinfo=timezone.utc)) for d in (D1, D2, D3)])
    assert _ev("InFnoBan()", utc_midnight, tl, "1d").tolist() == [False, False, True]
    weekly = _frame([pd.Timestamp(ist(date(2026, 3, 2), 0, 0)), pd.Timestamp(ist(D3, 0, 0))])     # the weeks of 2 and 9 March
    assert _ev("InFnoBan()", weekly, tl, "1w").tolist() == [False, True]


def test_no_look_ahead_appending_and_changing_later_dates():
    f = _intraday([D2, D3, D4])
    before = {t: _ev(t, f, _timeline(), "5m") for t in ("FutOIChange(1)", "FutBuildup(1)", "RolloverPct()", "CostOfCarry()", "InFnoBan()")}
    later = fno.build_timeline("S", [_day(D1, 100.0, 1000.0, 100.5, 100.0, spot=99.5), _day(D2, 102.0, 1200.0, 102.5, 100.0, spot=101.5),
                                     _day(D3, 101.0, 1300.0, 101.5, 200.0, spot=100.8),
                                     _day(D4, 90.0, 5000.0, 91.0, 900.0, spot=95.0), _day(date(2026, 3, 11), 80.0, 1.0, 81.0, 1.0, spot=80.0)],
                                [D3, D4, date(2026, 3, 11)])
    cut = pd.DatetimeIndex(f.index) < pd.Timestamp(ist(D4, 0, 0))
    for text, series in before.items():
        after = _ev(text, f, later, "5m")
        pd.testing.assert_series_equal(series[cut], after[cut], check_names=False)
    assert _ev("InFnoBan()", f, later, "5m")[~cut].all()                    # D4 is now banned: its own bars read it


def test_missing_data_is_explained_never_read_as_no_match():
    f = _intraday([D2])
    with pytest.raises(ScreenRuntimeError, match="not loaded"):
        evaluate(parse("RolloverPct() > 1"), SymbolData("S", {"5m": f}), base_tf="5m")
    empty = fno.build_timeline("S", [], [D2])
    with pytest.raises(ScreenRuntimeError, match="no futures rows"):
        _ev("FutOIChange(1) > 1", f, empty, "5m")
    assert _ev("InFnoBan()", f, empty, "5m").all()                           # the ban list stands on its own
    with pytest.raises(ScreenRuntimeError, match="no open interest"):
        _ev("Buildup(1) == \"LONG_BUILDUP\"", f, empty, "5m")


# ------------------------------------------------------------------------------------------------------- the loader
def test_loader_reads_the_shared_lake_as_of_the_run_time():
    _put([_fut(SYM, D1, MAR26, 100.0, 1000), _fut(SYM, D1, APR30, 100.5, 100),
          _fut(SYM, D2, MAR26, 102.0, 1200), _fut(SYM, D2, APR30, 102.5, 100),
          _fut(SYM, D2, MAR26, 103.0, 1250, version=2, ingested=ist(D3, 8, 0)),                 # a correction, ingested later
          _fut(SYM, D3, MAR26, 999.0, 9999),                                                    # a date after the run time
          _row(f"NFO:{SYM}:CE:{MAR26.isoformat()}:100", D2, 5.0, 77777),                         # options: not futures
          _fut(SYM + "X", D2, MAR26, 50.0, 1),                                                  # a look-alike underlying
          _row(f"NSE:{SYM}", D1, 99.5, source="nse_bhav:cm"), _row(f"NSE:{SYM}", D2, 101.5, source="nse_bhav:cm")])
    _put([_ban(SYM, D2), _ban(SYM, D3, fetched=ist(D3, 0, 0) + timedelta(hours=10))])          # the D3 list fetched after the run

    def load(end):
        async def go():
            async with _session_factory() as session:
                return await fno.load_timeline(session, f"NSE:{SYM}", ist(D1, 0, 0) - timedelta(days=5), end)
        return _run(go())
    tl = load(ist(D2, 20, 0))
    assert len(tl.close_ns) == 2 and tl.symbol == SYM
    np.testing.assert_allclose(tl.total_oi, [1100.0, 1300.0])                     # version 1 of D2 (v2 not ingested yet), no options
    assert tl.closes[1][f"NFO:{SYM}:FUT:{MAR26.isoformat()}"] == 102.0 and tl.ban_days.tolist() == [D2]
    np.testing.assert_allclose(tl.carry_pct[0], (100.0 / 99.5 - 1) * 365 / 21 * 100)
    late = load(ist(D3, 12, 0))
    assert late.closes[1][f"NFO:{SYM}:FUT:{MAR26.isoformat()}"] == 103.0 and late.total_oi[1] == 1350.0   # the correction wins
    assert len(late.close_ns) == 2 and late.ban_days.tolist() == [D2, D3]      # D3's futures row is labelled 15:30: not yet
    assert fno.underlying("nse:sbin") == "SBIN" and fno._spot_key("NIFTY").startswith("NSE_INDEX:")


def test_expiry_candidates_cover_every_monthly_expiry_in_the_window():
    c = fno.expiry_candidates(date(2025, 12, 20), date(2026, 3, 10))
    assert c[0] == date(2025, 12, 20) and c[-1] == date(2026, 7, 31) and len(c) == len(set(c)) == 12 + 17 + 14 + 17 + 16 + 17 + 16 + 17   # Dec 20- .. Jul
    for expiry in (date(2025, 12, 30), date(2026, 1, 27), date(2026, 2, 24), MAR26, APR30, date(2026, 6, 30)):
        assert expiry in c
    assert date(2026, 3, 14) not in c and date(2025, 12, 19) not in c and date(2026, 8, 25) not in c


def test_index_futures_read_the_index_close_as_spot():
    from app.universe.fo_eod import code_for
    key = f"NSE_INDEX:{code_for('NIFTY 50')}"
    assert fno._spot_key("NIFTY") == key

    async def go():
        async with _session_factory() as session:
            session.add_all([_fut("NIFTY", D1, MAR26, 22100.0, 10_000), _row(key, D1, 22000.0, source="nse_bhav:index")])
            await session.commit()
            try:
                return await fno.load_timeline(session, "NIFTY", ist(D1, 0, 0) - timedelta(days=2), ist(D1, 20, 0))
            finally:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_([key, f"NFO:NIFTY:FUT:{MAR26.isoformat()}"]),
                                                                   MdCandleRecord.source.in_(["nse_bhav:fo", "nse_bhav:index"]),
                                                                   MdCandleRecord.ts == ist(D1, 15, 30)))
                await session.commit()
    tl = _run(go())
    np.testing.assert_allclose(tl.carry_pct, [(22100 / 22000 - 1) * 365 / 21 * 100])


# ------------------------------------------------------------------------------------------------- API: flag and gating
@pytest.fixture
def screener_on():
    _set(screener_v2=True, screener_fno=False)
    yield


def test_fno_functions_are_hidden_and_refused_while_off(screener_on):
    headers, _ = _owner("s5c-off@example.com")
    reg = client.get("/api/screener/registry", headers=headers).json()["entries"]
    assert not ({"OIChange", "Buildup", *fno.EOD_FUNCTIONS} & set(reg)) and "RSI" in reg
    v = client.post("/api/screener/validate", json={"source": "FutOIChange(1) > 5 AND close > 1", "base_tf": "1d"}, headers=headers).json()
    assert not v["ok"] and "screener_fno" in v["problems"][-1]["message"] and "FutOIChange()" in v["problems"][-1]["message"]
    assert client.post("/api/screener/screens", json={"name": "f", "source": "InFnoBan()"}, headers=headers).status_code == 422
    run = client.post("/api/screener/run", json={"source": "RolloverPct() > 50", "symbols": ["X"]}, headers=headers)
    assert run.status_code == 422 and "screener_fno" in str(run.json())
    r = client.get("/api/screener/fno/presets", headers=headers)
    assert r.status_code == 503 and r.headers.get("x-feature-disabled") == "screener_fno"
    r = client.post("/api/alerts/rules", json={"name": "f", "kind": "instrument", "symbol": "ABC", "condition": "OIChange(1) > 5", "base_tf": "15m"},
                    headers=headers)
    assert r.status_code == 422 and "screener_fno" in str(r.json())
    assert client.get("/api/screener/fno/presets").status_code == 401


def test_presets_and_taxonomy(screener_on):
    from app.screener import taxonomy as tx
    import app.main  # noqa: F401 - registers every template source
    assert tx.sources()["fno"].flag == "screener_fno" and "fno" in tx.default_hidden() and tx.coverage_problems() == []
    filed = [s for s in tx.load().screens if s.source == "fno"]
    assert {s.id for s in filed} == {f"fno.{p.id}" for p in fno.PRESETS} and all(s.method == "volume_flow" for s in filed)
    _set(screener_fno=True)
    headers, _ = _owner("s5c-presets@example.com")
    body = client.get("/api/screener/fno/presets", headers=headers).json()
    assert [p["id"] for p in body["presets"]] == [f"fno.{p.id}" for p in fno.PRESETS] and all(p["ok"] for p in body["presets"])
    assert body["presets"][0]["text"] == 'FutBuildup(1, 0.5, 2) == "LONG_BUILDUP"' and "not advice" in body["note"]
    for p in body["presets"]:
        assert client.post("/api/screener/validate", json={"source": p["text"], "base_tf": p["base_tf"]}, headers=headers).json()["ok"], p


def test_a_run_reads_the_shared_lake_when_on(screener_on, monkeypatch):
    _set(screener_fno=True)
    headers, _ = _owner("s5c-run@example.com")
    other_headers, _ = _owner("s5c-run-other@example.com")
    _put([_fut(SYM, D1, MAR26, 100.0, 1000), _fut(SYM, D1, APR30, 100.5, 100),
          _fut(SYM, D2, MAR26, 102.0, 1200), _fut(SYM, D2, APR30, 102.5, 100)])
    frame = _frame([pd.Timestamp(ist(d, 0, 0)) for d in (D1, D2)])

    async def fake_fetch(session, tenant_id, symbols, exchange, base_tf, lookback, now=None, include_forming=False, **_kw):
        return [SymbolData(s, {base_tf: frame}) for s in symbols], {}, "broker:fake"
    from app.screener import routes as screener_routes
    monkeypatch.setattr(screener_routes, "fetch_frames", fake_fetch)
    body = {"source": 'FutBuildup(1, 0.5, 2) == "LONG_BUILDUP"', "symbols": [SYM, OTHER], "base_tf": "1d"}
    out = client.post("/api/screener/run", json=body, headers=headers)
    assert out.status_code == 200, out.text
    rows = {r["symbol"]: r for r in out.json()["results"]}
    assert out.json()["matched"] == [SYM] and "no futures rows" in rows[OTHER]["reason"]
    assert client.post("/api/screener/run", json=body, headers=other_headers).json()["matched"] == [SYM]     # shared exchange data
    bar = client.post("/api/screener/run", json={**body, "source": "OIChange(1) > 1"}, headers=headers).json()
    assert bar["matched"] == [] and all("no open interest" in r["reason"] for r in bar["results"])


# ------------------------------------------------------------------------------------------------------------- alerts
def test_fno_rules_pause_while_off_and_resume_at_bar_close(screener_on):
    from app.alerts import barclose
    from app.db.models import AlertEventRecord, AlertRuleRecord
    from tests.test_s4a_barclose import TUE, FakeFetch, _bars as s4_bars, _rule, ist as s4_ist
    assert TUE == D4
    _, me = _owner("s5c-pause@example.com")
    rid = _rule(me["tenant_id"], condition="FutOIChange(1) > 5", tf="5m", symbol=SYM)
    _put([_fut(SYM, D2, MAR26, 100.0, 1000), _fut(SYM, D3, MAR26, 101.0, 1200)])
    fetch = FakeFetch({SYM: s4_bars(s4_ist(TUE, 9, 15), [100.0] * 61)})

    def cycle(now):
        async def go():
            async with _session_factory() as session:
                await barclose.run_due(session, now=now, fetch=fetch, holidays=set())
                rule = await session.get(AlertRuleRecord, rid)
                events = list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rid)))
                return rule.status, rule.last_problem, len(events)
        return _run(go())
    now = s4_ist(TUE, 14, 21)
    status, problem, fired = cycle(now)
    assert status == "active" and "screener_fno" in problem and fired == 0                       # paused, not dropped
    _set(screener_fno=True)
    status, problem, fired = cycle(now + timedelta(minutes=2))
    assert status == "active" and problem is None and fired == 1                                # D4's bars see D3 vs D2: +20 %


# --- review of S5-C ---------------------------------------------------------------------------------------------------------

def test_the_loader_reads_only_the_shared_sources_the_preferred_one_first():
    """HIGH: a row an organisation's broker session or stream wrote (broker: / stream:) never reaches the shared figures,
    whatever its version; among shared sources the bhavcopy wins over a vendor row of the same stamp."""
    _put([_fut(SYM, D1, MAR26, 100.0, 1000), _row(f"NSE:{SYM}", D1, 99.5, source="nse_bhav:cm"),
          _fut(SYM, D1, MAR26, 999.0, 1, version=3, source="stream:kite"),                       # one organisation's stream
          _row(f"NSE:{SYM}", D1, 10.0, version=3, source="broker:kite"),                         # one organisation's broker bars
          _fut(SYM, D1, MAR26, 500.0, 5, version=2, source="vendor:test"),                       # shared, but less preferred
          _fut(SYM, D2, MAR26, 101.0, 1100, source="stream:kite")])                              # a date only a stream has

    async def go():
        async with _session_factory() as session:
            return await fno.load_timeline(session, SYM, ist(D1, 0, 0) - timedelta(days=5), ist(D3, 20, 0))
    tl = _run(go())
    assert len(tl.close_ns) == 1 and tl.closes[0][f"NFO:{SYM}:FUT:{MAR26.isoformat()}"] == 100.0 and tl.total_oi[0] == 1000.0
    np.testing.assert_allclose(tl.carry_pct, [(100.0 / 99.5 - 1) * 365 / 21 * 100])


def test_the_loader_reads_as_many_dates_back_as_the_calls_need():
    """MED: FutOIChange(60) on a few days of 5m bars reads 60 trade dates before the first bar (a fixed 60-day margin
    held about 42), so it has a value; the reads are batched for the universe."""
    from app.screener.runtime import SymbolData as SD
    assert fno.eod_lookback(parse('FutOIChange(60) > 1 AND FutBuildup() == ""')) == 60
    assert fno.eod_lookback(parse("FutOIChange($n) > 1"), {"n": 30}) == 30 and fno.eod_lookback(parse("RolloverPct() > 1")) == 1
    assert fno.margin_days(1) == fno.MIN_MARGIN_DAYS and fno.margin_days(60) >= 60 * 7 / 5
    days = [d.date() for d in pd.bdate_range(end=D3, periods=75)]
    _put([_fut(SYM, d, MAR26 if d < MAR26 else APR30, 100.0 + i, 1000 + 10 * i) for i, d in enumerate(days)]
         + [_fut(OTHER, d, MAR26 if d < MAR26 else APR30, 50.0, 500 + i) for i, d in enumerate(days)])
    frame = _intraday([D4])
    universe = [SD(SYM, {"5m": frame}), SD(OTHER, {"5m": frame})]
    ast = parse("FutOIChange(60) > 1")

    async def go():
        async with _session_factory() as session:
            await fno.attach_fno(session, universe, ist(D4, 15, 30), "5m", ast)
    _run(go())
    got = evaluate(parse("FutOIChange(60)"), universe[0], base_tf="5m")
    assert got.notna().all() and round(float(got.iloc[0]), 6) == round((1000 + 10 * 74) / (1000 + 10 * 14) * 100 - 100, 6)
    assert len(universe[1].fno.close_ns) == 75 and len(universe[0].fno.close_ns) == 75


def test_chart_studies_refuse_the_eod_functions_and_gate_the_family():
    from app.charts import studies
    rows = studies.check(["FutOIChange(1)", "OIChange(1)", "close"], "1d")
    assert not rows[0]["ok"] and "end-of-day F&O figures" in rows[0]["problems"][-1]["message"]
    assert rows[1]["ok"] and rows[2]["ok"]

    async def go():
        async with _session_factory() as session:
            return await studies.gate(session, None, rows)
    gated = _run(go())                                                       # screener_fno is off: the bar OI study is refused too
    assert not gated[1]["ok"] and "screener_fno" in gated[1]["problems"][-1]["message"] and gated[2]["ok"]
