"""Phase AQ: three options with a match %, feedback that refines them, and a remembered profile."""
from app.ai import advisor as ad
from app.ai.interview import InterviewAnswers
from app.deployments.routes import DeploymentCreateRequest
from tests.test_auth_api import _register, client
from tests.test_phase_ap_interview import _sessions


def _bars(df):
    return [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
            for ts, r in df.iterrows()]


def test_feedback_becomes_preferences():
    a = InterviewAnswers(language="mr", vehicle="option_sell", experience="learning")
    answers, p, changes = ad.apply_feedback(a, ad.Preferences(), ["too_risky", "too_many_trades", "dislike_strategy", "too_risky"],
                                            "orb_15m_5m", "balanced")
    assert p.risk_bias == -1 and p.trades_bias == -1 and p.rejected == ["orb_15m_5m"]   # duplicates count once
    assert answers.vehicle == "option_buy" and len(changes) == 4 and all(any("ऀ" <= ch <= "ॿ" for ch in c) for c in changes)
    assert p.feedback_log[-1]["codes"] == ["too_risky", "too_many_trades", "dislike_strategy"]
    answers, p, _ = ad.apply_feedback(answers, p, ["want_swing", "no_time"])
    assert answers.style == "swing" and answers.time == "auto" and p.trades_bias == -2


def test_desired_targets_follow_the_feedback():
    a = InterviewAnswers(experience="learning", risk="moderate", style="intraday", vehicle="option_buy")
    base = ad.desired(a, ad.Preferences())
    assert base == {"risk_pct": 1.0, "trades": 3, "rr": 2.0}
    calmer = ad.desired(a, ad.Preferences(risk_bias=-2, trades_bias=-1, reward_bias=1))
    assert calmer["risk_pct"] == 0.5 and calmer["trades"] == 2 and calmer["rr"] == 2.5
    assert ad.desired(InterviewAnswers(experience="new", risk="aggressive"), ad.Preferences(risk_bias=2))["risk_pct"] == 0.5   # the cap holds


def test_three_options_each_a_deployable_plan_with_a_match():
    a = InterviewAnswers(language="en", experience="learning", risk="moderate", vehicle="option_buy")
    r = ad.build_options(a, ad.Preferences(), _sessions(days=6), "5min")
    assert [o["option"]["id"] for o in r["options"]] == ["safe", "balanced", "active"]
    safe, balanced, active = r["options"]
    assert safe["risk_config"]["risk_per_trade_pct"] < balanced["risk_config"]["risk_per_trade_pct"]
    assert safe["risk_config"]["max_trades_per_day"] < active["risk_config"]["max_trades_per_day"]
    assert safe["risk_config"]["min_risk_reward"] > active["risk_config"]["min_risk_reward"]
    for o in r["options"]:
        assert 0 < o["option"]["match"] <= ad.MAX_MATCH and o["option"]["match_reasons"]
        DeploymentCreateRequest(**o["deployment"]).normalised()
    assert r["risk_config"] == balanced["risk_config"] and r["best_option"] in ("safe", "balanced", "active")
    assert r["preferences"]["match_history"] == [max(o["option"]["match"] for o in r["options"])]


def test_rejected_strategies_are_not_offered_again():
    a = InterviewAnswers(language="en", experience="learning")
    first = ad.build_options(a, ad.Preferences(), _sessions(days=6), "5min")
    gone = first["options"][1]["recommended"]["strategy_id"]
    _, p, _ = ad.apply_feedback(a, ad.Preferences(), ["dislike_strategy"], gone, "balanced")
    again = ad.build_options(a, p, _sessions(days=6), "5min")
    assert gone not in {o["recommended"]["strategy_id"] for o in again["options"] if o["recommended"]}


def test_simplicity_feedback_raises_the_match_of_a_trend_strategy():
    a = InterviewAnswers(language="en", experience="new")
    market = {"regime": {"kind": "TRENDING_UP"}}
    momentum = {"strategy_id": "rsi_adx_momentum_5m", "family": "momentum", "regime_fit": 3.0,
                "evidence": {"tested": False, "total_trades": 0, "net_pnl": 0.0, "profit_factor": None}}
    trend = dict(momentum, strategy_id="macd_ema_trend_5m", family="trend")
    cfg = ad.iv.risk_plan(a)[0]
    simple = ad.Preferences(simplicity=2)
    assert ad.match_score(a, simple, market, trend, cfg)[0] > ad.match_score(a, simple, market, momentum, cfg)[0]
    assert ad.match_score(a, ad.Preferences(rejected=["macd_ema_trend_5m"]), market, trend, cfg)[0] < ad.match_score(a, ad.Preferences(), market, trend, cfg)[0]


def test_plan_refine_choose_and_profile_endpoints():
    headers = {"Authorization": f"Bearer {_register('advisor@example.com')}"}
    candles = _bars(_sessions(days=5))
    answers = {"language": "mr", "experience": "new", "capital": 200000, "style": "intraday", "vehicle": "option_buy"}
    assert client.post("/api/ai/interview/start", headers=headers, json={"prompt": ""}).json()["profile"] is None

    plan = client.post("/api/ai/interview/plan", headers=headers, json={"answers": answers, "candles": candles, "data_source": "broker:upstox"})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert len(body["options"]) == 3 and body["feedback_options"] and len(body["preferences"]["match_history"]) == 1

    started = client.post("/api/ai/interview/start", headers=headers, json={"prompt": "strategy सांगा"}).json()
    assert started["profile"]["answers"]["capital"] == 200000          # remembered for next time

    target = body["options"][1]
    r = client.post("/api/ai/interview/refine", headers=headers, json={
        "answers": answers, "candles": candles, "data_source": "broker:upstox", "feedback": ["too_many_trades", "dislike_strategy"],
        "option_id": "balanced", "strategy_id": target["recommended"]["strategy_id"]})
    assert r.status_code == 200, r.text
    refined = r.json()
    assert refined["changes"] and len(refined["preferences"]["match_history"]) == 2
    assert target["recommended"]["strategy_id"] in refined["preferences"]["rejected"]
    assert client.post("/api/ai/interview/refine", headers=headers, json={"answers": answers, "candles": candles, "feedback": ["bogus"]}).status_code == 400

    chosen = client.post("/api/ai/interview/choose", headers=headers, json={"answers": answers, "option_id": "safe",
                                                                           "strategy_id": refined["options"][0]["recommended"]["strategy_id"], "match": 80})
    assert chosen.status_code == 200 and chosen.json()["preferences"]["chosen"][-1]["option"] == "safe"
    profile = client.get("/api/ai/profile", headers=headers).json()
    assert profile["preferences"]["trades_bias"] == -1 and profile["answers"]["language"] == "mr"
    assert client.delete("/api/ai/profile", headers=headers).status_code == 204
    assert client.get("/api/ai/profile", headers=headers).json()["answers"] is None
