"""Z6-b / P1-e: the weekly review - numbers vs the journal, own-weeks comparison, descriptive wording only, the
Copilot explanation (grounded and advice-filtered, else the rule lines), and per-user isolation."""
import asyncio
import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ai import output_filter, wording
from app.db.models import TradeRecord, User
from app.journal import review as rv
from app.journal import capture, service
from app.journal.settings import DisciplineSettings
from app.market_data.calendar import IST
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_api import _register_user_and_get_id

S = DisciplineSettings()


def _run(coro):
    return asyncio.run(coro)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _flag(name: str, on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())
    capture.reset_flag_cache()   # the capture hooks cache the flag


@pytest.fixture
def flag_on():
    _flag("discipline_engine", True)
    yield
    _flag("discipline_engine", False)


def ist(month, day, hh, mm=0):
    return datetime(2026, month, day, hh, mm, tzinfo=IST).astimezone(timezone.utc)


# --- pure review ------------------------------------------------------------------------------------------------------

def _rt(id_, setup, pnl, charges=5.0, risk=20.0, hold=30.0, unit=None):
    return rv.ReviewTrade(id=id_, setup=setup, unit=unit or f"t:{id_}", pnl=pnl, charges=charges, risk=risk, holding_minutes=hold if pnl is not None else None)


def test_review_numbers_from_the_trades():
    trades = [_rt(1, "pullback", 35.0), _rt(2, "pullback", -25.0), _rt(3, "breakout", -30.0, risk=0.0), _rt(4, "breakout", None)]
    score = {"score": 94, "components": [{"name": "stop_discipline", "clean_pct": 75.0, "max_points": 25.0}]}
    out = rv.build_review("2026-W40", trades, [(3, "no_stop"), (99, "no_stop")], score, [{"week": "2026-W39", "score": 100}], S)
    t = out["totals"]
    assert (t["trades"], t["closed"], t["open"], t["wins"], t["losses"]) == (4, 3, 1, 1, 2)
    assert t["net_pnl"] == -20.0 and t["charges"] == 15.0 and t["gross_pnl"] == -5.0 and t["win_rate"] == 33.3
    assert t["avg_r"] == 0.25 and t["r_trades"] == 2 and t["expectancy"] == -6.67 and t["profit_factor"] == 0.64
    assert t["largest_win"] == 35.0 and t["largest_loss"] == -30.0 and t["avg_holding_minutes"] == 30.0
    assert [g["setup"] for g in out["what_worked"]] == ["pullback"] and [g["setup"] for g in out["what_lost"]] == ["breakout"]
    assert out["behaviours"] == [{"tag": "no_stop", "label": "no stop", "trades": 1, "closed": 1, "net_pnl": -30.0, "avg_r": None}]
    assert out["costliest"]["tag"] == "no_stop"
    assert "Compared with 2026-W39 (score 100), the score is down by 6." in out["lines"]
    assert "Setup breakout lost ₹30.00 over 1 closed trade." in out["lines"]          # no "lost -₹30.00"
    assert not any("-₹" in line and " lost " in line for line in out["lines"])
    assert any("lowest component was stop discipline at 75%" in line for line in out["lines"])


def test_empty_week_has_no_score_and_one_line():
    out = rv.build_review("2026-W40", [], [], {"score": None, "components": []}, [], S)
    assert out["lines"] == ["No trades were journaled in 2026-W40."] and out["costliest"] is None


def test_review_wording_describes_and_never_advises():
    trades = [_rt(i, f"setup{i % 3}", p) for i, p in enumerate([50.0, -10.0, -40.0, 0.0, 12.5, None], start=1)]
    tags = [(i, tag) for i, tag in enumerate(rv.TAG_LABELS, start=1)]
    score = {"score": 61, "components": [{"name": n, "clean_pct": 50.0, "max_points": 20.0} for n in rv.COMPONENT_LABELS]}
    out = rv.build_review("2026-W40", trades, tags, score, [{"week": "2026-W39", "score": 55}], S)
    texts = out["lines"] + [out["disclaimer"]] + list(rv.TAG_LABELS.values()) + list(rv.COMPONENT_LABELS.values())
    for text in texts:
        assert wording.banned_terms(text) == [], text
        assert output_filter.blocked_terms(text) == [], text
        for word in ("should", "must", "try ", "consider", "avoid", "recommend", "best"):
            assert word not in text.lower(), (word, text)


# --- API --------------------------------------------------------------------------------------------------------------

def _seed(user_id, tenant_id, rows):
    async def go():
        ids = []
        async with _session_factory() as session:
            for kw in rows:
                rec = TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", **kw)
                session.add(rec)
                await session.flush()
                ids.append(rec.id)
            await session.commit()
        return ids
    return _run(go())


def _closed(setup, symbol, direction, at, minutes, entry, exit_, qty, stop, target, pnl, reason, charges=5.0):
    return dict(strategy_id=setup, symbol=symbol, direction=direction, entry_time=at, entry_price=entry, quantity=qty, stop_loss=stop,
                initial_stop_loss=stop or None, target1=target, exit_time=at + timedelta(minutes=minutes), exit_price=exit_, pnl=pnl,
                exit_reason=reason, charges=charges)


_SEQ = itertools.count(1)


@pytest.fixture
def journal():
    n = next(_SEQ)
    token_a, a_id, tenant = _run(_register_user_and_get_id(f"z6b-a{n}@example.com"))
    token_b, b_id, _ = _run(_register_user_and_get_id(f"z6b-b{n}@example.com"))

    async def same_tenant():
        async with _session_factory() as session:
            (await session.get(User, b_id)).tenant_id = tenant
            await session.commit()
    _run(same_tenant())
    ids = _seed(a_id, tenant, [
        _closed("setupA", "NIFTY", "LONG", ist(9, 28, 10), 30, 100.0, 104.0, 10, 98.0, 104.0, 35.0, "Target 1"),
        _closed("setupA", "NIFTY", "LONG", ist(9, 28, 10, 35), 25, 100.0, 98.0, 10, 98.0, 104.0, -25.0, "Stop Loss"),
        _closed("setupB", "BANKNIFTY", "SHORT", ist(9, 29, 10), 20, 200.0, 205.0, 5, 0.0, 190.0, -30.0, "Closed by hand"),
        dict(strategy_id="setupB", symbol="NIFTY", direction="LONG", entry_time=ist(9, 30, 10), entry_price=100.0, quantity=10,
             stop_loss=98.0, target1=104.0),
        _closed("setupA", "NIFTY", "LONG", ist(9, 22, 10), 30, 100.0, 101.5, 10, 98.0, 104.0, 10.0, "Target 1"),     # 2026-W39
    ])
    _seed(b_id, tenant, [_closed("setupZ", "NIFTY", "LONG", ist(9, 28, 11), 10, 100.0, 90.0, 100, 0.0, 120.0, -1000.0, "Closed by hand")])
    return token_a, token_b, ids


def test_review_numbers_match_the_journal(flag_on, journal):
    token_a, token_b, ids = journal
    r = client.get("/api/journal/review?week=2026-W40&history=2", headers=_h(token_a))
    assert r.status_code == 200, r.text
    out = r.json()
    t = out["totals"]
    # straight from the seeded journal: 35 - 25 - 30 net, 15 charges, 1 of 3 closed won, R (35/20 + -25/20) / 2
    assert (t["trades"], t["closed"], t["open"], t["net_pnl"], t["charges"], t["win_rate"], t["avg_r"]) == (4, 3, 1, -20.0, 15.0, 33.3, 0.25)
    assert out["behaviours"] == [{"tag": "no_stop", "label": "no stop", "trades": 1, "closed": 1, "net_pnl": -30.0, "avg_r": None}]
    assert out["costliest"]["tag"] == "no_stop"
    assert out["discipline"]["score"] == 94                 # 18.75 + 20 + 25 + 15 + 15, half up
    assert out["history"][0] == {"week": "2026-W39", "score": 100, "trades": 1, "net_pnl": 10.0, "avg_r": 0.5, "win_rate": 100.0}
    assert out["history"][1]["week"] == "2026-W38" and out["history"][1]["score"] is None
    assert out["explanation"]["source"] == "rules" and "Discipline score 94 out of 100" in out["explanation"]["text"]
    assert "setupZ" not in r.text                            # the teammate's trade is not in this trader's review

    week = client.get("/api/journal/week?week=2026-W40", headers=_h(token_a)).json()
    tag = next(tg for e in week["entries"] for tg in e["tags"] if tg["tag"] == "no_stop")
    client.patch(f"/api/journal/tags/{tag['id']}", headers=_h(token_a), json={"status": "DISMISSED"})
    again = client.get("/api/journal/review?week=2026-W40&history=0", headers=_h(token_a)).json()
    assert again["behaviours"] == [] and again["costliest"] is None and again["discipline"]["score"] == 100 and again["history"] == []

    b = client.get("/api/journal/review?week=2026-W40&history=0", headers=_h(token_b)).json()
    assert b["totals"]["trades"] == 1 and b["totals"]["net_pnl"] == -1000.0


def test_review_flag_off_answers_503():
    token, _, _ = _run(_register_user_and_get_id("z6b-off@example.com"))
    assert client.get("/api/journal/review", headers=_h(token)).status_code == 503


class _FakeProvider:
    name = "fake"

    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def complete(self, system, user, max_tokens=1200):
        self.calls += 1
        return self.text


def _with_provider(monkeypatch, provider):
    from app.ai import settings as ai_settings

    async def provider_for(*a, **k):
        return provider
    monkeypatch.setattr(ai_settings, "provider_for", provider_for)


def test_copilot_explains_with_the_numbers_only(flag_on, journal, monkeypatch):
    token_a, _, _ = journal
    rules = client.get("/api/journal/review?week=2026-W40&history=0&narrate=true", headers=_h(token_a)).json()
    assert rules["explanation"]["source"] == "rules" and "AI not used" in rules["explanation"]["note"]

    grounded = _FakeProvider("This week closed at a net P&L of -₹20.00 after ₹15.00 in charges, and the discipline score was 94 out of 100.")
    _with_provider(monkeypatch, grounded)
    out = client.get("/api/journal/review?week=2026-W40&history=0&narrate=true", headers=_h(token_a)).json()
    assert out["explanation"]["source"] == "ai" and "94 out of 100" in out["explanation"]["text"]
    assert out["totals"]["net_pnl"] == -20.0                 # the numbers never come from the model

    advice = _FakeProvider("I recommend you buy more NIFTY next week to win it back.")
    _with_provider(monkeypatch, advice)
    out = client.get("/api/journal/review?week=2026-W40&history=0&narrate=true", headers=_h(token_a)).json()
    assert out["explanation"]["source"] == "rules" and "not used" in out["explanation"]["note"] and advice.calls == 2
    invented = _FakeProvider("Your win rate was 80% this week.")
    _with_provider(monkeypatch, invented)
    out = client.get("/api/journal/review?week=2026-W40&history=0&narrate=true", headers=_h(token_a)).json()
    assert out["explanation"]["source"] == "rules"


def test_weekly_review_service_is_deterministic(flag_on, journal):
    token_a, _, ids = journal

    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, ids[0])
            user = await session.get(User, trade.user_id)
            first = await service.weekly_review(session, user, "2026-W40", history_weeks=1)
            second = await service.weekly_review(session, user, "2026-W40", history_weeks=1)
            return first, second
    first, second = _run(go())
    assert first == second


# --- review fixes: a structure is one trade; a narrated review never advises -----------------------------------------

def _leg(id_, pnl, *, group="ic1", meta=None, charges=40.0, **kw):
    at = ist(10, 6, 10)
    base = dict(id=id_, tenant_id=1, user_id=1, mode="PAPER", strategy_id="iron_condor", symbol=f"NIFTY {24000 + id_ * 100} CE",
                direction="SHORT", entry_time=at, entry_price=50.0, quantity=75, stop_loss=0.0, initial_stop_loss=None,
                exit_time=at + timedelta(minutes=90), exit_price=40.0, exit_reason="Structure target", pnl=pnl, charges=charges,
                leg_group_id=group, group_meta=json.dumps(meta if meta is not None else {"risk_per_unit": 40.0, "max_loss": 40.0, "quantity": 75}))
    base.update(kw)
    return TradeRecord(**base)


def test_a_multi_leg_structure_is_one_trade_in_the_review():
    legs = [_leg(1, 600.0), _leg(2, 500.0), _leg(3, -200.0), _leg(4, -150.0)]
    units = service.review_trades(legs)
    assert len(units) == 1
    u = units[0]
    assert u.pnl == 750.0 and u.charges == 160.0 and u.risk == 3000.0 and u.r == 0.25 and u.rows == (1, 2, 3, 4)
    out = rv.build_review("2026-W41", units, [(3, "overtrading"), (4, "overtrading")], {"score": 90, "components": []}, [], S)
    t = out["totals"]
    assert (t["trades"], t["legs"], t["closed"], t["wins"], t["losses"]) == (1, 4, 1, 1, 0)
    assert t["net_pnl"] == 750.0 and t["avg_r"] == 0.25 and t["win_rate"] == 100.0 and t["largest_loss"] is None
    assert out["behaviours"] == [{"tag": "overtrading", "label": "overtrading", "trades": 1, "closed": 1, "net_pnl": 750.0, "avg_r": 0.25}]
    assert out["lines"][0] == "2026-W41: 1 trade (4 order legs), 1 closed and 0 still open."

    # one leg still open: the decision is open; a single trade next to it stays its own decision
    still_open = [_leg(1, 600.0), _leg(2, None, exit_time=None, exit_price=None, exit_reason=None)]
    single = TradeRecord(id=9, tenant_id=1, user_id=1, mode="PAPER", strategy_id="s", symbol="NIFTY", direction="LONG", entry_time=ist(10, 6, 11),
                         entry_price=100.0, quantity=10, stop_loss=98.0, exit_time=ist(10, 6, 12), exit_price=101.0, pnl=10.0, charges=0.0)
    mixed = rv.build_review("2026-W41", service.review_trades(still_open + [single]), [], {"score": None, "components": []}, [], S)["totals"]
    assert (mixed["trades"], mixed["legs"], mixed["closed"], mixed["open"], mixed["net_pnl"], mixed["avg_r"]) == (2, 3, 1, 1, 10.0, 0.5)
    # no group meta: no R for that decision rather than a leg's meaningless stop
    assert service.review_trades([_leg(1, 100.0, meta={}), _leg(2, -50.0, meta={})])[0].risk == 0.0


_ADVICE = ["You should stop revenge trading next week.", "Next week, buy only on pullbacks.", "Consider skipping trades after a loss."]


def test_advice_check_is_strict_and_the_rule_lines_pass_it():
    for sentence in _ADVICE + ["Stop revenge trading.", "Try to wait for the plan price.", "It would be better to size down.",
                               "Avoid trading the open.", "Tomorrow the setup may work again."]:
        assert rv.advice_in(sentence), sentence
    trades = [_rt(i, f"setup{i % 3}", p) for i, p in enumerate([50.0, -10.0, -40.0, 0.0, 12.5, None], start=1)]
    tags = [(i, tag) for i, tag in enumerate(rv.TAG_LABELS, start=1)]
    score = {"score": 61, "components": [{"name": n, "clean_pct": 50.0, "max_points": 20.0} for n in rv.COMPONENT_LABELS]}
    out = rv.build_review("2026-W40", trades, tags, score, [{"week": "2026-W39", "score": 55}], S)
    for line in out["lines"] + ["Stop discipline was the lowest component at 75%.", "Plan adherence was 80% of decisions."]:
        assert rv.advice_in(line) == [], line


@pytest.mark.parametrize("sentence", _ADVICE)
def test_narrated_advice_falls_back_to_the_rule_lines(flag_on, journal, monkeypatch, sentence):
    token_a, _, _ = journal
    provider = _FakeProvider(f"This week closed at a net P&L of -₹20.00 after ₹15.00 in charges. {sentence}")
    _with_provider(monkeypatch, provider)
    out = client.get("/api/journal/review?week=2026-W40&history=0&narrate=true", headers=_h(token_a)).json()
    assert out["explanation"]["source"] == "rules", out["explanation"]
    assert "not used" in out["explanation"]["note"] and "advice" in out["explanation"]["note"]
    assert out["explanation"]["text"] == "\n".join(out["lines"]) and sentence not in out["explanation"]["text"]


def test_a_framed_narration_is_not_shown(flag_on, journal, monkeypatch):
    from app.ai import copilot
    token_a, _, _ = journal

    async def framed(*a, **k):
        return "This week closed at a net P&L of -₹20.00.", "framed"
    monkeypatch.setattr(copilot, "narrate", framed)
    _with_provider(monkeypatch, _FakeProvider("unused"))
    out = client.get("/api/journal/review?week=2026-W40&history=0&narrate=true", headers=_h(token_a)).json()
    assert out["explanation"]["source"] == "rules" and "specific trade call" in out["explanation"]["note"]
