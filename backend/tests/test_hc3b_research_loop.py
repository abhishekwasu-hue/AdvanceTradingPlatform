"""H-C3b: the research loop - drafts are validated, backtested in-sample only, every draft (valid or not) is a ledger
trial, the proposer sees earlier metrics (diagnose/revise), the draft cap holds, the chosen draft alone gets one
out-of-sample run, the sealed holdout is refused before any work, and a failing proposer ends the study cleanly."""
import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.ai import research, research_loop
from app.ai.research_loop import StudyInput
from app.ai.strategist import cond, ind, val
from app.backtest.data_policy import HoldoutError
from app.core.models import RiskConfig
from tests.test_auth_api import _session_factory
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def _frame(days=70, per_day=25, seed=4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx, t = [], datetime(2026, 1, 5, 3, 45, tzinfo=timezone.utc)                          # 09:15 IST
    d = 0
    while len(idx) < days * per_day:
        day = t + timedelta(days=d)
        d += 1
        if day.weekday() >= 5:
            continue
        idx += [day + timedelta(minutes=15 * i) for i in range(per_day)]
    close = 100 + np.cumsum(rng.normal(0, 0.4, len(idx)))
    return pd.DataFrame({"open": close - 0.1, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1000.0},
                        index=pd.DatetimeIndex(idx))


def _draft(name, fast, slow):
    return {"name": name, "long_conditions": [cond(ind("EMA", fast), "GT", ind("EMA", slow)).model_dump()],
            "short_conditions": [cond(ind("RSI", 14), "GT", val(70)).model_dump()], "stop_loss_atr_mult": 1.5}


def _study(me, propose, *, max_drafts=8, boundary=None, frame=None):
    async def go():
        async with _session_factory() as session:
            out = await research_loop.run_study(session, tenant_id=me["tenant_id"], user_id=me["id"],
                                                spec=StudyInput("EMA trend on NIFTY", "NIFTY 50", "NSE", "15min", max_drafts),
                                                frame=_frame() if frame is None else frame, risk=RiskConfig(), propose=propose,
                                                holdout_boundary=boundary)
            rows = await research.study_trials(session, me["tenant_id"], out["study_id"])
            out["oos_rows"] = [t for t in rows if t.status == "oos"]                         # the stored OOS check, not a trial
            return out, [t for t in rows if t.status != "oos"]
    return _run(go())


def test_drafts_are_validated_backtested_recorded_and_the_chosen_one_gets_oos(monkeypatch):
    _, me = _owner("hc3b-loop@example.com")
    queue = [_draft("a", 9, 21), {"name": "empty"}, _draft("b", 5, 34)]
    seen, windows = [], []
    real = research_loop._backtest

    async def spy(strategy, df, symbol, timeframe, risk):
        windows.append((df.index[0], df.index[-1]))
        return await real(strategy, df, symbol, timeframe, risk)
    monkeypatch.setattr(research_loop, "_backtest", spy)

    async def propose(idea, history):
        seen.append([h.get("status") for h in history])
        return queue.pop(0) if queue else None
    out, trials = _study(me, propose)
    assert [t.status for t in trials] == ["ok", "invalid", "ok"] and "at least one" in trials[1].reason
    assert seen == [[], ["ok"], ["ok", "invalid"], ["ok", "invalid", "ok"]]                 # the proposer reads earlier trials
    report = out["report"]
    assert (report["trials"], report["backtested"], report["invalid"]) == (3, 2, 1)
    oos_from = pd.Timestamp(report["out_of_sample"]["from"])
    assert report["out_of_sample"]["run"] is True and "trades" in report["out_of_sample"]["metrics"]
    assert len(windows) == 3                                                                   # 2 in-sample runs + ONE out-of-sample run
    assert len(out["oos_rows"]) == 1 and out["oos_rows"][0].dsl_hash == trials[report["chosen"]["seq"] - 1].dsl_hash
    assert all(end < oos_from for _, end in windows[:2]) and windows[2][0] == oos_from          # in-sample never sees OOS bars
    assert report["deflated"]["n_trials"] == 2 and "Chosen from 2 backtested trials (3 drafts)" in report["summary"]


def test_the_draft_cap_holds_and_the_holdout_is_refused_before_any_work():
    _, me = _owner("hc3b-cap@example.com")
    calls = []

    async def endless(idea, history):
        calls.append(1)
        return _draft(f"d{len(calls)}", 3 + len(calls), 30)
    out, trials = _study(me, endless, max_drafts=50)
    assert len(trials) == research_loop.MAX_DRAFTS == len(calls)
    with pytest.raises(HoldoutError):
        _study(me, endless, boundary=pd.Timestamp("2026-02-01", tz="UTC"))
    assert len(calls) == research_loop.MAX_DRAFTS                                              # nothing proposed after the refusal


def test_a_failing_proposer_ends_the_study_and_keeps_what_was_tried():
    _, me = _owner("hc3b-fail@example.com")
    state = {"n": 0}

    async def flaky(idea, history):
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("provider down")
        return _draft("only", 9, 21)
    out, trials = _study(me, flaky)
    assert [t.status for t in trials] == ["ok"] and out["report"]["backtested"] == 1
    assert out["report"]["pbo"]["pbo"] is None                                                 # one trial: no overfitting estimate

    async def nothing(idea, history):
        return None
    empty, none = _study(me, nothing)
    assert none == [] and empty["report"]["chosen"] is None and empty["report"]["out_of_sample"]["run"] is False


def test_the_prompt_carries_metrics_not_data():
    text = research_loop.draft_prompt("idea", [{"seq": 1, "status": "ok", "metrics": {"trades": 12}}], {"type": "object"})
    assert '"earlier_trials"' in text and '"trades": 12' in text and "close" not in text
