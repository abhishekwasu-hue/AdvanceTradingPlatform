"""Phase AT: the guide - a concept library, market-aware answers, AI-grounded when configured. English only (the owner's
ruling): a question in another script is still answered, in English."""
import re
import asyncio

from app.ai import knowledge as k
from tests.test_auth_api import _register, client

MEMORY = {"symbols": [{"symbol": "NIFTY BANK", "bias": "BULLISH", "regime": "RANGING", "higher_regime": "TRENDING_UP", "structure": "UPTREND",
                       "change_pct": 0.4, "last_price": 54210.0}],
          "cues": [{"symbol": "INDIA VIX", "last_price": 13.2, "change_pct": -1.0}], "history": {}, "updated_at": None}


DEVANAGARI = re.compile("[\u0900-\u097F]")


def test_every_concept_is_complete_and_english():
    ids = {c.id for c in k.CONCEPTS}
    assert len(ids) == len(k.CONCEPTS) >= 25
    for c in k.CONCEPTS:
        assert c.en and c.body_en and c.keywords
        assert not hasattr(c, "mr") and not hasattr(c, "body_mr")
        assert not DEVANAGARI.search(" ".join((c.en, c.body_en, *c.keywords)))
        assert set(c.related) <= ids


def test_questions_find_their_concepts():
    cases = {"what is RSI?": "rsi", "where do I put a stop-loss": "stop_loss", "What is theta?": "theta", "what position size": "position_sizing",
             "India VIX is high, what does it mean": "vix", "why CNC for swing?": "intraday_swing", "what is a breakout": "breakout",
             "how to avoid revenge trading": "discipline", "what is PCR": "pcr_oi"}
    for question, concept in cases.items():
        assert concept in [c.id for c in k.find_concepts(question)], question


def test_market_questions_use_the_market_memory():
    r = k.answer("What is BANK NIFTY doing today?", "en", MEMORY)
    assert r["used_market_memory"] and "NIFTY BANK right now" in r["answer"] and "sideways" in r["answer"]
    assert not k.answer("What is BANK NIFTY doing today?", "en", None)["used_market_memory"]
    assert not k.answer("what is RSI?", "en", MEMORY)["used_market_memory"]      # not a market question
    marathi = k.answer("RSI \u092e\u094d\u0939\u0923\u091c\u0947 \u0915\u093e\u092f?", "mr", MEMORY)   # "RSI - what is it?" in Marathi
    assert marathi["concepts"][0]["id"] == "rsi" and not DEVANAGARI.search(marathi["answer"])     # answered, in English
    fallback = k.answer("xyz qqq", "en")
    assert fallback["concepts"] == [] and "Try a word like" in fallback["answer"]
    assert k.answer("what is rsi", "en")["related"]


class _Provider:
    name = "anthropic"

    def __init__(self, fail=False):
        self.fail, self.seen = fail, None

    async def complete(self, system, user, *, max_tokens=2000):
        if self.fail:
            raise RuntimeError("down")
        self.seen = system
        return "RSI \u0939\u093e momentum."      # a reply in Devanagari: withheld (English only)


def test_ai_answer_is_grounded_and_falls_back():
    p = _Provider()
    r = asyncio.run(k.ai_answer(p, "what is RSI?", "mr", MEMORY, {"experience": "new", "style": "intraday"}))
    assert r["source"] == "library" and "Marathi" not in p.seen and "Answer in English only" in p.seen   # the English instruction, even for "mr"
    assert "RSI (0-100)" in p.seen and "NIFTY BANK" in p.seen and "experience=new" in p.seen
    down = asyncio.run(k.ai_answer(_Provider(fail=True), "what is RSI?", "mr", MEMORY, None))
    assert down["source"] == "library" and "unavailable" in down["note"]


def test_guide_endpoints():
    headers = {"Authorization": f"Bearer {_register('guide@example.com')}"}
    # English only: the answer is English whatever the stored setting, the requested language or the question's script.
    prefs = client.get("/api/ai/preferences", headers=headers).json()
    assert prefs == {"ai_language": "en", "languages": [{"code": "en", "label": "English"}]}
    default = client.post("/api/ai/ask", headers=headers, json={"question": "what is theta?"}).json()
    assert default["concepts"][0]["id"] == "theta" and "loses value every day" in default["answer"]
    assert client.put("/api/ai/preferences", headers=headers, json={"ai_language": "xx"}).status_code == 422
    assert client.put("/api/ai/preferences", headers=headers, json={"ai_language": "mr"}).json()["ai_language"] == "en"   # an older client
    r = client.post("/api/ai/ask", headers=headers, json={"question": "theta?", "language": "mr"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "library" and body["concepts"][0]["id"] == "theta" and "loses value every day" in body["answer"]
    assert not DEVANAGARI.search(body["answer"])
    en = client.post("/api/ai/ask", headers=headers, json={"question": "what is theta", "language": "en"}).json()
    assert "loses value every day" in en["answer"]
    listing = client.get("/api/ai/concepts?language=mr", headers=headers).json()["concepts"]
    assert len(listing) == len(k.CONCEPTS)
    assert client.get("/api/ai/concepts/vix?language=en", headers=headers).json()["title"] == "India VIX"
    assert client.get("/api/ai/concepts/nope", headers=headers).status_code == 404
    assert client.post("/api/ai/ask", json={"question": "rsi?"}).status_code == 401
