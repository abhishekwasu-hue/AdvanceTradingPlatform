"""Z6 / P1-e (ADR-0026): journal auto-capture, behaviour-tag detectors, the weekly discipline score, settings,
the `discipline_engine` flag and per-user isolation.

The detector tests run on fixture journals built in code (no database, a fixed clock); the API tests seed trades for
two users in one tenant and one user in another, and check that nobody sees anyone else's journal.
"""
import asyncio
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.enums import SignalDirection
from app.core.models import Trade
from app.db.models import JournalEntryRecord, JournalTagRecord, MarketEventRecord, TradeRecord, User
from app.journal import capture, detectors, service
from app.journal import settings as js
from app.journal.detectors import JournalTrade, MarketEvent, StopChange, detect
from app.journal.score import TagState, weekly_score
from app.market_data.calendar import IST
from app.platform import controls
from tests.test_auth_api import _session_factory, client
from tests.test_trading_api import _register_user_and_get_id

BACKEND = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
S = js.DisciplineSettings()


def ist(day: int, hh: int, mm: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hh, mm, tzinfo=IST).astimezone(timezone.utc)


def jt(id_, *, symbol="NIFTY", direction="LONG", entry=(6, 10, 0), price=100.0, qty=10.0, stop=98.0, target=104.0, plan=None,
       exit_at=None, exit_price=None, reason=None, pnl=None, final_stop=None, changes=(), group=None, group_stop=False, underlying=None):
    return JournalTrade(id=id_, symbol=symbol, underlying=underlying or symbol, direction=direction, entry_time=ist(*entry), entry_price=price,
                        quantity=qty, plan_entry=plan, initial_stop=stop, final_stop=stop if final_stop is None else final_stop, target1=target,
                        exit_time=ist(*exit_at) if exit_at else None, exit_price=exit_price, exit_reason=reason, pnl=pnl,
                        stop_changes=tuple(changes), group_id=group, has_group_stop=group_stop)


def tags_of(found, trade_id):
    return sorted(d.tag for d in found if d.trade_id == trade_id)


def only(tag, found):
    """One detector's findings (fixture journals often show several behaviours at once)."""
    return [d for d in found if d.tag == tag]


# --- detectors on fixture journals ------------------------------------------------------------------------------------

def test_revenge_trade_needs_a_recent_loss_and_a_larger_size():
    loser = jt(1, entry=(6, 9, 30), exit_at=(6, 10, 0), exit_price=98.0, reason="Stop Loss", pnl=-20.0)
    bigger_soon = jt(2, entry=(6, 10, 10), qty=20)                       # risk 40 > 20, 10 min after the loss
    found = only("revenge_trade", detect([loser, bigger_soon], S, now=NOW))
    assert tags_of(found, 2) == ["revenge_trade"]
    ev = next(d for d in found if d.tag == "revenge_trade").evidence
    assert ev["after_trade_id"] == 1 and ev["minutes_after_loss"] == 10.0 and ev["size_ratio"] == 2.0 and ev["size_basis"] == "risk"

    same_size = jt(3, entry=(6, 10, 10), qty=10)
    later = jt(4, entry=(6, 10, 40), qty=20)                            # 40 min after: outside the 15-minute window
    found = only("revenge_trade", detect([loser, same_size, later], S, now=NOW))
    assert "revenge_trade" not in tags_of(found, 3) + tags_of(found, 4)
    wider = js.validate({"revenge_window_minutes": 60})
    assert tags_of(only("revenge_trade", detect([loser, later], wider, now=NOW)), 4) == ["revenge_trade"]
    after_win = jt(5, entry=(6, 9, 30), exit_at=(6, 10, 0), exit_price=104.0, reason="Target 1", pnl=40.0)
    assert only("revenge_trade", detect([after_win, bigger_soon], S, now=NOW)) == []


def test_fomo_entry_is_a_chase_beyond_the_plan_entry():
    chased = jt(1, plan=100.0, price=100.6, stop=98.0)                   # 0.6 / 2.0 = 0.3R > 0.25R
    near = jt(2, plan=100.0, price=100.4, stop=98.0)                     # 0.2R
    better = jt(3, plan=100.0, price=99.0, stop=98.0)                    # filled better than the plan
    short = jt(4, direction="SHORT", plan=100.0, price=99.2, stop=102.0, target=96.0)   # sold 0.8 lower: 0.4R
    no_plan = jt(5, plan=None, price=110.0, stop=98.0)
    found = only("fomo_entry", detect([chased, near, better, short, no_plan], S, now=NOW))
    assert tags_of(found, 1) == ["fomo_entry"] and tags_of(found, 4) == ["fomo_entry"]
    assert tags_of(found, 2) == tags_of(found, 3) == tags_of(found, 5) == []
    assert next(d for d in found if d.trade_id == 1).evidence["chase_r"] == 0.3
    assert tags_of(only("fomo_entry", detect([near], js.validate({"fomo_chase_r": 0.1}), now=NOW)), 2) == ["fomo_entry"]


def test_averaging_a_loser_adds_to_an_open_position_under_water():
    first = jt(1, entry=(6, 10, 0), price=100.0)
    add_lower = jt(2, entry=(6, 10, 30), price=97.0, stop=95.0, target=104.0)
    add_higher = jt(3, entry=(6, 10, 40), price=103.0, stop=101.0, target=108.0)
    other_symbol = jt(4, symbol="BANKNIFTY", entry=(6, 10, 50), price=90.0, stop=88.0, target=95.0)
    found = only("averaging_loser", detect([first, add_lower, add_higher, other_symbol], S, now=NOW))
    assert tags_of(found, 2) == ["averaging_loser"]
    assert next(d for d in found if d.trade_id == 2).evidence["added_to_trade_id"] == 1
    assert "averaging_loser" not in tags_of(found, 3) + tags_of(found, 4)
    closed_first = jt(1, entry=(6, 10, 0), exit_at=(6, 10, 20), exit_price=98.0, reason="Stop Loss", pnl=-20.0)
    assert "averaging_loser" not in tags_of(only("averaging_loser", detect([closed_first, add_lower], S, now=NOW)), 2)
    # legs of one structure are one decision, never an "add" to each other
    leg_a = jt(6, group="g1", price=50.0, stop=0.0, group_stop=True)
    leg_b = jt(7, group="g1", entry=(6, 10, 0), price=40.0, stop=0.0, group_stop=True)
    assert only("averaging_loser", detect([leg_a, leg_b], S, now=NOW)) == []


def test_moved_stop_against_position_from_history_or_the_trade_row():
    widened = jt(1, stop=98.0, final_stop=97.0, changes=[StopChange(ist(6, 10, 5), 98.0, 97.0, "hand")])
    tightened = jt(2, stop=98.0, final_stop=99.0, changes=[StopChange(ist(6, 10, 5), 98.0, 99.0, "trailing")])
    row_only = jt(3, direction="SHORT", stop=102.0, final_stop=103.0, target=96.0)
    found = only("moved_stop_against", detect([widened, tightened, row_only], S, now=NOW))
    assert tags_of(found, 1) == ["moved_stop_against"] and tags_of(found, 3) == ["moved_stop_against"]
    assert tags_of(found, 2) == []
    assert next(d for d in found if d.trade_id == 1).evidence["moves"][0]["source"] == "hand"


def test_no_stop_missing_zero_or_wrong_side():
    missing = jt(1, stop=None)
    zero = jt(2, stop=0.0)
    wrong_side = jt(3, stop=101.0)                                       # a LONG with the stop above the entry
    ok = jt(4)
    structure = jt(5, stop=0.0, group="g", group_stop=True)              # judged by its group stop
    found = only("no_stop", detect([missing, zero, wrong_side, ok, structure], S, now=NOW))
    assert [d.trade_id for d in found if d.tag == "no_stop"] == [1, 2, 3]


def test_overtrading_against_a_fixed_norm_and_the_own_median():
    day = [jt(i, entry=(6, 10, i)) for i in range(1, 6)]               # five trades on one day
    fixed = js.validate({"overtrading_daily_norm": 3})
    found = detect(day, fixed, now=NOW)
    assert [d.trade_id for d in found if d.tag == "overtrading"] == [4, 5]
    assert next(d for d in found if d.trade_id == 4).evidence["trade_number"] == 4
    # own median: three earlier days with 2 trades each -> ceil(2 x 1.5) = 3, never under the minimum 3
    history = [jt(100 + d * 10 + k, entry=(d, 11, k)) for d in (1, 2, 5) for k in range(2)]
    found = detect(history + day, S, target_ids=[t.id for t in day], now=NOW)
    assert [d.trade_id for d in found if d.tag == "overtrading"] == [4, 5]
    assert "own median 2" in next(d for d in found if d.trade_id == 4).evidence["norm_basis"]
    # a four-leg structure is one decision
    legs = [jt(200 + k, entry=(7, 10, 0), group="ic", stop=0.0, group_stop=True) for k in range(4)]
    assert not [d for d in detect(legs, fixed, now=NOW) if d.tag == "overtrading"]


def test_early_exit_is_a_hand_exit_between_stop_and_target():
    by_hand = jt(1, exit_at=(6, 10, 30), exit_price=101.0, reason="Closed by hand", pnl=10.0)
    stopped = jt(2, exit_at=(6, 10, 30), exit_price=98.0, reason="Stop Loss", pnl=-20.0)
    rule_exit = jt(3, exit_at=(6, 10, 30), exit_price=101.0, reason="Time exit (30 min)", pnl=10.0)
    past_target = jt(4, exit_at=(6, 10, 30), exit_price=105.0, reason="Closed by hand", pnl=50.0)
    no_target = jt(5, target=None, exit_at=(6, 10, 30), exit_price=101.0, reason="Closed by hand", pnl=10.0)
    found = only("early_exit", detect([by_hand, stopped, rule_exit, past_target, no_target], S, now=NOW))
    assert [d.trade_id for d in found if d.tag == "early_exit"] == [1]
    assert next(d for d in found if d.tag == "early_exit").evidence["share_of_target_move"] == 0.25
    assert detectors.is_systematic_exit("Intraday square-off") and detectors.is_systematic_exit("Emergency Exit")
    assert not detectors.is_systematic_exit("Closed by hand")


def test_early_exit_counts_approved_ai_exits_and_exit_now_as_the_traders_decision():
    # The reason close_position writes for an approved AI EXIT_POSITION proposal (app/ai/monitor.execute); the rule
    # name may itself mention a stop - the trader pressed Approve, so it is still the trader's exit.
    ai_exit = jt(1, exit_at=(6, 10, 30), exit_price=101.0, reason="AI action #12 (stop_too_wide) approved", pnl=10.0)
    exit_now = jt(2, exit_at=(6, 10, 30), exit_price=100.5, reason="Exit now", pnl=5.0)
    rule = jt(3, exit_at=(6, 10, 30), exit_price=101.0, reason="Trailing stop", pnl=10.0)
    found = only("early_exit", detect([ai_exit, exit_now, rule], S, now=NOW))
    assert sorted(d.trade_id for d in found) == [1, 2]
    assert detectors.is_trader_exit("AI action #12 (stop_too_wide) approved") and not detectors.is_systematic_exit("Exit now (manual)")
    assert detectors.is_systematic_exit("Spread target (value 10.00 <= 12)") and not detectors.is_trader_exit("Spread target")


def test_held_through_event_respects_scope_and_kinds():
    budget = MarketEvent(1, "BUDGET", None, ist(7, 11, 0), "Union budget")
    results_other = MarketEvent(2, "RESULTS", "RELIANCE", ist(7, 9, 15))
    expiry = MarketEvent(3, "EXPIRY", "NIFTY", ist(7, 15, 30))
    overnight = jt(1, entry=(6, 14, 0), exit_at=(7, 12, 0), exit_price=101.0, reason="Target 1", pnl=10.0)
    closed_before = jt(2, entry=(6, 14, 0), exit_at=(7, 10, 0), exit_price=101.0, reason="Target 1", pnl=10.0)
    still_open = jt(3, entry=(6, 14, 0))
    events = [budget, results_other, expiry]
    found = only("held_through_event", detect([overnight, closed_before, still_open], S, events=events, now=ist(8, 10, 0), scope_match=service.scope_match))
    assert tags_of(found, 1) == ["held_through_event"] and tags_of(found, 3) == ["held_through_event"]
    assert tags_of(found, 2) == []
    assert [e["id"] for e in next(d for d in found if d.trade_id == 1).evidence["events"]] == [1]   # EXPIRY off by default
    with_expiry = js.validate({"event_kinds": ["EXPIRY"]})
    assert tags_of(only("held_through_event", detect([still_open], with_expiry, events=events, now=ist(8, 10, 0), scope_match=service.scope_match)), 3) == ["held_through_event"]
    assert service.scope_match("INDEX", "BANKNIFTY") and not service.scope_match("INDEX", "RELIANCE")


def test_a_disciplined_journal_has_no_tags():
    calm = [jt(1, entry=(5, 10, 0), plan=100.0, price=100.1, exit_at=(5, 11, 0), exit_price=104.0, reason="Target 1", pnl=39.0),
            jt(2, entry=(6, 10, 0), plan=100.0, price=100.0, exit_at=(6, 10, 30), exit_price=98.0, reason="Stop Loss", pnl=-20.0),
            jt(3, symbol="BANKNIFTY", entry=(6, 11, 0), price=50.0, stop=49.0, target=53.0, qty=10)]
    assert detect(calm, S, now=NOW) == []


def test_detection_is_deterministic_and_order_free():
    trades = [jt(1, entry=(6, 9, 30), exit_at=(6, 10, 0), exit_price=98.0, reason="Stop Loss", pnl=-20.0),
              jt(2, entry=(6, 10, 5), qty=30, plan=100.0, price=101.0, stop=None),
              jt(3, entry=(6, 10, 6), price=97.0), jt(4, entry=(6, 10, 7)), jt(5, entry=(6, 10, 8))]
    first = detect(trades, S, now=NOW)
    shuffled = list(trades)
    random.Random(7).shuffle(shuffled)
    assert detect(shuffled, S, now=NOW) == first
    assert [(d.trade_id, d.tag) for d in first] == sorted((d.trade_id, d.tag) for d in first)


# --- discipline score -------------------------------------------------------------------------------------------------

def test_score_components_and_known_value():
    units = {1: "t:1", 2: "t:2", 3: "t:3", 4: "t:4"}
    tags = [TagState(1, "no_stop", "SUGGESTED"), TagState(2, "revenge_trade", "CONFIRMED")]
    out = weekly_score(units, tags, S)
    comps = {c["name"]: c for c in out["components"]}
    assert comps["stop_discipline"]["clean_pct"] == 75.0 and comps["stop_discipline"]["points"] == 18.8
    assert comps["emotional_control"]["flagged"] == 1 and comps["plan_adherence"]["points"] == 20.0
    assert out["score"] == 88                                          # 18.75 + 18.75 + 20 + 15 + 15 = 87.5, half up
    assert sum(c["max_points"] for c in out["components"]) == 100.0


def test_score_is_deterministic_and_respects_decisions():
    units = {i: f"t:{i}" for i in range(1, 11)}
    tags = [TagState(i, tag, "SUGGESTED") for i, tag in [(1, "fomo_entry"), (2, "overtrading"), (3, "held_through_event"), (3, "early_exit")]]
    first = weekly_score(units, tags, S)
    for seed in range(5):
        shuffled = list(tags)
        random.Random(seed).shuffle(shuffled)
        assert weekly_score(dict(reversed(list(units.items()))), shuffled, S) == first
    dismissed = [TagState(t.trade_id, t.tag, "DISMISSED") for t in tags]
    assert weekly_score(units, dismissed, S)["score"] == 100
    strict = js.validate({"count_unconfirmed": False})
    assert weekly_score(units, tags, strict)["score"] == 100
    assert weekly_score({}, [], S)["score"] is None
    # one structure of four legs with one tagged leg is one flagged decision out of two
    legs = {1: "g:a", 2: "g:a", 3: "g:a", 4: "g:a", 5: "t:5"}
    out = weekly_score(legs, [TagState(2, "no_stop", "CONFIRMED")], S)
    assert out["decisions"] == 2 and {c["name"]: c for c in out["components"]}["stop_discipline"]["clean_pct"] == 50.0


def test_weights_change_the_score_by_their_proportions():
    units = {1: "t:1", 2: "t:2"}
    tags = [TagState(1, "overtrading", "CONFIRMED")]
    only_freq = js.validate({"weights": {"stop_discipline": 0, "plan_adherence": 0, "emotional_control": 0, "event_awareness": 0, "frequency_control": 5}})
    assert weekly_score(units, tags, only_freq)["score"] == 50


# --- settings ---------------------------------------------------------------------------------------------------------

def test_settings_validation_and_stored_values():
    with pytest.raises(js.SettingsError):
        js.validate({"nope": 1})
    with pytest.raises(js.SettingsError):
        js.validate({"revenge_window_minutes": 0})
    with pytest.raises(js.SettingsError):
        js.validate({"event_kinds": ["ECLIPSE"]})
    with pytest.raises(js.SettingsError):
        js.validate({"weights": {k: 0 for k in js.COMPONENT_TAGS}})
    with pytest.raises(js.SettingsError):
        js.validate({"fomo_chase_r": True})
    stored = js.from_stored(json.dumps({"fomo_chase_r": 99, "revenge_window_minutes": 30, "garbage": 1}))
    assert stored.fomo_chase_r == S.fomo_chase_r and stored.revenge_window_minutes == 30
    assert js.from_stored("not json") == S


def test_week_bounds_are_ist_weeks():
    label, start, end = service.week_bounds("2026-W41")
    assert label == "2026-W41" and start == datetime(2026, 10, 5, 0, 0, tzinfo=IST).astimezone(timezone.utc)
    assert end - start == timedelta(days=7)
    assert service.week_bounds(None, now=datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc))[0] == "2026-W42"   # Monday 01:30 IST
    assert service.previous_week("2026-W01") == "2025-W52"
    with pytest.raises(service.WeekError):
        service.week_bounds("2026-41")


# --- API: flag, capture, isolation ------------------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _flag(on: bool) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            flags["discipline_engine"] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())
    capture.reset_flag_cache()   # the hooks cache the flag for FLAG_TTL_SECONDS


@pytest.fixture
def flag_on():
    _flag(True)
    yield
    _flag(False)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_flag_is_off_by_default_and_routes_answer_503():
    assert "discipline_engine" in controls.FEATURE_FLAGS and "discipline_engine" in controls.DEFAULT_OFF_FLAGS
    token, _, _ = _run(_register_user_and_get_id("z6-off@example.com"))
    for path in ("/api/journal/week", "/api/journal/settings"):
        r = client.get(path, headers=_h(token))
        assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "discipline_engine"


def _seed(user_id, tenant_id, rows):
    async def go():
        ids = []
        async with _session_factory() as session:
            for kw in rows:
                rec = TradeRecord(tenant_id=tenant_id, user_id=user_id, mode="PAPER", strategy_id=kw.pop("strategy_id", "setupA"), **kw)
                session.add(rec)
                await session.flush()
                ids.append(rec.id)
            await session.commit()
        return ids
    return _run(go())


def _this_week_ist(day_offset: int, hh: int, mm: int = 0) -> datetime:
    label, start, _ = service.week_bounds(None)
    monday = start.astimezone(IST).date()
    return datetime.combine(monday + timedelta(days=day_offset), datetime.min.time(), tzinfo=IST).replace(hour=hh, minute=mm).astimezone(timezone.utc)


def test_week_journal_tags_score_and_isolation(flag_on):
    token_a, a_id, tenant = _run(_register_user_and_get_id("z6-a@example.com"))
    token_b, b_id, other_tenant = _run(_register_user_and_get_id("z6-b@example.com"))
    token_c, c_id, tenant_c = _run(_register_user_and_get_id("z6-c@example.com"))

    async def same_tenant():
        async with _session_factory() as session:
            b = await session.get(User, b_id)
            b.tenant_id = tenant
            await session.commit()
    _run(same_tenant())

    t0 = _this_week_ist(0, 9, 30)
    a_ids = _seed(a_id, tenant, [
        dict(symbol="NIFTY", direction="LONG", entry_time=t0, entry_price=100.0, quantity=10, stop_loss=98.0, initial_stop_loss=98.0, target1=104.0,
             exit_time=t0 + timedelta(minutes=30), exit_price=98.0, exit_reason="Stop Loss", pnl=-20.0, charges=5.0),
        dict(symbol="NIFTY", direction="LONG", entry_time=t0 + timedelta(minutes=35), entry_price=100.0, quantity=30, stop_loss=98.0,
             initial_stop_loss=98.0, target1=104.0, expected_price=99.0, exit_time=t0 + timedelta(minutes=60), exit_price=101.0,
             exit_reason="Closed by hand", pnl=30.0, charges=6.0),
    ])
    b_ids = _seed(b_id, tenant, [dict(symbol="BANKNIFTY", direction="SHORT", entry_time=t0, entry_price=200.0, quantity=5, stop_loss=0.0, target1=190.0)])
    _seed(c_id, tenant_c, [dict(symbol="NIFTY", direction="LONG", entry_time=t0, entry_price=100.0, quantity=1, stop_loss=0.0)])

    week = client.get("/api/journal/week", headers=_h(token_a))
    assert week.status_code == 200, week.text
    body = week.json()
    assert [e["trade_id"] for e in body["entries"]] == a_ids                  # only A's own trades, never B's (same tenant)
    second = body["entries"][1]
    assert sorted(t["tag"] for t in second["tags"]) == ["early_exit", "fomo_entry", "revenge_trade"]
    assert all(t["status"] == "SUGGESTED" and t["source"] == "AUTO" for t in second["tags"])
    assert second["plan"]["entry"] == 99.0 and second["plan"]["entry_source"] == "signal" and second["plan"]["stop"] == 98.0
    assert [f["leg"] for f in second["fills"]] == ["ENTRY", "EXIT"] and second["costs"] == {"charges": 6.0, "source": "ESTIMATED"}
    assert second["holding_minutes"] == 25.0 and second["r_multiple"] == 0.5 and second["source"] == "MANUAL"
    assert second["chart_entry"]["redraw"] == "market_lake_as_of" and second["chart_exit"]["at"]
    assert second["excursions"]["mfe"] == 1.0 and second["excursions"]["mae"] == 0.0
    score = body["score"]
    assert score["decisions"] == 2 and score["score"] is not None and 0 <= score["score"] <= 100

    # dismissing a tag sticks across re-runs and lifts the score
    fomo = next(t for t in second["tags"] if t["tag"] == "fomo_entry")
    assert client.patch(f"/api/journal/tags/{fomo['id']}", headers=_h(token_a), json={"status": "DISMISSED"}).status_code == 200
    again = client.get("/api/journal/week", headers=_h(token_a)).json()
    kept = next(t for t in again["entries"][1]["tags"] if t["tag"] == "fomo_entry")
    assert kept["status"] == "DISMISSED" and kept["decided_at"]
    assert again["score"]["score"] >= score["score"]

    # a trader's own tag, and a setting change removes SUGGESTED tags that no longer fire
    added = client.post(f"/api/journal/trades/{a_ids[0]}/tags", headers=_h(token_a), json={"tag": "overtrading", "note": "felt rushed"})
    assert added.status_code == 201 and added.json()["source"] == "USER" and added.json()["status"] == "CONFIRMED"
    assert client.post(f"/api/journal/trades/{a_ids[0]}/tags", headers=_h(token_a), json={"tag": "bad_luck"}).status_code == 422
    assert client.put("/api/journal/settings", headers=_h(token_a), json={"revenge_window_minutes": 1}).status_code == 200
    after = client.get("/api/journal/week", headers=_h(token_a)).json()
    assert "revenge_trade" not in [t["tag"] for t in after["entries"][1]["tags"]]
    assert "overtrading" in [t["tag"] for t in after["entries"][0]["tags"]]

    # isolation: B (same tenant) and C (other tenant) get 404 for A's trade and tag; B's own journal has only B's trade
    for token in (token_b, token_c):
        assert client.get(f"/api/journal/trades/{a_ids[0]}", headers=_h(token)).status_code == 404
        assert client.patch(f"/api/journal/tags/{fomo['id']}", headers=_h(token), json={"status": "CONFIRMED"}).status_code == 404
        assert client.post(f"/api/journal/trades/{a_ids[0]}/tags", headers=_h(token), json={"tag": "no_stop"}).status_code == 404
    b_week = client.get("/api/journal/week", headers=_h(token_b)).json()
    assert [e["trade_id"] for e in b_week["entries"]] == b_ids and b_week["entries"][0]["tags"][0]["tag"] == "no_stop"
    assert client.get("/api/journal/settings", headers=_h(token_b)).json()["settings"]["revenge_window_minutes"] == 15.0   # A's change is A's only

    detail = client.get(f"/api/journal/trades/{a_ids[1]}", headers=_h(token_a)).json()
    assert detail["trade_id"] == a_ids[1] and "early_exit" in [t["tag"] for t in detail["tags"]]


def test_settings_api_validates(flag_on):
    token, _, _ = _run(_register_user_and_get_id("z6-settings@example.com"))
    r = client.get("/api/journal/settings", headers=_h(token)).json()
    assert r["settings"] == r["defaults"] and "revenge_window_minutes" in r["ranges"]
    bad = client.put("/api/journal/settings", headers=_h(token), json={"fomo_chase_r": 50})
    assert bad.status_code == 422 and "fomo_chase_r" in bad.json()["detail"]
    ok = client.put("/api/journal/settings", headers=_h(token), json={"weights": {"event_awareness": 0}, "event_kinds": ["budget"]})
    assert ok.status_code == 200 and ok.json()["settings"]["weights"]["event_awareness"] == 0 and ok.json()["settings"]["event_kinds"] == ["BUDGET"]
    assert client.get("/api/journal/week?week=2026-W99", headers=_h(token)).status_code == 422


def test_events_calendar_feeds_held_through_event(flag_on):
    token, uid, tenant = _run(_register_user_and_get_id("z6-event@example.com"))
    t0 = _this_week_ist(0, 14, 0)
    ids = _seed(uid, tenant, [dict(symbol="RELIANCE", direction="LONG", entry_time=t0, entry_price=100.0, quantity=1, stop_loss=95.0, target1=110.0,
                                   exit_time=t0 + timedelta(days=1), exit_price=101.0, exit_reason="Target 1", pnl=1.0)])

    async def add_event():
        async with _session_factory() as session:
            session.add(MarketEventRecord(tenant_id=tenant, underlying="RELIANCE", event_date=(t0 + timedelta(days=1)).astimezone(IST).date(),
                                          start_time="10:00", kind="RESULTS", action="SIZE_CUT", description="Q2 results"))
            await session.commit()
    _run(add_event())
    body = client.get("/api/journal/week", headers=_h(token)).json()
    entry = next(e for e in body["entries"] if e["trade_id"] == ids[0])
    assert [t["tag"] for t in entry["tags"]] == ["held_through_event"]


# --- capture hooks ----------------------------------------------------------------------------------------------------

def _user(uid):
    async def go():
        async with _session_factory() as session:
            return await session.get(User, uid)
    return _run(go())


def test_capture_hooks_write_the_journal_only_with_the_flag_on():
    from app.trading.persistence import persist_trade
    _, uid, _ = _run(_register_user_and_get_id("z6-capture@example.com"))
    user = _user(uid)
    entry_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    def trade(price=100.0):
        return Trade(symbol="NIFTY", strategy_id="setupB", direction=SignalDirection.LONG, entry_time=entry_at, entry_price=price, quantity=10,
                     stop_loss=98.0, target1=104.0, expected_price=99.8)

    async def flag_off_part():
        async with _session_factory() as session:
            off = await persist_trade(session, user, trade())
            await capture.drain()
            assert await session.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == off.id)) is None

    async def flag_on_part():
        async with _session_factory() as session:
            on = await persist_trade(session, user, trade(), journal_source="PROPOSAL")
            assert on.id and on.symbol == "NIFTY"          # the caller's row is untouched and still usable
            await capture.on_marks(session, user.tenant_id, {on.id: 103.5})
            await capture.on_marks(session, user.tenant_id, {on.id: 97.5})
            await capture.on_stop_change(session, on, 98.0, 97.0, source="hand")
            from app.trading.position_monitor import close_position
            outcome = await close_position(session, on, 101.0, "Closed by hand")
            assert outcome.closed
        await capture.drain()            # the hooks only queue; the journal is written off the trade path
        async with _session_factory() as session:
            entry = await session.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == on.id))
            assert entry is not None and entry.source == "PROPOSAL" and entry.mode == "PAPER"
            assert float(entry.high_price) == 103.5 and float(entry.low_price) == 97.5
            assert [f["leg"] for f in json.loads(entry.fills_json)] == ["ENTRY", "EXIT"]
            assert json.loads(entry.stop_history_json)[0]["to"] == 97.0
            assert json.loads(entry.plan_json)["entry"] == 99.8
            assert json.loads(entry.chart_exit_json)["symbol"] == "NIFTY"

    _run(flag_off_part())
    _flag(True)
    try:
        _run(flag_on_part())
    finally:
        _flag(False)


def test_capture_never_raises_into_the_trade_path(monkeypatch):
    _, uid, _ = _run(_register_user_and_get_id("z6-boom@example.com"))
    user = _user(uid)
    _flag(True)
    try:
        def boom(*a, **k):
            raise RuntimeError("journal down")
        monkeypatch.setattr(capture, "apply_trade", boom)
        from app.trading.persistence import persist_trade

        async def go():
            async with _session_factory() as session:
                rec = await persist_trade(session, user, Trade(symbol="NIFTY", strategy_id="s", direction=SignalDirection.LONG,
                                                               entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=1, stop_loss=98.0))
                await capture.drain()   # the failing job ran (and was swallowed) without touching the trade
                return rec.id, rec.entry_price
        trade_id, price = _run(go())
        assert trade_id and price == 100.0
    finally:
        _flag(False)


def test_greeks_at_entry_for_an_option():
    rec = TradeRecord(id=1, tenant_id=1, user_id=1, mode="PAPER", symbol="NIFTY 24500 CE 29 OCT 26", strategy_id="s", direction="LONG",
                      entry_time=datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc), entry_price=150.0, quantity=75, stop_loss=100.0,
                      instrument_kind="OPTION", expiry=date(2026, 10, 29))
    g = capture.greeks_at_entry(rec, 24480.0)
    assert g["right"] == "CE" and g["strike"] == 24500.0 and 0 < g["iv"] < 2 and 0 < g["delta"] < 1
    assert capture.greeks_at_entry(rec, None) == {"unavailable": "no underlying price at entry"}
    rec.instrument_kind = "UNDERLYING"
    assert capture.greeks_at_entry(rec, 24480.0) == {"unavailable": "not an option"}


def test_tag_rows_default_to_auto_and_suggested():
    _, uid, tenant = _run(_register_user_and_get_id("z6-cascade@example.com"))

    async def go():
        async with _session_factory() as session:
            rec = TradeRecord(tenant_id=tenant, user_id=uid, mode="PAPER", symbol="X", strategy_id="s", direction="LONG",
                              entry_time=datetime.now(timezone.utc), entry_price=1.0, quantity=1, stop_loss=0.0)
            session.add(rec)
            await session.flush()
            session.add(JournalTagRecord(tenant_id=tenant, user_id=uid, trade_id=rec.id, tag="no_stop"))
            await session.commit()
            row = await session.scalar(select(JournalTagRecord).where(JournalTagRecord.trade_id == rec.id))
            assert row.source == "AUTO" and row.status == "SUGGESTED"
    _run(go())


# --- migration --------------------------------------------------------------------------------------------------------

def test_migration_chains_into_a_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    rev = script.get_revision("039888cda9b1")
    assert rev.down_revision == "e4c2a9b7d1f6"
    text = Path(rev.path).read_text()
    for table in ("journal_entries", "journal_tags", "journal_settings"):
        assert f"'{table}'" in text
