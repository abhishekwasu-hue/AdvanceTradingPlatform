"""Phase BD-lite: the market thesis - factors and agreement, scenarios from support/resistance, the shadow
multiplier (monotone, never applied, nothing in execution/risk imports it), the numbers check on a model
narrative, storage + API behind the default-off flag, Telegram /thesis, and next-session scoring."""
import asyncio
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.ai import thesis as th
from app.db.models import MarketEventRecord, MarketSnapshotRecord, ThesisRecord
from app.platform import controls
from tests.test_auth_api import _register, _session_factory, client

UTC = timezone.utc
NOW = datetime(2026, 10, 6, 5, 0, tzinfo=UTC)          # 10:30 IST on a Tuesday
ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


def _run(coro):
    return asyncio.run(coro)


def _tenant(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["tenant_id"], me["id"]


def _flag(on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["market_thesis"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _payload(last=25000.0, bias="BULLISH", bias_score=0.8, structure="UPTREND", higher="TRENDING_UP", sup=(24850.0, 24900.0), res=(25150.0, 25200.0), atr_pct=0.5):
    return {"last_price": last, "bias": bias, "bias_score": bias_score, "structure": {"trend": structure}, "higher_regime": {"kind": higher},
            "support": {"low": sup[0], "high": sup[1], "source": "zone"} if sup else None,
            "resistance": {"low": res[0], "high": res[1], "source": "zone"} if res else None, "atr_pct": atr_pct}


def _snapshot(symbol="NIFTY 50", **kw):
    payload = _payload(**kw)
    return {"symbol": symbol, "exchange": "NSE", "kind": "SYMBOL", "source": "upstox", "last_price": payload["last_price"], "change_pct": 0.4, "bias": payload["bias"],
            "regime": "TRENDING_UP", "higher_regime": payload["higher_regime"]["kind"], "structure": payload["structure"]["trend"], "captured_at": NOW.isoformat(), "payload": payload}


def _seed(tenant_id, symbol="NIFTY 50", when=NOW, **kw):
    payload = _payload(**kw)

    async def go():
        async with _session_factory() as session:
            session.add(MarketSnapshotRecord(tenant_id=tenant_id, kind="SYMBOL", symbol=symbol, exchange="NSE", timeframe="5min", source="upstox",
                                             last_price=payload["last_price"], change_pct=0.4, bias=payload["bias"], regime="TRENDING_UP",
                                             higher_regime=payload["higher_regime"]["kind"], structure=payload["structure"]["trend"], payload_json=json.dumps(payload), captured_at=when))
            session.add(MarketSnapshotRecord(tenant_id=tenant_id, kind="CUE", symbol="INDIA VIX", exchange="NSE", timeframe="day", source="upstox",
                                             last_price=13.0, change_pct=-2.0, payload_json="{}", captured_at=when))
            await session.commit()
    _run(go())


def test_factors_agreement_scenarios_and_shadow_multiplier_are_deterministic_and_monotone():
    snap = _snapshot()
    sentiment = {"score": 40.0, "label": "RISK_ON", "coverage": 0.8}
    news = [{"headline": "RBI holds rates", "symbols": ["NIFTY"], "classification": {"direction": "BULLISH", "severity": 4, "confidence": 0.9}}]
    globals_ = [{"symbol": "SP500_FUT", "change_pct": 1.0, "payload": {"as_of": NOW.isoformat()}}]
    rows = th.factor_rows(snap, sentiment, news, globals_, NOW)
    assert [r["factor"] for r in rows] == list(th.WEIGHTS) and all(r["available"] for r in rows)
    agree = th.agreement(rows)
    assert agree["direction"] == "BULLISH" and agree["agreeing"] == agree["with_opinion"] == 6 and agree["share"] == 1.0 and agree["confidence"] > 60 and not agree["conflict"]

    # One bearish factor: still bullish, but a conflict with a lower share.
    rows2 = th.factor_rows(snap, {"score": -60.0, "label": "RISK_OFF"}, news, globals_, NOW)
    agree2 = th.agreement(rows2)
    assert agree2["direction"] == "BULLISH" and agree2["conflict"] and agree2["share"] < 1.0 and agree2["confidence"] < agree["confidence"]
    split = {**agree2, "agreeing": 4, "with_opinion": 6, "share": 0.67}                  # two of six against: "factors disagree"

    # Missing inputs are rows without an opinion; coverage drops; a flat read is NEUTRAL.
    flat = th.factor_rows(_snapshot(bias="NEUTRAL", bias_score=0.0, structure="SIDEWAYS", higher="RANGING"), None, [], [], NOW)
    agree3 = th.agreement(flat)
    assert agree3["direction"] == "NEUTRAL" and agree3["coverage"] < 1.0 and [r["available"] for r in flat] == [True, True, True, False, False, False]

    scen = th.scenarios(snap, "en")
    assert scen["bull"]["trigger"] == 25200.0 and scen["bear"]["trigger"] == 24850.0 and scen["base"] == {**scen["base"], "low": 24850.0, "high": 25200.0}
    assert scen["bull"]["target"] == 25550.0 and scen["bear"]["target"] == 24500.0 and scen["bull"]["invalidation"] == 24850.0      # measured move = the zone span
    no_zones = th.scenarios(_snapshot(sup=None, res=None, atr_pct=1.0), "mr")
    assert no_zones["bull"]["trigger"] == 25250.0 and no_zones["bear"]["trigger"] == 24750.0 and "तेजी" in no_zones["bull"]["text"]

    # The shadow multiplier only ever goes down, and an event blackout zeroes it.
    base = th.shadow_multiplier(agree, snap, 13.0, [])
    assert base["size_multiplier"] == 1.0 and base["applied"] is False and base["mode"] == "shadow"
    assert th.shadow_multiplier(agree2, snap, 13.0, [])["size_multiplier"] == 1.0      # five of six agree: no cut
    conflicted = th.shadow_multiplier(split, snap, 13.0, [])
    assert conflicted["size_multiplier"] == 0.75 and conflicted["reasons"] == ["factors disagree"]
    volatile = th.shadow_multiplier(split, snap, 23.0, [])
    assert volatile["size_multiplier"] == 0.56 and "volatile" in " ".join(volatile["reasons"])
    cut = th.shadow_multiplier(agree, snap, 13.0, [{"action": "SIZE_CUT", "kind": "RBI", "size_cut_pct": 40}])
    assert cut["size_multiplier"] == 0.6
    blocked = th.shadow_multiplier(agree, snap, 13.0, [{"action": "BLOCK", "kind": "BUDGET", "size_cut_pct": None}])
    assert blocked["size_multiplier"] == 0.0
    for a, b in ((base, conflicted), (conflicted, volatile), (base, cut), (cut, blocked)):
        assert a["size_multiplier"] >= b["size_multiplier"]


def test_shadow_multiplier_is_never_read_by_execution_risk_or_guardian_code():
    """The overlay is shadow-only by construction: no order, sizing, risk or worker-trading path imports
    the thesis module, and no deployment/risk setting names it."""
    forbidden = ["execution", "risk_engine", "trading", "strategy_engine", "brokers", "deployments", "guardian"]
    offenders = []
    for folder in forbidden:
        path = ROOT / folder
        if not path.exists():
            continue
        for file in path.rglob("*.py"):
            text = file.read_text(encoding="utf-8")
            if re.search(r"\bthesis\b", text) or "shadow_multiplier" in text or "size_multiplier" in text and "thesis" in text:
                offenders.append(str(file.relative_to(ROOT)))
    assert offenders == []
    worker = (ROOT / "workers" / "trading_worker.py").read_text(encoding="utf-8")
    assert "shadow" not in worker and "size_multiplier" not in worker                 # the worker only builds and scores
    models = (ROOT / "db" / "models.py").read_text(encoding="utf-8")
    assert "thesis_overlay" not in models                                               # no deployment setting can switch it on


def test_numbers_check_accepts_only_numbers_from_the_inputs():
    snap = _snapshot()
    thesis = th.compose("NIFTY 50", snap, {"sentiment": {"score": 40.0, "label": "RISK_ON", "coverage": 0.8}, "globals": [], "cues": [{"symbol": "INDIA VIX", "last_price": 13.0}]}, [], [], "en", NOW)
    ok, bad = th.numbers_check("NIFTY 50 holds above 25,200.00 with room to 25550; below 24850 the idea fails. Confidence 73%.".replace("73", str(thesis["confidence"])), thesis)
    assert ok and bad == []
    ok, bad = th.numbers_check("A break of 25,300 opens 26000.", thesis)
    assert not ok and bad == ["25,300", "26000"]
    assert all(line and not line.startswith("None") for line in thesis["lines"]) and thesis["narrative_source"] == "rules"

    class _Provider:
        name = "anthropic"
        def __init__(self, answers): self.answers, self.calls = list(answers), 0
        async def complete(self, system, user, *, max_tokens=700):
            self.calls += 1
            assert "THESIS_JSON" in system and "<" not in system.split("THESIS_JSON")[0]
            return self.answers.pop(0)

    good = _Provider(["Bulls hold 25200.00; room to 25550.00, wrong below 24850.00."])
    text, why = _run(th.narrate(good, thesis, "en"))
    assert text and why == "ok" and good.calls == 1
    retry = _Provider(["Target 26000 soon.", "Room to 25550.00 above 25200.00."])
    text, why = _run(th.narrate(retry, thesis, "en"))
    assert text.startswith("Room") and retry.calls == 2
    stubborn = _Provider(["Target 26000.", "Target 27000."])
    text, why = _run(th.narrate(stubborn, thesis, "en"))
    assert text is None and "numbers check failed" in why and stubborn.calls == 2
    broken = _Provider([])
    text, why = _run(th.narrate(broken, thesis, "en"))
    assert text is None and "provider error" in why


def test_api_builds_stores_and_serves_the_thesis_behind_the_flag_and_scores_it_next_session():
    headers, tenant_id, _ = _tenant("bd-api@example.com")
    _flag(False)
    assert client.get("/api/ai/thesis/NIFTY%2050", headers=headers).status_code == 503              # default off
    _flag(True)
    assert client.get("/api/ai/thesis/NIFTY%2050", headers=headers).status_code == 404              # no read yet
    _seed(tenant_id)

    async def event():
        async with _session_factory() as session:
            # The organisation's own event (a global one would bite every other test's guardian today).
            session.add(MarketEventRecord(tenant_id=tenant_id, underlying="INDEX", event_date=NOW.astimezone(th.IST).date(), start_time="10:00", end_time="11:00",
                                          kind="RBI_POLICY", action="SIZE_CUT", size_cut_pct=50, description="RBI policy"))
            await session.commit()
    _run(event())

    async def build():
        async with _session_factory() as session:
            return await th.current(session, tenant_id, "NIFTY 50", lang="en", now=NOW)
    first = _run(build())
    assert first["direction"] == "BULLISH" and first["shadow"]["size_multiplier"] == 0.5 and first["shadow"]["applied"] is False and first["events"][0]["kind"] == "RBI_POLICY"
    assert first["scenarios"]["bull"]["trigger"] == 25200.0 and first["lines"][0].startswith("NIFTY 50 thesis: bullish")
    again = _run(build())
    assert again["id"] == first["id"]                                                                 # fresh enough: served from the store

    out = client.get("/api/ai/thesis/NIFTY%2050?language=mr", headers=headers).json()               # another language -> a new build, in Marathi
    assert out["direction"] == "BULLISH" and "तेजी" in out["lines"][0] and out["id"] != first["id"]

    async def pin_day():          # the API used the wall clock; pin its record to the test's day so scoring is deterministic
        async with _session_factory() as session:
            row = await session.get(ThesisRecord, out["id"])
            row.day, row.created_at = NOW.astimezone(th.IST).date(), NOW
            await session.commit()
    _run(pin_day())
    hist = client.get("/api/ai/thesis/history?symbol=NIFTY%2050", headers=headers).json()
    assert len(hist["items"]) == 2 and hist["scoreboard"]["scored"] == 0 and hist["scoreboard"]["shadow_applied"] is False
    other_headers, _, _ = _tenant("bd-other@example.com")
    assert client.get("/api/ai/thesis/history", headers=other_headers).json()["items"] == []        # tenant-scoped

    # Next session: the symbol's last read of the following day scores both theses.
    next_day = NOW + timedelta(days=1)
    _seed(tenant_id, when=next_day + timedelta(hours=1), last=25300.0)                              # +1.2% -> BULL
    _seed(tenant_id, when=next_day + timedelta(hours=5), last=25250.0)                              # the day's last read: +1.0%

    async def score(now):
        async with _session_factory() as session:
            n = await th.score_due(session, tenant_id, now=now)
            rows = list(await session.scalars(select(ThesisRecord).where(ThesisRecord.tenant_id == tenant_id)))
            return n, rows
    n, rows = _run(score(next_day + timedelta(hours=5, minutes=30)))
    assert n == 0 and all(r.scored_at is None for r in rows)                                        # the next session is still running
    n, rows = _run(score(next_day + timedelta(days=1)))
    assert n == 2 and all(r.outcome == "BULL" and r.score == 1.0 and json.loads(r.score_detail_json)["move_pct"] == 1.0 for r in rows)
    hist = client.get("/api/ai/thesis/history", headers=headers).json()
    assert hist["scoreboard"] == {"scored": 2, "hits": 2, "misses": 0, "flat": 0, "hit_rate": 1.0, "shadow_mode": "shadow", "shadow_applied": False}

    # A wrong call scores -1; a neutral call on a flat day scores +1; no later read -> UNKNOWN after the grace period.
    assert th.score_against("BEARISH", 100.0, 101.0)[:2] == ("BULL", -1.0)
    assert th.score_against("NEUTRAL", 100.0, 100.1)[:2] == ("RANGE", 1.0)
    assert th.score_against("BULLISH", 100.0, 100.1)[:2] == ("RANGE", 0.0)
    assert th.score_against("BULLISH", None, 100.0)[0] == "UNKNOWN"

    async def orphan():
        async with _session_factory() as session:
            session.add(ThesisRecord(tenant_id=tenant_id, symbol="OLDCO", day=(NOW - timedelta(days=10)).date(), direction="BULLISH", confidence=50, agreement=1.0,
                                     shadow_multiplier=1.0, last_price=10.0, lang="en", narrative_source="rules", thesis_json="{}", created_at=NOW - timedelta(days=10)))
            await session.commit()
            await th.score_due(session, tenant_id, now=NOW)
            return await session.scalar(select(ThesisRecord).where(ThesisRecord.symbol == "OLDCO"))
    row = _run(orphan())
    assert row.outcome == "UNKNOWN" and row.score is None and row.scored_at is not None


def test_capture_daily_builds_one_thesis_per_symbol_per_day_and_telegram_thesis_uses_it(monkeypatch):
    headers, tenant_id, _ = _tenant("bd-daily@example.com")
    _flag(True)
    _seed(tenant_id)
    _seed(tenant_id, symbol="RELIANCE", last=2900.0, sup=(2860.0, 2870.0), res=(2930.0, 2940.0))

    async def capture(now):
        async with _session_factory() as session:
            from app.ai import market_memory
            memory = await market_memory.latest(session, tenant_id, now=now)
            built = await th.capture_daily(session, tenant_id, memory, now=now)
            return built, list(await session.scalars(select(ThesisRecord).where(ThesisRecord.tenant_id == tenant_id)))
    built, rows = _run(capture(NOW))
    assert built == 2 and sorted(r.symbol for r in rows) == ["NIFTY 50", "RELIANCE"]
    built, rows = _run(capture(NOW + timedelta(hours=2)))
    assert built == 0 and len(rows) == 2                                                             # same IST day: nothing new

    # Telegram /thesis renders the same lines (flag on), plain text.
    from app.db.models import Tenant
    from app.telegram_inbound import service as tg

    async def text():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            return await tg._thesis_text(session, tenant.id, "RELIANCE", "en")
    out = _run(text())
    assert out.startswith("RELIANCE thesis:") and "Shadow overlay" in out and "never a signal" in out and "&" not in out.replace("P&L", "")
