"""ATP review 11: the Copilot's banned words (the frontend's compliance lint) are checked on the server against what a
model wrote - English and Marathi (खात्रीशीर, हमखास ...) - before any person reads it. A negated mention (a
disclaimer) passes; a claim makes the model try once more, else the rule-based text answers."""
import asyncio
import json

import pytest

from app.ai import copilot, generator, knowledge as k, strategist, wording
from app.db.models import Tenant, User
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import VALID_ANSWER, _owner


def _run(coro):
    return asyncio.run(coro)


class _Provider:
    name, model = "anthropic", "test-model"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    async def complete(self, system, user, *, max_tokens=2000):
        self.calls.append(user)
        return self.answers.pop(0)


@pytest.mark.parametrize("text,expected", [
    ("This template is the best one for you.", ["best", "for you"]),
    ("A guaranteed 2% a day.", ["guarantee"]),
    ("We recommend the ORB template.", ["recommend"]),
    ("A sure-shot, risk-free setup with easy profit.", ["assured / risk-free / sure-shot", "profit claim"]),
    ("Template B has a 92% match score.", ["match %"]),
    ("ही strategy खात्रीशीर आहे.", ["खात्रीशीर"]),
    ("हमखास नफा देणारा setup.", ["हमखास"]),
    ("यातून नक्की नफा मिळेल.", ["निश्चित / नक्की नफा"]),
    ("मी ही strategy शिफारस करतो.", ["शिफारस करतो"]),
    # self-review: a disclaimer or "no doubt" in another clause must not cancel the claim that follows
    ("Not financial advice. Guaranteed returns of 20% here.", ["guarantee", "profit claim"]),
    ("No doubt, guaranteed profit on this trade.", ["guarantee", "profit claim"]),
    ("Without doubt the best trade", ["best"]),
    ("Isn't it the best breakout?", ["best"]),
    ("हमखास नफा, शंका नाही", ["हमखास"]),
])
def test_claims_are_caught_in_english_and_marathi(text, expected):
    assert wording.banned_terms(text) == expected


@pytest.mark.parametrize("text", [
    "This is not a recommendation; no strategy is risk-free and there is no guarantee of profit.",
    "The platform does not recommend any strategy. Which one to run is for you to decide.",
    "Nothing here is guaranteed.",
    "ही शिफारस नाही; भूतकाळातले निकाल भविष्याची खात्री देत नाहीत.",          # the Copilot's own Marathi disclaimer
    "कोणताही setup खात्रीशीर नाही आणि हमखास नसतो.",
    # ordinary phrases, not claims
    "The best bid is 100 and the best ask 100.05.",
    "Best practice is to size positions small.",
    "At best a coin flip.",
    "A stop-loss guarantees nothing in a gap.",
    "",
])
def test_disclaimers_and_negations_pass(text):
    assert wording.banned_terms(text) == []


def test_copilot_answer_with_a_banned_word_is_retried_then_falls_back():
    facts = ["Today is a RANGING day for NIFTY 50 at 25200.0 (+0.4%)."]
    ok, why = copilot.grounded("NIFTY 50 at 25200 is the best setup for you.", facts, "What now?")
    assert not ok and "best" in why and "for you" in why
    fixed = _Provider(["NIFTY 50 at 25200 is a guaranteed winner.", "NIFTY 50 is at 25200.0 and ranging; paper only."])
    text, why = _run(copilot.narrate(fixed, "en", "brief", "What now?", facts))
    assert why == "ok" and text.startswith("NIFTY 50 is at") and "advice or a promise" in fixed.calls[1]
    stubborn = _Provider(["हमखास नफा: NIFTY 50 at 25200.", "NIFTY 50 at 25200 - खात्रीशीर."])
    text, why = _run(copilot.narrate(stubborn, "mr", "brief", "What now?", facts))
    assert text is None and "खात्रीशीर" in why                         # the caller keeps the rule text


def test_knowledge_answer_with_a_banned_word_falls_back_to_the_library():
    out = _run(k.ai_answer(_Provider(["RSI is the best indicator, guaranteed."]), "RSI म्हणजे काय?", "en", None, None))
    assert out["source"] == "library" and "best" in out["note"] and "guarantee" in out["note"]
    out = _run(k.ai_answer(_Provider(["RSI measures momentum between 0 and 100; it is not a recommendation."]), "RSI म्हणजे काय?", "en", None, None))
    assert out["source"] == "ai"


def test_generator_explanation_is_retried_then_withheld():
    headers, me = _owner("review-wording-gen@example.com")
    claim = json.loads(VALID_ANSWER)
    claim["explanation"] = "The best pullback system - sure-shot profits."
    claim_text = json.dumps(claim)

    async def gen(provider):
        async with _session_factory() as session:
            tenant = await session.get(Tenant, me["tenant_id"])
            user = await session.get(User, me["id"])
            return (await generator.generate(session, tenant, user, "Buy pullbacks in an uptrend", provider=provider)).id
    retried = _Provider([claim_text, VALID_ANSWER])
    draft = client.get(f"/api/ai/drafts/{_run(gen(retried))}", headers=headers).json()
    assert "advice or a promise" in retried.calls[1] and draft["explanation"] == json.loads(VALID_ANSWER)["explanation"]
    stubborn = _Provider([claim_text] * generator.MAX_ATTEMPTS)
    draft = client.get(f"/api/ai/drafts/{_run(gen(stubborn))}", headers=headers).json()
    assert draft["status"] == "DRAFT" and not draft["explanation"]
    assert any("explanation was withheld" in w for w in draft["warnings"])


def test_strategist_names_that_claim_are_renamed():
    cfg = json.loads(VALID_ANSWER)["config"]
    out = strategist.parse_ai(json.dumps({"strategies": [{"name": "Best sure-shot breakout", "config": cfg},
                                                          {"name": "EMA pullback", "config": cfg}]}))
    assert [name for name, _ in out] == ["AI strategy 1", "EMA pullback"]
