"""Phase AT: the guide - a bilingual concept library, market-aware answers, AI-grounded when configured."""
import asyncio

from app.ai import knowledge as k
from tests.test_auth_api import _register, client

MEMORY = {"symbols": [{"symbol": "NIFTY BANK", "bias": "BULLISH", "regime": "RANGING", "higher_regime": "TRENDING_UP", "structure": "UPTREND",
                       "change_pct": 0.4, "last_price": 54210.0}],
          "cues": [{"symbol": "INDIA VIX", "last_price": 13.2, "change_pct": -1.0}], "history": {}, "updated_at": None}


def test_every_concept_is_complete_in_both_languages():
    ids = {c.id for c in k.CONCEPTS}
    assert len(ids) == len(k.CONCEPTS) >= 25
    for c in k.CONCEPTS:
        assert c.en and c.mr and c.body_en and c.body_mr and c.keywords
        assert any("ऀ" <= ch <= "ॿ" for ch in c.body_mr)
        assert set(c.related) <= ids


def test_questions_find_their_concepts_in_marathi_and_english():
    cases = {"RSI म्हणजे काय?": "rsi", "stop-loss कुठे ठेवावा": "stop_loss", "What is theta?": "theta", "position size kiti gyava": "position_sizing",
             "India VIX जास्त आहे म्हणजे काय": "vix", "swing मध्ये CNC का?": "intraday_swing", "what is a breakout": "breakout",
             "revenge trading कसे टाळायचे": "discipline", "PCR म्हणजे काय": "pcr_oi"}
    for question, concept in cases.items():
        assert concept in [c.id for c in k.find_concepts(question)], question


def test_market_questions_use_the_market_memory():
    r = k.answer("आज बँक निफ्टी चा कल काय?", "mr", MEMORY)
    assert r["used_market_memory"] and "NIFTY BANK सध्या" in r["answer"] and "Sideways" in r["answer"]
    assert not k.answer("आज बँक निफ्टी चा कल काय?", "mr", None)["used_market_memory"]
    assert not k.answer("RSI म्हणजे काय?", "mr", MEMORY)["used_market_memory"]      # not a market question
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
        return "RSI हा momentum चा मापक आहे."


def test_ai_answer_is_grounded_and_falls_back():
    p = _Provider()
    r = asyncio.run(k.ai_answer(p, "RSI म्हणजे काय?", "mr", MEMORY, {"experience": "new", "style": "intraday"}))
    assert r["source"] == "ai" and r["answer"].startswith("RSI") and "Marathi" in p.seen
    assert "RSI (0-100)" in p.seen and "NIFTY BANK" in p.seen and "experience=new" in p.seen
    down = asyncio.run(k.ai_answer(_Provider(fail=True), "RSI म्हणजे काय?", "mr", MEMORY, None))
    assert down["source"] == "library" and "unavailable" in down["note"]


def test_guide_endpoints():
    headers = {"Authorization": f"Bearer {_register('guide@example.com')}"}
    # P0.9: the answer language is the user's AI setting (default English), whatever script the question uses.
    assert client.get("/api/ai/preferences", headers=headers).json()["ai_language"] == "en"
    default = client.post("/api/ai/ask", headers=headers, json={"question": "theta म्हणजे काय?"}).json()
    assert default["concepts"][0]["id"] == "theta" and "loses value every day" in default["answer"]
    assert client.put("/api/ai/preferences", headers=headers, json={"ai_language": "xx"}).status_code == 422
    assert client.put("/api/ai/preferences", headers=headers, json={"ai_language": "mr"}).json()["ai_language"] == "mr"
    r = client.post("/api/ai/ask", headers=headers, json={"question": "theta म्हणजे काय?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "library" and body["concepts"][0]["id"] == "theta" and "Option दररोज" in body["answer"]
    en = client.post("/api/ai/ask", headers=headers, json={"question": "what is theta", "language": "en"}).json()
    assert "loses value every day" in en["answer"]
    listing = client.get("/api/ai/concepts?language=mr", headers=headers).json()["concepts"]
    assert len(listing) == len(k.CONCEPTS)
    assert client.get("/api/ai/concepts/vix?language=en", headers=headers).json()["title"] == "India VIX"
    assert client.get("/api/ai/concepts/nope", headers=headers).status_code == 404
    assert client.post("/api/ai/ask", json={"question": "rsi?"}).status_code == 401
