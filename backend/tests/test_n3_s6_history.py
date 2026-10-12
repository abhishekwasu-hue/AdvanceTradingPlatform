"""N3 S6a: screen history - who passed on a past date and screen fitness (flag screener_history, off by default).

No look-ahead is the point of this module, so the core tests are truncation tests:
- the fast path (one full-series evaluation read at each date) equals running the screen on bars cut at each date;
- changing any bar after a date changes nothing at that date (signals, eligibility and the breadth regime).
The fitness arithmetic is checked on hand-built closes. The routes run on the shared lake's end-of-day rows only (an
index universe with its membership as of each date, a symbol list), write one trial to the research ledger, and refuse
the sealed holdout and anything that is not a daily bars-only screen."""
import asyncio
import json
import math
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.db.models import IndexMembershipRecord, MdCandleRecord, ResearchTrialRecord, SymbolHistoryRecord
from app.market_data.calendar import IST
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.screener import compile_screen
from app.screener import history as H
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant

UTC = timezone.utc
SCREEN = "close > SMA(close, 20) AND RSI(14) > 50"


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"n3s6-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _sessions(n, end=date(2025, 12, 31)):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _walk(seed, days, start=100.0, vol=0.02):
    rng = np.random.default_rng(seed)
    close = start * np.cumprod(1 + rng.normal(0.0005, vol, len(days)))
    open_ = close * (1 + rng.normal(0, 0.003, len(days)))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, len(days))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, len(days))))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": rng.integers(1000, 5000, len(days)).astype(float)},
                        index=H.bar_index(days))


def _universe(n=12, bars=160, seed=3):
    days = _sessions(bars)
    return {f"S{i:02d}": _walk(seed + i, days) for i in range(n)}, days


def _compiled(text):
    ast, validated = compile_screen(text, base_tf="1d")
    assert validated.ok, validated.problems
    return ast, validated


# --- no look-ahead ---------------------------------------------------------------------------------------------------------------

def test_the_fast_path_equals_running_the_screen_on_bars_cut_at_each_date():
    frames, days = _universe()
    ast, validated = _compiled(SCREEN)
    dates = days[10::9]                                              # includes dates inside the lookback (no match)
    sig = H.signals(ast, validated, frames, dates)
    for d in dates:
        assert sorted(sig.columns[sig.loc[d].to_numpy()]) == H.passers_on(ast, validated, frames, d)["matched"], d
    assert sig.to_numpy().any() and not sig.loc[dates[0]].any()     # some matches; none before the lookback has passed


def test_the_vectorised_and_the_per_symbol_paths_agree(monkeypatch):
    """Z10-d: the panel path (symbols sharing a bar index evaluated at once) gives the per-symbol answer; a symbol with
    its own index (a later listing, a delisting) takes the per-symbol path inside the same run."""
    from app.screener import panel
    frames, days = _universe(n=10)
    frames["LATE"] = frames["S00"].iloc[30:].copy()                        # listed later: its own bar index
    frames["GONE"] = frames["S01"].iloc[:110].copy()                       # delisted: ends early
    ast, validated = _compiled(SCREEN)
    dates = days[25::7]
    fast = H.signals(ast, validated, frames, dates)
    monkeypatch.setattr(panel, "supported", lambda *a, **k: False)
    slow = H.signals(ast, validated, frames, dates)
    assert fast.equals(slow) and not fast.loc[dates[-1], "GONE"]
    for d in dates[::3]:
        assert sorted(fast.columns[fast.loc[d].to_numpy()]) == H.passers_on(ast, validated, frames, d)["matched"]


def test_nothing_after_a_date_changes_that_date():
    frames, days = _universe()
    ast, validated = _compiled(SCREEN)
    d = days[100]
    before = H.signals(ast, validated, frames, [d])
    future = {s: f.copy() for s, f in frames.items()}
    for f in future.values():
        later = np.array([x > d for x in H.session_dates(f.index)])
        f.loc[later, ["open", "high", "low", "close"]] *= 3.0          # a wild future
    assert H.signals(ast, validated, future, [d]).equals(before)
    assert H.passers_on(ast, validated, future, d) == H.passers_on(ast, validated, frames, d)
    # The breadth regime and the eligibility read nothing after d either.
    calendar = days
    closes = H.closes_on_calendar(frames, calendar).to_numpy()
    closes_f = H.closes_on_calendar(future, calendar).to_numpy()
    traded = H.traded_on(frames, calendar).to_numpy()
    i = calendar.index(d)
    mask = np.ones(closes.shape[1], dtype=bool)
    assert H._breadth(closes, traded[i], i, mask) == H._breadth(closes_f, traded[i], i, mask)


def test_a_cross_sectional_screen_runs_date_by_date_on_cut_bars():
    frames, days = _universe(n=8)
    ast, validated = _compiled("PercentileRank(PctChange(close, 10)) > 70")
    assert validated.cross_sectional
    dates = days[40::20]
    sig = H.signals(ast, validated, frames, dates)
    for d in dates:
        assert sorted(sig.columns[sig.loc[d].to_numpy()]) == H.passers_on(ast, validated, frames, d)["matched"]
    with pytest.raises(H.HistoryError, match="date by date"):
        H.signals(ast, validated, frames, days[:H.MAX_CROSS_SECTIONAL_DATES + 1])


def test_only_eligible_symbols_pass_on_each_date():
    frames, days = _universe(n=4)
    ast, validated = _compiled("close > 0")
    dates = days[-3:]
    eligible = {dates[0]: {"S00", "S01"}, dates[1]: {"S02"}, dates[2]: set()}
    sig = H.signals(ast, validated, frames, dates, eligible=eligible)
    assert sorted(sig.columns[sig.loc[dates[0]].to_numpy()]) == ["S00", "S01"]
    assert list(sig.columns[sig.loc[dates[1]].to_numpy()]) == ["S02"] and not sig.loc[dates[2]].any()


# --- fitness arithmetic ---------------------------------------------------------------------------------------------------------

def _closes(rows, days):
    return {s: pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=H.bar_index(days[:len(c)]))
            for s, c in rows.items()}


def test_fitness_on_hand_built_closes():
    days = _sessions(6)
    # A rises 10% a session, B flat, C falls 10%; C is delisted after day 3 (its last close is kept).
    frames = _closes({"A": [100, 110, 121, 133.1, 146.41, 161.051], "B": [100] * 6, "C": [100, 90, 81, 72.9]}, days)
    sig = pd.DataFrame(False, index=pd.Index(days[:4]), columns=["A", "B", "C"])
    sig.loc[days[0], "A"] = True
    sig.loc[days[1], ["A", "C"]] = True
    sig.loc[days[2], "B"] = True
    out = H.fitness(sig, frames, days, horizons=[1, 5])
    h1, h5 = out["horizons"]
    # h=1, day0: A +10%, universe (A, B, C) mean (10 + 0 - 10)/3 = 0 -> excess 0.10
    # day1: A +10%, C -10% -> passers mean 0, universe 0 -> excess 0; day2: B 0, universe 0 -> excess 0
    # day3: no passer. Observations 4: A(d0), A(d1), C(d1), B(d2); hits: A d0, A d1 -> 2 of 4.
    assert h1.dates == 3 and h1.observations == 4 and h1.hit_rate == 0.5
    assert h1.excess_by_date == pytest.approx([0.1, 0.0, 0.0]) and h1.passers_mean == pytest.approx(0.025)
    # h=5 only day0 has a finished horizon: A +61.05%, universe (A, B, C at its last price 72.9): (61.051 + 0 - 27.1) / 3
    assert h5.dates == 1 and h5.passers_mean == pytest.approx(0.61051) and h5.universe_mean == pytest.approx((0.61051 - 0.271) / 3)
    assert h1.low_sample and out["avg_passers"] == pytest.approx(1.0) and out["turnover"] is not None
    assert "Low sample" in H.confidence_note(h1)


def test_rebalance_dates_and_the_window():
    days = _sessions(30)
    assert H.rebalance_dates(days, days[0], days[-1], 5) == days[::5]
    first, last = H.window_days(days[10], days[20], lookback_bars=50, horizon=20)
    assert first < days[10] - timedelta(days=50) and last > days[20] + timedelta(days=20)
    assert H.FLAG in DEFAULT_OFF_FLAGS


# --- the routes on the shared lake ------------------------------------------------------------------------------------------

def _lake_rows(symbol, frame, source="nse_bhav:cm"):
    rows = []
    for ts, r in frame.iterrows():
        day = ts.tz_convert(IST).date()
        end = datetime.combine(day, dtime(15, 30), IST).astimezone(UTC)
        rows.append(MdCandleRecord(instrument_key=f"NSE:{symbol}", timeframe="day", ts=end, source=source, version=1, open=float(r["open"]),
                                   high=float(r["high"]), low=float(r["low"]), close=float(r["close"]), volume=int(r["volume"]),
                                   ingested_at=datetime(2025, 1, 1, tzinfo=UTC)))
    return rows


def _seed(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _org(tag):
    t = _tenant(_email(tag), with_credentials=False)
    for name in ("screener_v2", H.FLAG):
        _flag(name, t["tenant_id"])
    return t


def _symbols(n, tag):
    stem = f"Q{uuid.uuid4().hex[:4].upper()}{tag}"
    frames, days = _universe(n=n, bars=160, seed=11)
    renamed = {f"{stem}{i}": f for i, f in enumerate(frames.values())}
    for s, f in renamed.items():
        _seed(*_lake_rows(s, f))
    return renamed, days


def test_the_routes_are_off_by_default():
    t = _tenant(_email("off"), with_credentials=False)
    _flag("screener_v2", t["tenant_id"])
    body = {"source": SCREEN, "date": "2025-12-01", "universe": {"symbols": ["X"]}}
    r = client.post("/api/screener/history/asof", headers=t["headers"], json=body)
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == H.FLAG


def test_asof_on_a_symbol_list_matches_the_cut_run():
    t = _org("asof")
    frames, days = _symbols(6, "A")
    d = days[120]
    r = client.post("/api/screener/history/asof", headers=t["headers"],
                    json={"source": SCREEN, "date": str(d), "universe": {"symbols": list(frames)}})
    assert r.status_code == 200, r.text
    body = r.json()
    ast, validated = _compiled(SCREEN)
    # The route's answer is the cut run on the lake's own bars (prices as stored, the warm-up window as read).
    from app.screener.history_routes import Universe, load
    first, _ = H.window_days(d, d, validated.lookback["1d"], 0)

    async def lake():
        async with _session_factory() as session:
            return await load(session, Universe(symbols=list(frames)), first, d, dates_hint=[d])
    lake_frames, _, _ = _run(lake())
    assert body["matched"] == H.passers_on(ast, validated, lake_frames, d)["matched"] and body["universe"] == 6
    assert all(len(f) >= H.warmup_bars(validated.lookback["1d"]) for f in lake_frames.values())
    assert "Unadjusted bars" in body["notes"][0] and "not recommendations" in body["disclaimer"]


def test_asof_on_an_index_uses_its_membership_on_that_date():
    t = _org("index")
    frames, days = _symbols(3, "I")
    names = list(frames)
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    d = days[120]
    isins = {s: f"INX{uuid.uuid4().hex[:9].upper()}" for s in names}
    rows = [SymbolHistoryRecord(isin=isins[s], symbol=s, valid_from=date(2020, 1, 1), source="test", checksum="x") for s in names]
    # The third name joined the index after d: it is not in the universe on d.
    rows += [IndexMembershipRecord(index_code=code, isin=isins[names[0]], valid_from=days[0], source="test", checksum="x"),
             IndexMembershipRecord(index_code=code, isin=isins[names[1]], valid_from=days[0], source="test", checksum="x"),
             IndexMembershipRecord(index_code=code, isin=isins[names[2]], valid_from=days[130], source="test", checksum="x")]
    _seed(*rows)
    r = client.post("/api/screener/history/asof", headers=t["headers"], json={"source": "close > 0", "date": str(d), "universe": {"index": code}})
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == sorted(names[:2])
    early = client.post("/api/screener/history/asof", headers=t["headers"],
                        json={"source": "close > 0", "date": str(days[0] - timedelta(days=5)), "universe": {"index": code}})
    assert early.status_code == 422 and "known from" in early.text


def test_fitness_records_a_trial_and_deflates_by_the_search():
    t = _org("fit")
    frames, days = _symbols(8, "F")
    body = {"source": SCREEN, "start": str(days[60]), "end": str(days[130]), "every": "weekly", "universe": {"symbols": list(frames)}}
    first = client.post("/api/screener/history/fitness", headers=t["headers"], json=body)
    assert first.status_code == 200, first.text
    out = first.json()
    assert [h["horizon"] for h in out["horizons"]] == [1, 5, 20] and out["dates"] == len(days[60:131:5])
    assert out["deflation"]["n_trials"] == 1 and "not a recommendation" in out["disclaimer"] and out["main_horizon"] == 5
    second = client.post("/api/screener/history/fitness", headers=t["headers"], json={**body, "source": "close > SMA(close, 50)"}).json()
    assert second["deflation"]["n_trials"] == 2

    async def trials():
        async with _session_factory() as session:
            return list(await session.scalars(select(ResearchTrialRecord).where(ResearchTrialRecord.tenant_id == t["tenant_id"])))
    rows = _run(trials())
    assert len(rows) == 2 and all(json.loads(r.dsl_json)["kind"] == "screen_fitness" for r in rows)


def test_refusals_holdout_timeframe_and_data(monkeypatch):
    t = _org("refuse")
    frames, days = _symbols(2, "R")
    base = {"universe": {"symbols": list(frames)}, "date": str(days[120])}
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", str(days[100]))
    sealed = client.post("/api/screener/history/asof", headers=t["headers"], json={**base, "source": SCREEN})
    assert sealed.status_code == 422 and "holdout" in sealed.text
    monkeypatch.delenv("BACKTEST_HOLDOUT_START")
    other_tf = client.post("/api/screener/history/asof", headers=t["headers"], json={**base, "source": "RSI(14, tf=\"1h\") > 50"})
    assert other_tf.status_code == 422
    chain = client.post("/api/screener/history/asof", headers=t["headers"], json={**base, "source": "PCR() > 1"})
    assert chain.status_code == 422
    both = client.post("/api/screener/history/asof", headers=t["headers"], json={"source": SCREEN, "date": base["date"],
                                                                                  "universe": {"index": "NIFTY50", "symbols": ["A"]}})
    assert both.status_code == 422


# --- review of S6a ---------------------------------------------------------------------------------------------------------------

def _sweep_cases():
    """One daily screen per registry function the history path runs (no data needs, not cross-sectional, daily): its own
    Z10-a case made a condition."""
    from app.quality import causal_cases as C
    from app.screener.registry import BOOL, CAT, FUNCTIONS
    out = []
    for name, spec in sorted(FUNCTIONS.items()):
        if spec.needs or spec.cross_sectional or (spec.timeframes and "1d" not in spec.timeframes):
            continue
        texts = [t for t in (C.SCREENQL_SERIES.get(name, []) + C.SCREENQL_LAST_BAR.get(name, [])) if "@" not in t]
        if not texts:
            continue
        e = texts[0]
        _, v = compile_screen(e, base_tf="1d")
        if spec.returns == BOOL or v.ok:
            cond = e
        elif spec.returns == CAT:
            if not spec.values:
                continue
            cond = f'{e} == "{spec.values[0]}"'
        elif spec.timeframed and e.endswith(")"):
            cond = f"{e} > {e}[1]"
        else:
            cond = f"({e}) > 0"
        out.append(pytest.param(name, cond, id=name))
    return out


@pytest.mark.parametrize("name, cond", _sweep_cases())
def test_every_registry_function_reads_the_same_fast_as_on_cut_bars(name, cond):
    """HIGH: the fast path (one series read at each date) equals the as-of run on bars cut at each date, for every
    function the history path takes - including the last-bar-only ones, which now run date by date."""
    frames, days = _universe(n=3, bars=260, seed=17)
    ast, validated = compile_screen(cond, base_tf="1d")
    assert validated.ok, (cond, validated.problems)
    dates = days[200::6]
    sig = H.signals(ast, validated, frames, dates)
    for d in dates:
        assert sorted(sig.columns[sig.loc[d].to_numpy()]) == H.passers_on(ast, validated, frames, d)["matched"], (name, d)


def test_a_last_bar_only_screen_runs_date_by_date_and_the_fast_path_refuses_a_scalar():
    frames, days = _universe(n=4, bars=300, seed=4)
    ast, validated = _compiled("PatternBullish()")
    assert H.last_bar_only_calls(ast) == ["PatternBullish"]
    dates = days[-30:]
    sig = H.signals(ast, validated, frames, dates)
    for d in dates:
        assert sorted(sig.columns[sig.loc[d].to_numpy()]) == H.passers_on(ast, validated, frames, d)["matched"]
    with pytest.raises(H.HistoryError, match="last bar only"):
        H.series_at_dates(ast, validated, frames, dates)                 # never read back in time
    with pytest.raises(H.HistoryError, match="last bar only"):
        H.signals(ast, validated, frames, days[:H.MAX_CROSS_SECTIONAL_DATES + 1])


def test_a_screen_reads_the_prices_known_at_each_date():
    """MED: a split after d is not known at d - an absolute-level screen reads the bars as of d (scales), a scale-free one
    is unchanged."""
    days = _sessions(80)
    adjusted = _walk(2, days, start=100.0, vol=0.001)                # one share of today: ~100 all along
    split_at = days[60]                                               # 1:2 split - before it a share traded at ~200
    scales = {"X": {d: ((0.5, 2.0) if d < split_at else (1.0, 1.0)) for d in days}}
    frames = {"X": adjusted}
    level, lv = _compiled("close > 150")
    dates = [days[50], days[70]]
    assert not H.signals(level, lv, frames, dates).loc[days[50], "X"]           # fully adjusted: ~100, a level not known then
    sig = H.signals(level, lv, frames, dates, scales=scales)
    assert sig.loc[days[50], "X"] and not sig.loc[days[70], "X"]                # ~200 before the split, ~100 after
    assert H.passers_on(level, lv, frames, days[50], scales=scales)["matched"] == ["X"]
    ratio, rv = _compiled("close > SMA(close, 5) * 0.5")
    assert H.signals(ratio, rv, frames, dates).equals(H.signals(ratio, rv, frames, dates, scales=scales))


def test_overlapping_horizons_use_independent_dates_and_delistings_are_counted():
    """MED: with daily dates and a 5-session horizon the t-statistic uses one date in five; LOW: a pick with no bar at
    d + h is counted; excluded pairs (a split inside unadjusted bars) are left out."""
    frames, days = _universe(n=6, bars=160, seed=8)
    frames["S05"] = frames["S05"].iloc[:120]                          # delisted inside the window
    ast, validated = _compiled("close > 0")
    dates = days[100:140]
    sig = H.signals(ast, validated, frames, dates)
    stats = H.fitness(sig, frames, days, horizons=[5])
    st = stats["horizons"][0]
    assert len(st.independent_excess) == math.ceil(len(st.excess_by_date) / 5) or len(st.independent_excess) <= len(st.excess_by_date) // 5 + 1
    assert st.as_dict()["independent_dates"] == len(st.independent_excess) and st.no_bar_at_horizon > 0
    excluded = {("S00", d) for d in dates}
    st2 = H.fitness(sig, frames, days, horizons=[5], excluded=excluded)["horizons"][0]
    assert st2.observations == st.observations - sum(1 for d in dates if days.index(d) + 5 < len(days))


def test_a_forward_window_that_crosses_the_holdout_is_refused(monkeypatch):
    """LOW: the dates end before the holdout, but their 20-session horizon would read into it - refused."""
    t = _org("holdfwd")
    frames, days = _symbols(2, "H")
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", str(days[150]))
    body = {"source": SCREEN, "universe": {"symbols": list(frames)}, "start": str(days[120]), "end": str(days[145]), "horizons": [20]}
    r = client.post("/api/screener/history/fitness", headers=t["headers"], json=body)
    assert r.status_code == 422 and "horizon" in r.text and "holdout" in r.text
    short = client.post("/api/screener/history/fitness", headers=t["headers"], json={**body, "end": str(days[125]), "horizons": [1]})
    assert short.status_code != 422 or "holdout" not in short.text
