"""Z7 / U6 BK5: breakout screen fitness (spec §6, SCREENER_SPEC §2.6) - the forward returns and failed-break rate of each
template's past matches by grade, timeframe and volatility regime, small samples marked; the nightly pass over the SHARED
lake's daily bars (flag screener_breakouts_fitness, off by default); the endpoint; and the template catalogue guards."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.screener.breakouts import fitness as fit
from app.screener.breakouts import fitness_job, templates
from app.screener.breakouts.quality import BreakoutRun, run_breakout
from app.screener.breakouts.screens import SCREENS
from tests.breakout_fixtures import TRUE_BREAK, base_then
from tests.sample_market import generate
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z7_bk1_api import _set


def _run(coro):
    return asyncio.run(coro)


# -------------------------------------------------------------------------------------------------------------- the math
def _fake_run(signals, tf="1d"):
    run = BreakoutRun("B1", tf, ("1w",), {})
    run.signals = signals
    return run


def test_forward_returns_are_signed_complete_and_in_r():
    f = pd.DataFrame({"close": [100.0, 101.0, 102.0, 99.0, 104.0, 110.0, 108.0]})
    sig = [{"side": "up", "bar": 1, "grade": "A", "status": "accepted", "stop_ref": 99.0, "checks": {"regime": "low"}},
           {"side": "down", "bar": 3, "grade": "C", "status": "failed", "stop_ref": 101.0, "checks": {"regime": "high"}},
           {"side": "up", "bar": 5, "grade": "B", "status": "pending", "stop_ref": 107.0, "checks": {}}]
    rows = fit.outcomes(_fake_run(sig), f, horizons=(1, 5))
    assert rows[0]["forward_pct"] == {1: pytest.approx(100 / 101), 5: pytest.approx(7 / 101 * 100)}
    assert rows[0]["forward_r"][1] == pytest.approx(1.0 / 2.0)                     # +1 over a 2-point stop distance
    assert rows[1]["forward_pct"][1] == pytest.approx(-5 / 99 * 100) and rows[1]["forward_pct"][5] is None   # signed; no partial horizon
    assert rows[1]["failed"] is True and rows[0]["failed"] is False and rows[2]["failed"] is None             # pending: not decided
    assert rows[2]["regime"] == "unknown" and rows[2]["forward_pct"][1] == pytest.approx(-2 / 110 * 100)


def test_buckets_and_the_low_sample_mark():
    rows = [{"side": "up", "grade": g, "regime": r, "timeframe": "1d", "failed": f, "forward_pct": {1: v, 5: None}, "forward_r": {1: v / 2, 5: None}}
            for g, r, f, v in (("A", "low", False, 2.0), ("A", "low", True, -1.0), ("B", "high", None, 3.0), ("C", "normal", False, 0.0))]
    agg = fit.aggregate(rows, horizons=(1, 5), min_samples=3)
    o = agg["overall"]
    assert o["signals"] == 4 and o["failed_rate"] == pytest.approx(1 / 3, abs=1e-4) and o["failed_samples"] == 3 and not o["low_sample"]
    assert o["horizons"]["1"] == {"samples": 4, "low_sample": False, "mean_pct": 1.0, "median_pct": 1.0, "hit_rate": 0.5, "mean_r": 0.5}
    assert o["horizons"]["5"]["samples"] == 0 and o["horizons"]["5"]["mean_pct"] is None and o["horizons"]["5"]["low_sample"]
    assert list(agg["by_grade"]) == ["A", "B", "C"] and agg["by_grade"]["A"]["low_sample"] and agg["by_grade"]["A"]["signals"] == 2
    assert list(agg["by_regime"]) == ["low", "normal", "high"] and agg["by_timeframe"]["1d"]["signals"] == 4
    assert "not a recommendation" in agg["disclaimer"]


def test_template_fitness_over_symbols_is_deterministic_and_matches_the_engine():
    frames = {f"S{i}": {"1d": generate(500, 100.0, i, daily=True)} for i in range(1, 5)}
    frames["SHORT"] = {"1d": generate(5, 100.0, 9, daily=True).iloc[:0]}           # no bars: skipped, said so
    t = templates.BY_ID["daily_swing_break"]
    params = templates.template_params(t, {"swing_degree": 0})
    a = fit.template_fitness("B2", "1d", frames, params)
    assert a == fit.template_fitness("B2", "1d", frames, params)
    want = sum(len(run_breakout("B2", fr, "1d", params=params, within=None).signals) for k, fr in frames.items() if k != "SHORT")
    assert a["overall"]["signals"] == want > 30 and a["symbols"] == 4 and "SHORT" in a["skipped"]
    assert set(a["by_grade"]) <= {"A", "B", "C"} and a["overall"]["horizons"]["20"]["samples"] <= a["overall"]["horizons"]["1"]["samples"]


def test_appending_bars_never_changes_a_decided_outcome():
    f = generate(400, 100.0, 3, daily=True)
    params = {"htf_filter": "off"}
    full = fit.outcomes(run_breakout("B1", {"1d": f}, "1d", params=params, within=None), f)
    part = fit.outcomes(run_breakout("B1", {"1d": f.iloc[:300]}, "1d", params=params, within=None), f.iloc[:300])
    by_bar = {(r["bar"], r["side"]): r for r in full}
    for r in part:
        other = by_bar[(r["bar"], r["side"])]
        assert r["grade"] == other["grade"] and r["regime"] == other["regime"]
        for h, v in r["forward_pct"].items():
            if v is not None:
                assert other["forward_pct"][h] == pytest.approx(v)


# -------------------------------------------------------------------------------------------- the templates and the docs
def test_every_screen_has_a_template_and_every_template_compiles():
    from app.screener import compile_screen
    assert {t.screen for t in templates.TEMPLATES} == set(SCREENS)
    for t in templates.TEMPLATES:
        text, why = templates.screenql(t.screen, t.signal_tf, templates.template_params(t))
        assert text is not None and compile_screen(text, base_tf=t.signal_tf)[1].ok, (t.id, why)


def test_the_eod_templates_are_the_daily_and_weekly_ones():
    ids = {t.id for t in fitness_job.eod_templates()}
    assert {"daily_52w_high", "weekly_52w_high", "daily_vcp", "daily_flag", "daily_range_retest"} <= ids
    assert not any(templates.BY_ID[i].signal_tf not in ("1d", "1w") for i in ids)


# ------------------------------------------------------------------------------------------------ the nightly pass + API
NOW = datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)          # 21:30 IST


async def _write_daily(key, frame, lag=timedelta(hours=4), source="vendor:test", version=1):
    from app.db.models import MdCandleRecord
    async with _session_factory() as session:
        for ts, row in frame.iterrows():
            end = ts.normalize() + pd.Timedelta(hours=10)               # the lake labels a daily bar by its END (15:30 IST)
            session.add(MdCandleRecord(instrument_key=key, timeframe="1d", ts=end.to_pydatetime(), source=source, version=version, open=row.open,
                                       high=row.high, low=row.low, close=row.close, volume=int(row.volume), ingested_at=(end + lag).to_pydatetime()))
        await session.commit()


async def _clear():
    from sqlalchemy import delete

    from app.db.models import BreakoutFitnessRunRecord, MdCandleRecord
    async with _session_factory() as session:
        await session.execute(delete(BreakoutFitnessRunRecord))
        await session.execute(delete(MdCandleRecord).where(MdCandleRecord.source.in_(["vendor:test", "stream:kite", "broker:kite"])))
        await session.commit()


@pytest.fixture
def lake(monkeypatch):
    _run(_clear())
    keys = ["NSE:FITA", "NSE:FITB"]
    end = pd.Timestamp("2026-10-08", tz="UTC")
    for i, k in enumerate(keys):
        _run(_write_daily(k, generate(320, 100.0, 11 + i, daily=True, end=end.to_pydatetime())))
    monkeypatch.setattr(fitness_job.config, "BREAKOUT_FITNESS_SYMBOLS", keys + ["NSE:NOBARS"])
    monkeypatch.setattr(fitness_job, "eod_templates", lambda: [templates.BY_ID["daily_52w_high"], templates.BY_ID["daily_swing_break"]])
    yield keys
    _run(_clear())


def test_the_pass_reads_only_closed_ingested_shared_bars(lake):
    async def go():
        async with _session_factory() as session:
            f = await fitness_job.load_daily(session, "NSE:FITA", NOW)
            early = await fitness_job.load_daily(session, "NSE:FITA", NOW - timedelta(days=2))
        return f, early
    f, early = _run(go())
    assert len(f) > 300 and list(f.columns) == ["open", "high", "low", "close", "volume"] and f.index.tz is not None
    assert len(early) < len(f) and early.index[-1] < f.index[-1]


def test_the_nightly_pass_stores_each_template_once_and_resumes(lake, monkeypatch):
    out = _run(fitness_job.run_nightly(_session_factory, now=NOW))
    assert out["complete"] and out["stored"] == 2 and out["symbols"] == 2 and out["no_data"] == 1
    again = _run(fitness_job.run_nightly(_session_factory, now=NOW))
    assert again["complete"] and again["stored"] == 0 and again["exists"] == 2

    async def rows():
        async with _session_factory() as session:
            return await fitness_job.latest(session)
    got = _run(rows())
    assert [r["template_id"] for r in got] == ["daily_52w_high", "daily_swing_break"]
    assert got[0]["as_of"] == "2026-10-09" and got[0]["symbols"] == 2 and got[0]["stats"]["horizons"] == [1, 5, 20]
    assert got[1]["signals"] == got[1]["stats"]["overall"]["signals"]
    _run(_clear())
    budget = _run(fitness_job.run_nightly(_session_factory, now=NOW, budget_seconds=0))
    assert not budget["complete"] and budget["pending"] == 2


def test_nightly_due_waits_for_the_hour_and_runs_once_a_day():
    late = datetime(2026, 10, 9, 15, 45, tzinfo=timezone.utc)           # 21:15 IST
    assert fitness_job.nightly_due(datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc), None) is None
    assert fitness_job.nightly_due(late, None).isoformat() == "2026-10-09"
    assert fitness_job.nightly_due(late, late.date()) is None


@pytest.fixture
def screener_on():
    _set(screener_v2=True, screener_breakouts=True, screener_breakouts_fitness=False, screener_breakouts_bk3=False, screener_breakouts_bk4=False)
    yield
    _set(screener_v2=False, screener_breakouts=False, screener_breakouts_fitness=False, screener_breakouts_bk3=False, screener_breakouts_bk4=False)


def test_the_flag_is_off_by_default_and_the_pass_does_not_start():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    assert "screener_breakouts_fitness" in FEATURE_FLAGS and "screener_breakouts_fitness" in DEFAULT_OFF_FLAGS
    assert "places no orders" in FEATURE_FLAGS["screener_breakouts_fitness"]

    async def enabled():
        async with _session_factory() as session:
            return await fitness_job.any_enabled(session)
    assert _run(enabled()) is False


def test_the_endpoint_is_gated_and_lists_visible_templates(screener_on, lake):
    headers, _ = _owner("bk5-fitness@example.com")
    assert client.get("/api/screener/breakouts/fitness", headers=headers).status_code == 503
    _set(screener_breakouts_fitness=True)
    assert _run(fitness_job.run_nightly(_session_factory, now=NOW))["stored"] == 2
    body = client.get("/api/screener/breakouts/fitness", headers=headers).json()
    assert [r["template_id"] for r in body["rows"]] == ["daily_52w_high", "daily_swing_break"]
    assert "not a recommendation" in body["disclaimer"] and body["horizons"] == [1, 5, 20]
    one = client.get("/api/screener/breakouts/fitness?template_id=daily_52w_high", headers=headers).json()
    assert [r["template_id"] for r in one["rows"]] == ["daily_52w_high"]
    assert client.get("/api/screener/breakouts/fitness?template_id=daily_vcp", headers=headers).status_code == 404   # BK3 off
    assert client.get("/api/screener/breakouts/fitness?template_id=nope", headers=headers).status_code == 404
    stats = one["rows"][0]["stats"]
    assert json.dumps(stats) and set(stats) >= {"overall", "by_grade", "by_regime", "by_timeframe", "min_samples"}


def test_the_worker_hook_starts_the_pass_only_when_due_and_enabled(lake, monkeypatch):
    from app.workers.trading_worker import TradingWorker
    worker = TradingWorker.__new__(TradingWorker)
    worker.session_factory = _session_factory
    worker._last_breakout_fitness_day = None
    worker._breakout_fitness_task = None

    async def go(flag_on):
        monkeypatch.setattr(fitness_job, "any_enabled", lambda session: _const(flag_on))
        from app.price_action.zones import job as zones_job
        monkeypatch.setattr(zones_job, "eod_ready", lambda session, day, now: _const(True))
        async with _session_factory() as session:
            started = await worker._maybe_start_breakout_fitness(session, NOW)
        if worker._breakout_fitness_task is not None:
            await worker._breakout_fitness_task
        return started
    assert _run(go(False)) is False
    assert _run(go(True)) is True and worker._last_breakout_fitness_day.isoformat() == "2026-10-09"
    assert _run(go(True)) is False                                         # done for the day


async def _const(v):
    return v


def test_the_pass_never_reads_tenant_or_user_bars():
    import inspect
    src = inspect.getsource(fitness_job)
    code = src.split('"""', 2)[2]                                          # the code, not the module notes
    assert "md_tenant_candles" not in code and "MdTenantCandle" not in code and "tenant_store" not in code
    assert "fetch_frames" not in code and "build_adapter" not in code and "MdCandleRecord" not in code   # reads via the chart layer's shared-lake reader
    assert np.isfinite(1.0)


def test_a_breakout_screen_fixture_has_fitness_rows():
    f = base_then([TRUE_BREAK] + [(103.8 + i, 105.5 + i, 103.5 + i, 105.2 + i, 1500.0) for i in range(25)])
    run = run_breakout("B1", {"1d": f}, "1d", within=None)
    rows = fit.outcomes(run, f)
    assert rows and rows[0]["forward_pct"][20] > 0 and rows[0]["failed"] is False


def test_a_broker_or_stream_row_never_reaches_the_fitness(lake):
    """BK5 review (MED): daily rows an organisation's broker session or stream wrote (`broker:` / `stream:`, even at a
    higher version) never reach the shared fitness - only the shared-licensed sources are read."""
    async def frames():
        async with _session_factory() as session:
            return await fitness_job.load_daily(session, "NSE:FITA", NOW)
    before = _run(frames())
    before_run = _run(fitness_job.run_nightly(_session_factory, now=NOW))

    async def stats():
        async with _session_factory() as session:
            return await fitness_job.latest(session)
    clean = _run(stats())
    end = pd.Timestamp("2026-10-08", tz="UTC")
    odd = generate(320, 5.0, 99, daily=True, end=end.to_pydatetime())                   # a different series, version 9
    _run(_write_daily("NSE:FITA", odd, source="stream:kite", version=9))
    _run(_write_daily("NSE:FITA", odd, source="broker:kite", version=9))
    after = _run(frames())
    pd.testing.assert_frame_equal(before, after)
    from sqlalchemy import delete

    from app.db.models import BreakoutFitnessRunRecord

    async def wipe():
        async with _session_factory() as session:
            await session.execute(delete(BreakoutFitnessRunRecord))
            await session.commit()
    _run(wipe())
    again = _run(fitness_job.run_nightly(_session_factory, now=NOW))
    assert before_run["complete"] and again["complete"]
    assert [r["stats"] for r in _run(stats())] == [r["stats"] for r in clean]


def test_latest_reads_each_template_s_newest_day_only(lake):
    """BK5 review (LOW): `latest` asks for each template's newest stored day (a grouped max), not every day."""
    _run(fitness_job.run_nightly(_session_factory, now=NOW - timedelta(days=1)))
    _run(fitness_job.run_nightly(_session_factory, now=NOW))
    from sqlalchemy import event

    from tests.test_auth_api import _engine
    rows_seen = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "breakout_fitness_runs" in statement:
            rows_seen.append(statement)

    async def go():
        async with _session_factory() as session:
            return await fitness_job.latest(session)
    event.listen(_engine.sync_engine, "before_cursor_execute", capture)
    try:
        got = _run(go())
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", capture)
    assert [r["as_of"] for r in got] == ["2026-10-09", "2026-10-09"] and len(got) == 2
    assert rows_seen and "max(" in rows_seen[0].lower() and "group by" in rows_seen[0].lower()
