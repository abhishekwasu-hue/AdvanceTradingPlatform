"""Phase V3: the guardian system prompt - runtime context from the account, the versioned
template, the deployment-suggestion mapping and the compliance rules that read it."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.ai import generator
from app.ai.prompt import PROMPT_VERSION, RuntimeContext, build_system_prompt, parse_suggestion, risk_profile_for
from app.db.models import Tenant, TradeRecord, User
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import VALID_ANSWER, _owner

IST_TODAY = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()


def _run(coro):
    return asyncio.run(coro)


class _CapturingProvider:
    name, model = "anthropic", "test-model"

    def __init__(self, answer):
        self.answer, self.systems, self.users = answer, [], []

    async def complete(self, system, user, *, max_tokens=2000):
        self.systems.append(system)
        self.users.append(user)
        return self.answer


def _answer(deployment=None, **overrides) -> str:
    data = json.loads(VALID_ANSWER)
    data["config"].update(overrides)
    if deployment is not None:
        data["deployment"] = deployment
    return json.dumps(data)


def _seed_closed(me, pnl, minutes_ago, reason="Target 1"):
    async def go():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            session.add(TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", strategy_id="s", symbol="RELIANCE", direction="LONG",
                                    entry_time=now - timedelta(minutes=minutes_ago + 30), entry_price=100.0, quantity=10, stop_loss=98.0, target1=104.0,
                                    exit_time=now - timedelta(minutes=minutes_ago), exit_price=100 + pnl / 10, exit_reason=reason, pnl=pnl))
            await session.commit()
    _run(go())


def _gen(me, provider, **kw):
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            user = await session.get(User, me["id"])
            return (await generator.generate(session, tenant, user, "Sell a put spread on NIFTY when it ranges", provider=provider, **kw)).id
    return _run(go())


def test_runtime_context_reflects_the_account_state():
    headers, me = _owner("v3-context@example.com")
    _seed_closed(me, +400.0, 40)
    for minutes in (30, 20, 10):
        _seed_closed(me, -200.0, minutes, reason="Stop Loss")
    client.post("/api/risk-guardian/events", headers=headers, json={"event_date": IST_TODAY, "underlying": "INDEX", "kind": "EXPIRY", "action": "SIZE_CUT", "size_cut_pct": 50, "description": "weekly expiry"})
    body = client.get("/api/ai/context?language=mr&regime=RANGING&symbol=NIFTY%2050", headers=headers).json()
    assert body["prompt_version"] == PROMPT_VERSION
    ctx = body["context"]
    assert ctx["risk_profile"] == "conservative" and ctx["user_language"] == "mr" and ctx["market_regime"] == "RANGING"
    assert ctx["allowed_instruments"].startswith("NIFTY 50 (requested); ")
    assert "last 4 closed trades (PAPER): 1 won, 3 lost, net -200, 3 stopped out; the last 3 in a row lost" == ctx["recent_trade_summary"]
    assert ctx["event_calendar"] == [f"{IST_TODAY} EXPIRY (INDEX): size cut 50.0% - weekly expiry"]
    assert ctx["drawdown_mode"] == "PAPER" and ctx["current_drawdown_pct"] > 0 and ctx["ceiling_risk_per_trade_pct"] == 2.0
    assert ctx["cooldown_minutes"] == 30 and ctx["max_portfolio_risk_pct"] == 6.0
    assert risk_profile_for(1.0) == "moderate" and risk_profile_for(1.5) == "aggressive"


def test_system_prompt_carries_context_rules_and_schema():
    ctx = RuntimeContext(account_capital=500_000.0, currency="INR", risk_profile="moderate", allowed_instruments="NIFTY options",
                         open_risk=12_000.0, open_risk_pct=2.4, current_drawdown_pct=3.5, drawdown_mode="LIVE",
                         recent_trade_summary="last 5 closed trades: 2 won, 3 lost", event_calendar=["2026-10-01 RBI_POLICY (all symbols): size cut 50%"],
                         market_regime="TRENDING_UP", user_language="mr", max_risk_per_trade_pct=1.0, ceiling_risk_per_trade_pct=2.0,
                         max_portfolio_risk_pct=6.0, daily_loss_limit_pct=2.0, cooldown_minutes=30, dd_level_1_pct=5.0, dd_level_2_pct=10.0, event_size_cut_pct=50.0)
    prompt = build_system_prompt(ctx)
    assert PROMPT_VERSION in prompt and "Reply in the user's language: Marathi (mr)" in prompt
    assert "Account capital: 500,000 INR" in prompt and "12,000 INR = 2.4% of capital" in prompt and "LIVE equity peak: 3.5%" in prompt
    assert "2026-10-01 RBI_POLICY" in prompt and "Market regime (from the platform's classifier): TRENDING_UP" in prompt
    assert "R3. Option selling must be defined-risk" in prompt and "count as ONE correlated bucket" in prompt and "30 minutes after a stop-out" in prompt
    assert '"deployment"' in prompt and "SHORT_STRADDLE" in prompt and "CALL_RATIO_SPREAD" in prompt and '"next_step"' in prompt
    assert "size = capital x risk% / stop distance per unit" in prompt and "never straight to full size" in prompt


def test_parse_suggestion_keeps_good_values_and_drops_bad_ones():
    good, warnings = parse_suggestion({"symbol": "NIFTY 50", "instrument_kind": "option", "option_strategy": "bull_put_spread", "expiry_rule": "nearest",
                                       "strike_rule": "otm", "strike_offset": 2, "spread_width": 2, "target_credit_pct": 60, "stop_credit_pct": 150,
                                       "exit_rules": {"break_even_at_r": 2}, "regime_filter": ["RANGING", "TRENDING_UP"], "next_step": "Backtest"})
    assert warnings == [] and good.option_strategy.value == "BULL_PUT_SPREAD" and good.is_credit_structure and not good.undefined_risk
    assert good.regime_filter == ["RANGING", "TRENDING_UP"] and good.next_step == "backtest" and "bull put spread" in good.describe()
    bad, warnings = parse_suggestion({"instrument_kind": "OPTION", "option_strategy": "MOON_SPREAD", "regime_filter": ["RANGING", "SIDEWAYS"],
                                      "exit_rules": {"trailing_stop_pct": 900, "break_even_at_r": 2}, "strike_offset": 99})
    assert bad is not None and bad.option_strategy.value == "SINGLE" and bad.regime_filter == ["RANGING"] and bad.strike_offset == 0
    assert bad.exit_rules.break_even_at_r == 2 and bad.exit_rules.trailing_stop_pct is None
    assert any("option_strategy dropped" in w for w in warnings) and any("unknown regime" in w for w in warnings) and any("trailing_stop_pct dropped" in w for w in warnings)
    assert parse_suggestion(None) == (None, []) and parse_suggestion("nonsense") == (None, [])


def test_generate_uses_the_guardian_prompt_and_stores_the_suggestion():
    headers, me = _owner("v3-generate@example.com")
    provider = _CapturingProvider(_answer(deployment={
        "symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_strategy": "BULL_PUT_SPREAD", "expiry_rule": "NEAREST", "strike_rule": "OTM", "strike_offset": 2,
        "spread_width": 2, "target_credit_pct": 60, "stop_credit_pct": 150, "exit_rules": {"break_even_at_r": 2, "trailing_stop_pct": 5},
        "regime_filter": ["RANGING"], "next_step": "backtest"}))
    draft = client.get(f"/api/ai/drafts/{_gen(me, provider, regime='RANGING', language='hi', symbol='NIFTY 50')}", headers=headers).json()
    assert len(provider.systems) == 1 and "Reply in the user's language: Hindi (hi)" in provider.systems[0]
    assert "Market regime (from the platform's classifier): RANGING" in provider.systems[0] and "NIFTY 50 (requested)" in provider.systems[0]
    assert provider.users[0].startswith("USER REQUEST:")
    assert draft["status"] == "DRAFT" and draft["prompt_version"] == PROMPT_VERSION and draft["runtime_context"]["user_language"] == "hi"
    assert draft["deployment"]["option_strategy"] == "BULL_PUT_SPREAD" and draft["deployment"]["regime_filter"] == ["RANGING"]
    assert "bull put spread, nearest expiry" in draft["deployment_text"] and "break-even at 2R" in draft["deployment_text"]
    by_rule = {c["rule"]: c for c in draft["compliance"]["checks"]}
    assert by_rule["R8"]["status"] == "PASS" and "defined risk" in by_rule["R8"]["detail"]
    assert by_rule["M4"]["status"] == "PASS" and by_rule["M5"]["status"] == "PASS" and by_rule["M9"]["status"] == "PASS"
    assert by_rule["M7"]["status"] == "PASS" and "x2.5" in by_rule["M7"]["detail"]
    assert draft["compliance"]["ok"]

    # An undefined-risk proposal is flagged (R8 warning), and an off-spec credit exit too (M7).
    provider = _CapturingProvider(_answer(deployment={"instrument_kind": "OPTION", "option_strategy": "SHORT_STRADDLE", "target_credit_pct": 90, "stop_credit_pct": 400}))
    draft = client.get(f"/api/ai/drafts/{_gen(me, provider)}", headers=headers).json()
    by_rule = {c["rule"]: c for c in draft["compliance"]["checks"]}
    assert by_rule["R8"]["status"] == "WARN" and "undefined risk" in by_rule["R8"]["detail"]
    assert by_rule["M7"]["status"] == "WARN" and draft["compliance"]["ok"]

    # The rule-based fallback still answers under the new prompt (no deployment block).
    fallback = client.post("/api/ai/drafts", headers=headers, json={"prompt": "Go long when RSI(14) crosses above 30 on 5min", "language": "mr"})
    assert fallback.status_code == 201, fallback.text
    assert fallback.json()["provider"] == "rule_based" and fallback.json()["deployment"] is None and fallback.json()["prompt_version"] == PROMPT_VERSION
    assert fallback.json()["runtime_context"]["user_language"] == "mr"
