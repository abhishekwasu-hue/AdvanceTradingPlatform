"""P2-8 Journal v2 J2 + J3 (docs/plans/journal-v2-PLAN.md section 3): the 20-second check-in (optional fields, his own
words only, private: an admin cannot read it, never logged, in his export, erased with his account) and the daily /
weekly / monthly reviews (pure, the first-20 rule, every line through the advice guard, empty states). Every user,
symbol and number here is made up."""
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import JournalCheckinRecord, JournalReflectionRecord, User
from app.journal import checkin, reviews
from app.journal.entries import EntryError
from app.journal.review import advice_in
from app.journal.reviews import Gate, Unit
from app.journal.settings import DisciplineSettings, SettingsError, validate
from tests.test_auth_api import _session_factory, client
from tests.test_m62_journal_layer import _h, _run, _trade, flag_on  # noqa: F401  (pytest fixture)
from tests.test_trading_api import _register_user_and_get_id

BACKEND = Path(__file__).resolve().parents[1]
S = DisciplineSettings()
LESSON = "waited for my own candle close, will keep that"     # a made-up line


# --- J2: the check-in fields ------------------------------------------------------------------------------------------

def test_clean_accepts_only_his_own_choices_and_keeps_what_he_left_out():
    assert checkin.clean({}, S) == {}
    out = checkin.clean({"feeling_before": "fomo", "confidence": 4, "plan_followed": "partly", "mistake": "EARLY_EXIT",
                         "lesson": "  same entry,   later exit ", "setup_name": "Buyer area"}, S)
    assert out == {"feeling_before": "FOMO", "confidence": 4, "plan_followed": "PARTLY", "mistake": "early_exit",
                   "lesson": "same entry, later exit", "setup_name": "Buyer area"}
    assert checkin.clean({"feeling_after": None, "confidence": None}, S) == {"feeling_after": None, "confidence": None}   # null clears
    own = validate({"custom_tags": ["chased"], "feelings": ["Steady", "Rushed"]})
    assert checkin.clean({"mistake": "chased", "feeling_after": "rushed"}, own) == {"mistake": "chased", "feeling_after": "Rushed"}
    for bad in ({"feeling_before": "angry"}, {"confidence": 0}, {"confidence": 6}, {"confidence": True}, {"confidence": 2.5},
                {"plan_followed": "maybe"}, {"mistake": "bad luck"}, {"lesson": "x" * 301}, {"mood_score": 3}):
        with pytest.raises(EntryError):
            checkin.clean(bad, S)


def test_mistake_choices_are_the_eight_tags_other_and_his_own():
    choices = checkin.mistake_choices(validate({"custom_tags": ["chased", "early_exit"]}))
    assert choices[:8] == ["revenge_trade", "fomo_entry", "averaging_loser", "moved_stop_against", "no_stop", "overtrading",
                           "early_exit", "held_through_event"]
    assert choices[8:] == ["other", "chased"]


def test_settings_feelings_and_first_review_count():
    assert S.feelings == ["calm", "confident", "unsure", "anxious", "excited", "fearful", "FOMO"] and S.min_closed_for_review == 20
    assert validate({"min_closed_for_review": 30}).min_closed_for_review == 30
    for bad in ({"min_closed_for_review": 2}, {"feelings": ["x" * 21]}, {"feelings": "calm"}):
        with pytest.raises(SettingsError):
            validate(bad)


def test_waiting_moment_open_then_close_then_done():
    now = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    opened = now - timedelta(hours=1)
    assert checkin.waiting_moment(opened=opened, closed=None, row=None, now=now) == "open"
    answered = SimpleNamespace(feeling_before="calm", confidence=None, dismissed_open=False, plan_followed=None, feeling_after=None,
                               dismissed_close=False)
    assert checkin.waiting_moment(opened=opened, closed=None, row=answered, now=now) is None
    assert checkin.waiting_moment(opened=opened, closed=now, row=answered, now=now) == "close"
    put_off = SimpleNamespace(**{**answered.__dict__, "dismissed_close": True})
    assert checkin.waiting_moment(opened=opened, closed=now, row=put_off, now=now) is None
    assert checkin.waiting_moment(opened=now - timedelta(days=5), closed=None, row=None, now=now) is None   # older: not asked


# --- J2: through the routes, and privacy ------------------------------------------------------------------------------

def _teammate(tenant_id: int, email: str, role: str = "SUPER_ADMIN") -> str:
    """Another account moved into his organisation (a platform admin, or a plain member)."""
    token, uid, _ = _run(_register_user_and_get_id(email))

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, uid)
            user.tenant_id, user.role = tenant_id, role
            await session.commit()
    _run(go())
    return token


def test_checkin_route_saves_optional_fields_notes_and_dismiss(flag_on, caplog):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-checkin@example.com"))
    trade = _trade(tenant, uid, symbol="P28CHK", entry_time=datetime.now(UTC) - timedelta(minutes=20))
    empty = client.get(f"/api/journal/trades/{trade}/checkin", headers=_h(token)).json()
    assert empty["answered"] is False and empty["feelings"][-1] == "FOMO" and empty["setup_name_suggested"] == "setupM"
    waiting = client.get("/api/journal/checkins/waiting", headers=_h(token)).json()["waiting"]
    assert [(w["id"], w["moment"]) for w in waiting] == [(trade, "open")]
    with caplog.at_level(logging.INFO):          # the app's log level (the test SQLite driver's own DEBUG trace aside)
        r = client.put(f"/api/journal/trades/{trade}/checkin", headers=_h(token), json={"feeling_before": "calm", "confidence": 3})
        assert r.status_code == 200 and r.json()["confidence"] == 3 and r.json()["answered"] is True
        r = client.put(f"/api/journal/trades/{trade}/checkin", headers=_h(token), json={"lesson": LESSON, "plan_followed": "YES"})
        assert r.json()["feeling_before"] == "calm" and r.json()["lesson"] == LESSON         # earlier answers kept
        note = client.post(f"/api/journal/trades/{trade}/checkin/notes", headers=_h(token), json={"text": "unease after entry"})
        assert note.status_code == 201 and note.json()["notes"][0]["text"] == "unease after entry"
    leaked = [(rec.name, rec.getMessage()[:160]) for rec in caplog.records if LESSON in rec.getMessage() or "unease after entry" in rec.getMessage()]
    assert leaked == []                                                                  # never logged
    assert client.get("/api/journal/checkins/waiting", headers=_h(token)).json()["waiting"] == []
    assert client.put(f"/api/journal/trades/{trade}/checkin", headers=_h(token), json={"confidence": 9}).status_code == 422
    assert client.put(f"/api/journal/trades/{trade}/checkin", headers=_h(token), json={"score": 1}).status_code == 422
    other = _trade(tenant, uid, symbol="P28CHK2", entry_time=datetime.now(UTC) - timedelta(minutes=5))
    assert client.post(f"/api/journal/trades/{other}/checkin/dismiss", headers=_h(token), json={"moment": "open"}).status_code == 200
    assert all(w["id"] != other for w in client.get("/api/journal/checkins/waiting", headers=_h(token)).json()["waiting"])


def test_admin_and_teammates_cannot_read_his_checkin(flag_on):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-private@example.com"))
    trade = _trade(tenant, uid, symbol="P28PRIV")
    client.put(f"/api/journal/trades/{trade}/checkin", headers=_h(token), json={"feeling_before": "anxious", "lesson": LESSON})
    for role, email in (("SUPER_ADMIN", "p28-admin@example.com"), ("USER", "p28-mate@example.com")):
        other = _teammate(tenant, email, role)
        assert client.get(f"/api/journal/trades/{trade}/checkin", headers=_h(other)).status_code == 404
        assert client.put(f"/api/journal/trades/{trade}/checkin", headers=_h(other), json={"confidence": 1}).status_code == 404
        assert LESSON not in client.get("/api/journal/review/extras", headers=_h(other)).text


def test_checkins_are_not_in_admin_pages_or_analytics_exports_but_in_his_export_and_erased():
    import inspect

    from app.admin import console
    from app.exports import service as exports
    from app.me import account
    assert all("checkin" not in name.lower() and "reflection" not in name.lower() for name in exports.DATASETS)
    assert "JournalCheckinRecord" not in inspect.getsource(console) and "JournalReflectionRecord" not in inspect.getsource(console)
    assert {"journal_checkins", "journal_reflections"} <= {name for name, _ in account._DATASETS}
    from app.retention import service
    src = inspect.getsource(service._erase_journal)
    assert "JournalCheckinRecord" in src and "JournalReflectionRecord" in src


def test_erase_removes_checkins_and_answers(flag_on):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-erase@example.com"))
    trade = _trade(tenant, uid, symbol="P28ERASE")
    client.put(f"/api/journal/trades/{trade}/checkin", headers=_h(token), json={"feeling_after": "calm"})
    client.put("/api/journal/reflection", headers=_h(token), json={"period": "2026-W47", "answer": "keep waiting for the close"})

    async def erase():
        from app.retention.service import _erase_journal
        async with _session_factory() as session:
            await _erase_journal(session, await session.get(User, uid))
            await session.commit()
            return (await session.scalar(select(func.count()).select_from(JournalCheckinRecord).where(JournalCheckinRecord.user_id == uid)),
                    await session.scalar(select(func.count()).select_from(JournalReflectionRecord).where(JournalReflectionRecord.user_id == uid)))
    assert _run(erase()) == (0, 0)


# --- J3: the pure reviews ---------------------------------------------------------------------------------------------

def _u(i, pnl, **kw):
    return Unit(id=i, kind="trade", symbol=f"DEMO{i}", setup=kw.pop("setup", "Buyer area"), entry_time=None, pnl=pnl, entry_order=i, **kw)


def test_share_follows_the_first_20_rule():
    assert reviews.share(3, 5, 20) is None and reviews.share(15, 20, 20) == 75.0 and reviews.share(0, 0, 5) is None
    g = Gate(7, 20)
    assert g.ready is False and g.to_dict()["note"] == "Not enough trades yet (7 of 20)."
    assert Gate(20, 20).to_dict() == {"ready": True, "closed_total": 20, "needed": 20}


def test_done_well_is_one_fact_from_his_own_trades():
    assert reviews.done_well([]) is None
    assert reviews.done_well([_u(1, 100), _u(2, -50)]) == "Stop kept on all 2 trades."
    no_stop = dict(stop_set=False)
    assert reviews.done_well([_u(1, -50, plan_followed="YES", **no_stop)]) == "Plan followed on a losing trade (DEMO1)."
    assert reviews.done_well([_u(1, 50, plan_followed="YES", **no_stop), _u(2, 10, **no_stop)]) == "Plan followed on all 1 trade you answered."
    assert reviews.done_well([_u(1, -50, **no_stop), _u(2, 20, **no_stop)]) == "No trade in a hurry after a loss."
    assert reviews.done_well([_u(1, 50, **no_stop)]) == "Every exit by your own rules."
    assert reviews.done_well([_u(1, 50, tags=("early_exit", "no_stop"), **no_stop)]) is None


def test_daily_card_counts_and_empty_state():
    empty = reviews.daily_card("2026-11-20", [], Gate(0, 20))
    assert empty["lines"] == ["No trades today."] and empty["done_well"] is None and empty["gate"]["ready"] is False
    units = [_u(1, 120.0, feeling_before="calm", plan_followed="YES", confirmed=("early_exit",)),
             _u(2, -40.0, feeling_before="anxious", feeling_after="calm", plan_followed="NO"), _u(3, None, feeling_before="calm")]
    card = reviews.daily_card("2026-11-20", units, Gate(25, 20), ["calm", "anxious"])
    assert card["lines"] == ["3 trades, 2 closed.", "Result: +₹80.", "Plan followed: 1 of 2 answered."]   # 2 < 20: no share
    assert card["feelings"] == [{"feeling": "calm", "count": 3}, {"feeling": "anxious", "count": 1}]
    assert card["confirmed_tags"] == [{"tag": "early_exit", "label": "Early exit", "count": 1}] and card["plan"]["rate"] is None
    assert reviews.daily_card("2026-11-20", units, Gate(25, 20), ["calm"]) == reviews.daily_card("2026-11-20", units, Gate(25, 20), ["calm"])


def test_weekly_extras_feelings_confidence_mistake_and_small_samples():
    units = [_u(i, 100.0 if i % 2 else -60.0, feeling_before="calm" if i < 4 else "anxious", confidence=4 if i < 3 else 2,
                plan_followed="YES" if i < 5 else "NO", mistake="early_exit" if i in (5, 6) else None,
                mae_amount=float(i * 10), mfe_amount=float(i * 20)) for i in range(1, 7)]
    out = reviews.weekly_extras("2026-W47", units, Gate(6, 5), ["calm", "anxious"], answer="keep the stop")
    calm, anxious = out["feelings"]
    assert (calm["value"], calm["trades"], calm["wins"], calm["net_pnl"], calm["win_rate"], calm["small"]) == ("calm", 3, 2, 140.0, None, True)
    assert anxious["trades"] == 3 and anxious["net_pnl"] == -20.0
    assert [r["value"] for r in out["confidence"]] == [2, 4]
    assert out["mistake"] == {"value": "early_exit", "label": "Early exit", "count": 2}
    assert out["plan"]["rate"] == 66.7                                                   # 6 answered >= 5: a share is shown
    assert "Largest move against you while open: ₹60 on DEMO6." in out["lines"]
    assert [n["id"] for n in out["notable"]] == [1, 3, 5] and out["answer"] == "keep the stop"
    assert out["question"] == reviews.WEEK_QUESTION


def test_monthly_best_and_worst_setup_with_small_sample_label():
    units = [_u(1, 300.0, setup="Buyer area"), _u(2, -100.0, setup="Seller area"), _u(3, 50.0, setup="Buyer area")]
    m = reviews.monthly("2026-11", units, Gate(3, 20), [{"week": "2026-W45", "score": 80}])
    assert m["best_setup"]["setup"] == "Buyer area" and m["worst_setup"]["setup"] == "Seller area"
    assert "Best setup by your numbers: Buyer area (+₹350, 2 trades, small sample)." in m["lines"]
    assert m["gate"]["note"] == "Not enough trades yet (3 of 20)." and m["weeks"][0]["score"] == 80
    assert reviews.monthly("2026-11", [], Gate(0, 20), [])["lines"] == ["No trades this month."]


def test_every_generated_line_passes_the_advice_guard():
    units = [_u(i, (-1) ** i * 50.0, feeling_before="calm", plan_followed="YES", mistake="fomo_entry", mae_amount=5.0, mfe_amount=9.0,
                confirmed=("revenge_trade",)) for i in range(1, 30)]
    g = Gate(40, 20)
    texts = (reviews.daily_card("d", units, g)["lines"] + [reviews.daily_card("d", units, g)["done_well"]]
             + reviews.weekly_extras("w", units, g)["lines"] + reviews.monthly("m", units, g, [])["lines"])
    for line in filter(None, texts):
        assert advice_in(line) == [] and not re.search(r"\b(buy|sell|should|target)\b", line, re.I)


# --- J3: through the routes -------------------------------------------------------------------------------------------

def test_review_routes_answer_with_the_gate_and_store_the_reflection(flag_on):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-reviews@example.com"))
    day = client.get("/api/journal/daily?day=2026-11-20", headers=_h(token)).json()
    assert day["lines"] == ["No trades today."] and day["gate"]["note"] == "Not enough trades yet (0 of 20)."
    now = datetime.now(UTC)
    _trade(tenant, uid, symbol="P28REV", entry_time=now - timedelta(hours=3), exit_time=now - timedelta(hours=2), exit_price=104.0, pnl=40.0)
    week = client.get("/api/journal/review/extras", headers=_h(token)).json()
    assert week["gate"]["closed_total"] == 1 and week["plan"]["answered"] == 0 and week["answer"] is None
    r = client.put("/api/journal/reflection", headers=_h(token), json={"period": week["week"], "answer": "wait for the close"})
    assert r.json()["answer"] == "wait for the close"
    assert client.get("/api/journal/review/extras", headers=_h(token)).json()["answer"] == "wait for the close"
    month = client.get("/api/journal/monthly", headers=_h(token)).json()
    assert month["closed"] >= 0 and isinstance(month["weeks"], list) and month["question"] == reviews.MONTH_QUESTION
    assert client.get("/api/journal/monthly?month=2026-13", headers=_h(token)).status_code == 422
    assert client.put("/api/journal/reflection", headers=_h(token), json={"period": "soon", "answer": "x"}).status_code == 422


def test_review_routes_are_behind_the_flag():
    token, _, _ = _run(_register_user_and_get_id("p28-reviews-off@example.com"))
    for path in ("/api/journal/daily", "/api/journal/review/extras", "/api/journal/monthly", "/api/journal/checkins/waiting"):
        assert client.get(path, headers=_h(token)).status_code == 503


def test_migration_is_additive_rollback_safe_and_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    text = Path(script.get_revision("f4b6d8a0c2e5").path).read_text()
    assert "ROLLBACK_SAFE = True" in text and "'journal_checkins'" in text and "'journal_reflections'" in text
