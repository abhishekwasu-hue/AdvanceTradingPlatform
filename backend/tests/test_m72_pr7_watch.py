"""M72 PR-7 (CP-7, F13 / F14): the Copilot watch. Opt-in per type with the user's OWN number (Not set = cannot be on);
cards about his own rows only, addressed to him alone, off the organisation's shared channels and phones (MA-7); no
proposal, no order; dedupe without a table; flag OFF = nothing."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select, update

from app.ai import watch
from app.alerts import push_kinds
from app.core.enums import NotificationType
from app.db.models import AiActionRecord, AlertDeliveryRecord, NotificationRecord, TradeRecord
from app.rhythm import words
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_ui2_4a1_prefs import _invite, _who

NOW = datetime.now(UTC)
ON = {"near_levels": True, "near_levels_room_left_pct": 20.0}


def _t(**kw):
    base = dict(id=7, symbol="RELIANCE", direction="LONG", entry_price=100.0, stop_loss=90.0, target1=None, mark_price=95.0)
    return SimpleNamespace(**{**base, **kw})


def _clean(cards):
    for c in cards:
        assert words.clean(c.title) and words.clean(c.message), c


def test_near_his_own_levels_uses_his_number_only():
    long_near = _t(mark_price=91.5)                                  # 15 % of the 10-point room left
    assert [c.key for c in watch.near_levels_cards([long_near], 20.0)] == ["near_levels:7:invalidation"]
    assert watch.near_levels_cards([long_near], 10.0) == []
    short = _t(id=8, direction="SHORT", entry_price=100.0, stop_loss=110.0, target1=80.0, mark_price=82.0)
    cards = watch.near_levels_cards([short], 10.0)                    # 2 of 20 points left to his profit level
    assert [c.key for c in cards] == ["near_levels:8:profit"] and "10 % of the room" in cards[0].message
    past = watch.near_levels_cards([_t(mark_price=89.0)], 5.0)
    assert "at or past your own invalidation level" in past[0].message
    assert watch.near_levels_cards([_t(mark_price=None)], 100.0) == []
    _clean(cards + past)
    assert cards[0].ask_kind == "position" and cards[0].ask_id == 8 and cards[0].holds


def test_short_strike_distance():
    h = SimpleNamespace(trade_id=3, underlying="NIFTY", right="CE", strike=25000.0, side="SELL", label="NIFTY 25000 CE")
    long_leg = SimpleNamespace(**{**h.__dict__, "trade_id": 4, "side": "BUY"})
    cards = watch.strike_distance_cards([h, long_leg], {"NIFTY": 24800.0}, 1.0)
    assert [c.key for c in cards] == ["strike_distance:3"] and "200 points (0.8 %)" in cards[0].message
    assert watch.strike_distance_cards([h], {"NIFTY": 24000.0}, 1.0) == []
    assert "past the strike by 100 points" in watch.strike_distance_cards([h], {"NIFTY": 25100.0}, 1.0)[0].message
    assert watch.strike_distance_cards([h], {}, 50.0) == []                     # no price: not judged, never guessed
    _clean(cards)


def _slot(at, spot, iv, oi):
    row = SimpleNamespace(strike=25000.0, call_iv=iv, put_iv=None, call_oi=oi, put_oi=None)
    return SimpleNamespace(slot_start=at, captured_at=at, chain=SimpleNamespace(expiry="2031-06-26", underlying_ltp=spot, rows=[row]))


def test_greeks_and_oi_between_the_two_latest_readings():
    h = SimpleNamespace(trade_id=5, underlying="NIFTY", right="CE", strike=25000.0, side="SELL", label="NIFTY 25000 CE",
                        expiry=date(2031, 6, 26))
    at = datetime(2031, 6, 11, 5, 0, tzinfo=UTC)
    slots = {"NIFTY": [_slot(at, 24800.0, 14.0, 1000.0), _slot(at + timedelta(minutes=5), 24900.0, 17.5, 1300.0)]}
    choice = {"greeks_jump": True, "iv_jump_points": 3.0, "delta_jump": 0.01, "oi_shift": True, "oi_shift_pct": 25.0}
    no_rate = watch.chain_cards([h], slots, choice, None)
    assert [c.kind for c in no_rate] == ["greeks_jump", "oi_shift"]
    assert "IV 14 -> 17.5 (3.5 IV points)" in no_rate[0].message and "delta" not in no_rate[0].message   # no rate: no delta
    assert "1,000 -> 1,300 (30 %)" in no_rate[1].message
    with_rate = watch.chain_cards([h], slots, choice, 6.5)
    assert "delta" in with_rate[0].message
    assert watch.chain_cards([h], slots, {**choice, "iv_jump_points": 5.0, "delta_jump": None, "oi_shift_pct": 40.0}, None) == []
    assert watch.chain_cards([h], {"NIFTY": slots["NIFTY"][:1]}, choice, 6.5) == []          # one reading: nothing to compare
    other_expiry = SimpleNamespace(**{**h.__dict__, "expiry": date(2031, 7, 31)})
    assert watch.chain_cards([other_expiry], slots, choice, 6.5) == []
    _clean(with_rate)
    assert not with_rate[0].holds and with_rate[0].key.startswith("greeks_jump:5:")


def test_a_setup_on_his_watchlist_is_numberless_and_new_only():
    since = datetime(2031, 6, 11, 4, 0, tzinfo=UTC)
    items = [{"id": "history:41", "at": "2031-06-11T05:00:00+00:00", "symbol": "TCS", "direction": "LONG", "source": {"kind": "strategy"},
              "setup": "Pullback 20", "reasons": ["Price at a buyer area 3510"]},
             {"id": "history:42", "at": "2031-06-11T03:00:00+00:00", "symbol": "TCS", "source": {"kind": "strategy"}},
             {"id": "history:43", "at": "2031-06-11T05:00:00+00:00", "symbol": "INFY", "source": {"kind": "scan"}}]
    cards = watch.watchlist_cards(items, ["tcs"], since)
    assert [c.key for c in cards] == ["watchlist_signal:history:41"]
    assert words.numberless(cards[0].message, ["TCS"]) and cards[0].ask_kind == "signal" and cards[0].ask_id == 41


def test_phones_never_get_a_watch_card():
    n = SimpleNamespace(event_type=NotificationType.COPILOT_WATCH.value, severity="INFO", ai_action_id=None)
    assert push_kinds.phone_kind(n) is None


def test_ask_of_keeps_only_the_symbol_and_his_object():
    assert watch.ask_of('{"ask": {"symbol": "TCS", "object": {"kind": "signal", "id": 4}, "pnl": 9}}') == {
        "symbol": "TCS", "object": {"kind": "signal", "id": 4}}
    assert watch.ask_of('{"ask": {"object": {"kind": "order", "id": 4}}}') is None
    assert watch.ask_of("not json") is None


def _open_trade(who, symbol, mark):
    async def go():
        async with _session_factory() as session:
            row = TradeRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", symbol=symbol, strategy_id="manual",
                              direction="LONG", entry_time=NOW - timedelta(minutes=30), entry_price=100.0, quantity=5, stop_loss=90.0,
                              mark_price=mark)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _mark(trade_id, mark):
    async def go():
        async with _session_factory() as session:
            await session.execute(update(TradeRecord).where(TradeRecord.id == trade_id).values(mark_price=mark))
            await session.commit()
    _run(go())


def _count(model, *where):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(model).where(*where))
    return _run(go())


def _due():
    async def go():
        async with _session_factory() as session:
            return await watch.run_due(session, datetime.now(UTC))
    return _run(go())


def test_settings_route_flags_and_his_own_number():
    who = _who("pr7-settings")
    _run(_flag("ui_v2", True))
    _run(_flag("copilot_watch", False))
    assert client.get("/api/copilot-watch/settings", headers=who["headers"]).status_code == 404
    _run(_flag("copilot_watch", True, [who["tenant_id"]]))
    try:
        got = client.get("/api/copilot-watch/settings", headers=who["headers"]).json()
        assert not any(got["prefs"][k] for k in watch.KINDS) and got["prefs"]["near_levels_room_left_pct"] is None
        assert got["risk_free_rate_set"] is False and got["telegram"]["reaches_you"] is False
        bad = client.put("/api/copilot-watch/settings", headers=who["headers"], json={"near_levels": True})
        assert bad.status_code == 422                                    # on without his own number: refused
        ok = client.put("/api/copilot-watch/settings", headers=who["headers"], json=ON)
        assert ok.status_code == 200 and ok.json()["prefs"]["near_levels"] is True
    finally:
        _run(_flag("copilot_watch", False))


def test_the_worker_raises_his_card_once_for_him_alone():
    owner = _who("pr7-run")
    member = _invite(owner, "USER")
    _run(_flag("ui_v2", True))
    _run(_flag("copilot_watch", True, [owner["tenant_id"]]))
    try:
        trade_id = _open_trade(owner, "PR7WATCH", 91.0)
        _open_trade(member, "PR7OTHER", 91.0)                            # the member opted in to nothing: no card
        assert client.put("/api/copilot-watch/settings", headers=owner["headers"], json=ON).status_code == 200
        mine = (NotificationRecord.tenant_id == owner["tenant_id"], NotificationRecord.event_type == NotificationType.COPILOT_WATCH.value)
        actions_before = _count(AiActionRecord, AiActionRecord.tenant_id == owner["tenant_id"])
        assert _due() >= 1
        assert _count(NotificationRecord, *mine) == 1
        assert _count(NotificationRecord, *mine, NotificationRecord.user_id == owner["user_id"], NotificationRecord.related_trade_id == trade_id) == 1
        assert _count(AiActionRecord, AiActionRecord.tenant_id == owner["tenant_id"]) == actions_before      # no proposal row
        assert _count(AlertDeliveryRecord, AlertDeliveryRecord.tenant_id == owner["tenant_id"]) == 0          # no shared channel
        _due()
        assert _count(NotificationRecord, *mine) == 1                   # still holds: not sent again
        _mark(trade_id, 99.0)
        _due()                                                          # cleared: re-armed
        _mark(trade_id, 91.0)
        _due()
        assert _count(NotificationRecord, *mine) == 2                   # holds again: one new card
        feed = client.get("/api/notifications", headers=owner["headers"]).json()
        card = next(n for n in feed if n["event_type"] == "COPILOT_WATCH")
        assert card["ask"] == {"symbol": "PR7WATCH", "object": {"kind": "position", "id": trade_id}}
        assert words.clean(card["title"]) and words.clean(card["message"])
        other = client.get("/api/notifications", headers=member["headers"]).json()
        assert not [n for n in other if n["event_type"] == "COPILOT_WATCH"]               # never another member's
        _run(_flag("copilot_watch", False))
        _mark(trade_id, 99.0)
        _due()
        _mark(trade_id, 91.0)
        _due()
        assert _count(NotificationRecord, *mine) == 2                   # flag off: nothing evaluated or sent
    finally:
        _run(_flag("copilot_watch", False))
        _mark(trade_id, 100.0)
