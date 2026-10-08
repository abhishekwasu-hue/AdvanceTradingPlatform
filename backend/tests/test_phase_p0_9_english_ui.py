"""P0.9: English-only dashboard, the AI answer language setting, compliance round 2 and the data fixes.

A  the dashboard is English (frontend check: frontend/scripts/check-devanagari.mjs); the AI's written answers follow the
   user's `ai_language` (default English) whatever script the question uses.
B  the interview states no market direction and pre-selects nothing; a single stock gets no price scenarios and no
   score; an index's score is labelled a model score, not a forecast; "held up on unseen data" needs real broker candles
   and 30+ unseen trades.
C  a stop gapped through fills at the open; every AI task's model and an estimated cost per call are on the provider card.
"""
import json

import pandas as pd

from app.ai import market_study as ms
from app.ai import strategist as st
from app.ai import thesis as th
from app.strategy_engine.declarative import Condition, CustomStrategyConfig, Operand
from tests.test_auth_api import _register, client
from tests.test_phase_ap_interview import _sessions
from tests.test_phase_aw_strategist import _day, op
from tests.test_phase_bd_thesis import _snapshot
from tests.test_trading_worker import _upgrade_plan

NOW = th.datetime(2026, 10, 8, 9, 0, tzinfo=th.timezone.utc)


# --- A ------------------------------------------------------------------------------------------------------------------
def test_ai_answer_language_is_a_user_setting_defaulting_to_english():
    headers = {"Authorization": f"Bearer {_register('p09-lang@example.com')}"}
    prefs = client.get("/api/ai/preferences", headers=headers).json()
    assert prefs["ai_language"] == "en" and {lang["code"] for lang in prefs["languages"]} == {"en", "mr"}
    # A question in Marathi script is answered in English until the user chooses Marathi.
    assert "loses value every day" in client.post("/api/ai/ask", headers=headers, json={"question": "theta म्हणजे काय?"}).json()["answer"]
    assert client.put("/api/ai/preferences", headers=headers, json={"ai_language": "mr"}).status_code == 200
    assert "Option दररोज" in client.post("/api/ai/ask", headers=headers, json={"question": "what is theta"}).json()["answer"]
    # An explicit language on the request still wins (the Telegram bot answers in the language of the message).
    assert "loses value every day" in client.post("/api/ai/ask", headers=headers, json={"question": "what is theta", "language": "en"}).json()["answer"]
    other = {"Authorization": f"Bearer {_register('p09-lang-other@example.com')}"}
    assert client.get("/api/ai/preferences", headers=other).json()["ai_language"] == "en"         # per user, not per organisation


# --- B ------------------------------------------------------------------------------------------------------------------
def test_interview_states_no_market_direction_and_describes_templates_neutrally():
    headers = {"Authorization": f"Bearer {_register('p09-interview@example.com')}"}
    df = _sessions(days=5)
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    plan = client.post("/api/ai/interview/plan", headers=headers, json={
        "answers": {"language": "en", "experience": "new", "capital": 100000, "risk": "moderate", "style": "intraday", "view": "bearish"},
        "base_timeframe": "5min", "candles": candles, "data_source": "sample"}).json()
    text = json.dumps({"sections": [o["sections"] for o in plan["options"]], "warnings": [o["warnings"] for o in plan["options"]],
                       "options": [o["option"] for o in plan["options"]]})
    for phrase in ("Overall bias", "Built from your own answers", "Template shown first", "the plan follows the market", "Recommended", "recommended:"):
        assert phrase not in text, phrase
    assert all(o["option"]["summary"] for o in plan["options"]) and "Middle risk" in text and "This template:" in text


def test_a_single_stock_gets_no_price_scenarios_and_an_index_score_is_a_model_score():
    snapshot = _snapshot("RELIANCE")
    memory = {"symbols": [snapshot], "cues": [], "sentiment": None, "globals": []}
    stock = th.compose("RELIANCE", snapshot, memory, [], [], "en", NOW)
    assert stock["scenarios"] == {} and stock["confidence"] is None and "confidence" not in stock["agreement"]
    index = th.compose("NIFTY 50", _snapshot("NIFTY 50"), memory, [], [], "en", NOW)
    assert set(index["scenarios"]) >= {"bull", "bear"} and "not a forecast" in index["lines"][0] and "% confidence" not in index["lines"][0]
    flagged = th.compose("RELIANCE", snapshot, memory, [], [], "en", NOW, stock_targets=True)
    assert flagged["scenarios"] and flagged["confidence"] is not None

    df = _sessions(days=6, minutes=1)
    assert ms.study(df, "RELIANCE", "en")["scenarios"] == []
    assert ms.study(df, "RELIANCE", "en", stock_detail=True)["scenarios"]
    assert {s["id"] for s in ms.study(df, "NIFTY 50", "en")["scenarios"]} >= {"bull", "bear"}


def test_held_up_on_unseen_data_needs_real_candles_and_thirty_unseen_trades():
    good = {"trades": 40, "expectancy_r": 0.3}
    assert st._verdict("en", good, {"trades": 35, "expectancy_r": 0.2}, real_data=True)[0] == "robust"
    assert st._verdict("en", good, {"trades": 35, "expectancy_r": 0.2}, real_data=False) == ("sample", "Sample data - these figures are not real performance.")
    verdict, text = st._verdict("en", good, {"trades": 12, "expectancy_r": 0.9}, real_data=True)
    assert verdict == "insufficient" and "12 trade(s)" in text and "30" in text
    assert st._verdict("en", good, {"trades": 30, "expectancy_r": -0.1}, real_data=True)[0] == "overfit"


# --- C ------------------------------------------------------------------------------------------------------------------
def test_a_stop_gapped_through_fills_at_the_open_not_at_the_stop():
    df = _day(100, [100.0] * 30 + [101.0] + [100.5] * 344)
    df.iloc[32, df.columns.get_loc("open")] = 90.0          # the bar after the entry opens far below the stop
    df.iloc[32, df.columns.get_loc("low")] = 89.0
    cfg = CustomStrategyConfig(name="t", timeframe="1min", long_conditions=[Condition(left=op("CLOSE"), operator="CROSSES_ABOVE", right=Operand(type="value", value=100.5))],
                               stop_loss_atr_mult=1.0, target_rr=(1.5, 2.5))
    trades = st.simulate(cfg, df, warmup=20)
    assert trades[0]["reason"] == "stop" and trades[0]["exit"] == 90.0 and trades[0]["r"] < -1.5      # worse than 1R: the gap


def test_breakout_templates_on_a_random_walk_are_not_all_one_r_losers():
    """The old sample series (a 40-bar 1.5% sine) reversed every breakout on schedule, so every breakout template lost
    exactly 1R plus costs. On a random walk the same templates produce a mix of outcomes."""
    import numpy as np
    rng = np.random.default_rng(11)
    steps = rng.normal(0, 0.0006, 375 * 10)
    closes = 24_000 * np.exp(np.cumsum(steps))
    days = pd.bdate_range("2026-09-14", periods=10)
    idx = [pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=3, minutes=45 + m) for d in days for m in range(375)]
    opens = np.concatenate([[closes[0]], closes[:-1]])
    df = pd.DataFrame({"open": opens, "close": closes, "high": np.maximum(opens, closes) * 1.0004, "low": np.minimum(opens, closes) * 0.9996,
                       "volume": 1000.0}, index=pd.DatetimeIndex(idx))
    study = ms.study(df, "NIFTY 50", "en")
    out = st.build(df, study, "en", direction="both", real_data=False)
    rs = [t["r"] for c in out["candidates"] for t in c["trades"]]
    assert rs and len({round(r, 2) for r in rs}) > 3 and any(r > 0 for r in rs)
    assert all(c["verdict"] == "sample" for c in out["candidates"])


def test_the_provider_card_lists_every_task_model_and_an_estimated_cost():
    headers = {"Authorization": f"Bearer {_register('p09-models@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "pro")
    body = client.get("/api/ai/provider", headers=headers).json()
    rows = {r["task"]: r for r in body["task_models"]["anthropic"]}
    assert rows["narration"]["tier"] == "fast" and rows["strategy_generation"]["tier"] == "strong"
    assert rows["narration"]["model"] != rows["strategy_generation"]["model"]
    assert 0 < rows["narration"]["est_inr_per_call"] < rows["strategy_generation"]["est_inr_per_call"]
    assert body["typical_call_tokens"]["fast"]["input"] > 0 and "general" not in rows
