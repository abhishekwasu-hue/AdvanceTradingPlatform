"""P0.10: the last fixes from the screenshot review.

3  risk settings never change with experience; an unset profile shows the defaults labelled "not set yet".
4  the experience tip is general teaching, not a judgement about the trader.
5  the instrument the trader chose is kept (futures -> futures, option selling -> a hedged spread); differences are
   information only.
6  sample candles are shaped like NSE sessions (tests/sample_market.py ports frontend/src/utils/sampleData.ts).
7  the two breakout templates give different, sane results on the sample data (the numbers behind the SAMPLE blur).
8  frequent short AI jobs run on the cheapest model tier.
2  the briefing says how old its market figures are and hides stale or placeholder ones.
1  the interview intro carries its Marathi line.
9  the coach describes patterns in the trader's own trades.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.ai import advisor, briefing, coach
from app.ai import interview as iv
from app.ai import market_study as ms
from app.ai import providers as prov
from app.ai import settings as ai_settings
from app.ai import strategist as st
from app.core.models import RiskConfig
from tests.sample_market import SESSION_BARS, generate


def _answers(**kw) -> iv.InterviewAnswers:
    base = {"language": "en", "experience": "new", "capital": 200000, "risk": "aggressive", "style": "intraday",
            "instrument": "index", "symbol": "NIFTY 50", "vehicle": "underlying", "daily_loss": 5}
    base.update(kw)
    return iv.InterviewAnswers(**base)


# --- 3 / 4 -------------------------------------------------------------------------------------------------------------
def test_risk_settings_do_not_depend_on_experience():
    new, _ = iv.risk_plan(_answers(experience="new"))
    old, _ = iv.risk_plan(_answers(experience="experienced"))
    assert new == old                                   # same answers, same settings - experience is information only
    assert new.risk_per_trade_pct == 1.5                # the trader's own "aggressive" answer, not a beginner cap of 0.5
    capped, notes = iv.risk_plan(_answers(), {"risk_per_trade_pct": 1.0})
    assert capped.risk_per_trade_pct == 1.0 and "operator's maximum" in notes[0]
    assert iv.default_exit_rules(_answers(experience="new")) == iv.default_exit_rules(_answers(experience="experienced"))
    for tilt in ("safe", "balanced", "active"):
        a_new, _, _ = advisor._tilted_config(_answers(experience="new"), advisor.Preferences(), tilt, None)
        a_old, _, _ = advisor._tilted_config(_answers(experience="experienced"), advisor.Preferences(), tilt, None)
        assert a_new == a_old


def test_experience_tip_is_general_teaching():
    q = next(q for q in iv.QUESTIONS if q.id == "experience")
    assert q.why_en == "Smaller risk per trade and PAPER first is a common way to start; you set your own risk."
    assert "beginner" not in q.why_en.lower() and q.why_mr


def test_briefing_shows_default_risk_settings_as_not_set_yet():
    cfg = RiskConfig()
    line = briefing.risk_line("en", cfg, saved=False)
    assert line.startswith("Default risk settings (not set yet):") and f"{cfg.risk_per_trade_pct:g}% risk per trade" in line
    assert briefing.risk_line("en", cfg, saved=True).startswith("Your risk settings:")
    dt = {"kind": "RANGE", "regime": "RANGING", "vix": 13.0}
    lines = briefing.game_plan("en", dt, {"globals": []}, "new", [], cfg=cfg, cfg_saved=False)["lines"]
    assert any(line.startswith("Default risk settings (not set yet)") for line in lines)
    assert not any("as a beginner" in line for line in lines)


# --- 5 -----------------------------------------------------------------------------------------------------------------
def test_the_chosen_instrument_is_kept():
    contract, notes, text = iv.contract_plan(_answers(experience="new", vehicle="underlying"), "BULLISH", 24_500.0)
    assert contract["instrument_kind"] == "FUTURE" and "futures" in text                 # futures chosen -> futures
    assert any("For your information" in n and "lot value" in n and "24,500" in n for n in notes)
    assert not any("first account" in n for n in notes)
    sell, sell_notes, _ = iv.contract_plan(_answers(experience="new", vehicle="option_sell"), "NEUTRAL")
    assert sell["instrument_kind"] == "OPTION" and sell.get("option_position") != "BUY" and sell["option_strategy"] == "IRON_CONDOR"
    assert any("You chose option selling" in n for n in sell_notes)
    buy, _, _ = iv.contract_plan(_answers(experience="new", vehicle="option_buy"), "BULLISH")
    buy_old, _, _ = iv.contract_plan(_answers(experience="experienced", vehicle="option_buy"), "BULLISH")
    assert buy == buy_old                                                                  # experience changes nothing
    stock, _, _ = iv.contract_plan(_answers(instrument="stock", symbol="RELIANCE", vehicle="option_buy"), "BULLISH")
    assert stock["instrument_kind"] == "UNDERLYING"                                       # a fact: cash stocks have no options


def test_a_futures_plan_is_built_end_to_end():
    df = generate(SESSION_BARS * 6, 24_500, 11)
    plan = iv.build_plan(_answers(experience="new", vehicle="underlying"), df, "1min")
    assert plan["deployment"]["instrument_kind"] == "FUTURE"


# --- 6 / 7 -------------------------------------------------------------------------------------------------------------
def test_sample_candles_move_like_an_index():
    df = generate(SESSION_BARS * 12, 24_500, 7)
    ist = df.index.tz_convert("Asia/Kolkata")
    assert ist.time.min().strftime("%H:%M") == "09:15" and ist.time.max().strftime("%H:%M") == "15:29"
    days = df.groupby(ist.date).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    moves = (days.close / days.open - 1).abs() * 100
    assert 0.2 < moves.mean() < 1.5 and moves.max() < 3.0
    study = ms.study(df, "NIFTY 50", "en")
    ladder = {row["key"]: row["distance_pct"] for row in study["ladder"]}
    # P0.9's round-the-clock minute bars put R2 at +10.98% and PDH at +7.08% from the price.
    assert abs(ladder["r2"]) < 4 and abs(ladder["pdh"]) < 3


def _breakout_rows(seed: int):
    df = generate(SESSION_BARS * 12, 24_500, seed)
    study = ms.study(df, "NIFTY 50", "en")
    out = st.build(df, study, "en", direction="both", real_data=False)
    return out, [c for c in out["candidates"] if c["family"] == "breakout"]


def test_the_two_breakout_templates_give_different_results_on_sample_data(capsys):
    out, rows = _breakout_rows(5)
    with capsys.disabled():
        print("\n[P0.10] breakout templates on sample NIFTY 50 (seed 5, 12 sessions) - the figures behind the SAMPLE blur:")
        for c in rows:
            a, o = c["all"], c["out_of_sample"]
            print(f"  {c['id']:<14} trades {a['trades']:>3}  win {a['win_rate']:>5}%  expectancy {a['expectancy_r']:+.3f}R  total {a['total_r']:+.2f}R"
                  f"  | unseen sessions: {o['trades']} trades, {o['expectancy_r']:+.3f}R  -> verdict {c['verdict']}")
    assert {c["id"] for c in rows} == {"orb_breakout", "pd_breakout"}
    orb, pd_ = (next(c for c in rows if c["id"] == i) for i in ("orb_breakout", "pd_breakout"))
    assert (orb["all"]["trades"], orb["all"]["expectancy_r"]) != (pd_["all"]["trades"], pd_["all"]["expectancy_r"])
    assert [(t["date"], t["entry"]) for t in orb["trades"]] != [(t["date"], t["entry"]) for t in pd_["trades"]]
    for c in rows:   # not the old "every breakout loses exactly 1R" pattern
        rs = [t["r"] for t in c["trades"]]
        assert any(r > 0 for r in rs) and len({round(r, 2) for r in rs}) > 3
        assert c["verdict"] == "sample"
    assert all(c["verdict"] == "sample" for c in out["candidates"])


# --- 8 -----------------------------------------------------------------------------------------------------------------
def test_frequent_short_jobs_run_on_the_cheapest_tier():
    assert prov.TASK_TIERS["classification"] == prov.TASK_TIERS["scanner_read"] == "cheap"
    assert prov.model_for("anthropic", "claude-mine", "classification") == prov.default_models()["anthropic"]["cheap"] == "claude-haiku-5-5"
    assert prov.model_for("openai", None, "scanner_read") == "gpt-4.1-nano"
    assert prov.build_provider("anthropic", "k", None, task="classification").effort == "low"
    rows = {r["task"]: r for r in ai_settings.task_models()["anthropic"]}
    assert rows["classification"]["tier"] == "cheap" and not rows["classification"]["estimated_price"]
    assert rows["classification"]["est_inr_per_call"] < rows["narration"]["est_inr_per_call"] < rows["strategy_generation"]["est_inr_per_call"]


# --- 2 -----------------------------------------------------------------------------------------------------------------
NOW = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)      # 11:30 IST, market open


def _memory(age: timedelta, changes=(0.4, -0.7)):
    at = (NOW - age).isoformat()
    return {"updated_at": at, "symbols": [{"symbol": s, "change_pct": c} for s, c in zip(("NIFTY 50", "RELIANCE"), changes)]}


def test_briefing_figures_carry_their_age_and_are_hidden_when_stale_or_placeholder():
    fresh = briefing.data_freshness(_memory(timedelta(minutes=10)), NOW, market_open=True)
    assert fresh == {"state": "fresh", "updated_at": fresh["updated_at"], "age_minutes": 10.0, "figures_shown": True}
    stale = briefing.data_freshness(_memory(timedelta(hours=27)), NOW, market_open=True)
    assert stale["state"] == "stale" and stale["age_minutes"] == 27 * 60 and not stale["figures_shown"]
    placeholder = briefing.data_freshness(_memory(timedelta(minutes=5), changes=(0.4, 0.4)), NOW, market_open=True)
    assert placeholder["state"] == "suspect" and not placeholder["figures_shown"]
    overnight = briefing.data_freshness(_memory(timedelta(hours=16)), NOW, market_open=False)
    assert overnight["figures_shown"]                                    # last evening's read before the open is fine
    assert briefing.data_freshness({"symbols": []}, NOW, market_open=True)["state"] == "none"


# --- 1 / 9 -------------------------------------------------------------------------------------------------------------
def test_interview_intro_is_bilingual_and_coach_describes_patterns():
    s = iv.start("")
    assert s["intro"] and s["intro_mr"] and "शिफारस" in s["intro_mr"]
    empty = coach.review([], "en", days=30, mode="PAPER") if hasattr(coach, "review") else None
    if empty is not None:
        assert "patterns in your own trades" in empty["focus"][0] and "what to fix" not in empty["focus"][0]


@pytest.mark.parametrize("bad", ["beginner gets smaller risk", "first account", "experience cap"])
def test_no_profile_based_suitability_wording_left(bad):
    import inspect
    text = inspect.getsource(iv) + inspect.getsource(advisor) + inspect.getsource(briefing)
    assert bad not in text
