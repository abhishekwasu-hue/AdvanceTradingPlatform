"""H-C1 d: grounding - numbers from the trader's question are "user_provided", never "verified"; a direction the
answer asserts must be in the facts; the generator's explanation and the scanner's free text pass the number check."""
import asyncio
import json

import pytest

from app.ai import copilot, generator, grounding
from app.db.models import Tenant, User
from app.scanner import ai as scanner_ai
from app.strategy_engine.declarative import CustomStrategyConfig
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import VALID_ANSWER, _ScriptedProvider, _owner


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("text, found", [
    ("NIFTY looks bullish here.", {"BULL"}),
    ("The trend is down: a clear downtrend.", {"BEAR"}),
    ("The bull case needs 25200; the bear case is below 24850.", set()),
    ("A bearish divergence formed on RSI.", set()),
    ("It is not bullish today.", set()),
    ("बाजार तेजीत आहे.", {"BULL"}),
    ("बाजार तेजीत नाही.", set()),
    ("मंदीचा कल दिसतो.", {"BEAR"}),
    ("Regime TRENDING_UP, bias BULLISH", {"BULL"}),
])
def test_direction_claims_in_both_languages(text, found):
    assert grounding.directions_in(text) == found


def test_a_direction_must_be_in_the_facts():
    facts = "NIFTY 50 bias BULLISH, regime TRENDING_UP"
    assert grounding.check_direction("NIFTY 50 looks bullish.", facts) == (True, [])
    assert grounding.check_direction("NIFTY 50 turned bearish.", facts) == (False, ["bearish claim"])
    assert grounding.check_direction("Momentum is bullish.", "No market read yet.")[0] is False      # nothing in the facts supports it
    assert grounding.check_direction("RSI is at 61.", "No market read yet.")[0] is True                # no claim, nothing to check


def test_numbers_from_the_question_are_user_provided_not_verified():
    tags = grounding.provenance("NIFTY is at 25200; your 26000 level is above it, 999 is nowhere.",
                                grounding.allowed_from_text("NIFTY 50 last 25200"), grounding.allowed_from_text("what about 26000?"))
    assert tags == {"verified": ["25200"], "user_provided": ["26000"], "unsupported": ["999"]}


def test_generator_explanation_needs_numbers_from_the_strategy():
    data = json.loads(VALID_ANSWER)
    config = CustomStrategyConfig.model_validate(data["config"])
    ctx = {"capital": 300000, "risk_per_trade_pct": 1.0}
    assert generator.text_problem("Buys pullbacks when RSI crosses 40 with EMA 20 above EMA 50.", [], config, None, ctx, "pullbacks") is None
    assert generator.text_problem("Risk 1% of 300000 per trade.", [], config, None, ctx, "x") is None
    assert "numbers not in the strategy: 72%" in generator.text_problem("Wins 72% of the time.", [], config, None, ctx, "x")
    assert generator.text_problem("Fine.", ["Targets 5000 points"], config, None, ctx, "x").startswith("numbers not in the strategy")
    assert "advice/guarantee" in generator.text_problem("A sure-shot pullback buy.", [], config, None, ctx, "x")
    assert generator.text_problem("Uses the 15 minute bars you asked for.", [], config, None, ctx, "15 minute bars") is None


def test_generator_retries_then_withholds_an_ungrounded_explanation():
    headers, me = _owner("hc1d-gen@example.com")
    bad = json.loads(VALID_ANSWER)
    bad["explanation"] = "This wins 87% of trades."

    async def gen(answers):
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            user = await session.get(User, me["id"])
            provider = _ScriptedProvider(answers)
            draft = await generator.generate(session, tenant, user, "Buy pullbacks in an uptrend on 5 minute bars", provider=provider)
            return draft, provider
    draft, provider = _run(gen([json.dumps(bad), VALID_ANSWER]))
    assert len(provider.calls) == 2 and "numbers not in the strategy" in provider.calls[1]
    assert draft.status == "DRAFT" and draft.explanation == json.loads(VALID_ANSWER)["explanation"]
    draft, _ = _run(gen([json.dumps(bad), json.dumps(bad)]))
    assert draft.status == "DRAFT" and draft.explanation.startswith("(AI explanation withheld: numbers not in the strategy: 87%")


def test_scanner_read_free_text_is_grounded_on_the_scan():
    payload = {"filters": {"indicator": ["RSI(14) GT 60"]}, "matches": [{"symbol": "RISING", "close": 123.45, "labels": ["RSI(14) GT 60"], "regime": None}]}
    ok = scanner_ai.ScanRead(summary="One match above RSI 60 at 123.45.", ranked=[scanner_ai.RankedSymbol(symbol="RISING", thesis="RSI(14) over 60.")])
    assert scanner_ai.read_problem(ok, payload) is None
    made_up = scanner_ai.ScanRead(summary="Target 150 next week.", ranked=[])
    assert "numbers not in the scan: 150" in scanner_ai.read_problem(made_up, payload)
    advice = scanner_ai.ScanRead(summary="A guaranteed mover.", ranked=[])
    assert "advice/guarantee" in scanner_ai.read_problem(advice, payload)


class _Provider:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    async def complete(self, system, user, *, max_tokens=700):
        self.calls.append(user)
        return self.answers.pop(0)


def test_copilot_refuses_a_direction_the_facts_do_not_carry():
    facts = ["NIFTY 50 last 25200, bias BULLISH, regime TRENDING_UP"]
    provider = _Provider(["NIFTY 50 at 25200 has turned bearish.", "NIFTY 50 at 25200 is in an uptrend."])
    text, why = _run(copilot.narrate(provider, "en", "market", "how is nifty?", facts))
    assert text == "NIFTY 50 at 25200 is in an uptrend." and "bearish claim" in provider.calls[1]
    stubborn = _Provider(["NIFTY 50 is bearish.", "NIFTY 50 is bearish."])
    text, why = _run(copilot.narrate(stubborn, "en", "market", "how is nifty?", facts))
    assert text is None and "bearish claim" in why


def test_the_copilot_api_tags_numbers_the_trader_supplied(monkeypatch):
    from app.ai import routes as ai_routes
    from tests.test_phase_p0_8b_grounding import _Provider as NamedProvider, _deploy, _tenant
    t = _tenant("hc1d-copilot@example.com")
    _deploy(t, symbol="NIFTY 50")
    provider = NamedProvider(["Your 24000 level is below anything in today's read; the plan stays paper only."])

    async def provider_for(session, tenant, **_kw):
        return provider
    monkeypatch.setattr(ai_routes.ai_settings, "provider_for", provider_for)
    out = client.post("/api/ai/copilot", headers=t["headers"], json={"message": "Should I watch 24000 today?", "language": "en"}).json()
    assert out["source"] == "ai", out
    assert out["numbers"]["user_provided"] == ["24000"] and "24000" not in out["numbers"]["verified"]
