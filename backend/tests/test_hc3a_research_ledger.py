"""H-C3a: the research study's append-only trial ledger and its honest report - the chosen trial is deflated by every
trial tried (Deflated Sharpe), overfitting is estimated across trials (PBO by CSCV), invalid drafts still count as
drafts, the out-of-sample check is reported as not run until it is, and the window never reaches the sealed holdout."""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.ai import research
from app.backtest.data_policy import HoldoutError
from app.db.models import ResearchTrialRecord
from tests.test_auth_api import _session_factory
from tests.test_phase_l_ai import _owner

DAYS = 120


def _run(coro):
    return asyncio.run(coro)


def _record_all(tenant_id, user_id, study, specs):
    async def go():
        async with _session_factory() as session:
            for i, (status, returns) in enumerate(specs):
                await research.record_trial(session, tenant_id=tenant_id, user_id=user_id, study_id=study, dsl={"rule": i},
                                            symbol="nifty 50", exchange="NSE", timeframe="15m", status=status,
                                            reason=None if status == "ok" else "did not validate", metrics={"trades": 40},
                                            returns=returns)
            await session.commit()
            return await research.study_trials(session, tenant_id, study)
    return _run(go())


def test_noise_trials_are_deflated_and_flagged_as_overfit_risk():
    _, me = _owner("hc3a-noise@example.com")
    rng = np.random.default_rng(7)
    specs = [("ok", list(rng.normal(0, 0.01, DAYS))) for _ in range(12)] + [("invalid", None)]
    trials = _record_all(me["tenant_id"], me["id"], str(uuid.uuid4()), specs)
    assert [t.seq for t in trials] == list(range(1, 14)) and trials[0].symbol == "NIFTY 50"
    report = research.study_report(trials)
    assert (report["trials"], report["backtested"], report["invalid"]) == (13, 12, 1)
    assert report["deflated"]["n_trials"] == 12 and report["deflated"]["dsr"] < 0.5          # luck, once the search is counted
    assert report["pbo"]["pbo"] is not None and report["pbo"]["pbo"] >= 0.2
    assert report["out_of_sample"]["run"] is False and "It is not a recommendation" in report["disclaimer"]
    assert "Chosen from 12 backtested trials (13 drafts)" in report["summary"] and "weak" in report["summary"]
    assert len(report["not_simulated"]) >= 4


def test_a_real_edge_survives_deflation():
    _, me = _owner("hc3a-edge@example.com")
    rng = np.random.default_rng(11)
    specs = [("ok", list(rng.normal(0, 0.01, DAYS))) for _ in range(5)] + [("ok", list(rng.normal(0.004, 0.01, DAYS)))]
    report = research.study_report(_record_all(me["tenant_id"], me["id"], str(uuid.uuid4()), specs), oos={"run": True, "sharpe_daily": 0.3})
    assert report["chosen"]["seq"] == 6 and report["deflated"]["dsr"] > 0.95 and "strong" in report["summary"]
    assert report["out_of_sample"] == {"run": True, "sharpe_daily": 0.3}


def test_more_trials_raise_the_bar():
    _, me = _owner("hc3a-bar@example.com")
    rng = np.random.default_rng(3)
    winner = list(rng.normal(0.0015, 0.01, DAYS))
    few = research.study_report(_record_all(me["tenant_id"], me["id"], str(uuid.uuid4()),
                                            [("ok", winner)] + [("ok", list(rng.normal(0, 0.01, DAYS))) for _ in range(2)]))
    many = research.study_report(_record_all(me["tenant_id"], me["id"], str(uuid.uuid4()),
                                             [("ok", winner)] + [("ok", list(rng.normal(0, 0.01, DAYS))) for _ in range(30)]))
    assert many["deflated"]["sr0"] > few["deflated"]["sr0"]                                     # the expected best of N grows with N


def test_a_draft_that_never_trades_is_weak_evidence_not_moderate():
    _, me = _owner("hc3a-flat@example.com")
    report = research.study_report(_record_all(me["tenant_id"], me["id"], str(uuid.uuid4()), [("ok", [0.0] * DAYS), ("ok", [0.0] * DAYS)]))
    assert report["deflated"]["dsr"] is None and "no evidence" in report["deflated"]["note"] and "evidence weak" in report["summary"]


def test_the_cut_falls_on_a_session_start():
    idx = pd.DatetimeIndex([pd.Timestamp(f"2026-03-0{d} 03:45", tz="UTC") + pd.Timedelta(minutes=15 * i) for d in (2, 3, 4, 5, 6) for i in range(25)])
    _, cut, _ = research.split_window(idx, 0.3)
    assert cut == pd.Timestamp("2026-03-06 03:45", tz="UTC")                                       # 09:15 IST, not mid-session
    is_days = research.trading_days(idx[idx < cut])
    assert set(is_days).isdisjoint(research.trading_days(idx[idx >= cut]))


def test_the_ledger_is_tenant_scoped_and_needs_returns_for_ok_trials():
    _, me = _owner("hc3a-scope@example.com")
    _, other = _owner("hc3a-scope-other@example.com")
    study = str(uuid.uuid4())
    _record_all(me["tenant_id"], me["id"], study, [("ok", [0.01, -0.01, 0.0])])

    async def go():
        async with _session_factory() as session:
            mine = await research.study_trials(session, me["tenant_id"], study)
            theirs = await research.study_trials(session, other["tenant_id"], study)
            with pytest.raises(ValueError):
                await research.record_trial(session, tenant_id=me["tenant_id"], user_id=None, study_id=study, dsl={}, symbol="X",
                                            exchange="NSE", timeframe="1d", status="ok", returns=[])
            with pytest.raises(ValueError):
                await research.record_trial(session, tenant_id=me["tenant_id"], user_id=None, study_id=study, dsl={}, symbol="X",
                                            exchange="NSE", timeframe="1d", status="maybe")
            return mine, theirs
    mine, theirs = _run(go())
    assert len(mine) == 1 and theirs == []
    empty = research.study_report([])
    assert empty["chosen"] is None and "nothing to choose" in empty["summary"]


def test_daily_returns_and_the_window_never_touch_the_holdout(monkeypatch):
    days = [date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11)]
    trades = [SimpleNamespace(exit_time=datetime(2026, 3, 10, 5, 0, tzinfo=timezone.utc), pnl=500.0),
              SimpleNamespace(exit_time=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc), pnl=-200.0),
              SimpleNamespace(exit_time=datetime(2026, 3, 10, 19, 0, tzinfo=timezone.utc), pnl=100.0),      # 00:30 IST next day
              SimpleNamespace(exit_time=datetime(2026, 3, 12, 5, 0, tzinfo=timezone.utc), pnl=9999.0),     # outside the window: ignored
              SimpleNamespace(exit_time=None, pnl=None)]
    assert research.daily_returns(trades, 100_000, days) == [0.0, 0.003, 0.001]
    with pytest.raises(ValueError):
        research.daily_returns(trades, 0, days)
    index = pd.date_range("2026-01-01", periods=60, freq="1D", tz="UTC")
    start, cut, end = research.split_window(index, 0.25)
    assert start == index[0] and end == index[-1] and index[0] < cut < index[-1] and cut == index[45]
    with pytest.raises(HoldoutError):
        research.split_window(index, 0.25, boundary=pd.Timestamp("2026-02-01", tz="UTC"))
    with pytest.raises(ValueError):
        research.split_window(index, 0.95)


def test_trials_are_never_rewritten():
    _, me = _owner("hc3a-append@example.com")
    study = str(uuid.uuid4())
    _record_all(me["tenant_id"], me["id"], study, [("ok", [0.01, 0.0, -0.01])])
    _record_all(me["tenant_id"], me["id"], study, [("error", None)])

    async def rows():
        async with _session_factory() as session:
            return list(await session.scalars(select(ResearchTrialRecord).where(ResearchTrialRecord.study_id == study)
                                              .order_by(ResearchTrialRecord.id)))
    got = _run(rows())
    assert [(r.seq, r.status) for r in got] == [(1, "ok"), (2, "error")] and got[0].dsl_hash == research.dsl_hash({"rule": 0})
    assert timedelta(0) <= got[1].created_at.replace(tzinfo=timezone.utc) - got[0].created_at.replace(tzinfo=timezone.utc)
