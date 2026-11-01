"""M68 PR-8 (TS-4): templates, the strategy library, the Copilot and the engine may only SUGGEST values. Nothing is applied
until the trader accepts it (a tick or a value he sends); no symbol, exchange or base candles is pre-chosen for a bot.

Rows: TS1-075 (Strategy Lab draft exits), TS1-076 (interview risk answers), TS1-077 (interview contract plan), TS1-078
(Copilot stop floor / minimum R:R), TS1-079 (monitor nudges, tests/test_phase_l_ai.py), TS1-080 (Telegram /paper,
tests/test_z3b_paper_account.py), TS1-081 (Options Builder, tests/test_p1c_options_builder_api.py), TS1-083 (ladder
card), TS1-098 / 099 (no pre-chosen symbol, exchange, base candles).
"""
from app.ai import interview, prompt, strategist
from app.ai.compliance import evaluate_config
from app.strategy_engine.declarative import CustomStrategyConfig
from tests.def0_legacy import legacy
from tests.test_deployments_api import _auth, _create, _store_broker

# --- TS1-099: a bot via the API without an exchange or base candles ------------------------------------------------------

def test_a_bot_without_exchange_or_base_takes_the_symbols_exchange_and_the_strategys_own_candle():
    headers = _auth("m68-pr8-base@example.com")
    _store_broker(headers)
    one = _create(headers)                                                           # ema_rsi_scalper_1m: 1-minute
    assert one.status_code == 201, one.text
    assert (one.json()["exchange"], one.json()["timeframe"]) == ("NSE", "1min")
    five = _create(headers, strategy_id="rsi_adx_momentum_5m", symbol="tcs")          # its own 5-minute candle
    assert five.status_code == 201, five.text
    assert five.json()["timeframe"] == "5min"
    multi = _create(headers, strategy_id="mtf_5m_30m_trend_pullback", symbol="infy")  # the smallest of 5 / 30 minutes
    assert multi.status_code == 201 and multi.json()["timeframe"] == "5min"
    # what he sends is kept exactly (parity with the old explicit request)
    sent = _create(headers, strategy_id="macd_ema_trend_5m", symbol="sbin", exchange="NSE", timeframe="1min")
    assert sent.status_code == 201 and (sent.json()["exchange"], sent.json()["timeframe"]) == ("NSE", "1min")
    # a daily strategy reads daily candles (its own), held overnight as he says
    daily = _create(headers, strategy_id="swing_ema_pullback_d", symbol="hdfcbank", holding="SWING")
    assert daily.status_code == 201, daily.text
    assert daily.json()["timeframe"] == "day"


def test_the_index_exchange_is_a_market_fact():
    from app.deployments.routes import DeploymentCreateRequest, _own_base
    from app.strategy_engine.registry import registry
    req = DeploymentCreateRequest(strategy_id="ema_rsi_scalper_1m", symbol="sensex", instrument_kind="OPTION", option_position="BUY")
    _own_base(req, registry.get("ema_rsi_scalper_1m"))
    assert (req.exchange, req.timeframe) == ("BSE", "1min")


# --- TS1-075: the Strategy Lab draft --------------------------------------------------------------------------------

def test_the_strategy_lab_draft_carries_no_exits_of_its_own_only_suggested_ones():
    exits = strategist.suggested_exits()
    assert exits == {"holding": "INTRADAY", "exit_rules": {"time_exit_at": "15:10", "break_even_at_r": 1.0}}
    exits["exit_rules"]["time_exit_at"] = "x"
    assert strategist.SUGGESTED_EXITS["exit_rules"]["time_exit_at"] == "15:10"          # a copy: the constant never changes
    words = strategist.simulation_assumptions()
    assert words == ["At most 3 trades a day", "No new entry after 14:45", "Open trades closed at 15:15",
                     "Costs and slippage 0.03 % of the price per round trip"]
    source = open(strategist.__file__, encoding="utf8").read()
    assert '"deployment": {"strategy_id": None, "symbol": symbol.upper(), "timeframe": cfg.timeframe, "mode": "PAPER"},' in source
    from app.ai import routes
    adopt = open(routes.__file__, encoding="utf8").read()
    assert '"deployment": {"strategy_id": strategy_id, "symbol": candidate.symbol, "timeframe": config.timeframe, "mode": "PAPER"},' in adopt
    assert '"holding": "INTRADAY", "exit_rules": {"time_exit_at": "15:10"' not in adopt


# --- TS1-076 / 077: the interview -----------------------------------------------------------------------------------

def test_the_interview_risk_answers_show_their_own_percent():
    """TS1-076: the answer IS the number - each option's label is exactly the percent the plan uses."""
    risk = next(q for q in interview.QUESTIONS if q.id == "risk")
    shown = {o.value: o.en for o in risk.options if o.value in interview.RISK_PCT}
    assert shown == {k: f"{v:g}%" for k, v in interview.RISK_PCT.items()}
    assert any(o.value == "off" for o in risk.options)                       # "Not set - no limit" stays an answer


def test_the_interview_contract_picks_are_suggestions_listed_in_words():
    a = interview.InterviewAnswers(language="en", symbol="NIFTY", instrument="index", vehicle="option_sell", style="intraday",
                                   experience="learning", capital=300000, risk="moderate", goal="big_trends")
    contract, _, _ = interview.contract_plan(a, "BULLISH")
    picks = interview.suggested_contract(contract)
    assert [p["key"] for p in picks] == ["option_strategy", "expiry_rule", "spread_width", "target_credit_pct", "stop_credit_pct"]
    assert interview.suggested_text(picks) == ("Structure Bull put spread; Expiry Nearest; Spread width 2 strikes; "
                                              "Take profit 50 % of the credit; Stop 150 % of the credit")
    bought = interview.suggested_contract({"instrument_kind": "OPTION", "option_position": "BUY", "expiry_rule": "NEXT", "strike_rule": "ITM", "strike_offset": 1})
    assert interview.suggested_text(bought) == "Expiry Next; Strike In the money, 1 step"
    assert interview.suggested_contract({"instrument_kind": "UNDERLYING"}) == []                 # the stock itself: nothing picked
    assert interview.suggested_contract({"instrument_kind": "FUTURE", "expiry_rule": "NEAREST"})[0]["value"] == "Nearest"


def test_an_interview_deploy_with_suggested_values_needs_his_acceptance():
    from app.ai.routes import CandidateDeployBody
    assert CandidateDeployBody(candidate_id=1).accept_suggested is False
    from app.ai import routes
    source = open(routes.__file__, encoding="utf8").read()
    gate = source.index("suggested = interview.suggested_contract(payload)")
    assert source.index("if suggested and not body.accept_suggested:", gate) < source.index("request = DeploymentCreateRequest(**payload)", gate)


# --- TS1-078: the Copilot generator ---------------------------------------------------------------------------------

def test_the_copilot_never_widens_his_stop_and_states_no_minimum_rr():
    cfg = CustomStrategyConfig.model_validate({"name": "x", "timeframe": "5min", "long_conditions": [
        {"left": {"type": "indicator", "indicator": "RSI", "period": 14}, "operator": "GT", "right": {"type": "value", "value": 50}}],
        "short_conditions": [], "stop_loss_atr_mult": 0.5, "atr_period": 14, "target_rr": [1.5, 2.5], "min_rr": None})
    fixed, report = evaluate_config(cfg, legacy(), {}, autofix=True)
    assert report.ok and fixed.stop_loss_atr_mult == 0.5 and report.fixes == [] and "M2" in report.warnings
    text = open(prompt.__file__, encoding="utf8").read()
    assert "the platform raises a tighter one" not in text and "`min_rr` >= 1.0" not in text
    assert "the platform never changes the user's stop" in text and '"min_rr": number|null' in text


# --- TS1-083: the progression ladder card ---------------------------------------------------------------------------

def test_the_ladder_targets_are_labelled_milestones_that_lock_nothing():
    from app.paper import service
    assert service.LADDER_TARGETS_NOTE == "Milestones for your own review - they lock or unlock nothing."
    from app.journal.review import advice_in
    assert advice_in(service.LADDER_TARGETS_NOTE) == []                       # a plain fact, no advice wording
    source = open(service.__file__, encoding="utf8").read()
    assert '"targets_note": LADDER_TARGETS_NOTE,' in source


def test_the_interview_deploy_route_refuses_suggested_picks_until_he_accepts_them(monkeypatch):
    """TS1-077 over HTTP: a plan carrying its own contract picks is refused (400) without accept_suggested and created
    (201) with it - the same candidate, the same risk acceptance."""
    import asyncio
    import json

    from app.db.models import AiCandidateRecord
    from tests.server_evidence import serve_candles
    from tests.test_auth_api import _session_factory, client
    from tests.test_contract_rules import _load_master
    from tests.test_phase_ap_interview import _sessions
    from tests.test_trading_worker import _tenant
    _load_master()                                                          # the NIFTY options list, as on the server
    headers = _tenant("m68-pr8-accept@example.com")["headers"]
    df = _sessions(days=5)
    serve_candles(monkeypatch, [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                                 "volume": r.volume} for ts, r in df.iterrows()])
    plan = client.post("/api/ai/interview/plan", headers=headers, json={
        "answers": {"language": "en", "experience": "learning", "capital": 300000, "risk": "moderate", "style": "intraday",
                    "vehicle": "option_buy", "goal": "big_trends", "symbol": "NIFTY 50", "instrument": "index"},
        "base_timeframe": "5min"})
    assert plan.status_code == 200, plan.text
    cid = plan.json()["candidate_id"]

    async def with_evidence() -> list:
        async with _session_factory() as session:
            row = await session.get(AiCandidateRecord, cid)
            metrics = json.loads(row.metrics_json or "{}")
            metrics["evidence"] = {"tested": True, "total_trades": 18, "win_rate": 0.5}
            row.metrics_json = json.dumps(metrics)
            await session.commit()
            return interview.suggested_contract(json.loads(row.deployment_json or "{}"))
    picks = asyncio.run(with_evidence())
    assert picks, "the option plan carries its own contract picks"
    refused = client.post("/api/ai/interview/deploy", headers=headers, json={"candidate_id": cid, "accept_risk": True})
    assert refused.status_code == 400 and "accept_suggested" in refused.text and interview.suggested_text(picks)[:40] in refused.text
    accepted = client.post("/api/ai/interview/deploy", headers=headers,
                           json={"candidate_id": cid, "accept_risk": True, "accept_suggested": True})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["mode"] == "PAPER" and accepted.json()["deployment"]["mode"] == "PAPER"


def test_a_strategy_whose_smallest_candle_is_not_a_broker_base_asks_for_base_candles():
    """TS1-099: _own_base never picks a base the broker does not serve - "1d" (not "day", not an intraday base) is asked for."""
    import pytest
    from fastapi import HTTPException

    from app.deployments.routes import SUPPORTED_BASE_TIMEFRAMES, DeploymentCreateRequest, _own_base

    class _Stub:
        name = "stub"
        timeframes = ["1d"]
    assert "1d" not in SUPPORTED_BASE_TIMEFRAMES
    req = DeploymentCreateRequest(strategy_id="ema_rsi_scalper_1m", symbol="reliance")
    with pytest.raises(HTTPException) as refused:
        _own_base(req, _Stub())
    assert refused.value.status_code == 422 and str(refused.value.detail).startswith("Needs your setting: base candles")
    assert req.timeframe is None and req.exchange == "NSE"                 # the exchange is still the symbol's own fact
