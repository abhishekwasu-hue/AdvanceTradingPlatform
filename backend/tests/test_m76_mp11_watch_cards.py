"""M76 MP-11 (D-1): watch cards from the Copilot workspace - opt-in, his own numbers, information only.

- every type OFF and every number Not set until he saves one; a type on without his number is refused (422);
- the checks are pure: price inside his watched area, a commitment candle at it (once per candle), his short strike within
  his points, IV at his strike by his IV points (the Copilot watch's own check), his broker's margin in use at his %,
  his N minutes before his own square-off time (trading days; not saved = none);
- `since` of a watched area is the server's (the candle check never reads bars before he watched it);
- the worker raises a card once for him alone, with "Open here" (the workspace, in place); flag off = nothing;
- nothing on an order path reads any of it.

Made-up data only."""
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from app.charts import workspace_watch as ww
from app.core.enums import NotificationType
from app.db.models import AiActionRecord, AlertDeliveryRecord, NotificationRecord
from app.rhythm import words
from tests.tenancy_route_table import ROUTE_TABLE
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixture
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_m72_pr7_watch import _slot
from tests.test_ui2_4a1_prefs import _invite, _who

WATCH = "/api/charts/workspace/watch"
AREA = {"id": "a1", "exchange": "NSE", "symbol": "ZZWATCH", "tf": "1h", "side": "buyer", "low": 100.0, "high": 110.0}


def _clean(cards):
    for c in cards:
        assert words.clean(c.title) and words.clean(c.message), c


def test_price_inside_his_watched_area():
    cards = ww.area_entry_cards([AREA], {"ZZWATCH": 104.5})
    assert [c.key for c in cards] == ["area_entry:a1"] and cards[0].holds
    assert "ZZWATCH at 104.5 is inside your watched buyer area 100 - 110 (1h)" in cards[0].message
    assert ww.area_entry_cards([AREA], {"ZZWATCH": 110.0})                      # an edge counts
    assert ww.area_entry_cards([AREA], {"ZZWATCH": 111.0}) == []
    assert ww.area_entry_cards([AREA], {}) == []                                 # no price: no card, never a guess
    _clean(cards)


def test_a_commitment_candle_at_his_area_once_per_candle():
    assert ww.rejection_card(AREA, None) is None
    c = ww.rejection_card(AREA, {"bar_end": "2031-06-11T05:15:00+00:00", "form": "pin bar", "role": "demand"})
    assert c is not None and not c.holds and c.key == "area_rejection:a1:2031-06-11T05:15:00+00:00"
    assert "a commitment candle (pin bar) closed at your watched buyer area 100 - 110 (1h)" in c.message
    _clean([c])


def test_short_strike_within_his_points():
    h = SimpleNamespace(trade_id=3, underlying="NIFTY", right="PE", strike=24000.0, side="SELL", label="NIFTY 24000 PE")
    bought = SimpleNamespace(**{**h.__dict__, "trade_id": 4, "side": "BUY"})
    cards = ww.strike_points_cards([h, bought], {"NIFTY": 24080.0}, 100.0)
    assert [c.key for c in cards] == ["strike_points:3"] and "80 points from the strike 24,000 (your level: 100 points)" in cards[0].message
    assert ww.strike_points_cards([h], {"NIFTY": 24200.0}, 100.0) == []
    assert "past the strike by 20 points" in ww.strike_points_cards([h], {"NIFTY": 23980.0}, 100.0)[0].message
    assert ww.strike_points_cards([h], {}, 100.0) == []
    _clean(cards)


def test_iv_spike_is_the_copilot_watch_check_with_his_iv_points():
    h = SimpleNamespace(trade_id=5, underlying="NIFTY", right="CE", strike=25000.0, side="SELL", label="NIFTY 25000 CE",
                        expiry=date(2031, 6, 26))
    at = datetime(2031, 6, 11, 5, 0, tzinfo=UTC)
    slots = {"NIFTY": [_slot(at, 24800.0, 14.0, 1000.0), _slot(at + timedelta(minutes=5), 24900.0, 17.5, 1300.0)]}
    cards = ww.iv_spike_cards([h], slots, 3.0)
    assert [c.kind for c in cards] == ["iv_spike"] and cards[0].key.startswith("iv_spike:5:")
    assert cards[0].title == "NIFTY 25000 CE: IV moved at your strike" and "IV 14 -> 17.5 (3.5 IV points)" in cards[0].message
    assert ww.iv_spike_cards([h], slots, 5.0) == []
    _clean(cards)


def test_margin_in_use_at_his_level():
    c = ww.margin_card(72_000.0, 100_000.0, 70.0, "upstox")
    assert c is not None and "Margin in use 72 % (72,000 of 100,000, upstox); your level 70 %." in c.message
    assert ww.margin_card(60_000.0, 100_000.0, 70.0, "upstox") is None
    assert ww.margin_card(10.0, 0.0, 1.0, "upstox") is None                       # no figure: no card
    _clean([c])


def test_reminder_before_his_own_square_off_time():
    from app.market_data.calendar import IST as ist
    at = datetime(2031, 6, 11, 15, 2, tzinfo=ist)
    c = ww.cut_off_card(at, "15:10", 15, True)
    assert c is not None and c.key == "cut_off_reminder:2031-06-11" and not c.holds and "8 min left" in c.message
    assert ww.cut_off_card(at, "15:10", 5, True) is None                          # not yet within his minutes
    assert ww.cut_off_card(at.replace(minute=11), "15:10", 15, True) is None      # past it
    assert ww.cut_off_card(at, None, 15, True) is None                            # his square-off time not saved
    assert ww.cut_off_card(at, "15:10", 15, False) is None                        # not a trading day
    _clean([c])


def test_open_here_and_telegram_link(monkeypatch):
    card = ww.area_entry_cards([AREA], {"ZZWATCH": 104.5})[0]
    meta = ww.metadata(card)
    assert ww.open_of(meta) == {"symbol": "ZZWATCH"} and ww.open_path("ZZWATCH") == "/copilot/read?symbol=ZZWATCH"
    assert ww.open_of('{"watch": "near_levels", "open": {"symbol": "X"}}') is None   # a Copilot watch card: no "Open here"
    assert ww.open_of("not json") is None
    from app.core import config
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    assert "Open here" not in ww.telegram_html(card)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://example.test")
    assert 'href="https://example.test/copilot/read?symbol=ZZWATCH">Open here</a>' in ww.telegram_html(card)


def test_since_is_the_servers():
    now = datetime(2031, 6, 11, 5, 0, tzinfo=UTC)
    body = {"areas": [{**AREA, "since": "2020-01-01T00:00:00+00:00"}, {**AREA, "id": "a2", "since": "2020-01-01T00:00:00+00:00"}]}
    out = ww.stamp_areas(body, [{"id": "a1", "since": "2031-06-01T00:00:00+00:00"}], now)
    assert [a["since"] for a in out["areas"]] == ["2031-06-01T00:00:00+00:00", now.isoformat()]


def _count(model, *where):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(model).where(*where))
    return _run(go())


def _due():
    async def go():
        async with _session_factory() as session:
            return await ww.run_due(session, datetime.now(UTC))
    return _run(go())


def test_settings_route_flags_his_numbers_and_read_only():
    who = _who("mp11-settings")
    _run(_flag("ui_v2", True))
    _run(_flag("copilot_workspace", False))
    assert client.get(WATCH, headers=who["headers"]).status_code == 404
    _run(_flag("copilot_workspace", True, [who["tenant_id"]]))
    try:
        got = client.get(WATCH, headers=who["headers"]).json()
        assert not any(got["prefs"][k] for k in ww.KINDS) and got["prefs"]["areas"] == [] and got["blocks"] is False
        assert all(got["prefs"][k] is None for k in ("strike_points_n", "iv_spike_points", "margin_use_pct", "cut_off_minutes"))
        for bad in ({"strike_points": True}, {"iv_spike": True}, {"margin_use": True}, {"cut_off_reminder": True},
                    {"areas": [AREA, AREA]}, {"areas": [{**AREA, "low": 120.0}]}, {"areas": [{**AREA, "symbol": "x;y"}]}):
            assert client.put(WATCH, headers=who["headers"], json=bad).status_code == 422, bad
        ok = client.put(WATCH, headers=who["headers"], json={"area_entry": True, "areas": [{**AREA, "since": "2020-01-01T00:00:00+00:00"}]})
        assert ok.status_code == 200
        since = ok.json()["prefs"]["areas"][0]["since"]
        assert since and not since.startswith("2020")                             # the page's own `since` is not kept
        again = client.put(WATCH, headers=who["headers"], json={"area_entry": True, "areas": [AREA]}).json()
        assert again["prefs"]["areas"][0]["since"] == since                       # an area already watched keeps its own
        assert {m for r in ww.router.routes for m in getattr(r, "methods", set())} == {"GET", "PUT"}
        assert ("GET", WATCH) in ROUTE_TABLE and ("PUT", WATCH) in ROUTE_TABLE
    finally:
        _run(_flag("copilot_workspace", False))


def test_the_worker_raises_his_card_once_with_open_here(monkeypatch):
    owner = _who("mp11-run")
    member = _invite(owner, "USER")
    prices = {"ZZWATCH": 104.0}

    async def fake_prices(session, user, wanted):
        return {s: prices[s] for s, _e in wanted if s in prices}
    monkeypatch.setattr(ww, "_prices", fake_prices)
    _run(_flag("ui_v2", True))
    _run(_flag("copilot_workspace", True, [owner["tenant_id"]]))
    try:
        # the candle check runs too (no bars for this made-up symbol: no candle, no card, no error)
        assert client.put(WATCH, headers=owner["headers"], json={"area_entry": True, "area_rejection": True, "areas": [AREA]}).status_code == 200
        mine = (NotificationRecord.tenant_id == owner["tenant_id"], NotificationRecord.event_type == NotificationType.COPILOT_WATCH.value)
        actions = _count(AiActionRecord, AiActionRecord.tenant_id == owner["tenant_id"])
        assert _due() >= 1
        assert _count(NotificationRecord, *mine, NotificationRecord.user_id == owner["user_id"]) == 1
        assert _count(AiActionRecord, AiActionRecord.tenant_id == owner["tenant_id"]) == actions          # no proposal row
        assert _count(AlertDeliveryRecord, AlertDeliveryRecord.tenant_id == owner["tenant_id"]) == 0      # no shared channel
        _due()
        assert _count(NotificationRecord, *mine) == 1                                                    # holds: not again
        prices["ZZWATCH"] = 120.0
        _due()                                                                                           # left it: re-armed
        prices["ZZWATCH"] = 101.0
        _due()
        assert _count(NotificationRecord, *mine) == 2
        feed = client.get("/api/notifications", headers=owner["headers"]).json()
        card = next(n for n in feed if n["event_type"] == "COPILOT_WATCH")
        assert card["open_here"] == {"symbol": "ZZWATCH"} and words.clean(card["title"]) and words.clean(card["message"])
        assert not [n for n in client.get("/api/notifications", headers=member["headers"]).json() if n["event_type"] == "COPILOT_WATCH"]
        _run(_flag("copilot_workspace", False))
        prices["ZZWATCH"] = 120.0
        _due()
        prices["ZZWATCH"] = 101.0
        _due()
        assert _count(NotificationRecord, *mine) == 2                                                    # flag off: nothing
    finally:
        _run(_flag("copilot_workspace", False))


def test_no_order_path_reads_the_watch():
    app = Path(__file__).resolve().parents[1] / "app"
    readers = sorted(p.relative_to(app).as_posix() for p in app.rglob("*.py")
                     if "workspace_watch" in p.read_text(encoding="utf-8") or "WorkspaceWatchPrefs" in p.read_text(encoding="utf-8"))
    assert readers == ["charts/workspace_watch.py", "main.py", "me/prefs.py", "notifications/routes.py", "workers/trading_worker.py"]
    src = (app / "charts" / "workspace_watch.py").read_text(encoding="utf-8")
    for call in (r"\bplace\(", r"fno_ticket", r"place_order", r"cancel_order", r"modify_order", r"exit_trade"):
        assert not re.search(call, src), call
