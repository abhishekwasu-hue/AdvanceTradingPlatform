"""Phase AP: the strategy interview - ask first, read the market, then plan like a professional."""
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.ai import interview as iv
from app.deployments.routes import DeploymentCreateRequest
from tests.test_auth_api import _register, client

UTC = timezone.utc


def _sessions(days=8, drift=0.05, amp=6.0, base=24_000.0, sign=1, minutes=5):
    """Weekday NSE sessions (09:15-15:30 IST = 03:45-10:00 UTC) of a steady trend with waves."""
    rows, i = [], 0
    day0 = datetime(2026, 9, 14, 3, 45, tzinfo=UTC)
    for d in range(days + 4):
        start = day0 + timedelta(days=d)
        if start.weekday() >= 5:
            continue
        for k in range(375 // minutes):
            rows.append((start + timedelta(minutes=minutes * k), base + sign * drift * i * base / 1000 + amp * math.sin(i / 6)))
            i += 1
        if len({r[0].date() for r in rows}) >= days:
            break
    closes = [c for _, c in rows]
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame({"open": opens, "close": closes, "high": [max(o, c) + 3 for o, c in zip(opens, closes)],
                         "low": [min(o, c) - 3 for o, c in zip(opens, closes)], "volume": [1000.0] * len(closes)},
                        index=pd.DatetimeIndex([t for t, _ in rows]))


def test_vague_requests_start_the_interview_and_rule_requests_do_not():
    assert iv.is_vague("ट्रेडिंग स्ट्रॅटेजी सांगा")
    assert iv.is_vague("give me a trading strategy")
    assert not iv.is_vague("Buy when EMA20 crosses above EMA50 and RSI(14) > 55")
    started = iv.start("ट्रेडिंग स्ट्रॅटेजी सांगा")
    assert started["needs_interview"] and started["language"] == "mr" and "experience" in started["remaining"]
    assert {q["id"] for q in started["questions"]} == set(iv.QUESTION_IDS)
    assert all(q["mr"] and q["en"] and q["why_mr"] for q in started["questions"])


def test_prefill_reads_what_the_trader_already_said():
    p = iv.prefill_from_prompt("मला bank nifty मध्ये option buying intraday strategy हवी, capital 50000, मी नवशिका आहे")
    assert p["language"] == "mr" and p["symbol"] == "NIFTY BANK" and p["instrument"] == "index"
    assert p["vehicle"] == "option_buy" and p["style"] == "intraday" and p["capital"] == "50000" and p["experience"] == "new"
    q = iv.prefill_from_prompt("I have 2 lakh and want nifty scalping")
    assert q["language"] == "en" and q["capital"] == "200000" and q["style"] == "scalping" and q["symbol"] == "NIFTY 50"
    assert "symbol" not in iv.start("I have 2 lakh and want nifty scalping")["remaining"]


def test_risk_plan_caps_a_beginner_and_keeps_two_losses_inside_the_day():
    cfg, notes = iv.risk_plan(iv.InterviewAnswers(experience="new", risk="aggressive", daily_loss=3, capital=200_000))
    assert cfg.risk_per_trade_pct == 0.5 and cfg.capital == 200_000         # P0.8-D: the entered capital - no allocation advice
    assert cfg.max_daily_loss_pct == 1.5 and cfg.max_open_positions == 1 and cfg.max_consecutive_losses == 2
    assert notes
    cfg, _ = iv.risk_plan(iv.InterviewAnswers(experience="experienced", risk="aggressive", daily_loss=1))
    assert cfg.max_daily_loss_pct == 1.0 and cfg.risk_per_trade_pct == 0.5  # two losses fit the 1% day
    cfg, _ = iv.risk_plan(iv.InterviewAnswers(experience="experienced", risk="aggressive", daily_loss=3), {"risk_per_trade_pct": 1.0})
    assert cfg.risk_per_trade_pct == 1.0                                       # the platform ceiling wins


def test_contract_plan_protects_a_beginner():
    contract, notes, _ = iv.contract_plan(iv.InterviewAnswers(experience="new", vehicle="option_sell"), "BULLISH")
    assert contract["instrument_kind"] == "OPTION" and contract["option_position"] == "BUY" and contract["expiry_rule"] == "NEXT"
    assert any("selling" in n for n in notes)
    contract, _, _ = iv.contract_plan(iv.InterviewAnswers(experience="experienced", vehicle="option_sell"), "BEARISH")
    assert contract["option_strategy"] == "BEAR_CALL_SPREAD"
    contract, _, _ = iv.contract_plan(iv.InterviewAnswers(experience="experienced", vehicle="underlying"), "NEUTRAL")
    assert contract["instrument_kind"] == "FUTURE"
    contract, _, _ = iv.contract_plan(iv.InterviewAnswers(experience="new", vehicle="underlying"), "NEUTRAL")
    assert contract["instrument_kind"] == "OPTION"                            # no index futures lot for a first account
    contract, _, _ = iv.contract_plan(iv.InterviewAnswers(instrument="stock", symbol="TATAMOTORS", vehicle="option_buy"), "NEUTRAL")
    assert contract == {"instrument_kind": "UNDERLYING"}


def test_regime_fit_matches_method_to_market():
    assert iv.regime_fit("trend", "TRENDING_UP") > iv.regime_fit("reversion", "TRENDING_UP")
    assert iv.regime_fit("reversion", "RANGING") > iv.regime_fit("trend", "RANGING")
    assert iv.regime_fit("breakout", "QUIET") == 3


def test_market_read_sees_an_uptrend():
    m = iv.analyse_market(_sessions(), "5min", "en")
    assert m["bias"] == "BULLISH" and m["regime"]["kind"] == "TRENDING_UP" and m["structure"]["trend"] != "DOWNTREND"
    assert m["higher_timeframe"] in ("60min", "30min", "15min") and m["bias_reasons"]
    down = iv.analyse_market(_sessions(sign=-1, base=26_000.0), "5min", "en")
    assert down["bias"] == "BEARISH"


def test_plan_in_marathi_with_a_deployable_paper_setup():
    a = iv.InterviewAnswers(language="mr", experience="new", vehicle="option_sell", view="bearish")
    plan = iv.build_plan(a, _sessions(days=6), "5min")
    assert plan["recommended"]["family"] in ("trend", "momentum")          # an uptrend picks a trend method
    assert [s["id"] for s in plan["sections"]] == ["market", "strategy", "risk", "capital", "rr", "contract", "checklist", "steps"]
    assert all(any("ऀ" <= ch <= "ॿ" for ch in s["title"] + " ".join(s["lines"])) for s in plan["sections"])
    assert any("मत" in w for w in plan["warnings"])                           # bearish view vs a bullish market
    dep = plan["deployment"]
    assert dep["mode"] == "PAPER" and dep["regime_filter"] == ["TRENDING_UP", "TRENDING_DOWN"]
    DeploymentCreateRequest(**dep).normalised()                               # the create API accepts it as is
    assert plan["risk_config"]["risk_per_trade_pct"] == 0.5 and "INR" in plan["ai_prompt"]
    tested = [r for r in [plan["recommended"], *plan["alternatives"]] if r["evidence"]["tested"]]
    assert tested


def test_plan_endpoint_end_to_end_in_english():
    headers = {"Authorization": f"Bearer {_register('interview@example.com')}"}
    df = _sessions(days=5)
    candles = [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
               for ts, r in df.iterrows()]
    started = client.post("/api/ai/interview/start", headers=headers, json={"prompt": "give me a strategy for nifty"}).json()
    assert started["needs_interview"] and started["prefill"]["symbol"] == "NIFTY 50"
    r = client.post("/api/ai/interview/plan", headers=headers, json={
        "answers": {"language": "en", "experience": "learning", "capital": 300000, "risk": "moderate", "style": "intraday",
                    "vehicle": "option_buy", "goal": "big_trends"},
        "base_timeframe": "5min", "candles": candles, "data_source": "broker:upstox"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["language"] == "en" and plan["risk_config"]["capital"] == 300_000 and plan["risk_config"]["risk_per_trade_pct"] == 1.0
    assert plan["deployment"]["strategy_id"] == plan["recommended"]["strategy_id"]
    assert not any("SAMPLE" in w for w in plan["warnings"])
    assert client.post("/api/ai/interview/plan", headers=headers, json={"answers": {}, "base_timeframe": "day", "candles": candles}).status_code == 400
    assert client.post("/api/ai/interview/start", json={"prompt": "x"}).status_code == 401
