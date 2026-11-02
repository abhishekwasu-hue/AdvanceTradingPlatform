"""SB-UX SU4 (owner order CP-UX + SB-UX, 30 Oct; DEF-0): the Strategy Builder's stop, target and minimum R:R are the
user's own values - blank = off end to end - and the strategy interview adds no risk rule the user did not state."""
import json

import pytest
from pydantic import ValidationError

from app.ai import advisor as ad
from app.ai import interview as iv
from app.backtest.engine import run_backtest
from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.strategy_engine.declarative import Condition, CustomStrategyConfig, DeclarativeStrategy, Operand
from app.strategy_engine.nlu_parser import parse_strategy_description
from tests.test_auth_api import _register, client
from tests.test_phase_ap_interview import _sessions
from tests.utils import decline_then_rally, make_series, rally_then_decline

RSI = Operand(type="indicator", indicator="RSI", period=14)


def _config(**exits) -> CustomStrategyConfig:
    return CustomStrategyConfig(name="RSI cross 50", timeframe="1min",
                                long_conditions=[Condition(left=RSI, operator="CROSSES_ABOVE", right=Operand(type="value", value=50))],
                                short_conditions=[Condition(left=RSI, operator="CROSSES_BELOW", right=Operand(type="value", value=50))],
                                **exits)


def _payload(**exits) -> dict:
    return {"name": "Own exits", "timeframe": "1min", "short_conditions": [],
            "long_conditions": [{"left": {"type": "indicator", "indicator": "RSI", "period": 14}, "operator": "CROSSES_ABOVE",
                                 "right": {"type": "value", "value": 50}}], **exits}


# --- (a) the builder: no prefilled stop / target / R:R; blank = off end to end ------------------------------------------

def test_no_stop_target_or_minimum_is_filled_in():
    cfg = _config()
    assert cfg.stop_loss_atr_mult is None and cfg.target_rr is None and cfg.min_rr is None


def test_blank_values_are_stored_and_returned_as_off():
    headers = {"Authorization": f"Bearer {_register('su4-blank@example.com')}"}
    created = client.post("/api/custom-strategies", headers=headers,
                          json=_payload(stop_loss_atr_mult=None, target_rr=None, min_rr=None))
    assert created.status_code in (200, 201), created.text
    stored = client.get(f"/api/custom-strategies/{created.json()['id']}", headers=headers).json()["config"]
    assert stored["stop_loss_atr_mult"] is None and stored["target_rr"] is None and stored["min_rr"] is None
    # nothing sent at all is the same: off, never a default
    bare = client.post("/api/custom-strategies", headers=headers, json=_payload()).json()
    assert bare["config"]["stop_loss_atr_mult"] is None and bare["config"]["target_rr"] is None


def test_the_users_own_values_are_kept():
    headers = {"Authorization": f"Bearer {_register('su4-own@example.com')}"}
    created = client.post("/api/custom-strategies", headers=headers,
                          json=_payload(stop_loss_atr_mult=1.0, target_rr=[1.5, 2.0], min_rr=1.2)).json()
    stored = client.get(f"/api/custom-strategies/{created['id']}", headers=headers).json()["config"]
    assert stored["stop_loss_atr_mult"] == 1.0 and stored["target_rr"] == [1.5, 2.0]
    assert stored["min_rr"] == pytest.approx(1.2)


@pytest.mark.parametrize("exits, words", [
    ({"target_rr": [2.0, 2.0]}, "needs your stop-loss"),
    ({"stop_loss_atr_mult": 1.0, "min_rr": 1.5}, "needs your target"),
    ({"stop_loss_atr_mult": 1.0, "target_rr": [0, 2.0]}, "above 0 R"),
])
def test_r_without_its_base_is_refused_in_words_not_filled_in(exits, words):
    with pytest.raises(ValidationError, match=words):
        _config(**exits)
    headers = {"Authorization": f"Bearer {_register(f'su4-refuse-{len(words)}@example.com')}"}
    assert client.post("/api/custom-strategies", headers=headers, json=_payload(**exits)).status_code == 422


def test_no_stop_set_signals_carry_no_stop_and_no_target():
    long_sig = DeclarativeStrategy("custom:t", _config()).analyze({"1min": make_series(decline_then_rally()).iloc[:43]}, "T")
    assert long_sig.direction == SignalDirection.LONG
    assert long_sig.stop_loss == 0.0 and long_sig.target1 is None and long_sig.target2 is None and long_sig.risk_reward is None
    short_sig = DeclarativeStrategy("custom:t", _config()).analyze({"1min": make_series(rally_then_decline()).iloc[:43]}, "T")
    assert short_sig.direction == SignalDirection.SHORT and short_sig.stop_loss == 0.0 and short_sig.target1 is None


def test_own_stop_without_a_target_carries_the_stop_only():
    sig = DeclarativeStrategy("custom:t", _config(stop_loss_atr_mult=1.5)).analyze({"1min": make_series(decline_then_rally()).iloc[:43]}, "T")
    assert sig.direction == SignalDirection.LONG and 0 < sig.stop_loss < sig.entry
    assert sig.target1 is None and sig.target2 is None and sig.risk_reward is None


def test_own_stop_and_target_work_as_before():
    sig = DeclarativeStrategy("custom:t", _config(stop_loss_atr_mult=1.0, target_rr=(1.5, 2.0))).analyze(
        {"1min": make_series(decline_then_rally()).iloc[:43]}, "T")
    risk = sig.entry - sig.stop_loss
    assert sig.target1 == pytest.approx(sig.entry + 1.5 * risk, abs=0.02) and sig.target2 == pytest.approx(sig.entry + 2.0 * risk, abs=0.02)
    assert sig.risk_reward == 1.5


def test_blank_exits_in_a_backtest_exit_on_nothing_the_user_did_not_set():
    df = make_series(decline_then_rally() + rally_then_decline())
    off = run_backtest(DeclarativeStrategy("custom:off", _config()), df, "TEST", "1min", RiskConfig())
    assert off.total_trades >= 1
    assert {t.exit_reason for t in off.trades} <= {"End of backtest"}       # no stop-loss, no target was set
    own = run_backtest(DeclarativeStrategy("custom:own", _config(stop_loss_atr_mult=0.5, target_rr=(0.5, 0.5))), df, "TEST", "1min", RiskConfig())
    assert {t.exit_reason for t in own.trades} & {"Stop Loss", "Target 1", "Target 2"}


def test_the_plain_words_parser_fills_in_no_exit():
    result = parse_strategy_description("Buy when RSI(14) crosses above 60.")
    assert result.config.stop_loss_atr_mult is None and result.config.target_rr is None and result.config.min_rr is None
    assert "Stop loss: not stated - off" in result.interpreted
    stated = parse_strategy_description("Buy when RSI crosses above 60. Target risk reward of 2.")
    assert stated.config.target_rr == (2.0, 2.0)                             # one target said = one target


# --- (b) the interview adds no risk rule the user did not state ---------------------------------------------------------

ADVICE = ("Professional", "professional", "0.5-1%", "revenge", "afford to lose", "fight the trend", "three losses", "3 losing trades",
          "two losing trades", "a quarter smaller", "at least 1 ATR", "15:10", "Stop moves to cost")


def test_the_limit_questions_have_no_default_and_no_advice():
    for qid in ("risk", "daily_loss"):
        q = next(q for q in iv.QUESTIONS if q.id == qid)
        assert q.default is None and any(o.value == iv.NOT_SET for o in q.options)
        for word in ADVICE:
            assert word not in q.why_en, (qid, word)
    for q in iv.QUESTIONS:
        assert "Professional" not in q.why_en and "professional" not in q.why_en, q.id


@pytest.mark.parametrize("given", [None, "", "off", "null", float("nan")])
def test_not_set_answers_are_no_limit(given):
    a = iv.InterviewAnswers(risk=given, daily_loss=given)
    assert a.risk is None and a.daily_loss is None
    cfg, notes = iv.risk_plan(a)
    assert cfg == RiskConfig() and not notes


def test_the_plan_is_only_what_was_answered():
    cfg, notes = iv.risk_plan(iv.InterviewAnswers(style="swing", risk="aggressive", daily_loss=1))
    assert (cfg.risk_per_trade_pct, cfg.max_daily_loss_pct, notes) == (1.5, 1.0, [])        # no swing buffer, no loss-count cap
    cfg, notes = iv.risk_plan(iv.InterviewAnswers(risk="moderate", daily_loss=5))
    assert (cfg.risk_per_trade_pct, cfg.max_daily_loss_pct, notes) == (1.0, 5.0, [])         # no "three losses a day" cap
    cfg, notes = iv.risk_plan(iv.InterviewAnswers(risk="aggressive", daily_loss=5), {"risk_per_trade_pct": 1.0, "max_daily_loss_pct": 3.0})
    assert (cfg.risk_per_trade_pct, cfg.max_daily_loss_pct) == (1.0, 3.0) and len(notes) == 2  # the operator's ceilings, said


@pytest.mark.parametrize("lang", ["en", "mr"])
def test_the_options_carry_only_the_users_own_settings(lang):
    a = iv.InterviewAnswers(language=lang, experience="new", style="intraday", time="auto", vehicle="option_buy", symbol="NIFTY 50", instrument="index")
    _, p, _ = ad.apply_feedback(a, ad.Preferences(), ["too_risky", "low_reward", "too_many_trades", "no_time"])
    plan = ad.build_options(a, p, _sessions(days=6), "5min")
    for o in plan["options"]:
        assert o["risk_config"] == RiskConfig().model_dump()                                  # nothing answered = nothing set
        assert o["deployment"]["exit_rules"] == {"break_even_at_r": None, "time_exit_at": None}
        text = json.dumps({"sections": o["sections"], "warnings": o["warnings"], "option": o["option"]}, ensure_ascii=False)
        if lang == "en":
            for word in ADVICE:
                assert word not in text, word
    assert "at least 1 ATR" not in plan["ai_prompt"] and "1:0" not in plan["ai_prompt"]


def test_feedback_never_changes_a_risk_number():
    a = iv.InterviewAnswers(language="en", risk="moderate", daily_loss=2)
    _, p, changes = ad.apply_feedback(a, ad.Preferences(), ["too_risky", "more_risk_ok", "low_reward", "too_few_trades"])
    cfg, _, rules = ad._tilted_config(a, p, "safe", None)
    assert (cfg.risk_per_trade_pct, cfg.max_daily_loss_pct, cfg.max_trades_per_day, cfg.min_risk_reward) == (1.0, 2.0, None, 0.0)
    assert rules == {"break_even_at_r": None, "time_exit_at": None}
    assert all("Smaller risk" not in c and "higher minimum" not in c for c in changes)


def test_a_no_stop_signal_never_exits_at_its_stop_on_the_underlying():
    from app.trading.exit_logic import determine_exit_price, underlying_exit
    assert underlying_exit("SHORT", 0.0, None, None, 25_000.0) is None          # stop 0 = no stop set (DEF-0 D7)
    assert underlying_exit("LONG", 0.0, None, None, 25_000.0) is None
    assert underlying_exit("SHORT", 25_100.0, None, None, 25_150.0) == "Stop Loss"   # an own stop still exits
    assert determine_exit_price("SHORT", 0.0, None, None, 24_900.0, 25_100.0) is None
