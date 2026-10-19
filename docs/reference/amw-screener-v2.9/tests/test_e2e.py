"""End-to-end workflows on SIMULATED fixture data with mocked AI and Telegram (no network, no paid calls).

A: scan -> canonical signal -> annotated chart -> report -> Telegram message all reference the SAME signal record.
B: natural-language strategy -> (mocked) AI draft -> validation -> publish -> deterministic backtest -> report data.
E: backtest integrity: same inputs give identical results; entries after signals; costs reduce returns.
"""
import json
import time

import numpy as np
import pandas as pd
import pytest

from src.ai import crypto
from src.data.fixture_provider import FixtureProvider
from src.services.app_services import Services
from tests.conftest import make_ohlc


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    return Services(db_url=f"sqlite:///{tmp_path}/e2e.db", provider=FixtureProvider(end="2026-10-16"))


def _scan(svc, symbols="AAA BBB CCC DDD EEE FFF GGG HHH", strategies=("trend_continuation", "pullback", "breakout")):
    uni = svc.universe("Custom", symbols)
    j = svc.start_scan(uni.symbols, "e2e", list(strategies))
    while j.status in ("queued", "running"):
        time.sleep(0.05)
    assert j.status == "done", j.error
    return j.result


def test_workflow_a_single_source_of_truth(svc):
    from src.reports import builder
    from src.services import alerts
    from tests.test_notify import OWN, MockTG
    u = svc.local_user
    tg = MockTG()
    alerts.set_own_bot(svc.store, u, OWN, "42", tg)
    svc.store.add_alert_rule(u, name="all", enabled=True, strategies=[], min_score=0, directions=[], timeframes=[], symbols=[],
                             cooldown_minutes=0, channels=["in_app"])
    res = _scan(svc)
    qualified = [r for r in res.signals if r["status"] == "qualified"]
    assert qualified, "fixture produced no qualified signal"
    row = max(qualified, key=lambda r: r["score"])
    for c in [x for x in (row["conditions_failed"] or "").split(" | ") if x.startswith("[M]")]:
        pytest.fail(f"qualified signal has a failed mandatory condition: {c}")
    key = svc.store.scoped_key(u, row["key"])
    rec = svc.store.get_signal(key, u)
    assert rec["payload"]["schema"] == "amw.signal.v1" and rec["score"] == row["score"]
    js = json.loads(builder.generate(svc, u, key, "json")[1])["summary"]
    assert (js["signal_ref"], js["score"], js["stop"], js["signal_bar"], js["data_snapshot"]) == (
        key, rec["score"], round(rec["stop"], 2), rec["signal_time"], rec["payload"]["data_snapshot"])
    assert js["chart_data_matches_signal_snapshot"] is True
    ctx = builder.load_context(svc, u, key)
    _, man = builder.to_png(ctx)
    assert [m for m in man if m["kind"] == "stop"][0]["price"] == pytest.approx(rec["stop"])
    assert [m for m in man if m["kind"] == "signal"][0]["time"] == rec["signal_time"]
    msg = alerts.format_message({**rec["payload"], "key": key})
    assert key[:8] in msg and str(rec["payload"]["score"]) in msg
    st, _ = alerts.send_telegram(svc.store, u, msg, tg)
    assert st == "sent" and tg.sent()[-1]["body"]["text"] == msg
    ev = [e for e in svc.store.alert_events(u) if e["channel"] == "in_app"]
    assert ev and all(e["signal_key"] == svc.store.scoped_key(u, e["signal_key"]) or len(e["signal_key"]) == 16 for e in ev)


def test_workflow_b_strategy_lab_from_natural_language(svc):
    from src.ai.agent import draft_strategy
    from src.lab.contract import StrategySpec
    from src.lab.service import lab_backtest
    from tests.test_ai_byok import OA1, MockProviders
    u = svc.local_user
    mock = MockProviders()
    svc.ai.transport = mock
    svc.ai.save_key(u, "openai", OA1, "gpt-test")
    mock.reply = "```\n//@version=5\nindicator('x')\nlong_signal = close > ta.ema(close, 20) and ta.ema(close, 20) > ta.ema(close, 50)\n```"
    svc.ai.save_policy(u, {"limits": {"confirm_calls_above": 5}})
    d = draft_strategy(svc.ai, u, "Close above the 20 EMA which is above the 50 EMA", "pine")
    assert d.ok and d.calls == 1
    sid = svc.store.lab_create(u, "nl strategy", "pine", "from NL", d.source, None, StrategySpec(language="pine").model_dump())
    assert svc.store.lab_publish(u, sid) == 1
    clean = make_ohlc(list(np.linspace(100, 200, 320) + np.sin(np.arange(320)) * 3), spread=0.6)
    bt = lab_backtest(svc, u, sid, None, clean, "1D", warmup=210)
    assert bt["run_status"] == "COMPLETED" and bt["language"] == "pine" and bt["version"] == 1
    tr = bt["trades"]
    assert len(tr) == bt["all"]["trades"] and "evidence" in bt["all"]
    if len(tr):
        assert (pd.to_datetime(tr.entry_time) > pd.to_datetime(tr.signal_time)).all()
        assert bt["all"]["win_rate_pct"] == pytest.approx(100 * (tr.r_multiple > 0).mean(), abs=0.05)   # stats from trades, not AI


def test_workflow_e_backtest_determinism_and_costs(svc):
    from src.backtest.engine import run_backtest
    cfg = svc.cfg()
    clean = make_ohlc(list(np.linspace(100, 220, 400) + np.sin(np.arange(400) / 3) * 4), spread=0.7)
    a = run_backtest(clean, "1D", cfg, "pullback", warmup=210)
    b = run_backtest(clean, "1D", cfg, "pullback", warmup=210)
    assert a["trades"].to_dict("records") == b["trades"].to_dict("records") and a["all"] == b["all"]
    if len(a["trades"]):
        t = a["trades"]
        assert (pd.to_datetime(t.entry_time) > pd.to_datetime(t.signal_time)).all()
        cfg2 = json.loads(json.dumps(cfg))
        cfg2["costs"].update(brokerage_bps=50, exchange_tax_bps=50, slippage_bps=50)
        c = run_backtest(clean, "1D", cfg2, "pullback", warmup=210)
        assert c["trades"].ret_pct.sum() < t.ret_pct.sum()
