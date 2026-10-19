"""Phase AV: the Copilot home - the trade coach, the daily briefing and the ask-anything router. English only (the owner's
ruling): the Copilot answers in English even when the trader writes in another language; only the strategy interview is
trilingual. Non-English messages here are written with \\u escapes (no Devanagari in the source - scripts/check_english_only.py)."""
import asyncio
import re
from datetime import datetime, timedelta, timezone

from app.ai import briefing, coach, copilot
from app.core.models import RiskConfig
from app.db.models import MarketSnapshotRecord, StrategyDeploymentRecord, TradeRecord
from tests.test_auth_api import _register, _session_factory, client

UTC = timezone.utc
DEVANAGARI = re.compile("[\u0900-\u097F]")
# "How are my trades?", "Tell me an intraday strategy", "What should I do today?" - in Marathi.
MR_MY_TRADES = "\u092e\u093e\u091d\u0947 trades \u0915\u0938\u0947 \u0906\u0939\u0947\u0924?"
MR_STRATEGY = "\u092e\u0932\u093e intraday strategy \u0938\u093e\u0902\u0917\u093e"
MR_TODAY = "\u0906\u091c \u0915\u093e\u092f \u0915\u0930\u0942?"
DAY = datetime(2026, 9, 21, 4, 0, tzinfo=UTC)   # Monday 09:30 IST


def _t(i, minutes, pnl, *, strategy="ema_rsi_scalper_1m", hold=10, entry=100.0, stop=99.0, qty=100, day=0, mode="PAPER"):
    start = DAY + timedelta(days=day, minutes=minutes)
    return {"id": i, "symbol": "NIFTY 50", "strategy_id": strategy, "mode": mode, "entry_time": start, "exit_time": start + timedelta(minutes=hold),
            "entry_price": entry, "stop_loss": stop, "quantity": qty, "pnl": pnl, "exit_reason": "Stop Loss" if pnl < 0 else "Target 1"}


def test_coach_finds_the_beginner_mistakes():
    risk = RiskConfig(capital=100_000, max_daily_loss_pct=1.0, max_trades_per_day=3, max_consecutive_losses=3)
    trades = [_t(1, 0, -100), _t(2, 12, -250, hold=60), _t(3, 80, -100), _t(4, 95, -120), _t(5, 120, -900)]   # a revenge-filled losing day
    trades += [_t(10 + d, 30, 180, day=d, strategy="orb_15m_5m") for d in range(1, 7)]                       # calm winning days
    r = coach.review(trades, "mr", risk, days=30, mode="ALL")         # an older "mr" caller still gets English
    ids = {f["id"] for f in r["flags"]}
    assert {"revenge", "overtrading", "loss_limit", "stop_discipline", "sample"} <= ids
    revenge = next(f for f in r["flags"] if f["id"] == "revenge")
    assert revenge["evidence"] == [2, 3, 4, 5] and "minutes of a loss" in revenge["text"] and not DEVANAGARI.search(revenge["text"])
    assert r["stats"]["trades"] == 11 and r["stats"]["longest_losing_streak"] == 5 and r["stats"]["worst_day"] == -1470.0
    assert r["by_strategy"][0]["key"] == "orb_15m_5m" and r["grade"] in ("C", "D") and len(r["focus"]) == 3
    assert r["flags"][0]["severity"] == "high" and r["equity"][-1] == r["stats"]["net_pnl"]
    en = coach.review(trades, "en", risk)
    assert "Revenge trading" in [f["title"] for f in en["flags"]] and coach.summary_lines("en", en)[0].startswith("11 closed trades")
    empty = coach.review([], "mr")
    assert empty["stats"]["trades"] == 0 and "PAPER" in empty["focus"][0]


def test_coach_praises_discipline():
    risk = RiskConfig(capital=100_000, max_daily_loss_pct=3.0, max_trades_per_day=5)
    trades = [_t(i, 30, 220 if i % 3 else -100, day=i, hold=20 if i % 3 else 15) for i in range(1, 31)]
    r = coach.review(trades, "en", risk)
    assert [f["id"] for f in r["flags"]] == ["good_discipline"] and r["grade"] == "A"


MEMORY = {"symbols": [{"symbol": "NIFTY 50", "regime": "RANGING", "bias": "NEUTRAL", "change_pct": 0.1, "higher_regime": "RANGING"}],
          "cues": [{"symbol": "INDIA VIX", "last_price": 13.0, "change_pct": -1.0}], "globals": [], "history": {}}


def test_day_type_and_game_plan():
    dt = briefing.day_type(MEMORY)
    assert dt["kind"] == "RANGE" and dt["vix"] == 13.0
    plan = briefing.game_plan("mr", dt, MEMORY, "new", [{"action": "BLOCK", "description": "RBI policy"}])
    assert "sideways" in plan["headline"] and "reversion" in plan["fit_families"] and "trend" in plan["avoid_families"]
    assert any("RBI policy" in line and "blocked" in line for line in plan["lines"]) and any("Default risk settings (not set yet)" in line for line in plan["lines"])
    assert not DEVANAGARI.search(plan["headline"] + " ".join(plan["lines"]))
    fear = dict(MEMORY, cues=[{"symbol": "INDIA VIX", "last_price": 24.0}])
    assert briefing.day_type(fear)["kind"] == "VOLATILE"
    assert briefing.day_type({"symbols": [], "cues": []})["kind"] == "UNKNOWN"


def _tenant(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["tenant_id"], me["id"]


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    asyncio.run(go())


def test_brief_endpoint_explains_the_day_and_the_deployments():
    headers, tenant_id, user_id = _tenant("copilot-brief@example.com")
    now = datetime.now(UTC)
    ist = timezone(timedelta(hours=5, minutes=30))
    ist_midnight = datetime.now(ist).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    entry_at = max(now - timedelta(minutes=30), ist_midnight + timedelta(seconds=1))   # both inside today's IST day, even
    exit_at = max(now - timedelta(minutes=5), min(entry_at + timedelta(seconds=1), now))  # just after IST midnight
    _add(MarketSnapshotRecord(tenant_id=tenant_id, kind="SYMBOL", symbol="NIFTY 50", exchange="NSE", timeframe="5min", source="upstox",
                              bias="NEUTRAL", regime="RANGING", higher_regime="RANGING", structure="RANGE", last_price=25000, change_pct=0.1,
                              payload_json="{}", captured_at=now),
         StrategyDeploymentRecord(tenant_id=tenant_id, strategy_id="ema_rsi_scalper_1m", symbol="NIFTY 50", exchange="NSE", timeframe="1min",
                                  mode="PAPER", status="ACTIVE"),
         TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="NIFTY 50", strategy_id="x", direction="LONG",
                     entry_time=entry_at, entry_price=100, quantity=10, stop_loss=99, exit_time=exit_at,
                     exit_price=98, pnl=-500.0))
    r = client.get("/api/ai/brief?language=mr", headers=headers)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["day_type"]["kind"] == "RANGE" and "sideways" in b["plan"]["headline"]
    dep = b["deployments"][0]
    assert dep["family"] == "trend" and dep["regime"] == "RANGING" and any("no trade is the correct result" in w for w in dep["why"])
    assert b["language"] == "en" and not DEVANAGARI.search(str(b))
    checks = {c["id"]: c for c in b["checklist"]}
    assert checks["broker"]["ok"] is False and checks["risk"]["ok"] is False
    paper = b["you"]["PAPER"]
    assert paper["realised_pnl"] == -500.0 and paper["loss_used"] == 500.0 and paper["consecutive_losses"] == 1
    assert client.get("/api/ai/brief").status_code == 401


def test_coach_endpoint_and_copilot_routing(monkeypatch):
    headers, tenant_id, user_id = _tenant("copilot-ask@example.com")
    now = datetime.now(UTC)
    _add(*[TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="NIFTY 50", strategy_id="orb_15m_5m", direction="LONG",
                       entry_time=now - timedelta(days=i, minutes=40), entry_price=100, quantity=10, stop_loss=99,
                       exit_time=now - timedelta(days=i, minutes=10), exit_price=101, pnl=10.0 if i % 2 else -10.0) for i in range(1, 7)])
    c = client.get("/api/ai/coach?language=en&days=30&mode=PAPER", headers=headers).json()
    assert c["stats"]["trades"] == 6 and c["period"]["mode"] == "PAPER"
    assert client.get("/api/ai/coach?mode=BAD", headers=headers).status_code == 422

    ask = lambda m: client.post("/api/ai/copilot", headers=headers, json={"message": m}).json()
    coached = ask("How am I doing? Review my trades")
    assert coached["intent"] == "coach" and coached["action"]["tab"] == "coach" and "closed trades" in coached["answer"] and coached["source"] == "rules"
    client.put("/api/ai/preferences", headers=headers, json={"ai_language": "mr"})      # an older client: still English
    marathi = ask(MR_MY_TRADES)
    assert marathi["language"] == "en" and not DEVANAGARI.search(marathi["answer"]) and marathi["action"]["label"]
    interviewed = ask("Build me an intraday strategy")
    assert interviewed["intent"] == "interview" and interviewed["prefill"]["style"] == "intraday" and interviewed["action"]["tab"] == "strategy"
    assert ask(MR_STRATEGY)["language"] == "en"
    why = ask("why no trade?")
    assert why["intent"] == "deployments" and not DEVANAGARI.search(why["answer"])
    guide = ask("What is RSI?")
    assert guide["intent"] == "guide" and guide["concepts"][0]["id"] == "rsi"
    today = ask("What should I do today?")
    assert today["intent"] == "brief" and today["language"] == "en" and today["brief"]["day_type"]["kind"] == "UNKNOWN"

    class Provider:
        name = "anthropic"
        seen = None

        async def complete(self, system, user, *, max_tokens=2000):
            Provider.seen = system
            return "\u0906\u091c \u092c\u093e\u091c\u093e\u0930 sideways."      # a Devanagari reply

    async def provider_for(session, tenant, **_kw):
        return Provider()
    from app.ai import routes as ai_routes
    monkeypatch.setattr(ai_routes.ai_settings, "provider_for", provider_for)
    narrated = ask(MR_TODAY)
    # English only: the instructions say English whatever the message's language, and the AI's Devanagari reply is
    # withheld by the English filter - the rule text is shown.
    assert narrated["source"] == "rules" and narrated["language"] == "en" and "=== FACTS ===" in Provider.seen
    assert "Answer in English only" in Provider.seen and "Marathi" not in Provider.seen
    assert "not in plain English" in narrated["note"] and not DEVANAGARI.search(narrated["answer"])


def test_intents():
    cases = {"which strategy fits me": "interview", "review my trades": "coach", "what are my mistakes": "coach", "why no trade?": "deployments",
             "what is today's plan?": "brief", "what is theta": "guide", "what is NIFTY doing today?": "guide",
             MR_MY_TRADES: "guide"}           # no Marathi keywords any more: a Marathi message goes to the guide, answered in English
    for message, expected in cases.items():
        assert copilot.intent(message) == expected, message


class _SpyProvider:
    name = "anthropic"

    def __init__(self, reply):
        self.reply, self.systems, self.users = reply, [], []

    async def complete(self, system, user, *, max_tokens=2000):
        self.systems.append(system)
        self.users.append(user)
        return self.reply


def test_non_english_message_gets_english_instructions_and_the_english_filter(monkeypatch):
    """The owner's ruling: outside the strategy interview the Copilot answers in English even when the trader writes in
    another language - the instruction names English, and the reply passes the English filter (`output_filter.check` with
    "en"): a reply in another script is withheld, an English one with advice words is blocked."""
    from app.ai import output_filter
    seen_langs = []
    real_check = output_filter.check

    def spy(text, lang="en", **kw):
        seen_langs.append(lang)
        return real_check(text, lang, **kw)
    monkeypatch.setattr(output_filter, "check", spy)
    facts = ["NIFTY 50 regime RANGING.", "Default risk settings (not set yet)."]
    for message in (MR_TODAY, "\u0906\u091c \u0915\u094d\u092f\u093e \u0915\u0930\u0942\u0901?"):   # Marathi, Hindi
        english = _SpyProvider("NIFTY 50 regime is RANGING; your risk settings are still the defaults.")
        text, why = asyncio.run(copilot.narrate(english, "mr", "brief", message, facts))
        assert text and why == "ok" and not DEVANAGARI.search(text)
        assert "Answer in English only" in english.systems[0] and "also\nwhen the question is written in another language" in english.systems[0]
        assert "Marathi" not in english.systems[0] and "Hindi" not in english.systems[0]
        assert message in english.users[0]                       # the trader's words are passed as the question, unchanged
        devanagari = _SpyProvider("NIFTY 50 \u0930\u0947\u0902\u091c \u092e\u0947\u0902 \u0939\u0948.")
        text, why = asyncio.run(copilot.narrate(devanagari, "mr", "brief", message, facts))
        assert text is None and why == output_filter.NOT_ENGLISH_REASON
        advice = _SpyProvider("NIFTY 50 is RANGING - I recommend you buy now, guaranteed.")
        text, why = asyncio.run(copilot.narrate(advice, "mr", "brief", message, facts))
        assert text is None and ("advice" in why or "words" in why)
    assert seen_langs and set(seen_langs) == {"en"}
