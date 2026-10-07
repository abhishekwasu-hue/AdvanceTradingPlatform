"""Phase AV: the Copilot home - the trade coach, the daily briefing and the ask-anything router."""
import asyncio
from datetime import datetime, timedelta, timezone

from app.ai import briefing, coach, copilot
from app.core.models import RiskConfig
from app.db.models import MarketSnapshotRecord, StrategyDeploymentRecord, TradeRecord
from tests.test_auth_api import _register, _session_factory, client

UTC = timezone.utc
DAY = datetime(2026, 9, 21, 4, 0, tzinfo=UTC)   # Monday 09:30 IST


def _t(i, minutes, pnl, *, strategy="ema_rsi_scalper_1m", hold=10, entry=100.0, stop=99.0, qty=100, day=0, mode="PAPER"):
    start = DAY + timedelta(days=day, minutes=minutes)
    return {"id": i, "symbol": "NIFTY 50", "strategy_id": strategy, "mode": mode, "entry_time": start, "exit_time": start + timedelta(minutes=hold),
            "entry_price": entry, "stop_loss": stop, "quantity": qty, "pnl": pnl, "exit_reason": "Stop Loss" if pnl < 0 else "Target 1"}


def test_coach_finds_the_beginner_mistakes():
    risk = RiskConfig(capital=100_000, max_daily_loss_pct=1.0, max_trades_per_day=3, max_consecutive_losses=3)
    trades = [_t(1, 0, -100), _t(2, 12, -250, hold=60), _t(3, 80, -100), _t(4, 95, -120), _t(5, 120, -900)]   # a revenge-filled losing day
    trades += [_t(10 + d, 30, 180, day=d, strategy="orb_15m_5m") for d in range(1, 7)]                       # calm winning days
    r = coach.review(trades, "mr", risk, days=30, mode="ALL")
    ids = {f["id"] for f in r["flags"]}
    assert {"revenge", "overtrading", "loss_limit", "stop_discipline", "sample"} <= ids
    revenge = next(f for f in r["flags"] if f["id"] == "revenge")
    assert revenge["evidence"] == [2, 3, 4, 5] and "तोट्यानंतर" in revenge["text"]
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
    assert "Sideways" in plan["headline"] and "reversion" in plan["fit_families"] and "trend" in plan["avoid_families"]
    assert any("RBI policy" in line and "बंद" in line for line in plan["lines"]) and any("नवशिक्या" in line for line in plan["lines"])
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
    _add(MarketSnapshotRecord(tenant_id=tenant_id, kind="SYMBOL", symbol="NIFTY 50", exchange="NSE", timeframe="5min", source="upstox",
                              bias="NEUTRAL", regime="RANGING", higher_regime="RANGING", structure="RANGE", last_price=25000, change_pct=0.1,
                              payload_json="{}", captured_at=now),
         StrategyDeploymentRecord(tenant_id=tenant_id, strategy_id="ema_rsi_scalper_1m", symbol="NIFTY 50", exchange="NSE", timeframe="1min",
                                  mode="PAPER", status="ACTIVE"),
         TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="NIFTY 50", strategy_id="x", direction="LONG",
                     entry_time=now - timedelta(minutes=30), entry_price=100, quantity=10, stop_loss=99, exit_time=now - timedelta(minutes=5),
                     exit_price=98, pnl=-500.0))
    r = client.get("/api/ai/brief?language=mr", headers=headers)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["day_type"]["kind"] == "RANGE" and "Sideways" in b["plan"]["headline"]
    dep = b["deployments"][0]
    assert dep["family"] == "trend" and dep["regime"] == "RANGING" and any("trade न होणे हाच योग्य" in w for w in dep["why"])
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
    coached = ask("माझे trades कसे आहेत?")
    assert coached["intent"] == "coach" and coached["action"]["tab"] == "coach" and "बंद trades" in coached["answer"] and coached["source"] == "rules"
    interviewed = ask("मला intraday strategy सांगा")
    assert interviewed["intent"] == "interview" and interviewed["prefill"]["style"] == "intraday" and interviewed["action"]["tab"] == "strategy"
    why = ask("trade का होत नाही?")
    assert why["intent"] == "deployments" and "deployment चालू नाही" in why["answer"]
    guide = ask("RSI म्हणजे काय?")
    assert guide["intent"] == "guide" and guide["concepts"][0]["id"] == "rsi"
    today = ask("What should I do today?")
    assert today["intent"] == "brief" and today["language"] == "en" and today["brief"]["day_type"]["kind"] == "UNKNOWN"

    class Provider:
        name = "anthropic"
        seen = None

        async def complete(self, system, user, *, max_tokens=2000):
            Provider.seen = system
            return "आज बाजार sideways आहे."

    async def provider_for(session, tenant, **_kw):
        return Provider()
    from app.ai import routes as ai_routes
    monkeypatch.setattr(ai_routes.ai_settings, "provider_for", provider_for)
    narrated = ask("आज काय करू?")
    assert narrated["source"] == "ai" and narrated["answer"].startswith("आज बाजार") and "Marathi" in Provider.seen and "=== FACTS ===" in Provider.seen


def test_intents():
    cases = {"strategy सांगा": "interview", "review my trades": "coach", "माझ्या चुका सांगा": "coach", "why no trade?": "deployments",
             "आजचा plan काय?": "brief", "theta म्हणजे काय": "guide", "आज NIFTY चा कल काय?": "guide"}
    for message, expected in cases.items():
        assert copilot.intent(message) == expected, message
