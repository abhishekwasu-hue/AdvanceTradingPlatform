"""Master Order 4 slice 1: the daily rhythm (app/rhythm) - the pre-market brief, the mid-session pulse, the closing wrap
and the weekly review.

Covered: the IST schedule (trading days only, holidays, the weekend review, once per period); each digest from fixtures
(the user's own market memory, events, holidays, signals, PAPER positions; the lake closes faked) with its content, in
structure words only - no buy / sell / target / stop wording anywhere and no number in a setup line; "nothing" when the
data is missing (never a sample figure); Telegram opt-in (default OFF) respected and sent once to the user's own chat
only; both flags off = 404 and nothing sent; the settings routes."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, update

from app.alerts.channels import TelegramConfig, encrypt_config
from app.chart_analyze.signal_language import call_words
from app.core import config as app_config
from app.db.models import AlertChannelRecord, MarketEventRecord, MarketSnapshotRecord, NotificationLinkRecord, SignalHistoryRecord, TradeRecord, UserPrefsRecord
from app.market_data import calendar as market_calendar
from app.market_data.calendar import IST
from app.rhythm import build, deliver, schedule, words
from app.rhythm import routes as rhythm_routes
from app.secrets_store.envelope import ensure_tenant_key
from tests.test_auth_api import _session_factory, client
from tests.test_settings_a_connect_ai import _flags
from tests.test_ui2_4a1_prefs import _invite, _who

# A Wednesday far from every other suite's dates, so no shared row (events, holidays) is read by mistake.
DAY = date(2031, 6, 11)
HOLIDAY = date(2031, 6, 12)


def _run(coro):
    return asyncio.run(coro)


def _at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).astimezone(timezone.utc)


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _flags_on(monkeypatch):
    rhythm_routes.reset_cache()
    _flags(ui_v2=True, daily_rhythm=True)

    async def holidays(*_a, **_k):
        return {HOLIDAY}
    monkeypatch.setattr(build, "family_holidays", holidays)
    monkeypatch.setattr(market_calendar, "family_holidays", holidays)
    monkeypatch.setattr(app_config, "ATP_TELEGRAM_BOT_TOKEN", "424242:PLATFORM-token-value", raising=False)
    yield
    _flags(ui_v2=False, daily_rhythm=False, discipline_engine=False)
    rhythm_routes.reset_cache()

    async def close_mine():
        async with _session_factory() as session:
            await session.execute(update(TradeRecord).where(TradeRecord.symbol.like("MO4%"), TradeRecord.exit_time.is_(None)).values(
                exit_time=datetime.now(timezone.utc), exit_price=TradeRecord.entry_price, pnl=0.0, exit_reason="test cleanup"))
            await session.commit()
    _run(close_mine())


def _me(who):
    return SimpleNamespace(id=who["user_id"], tenant_id=who["tenant_id"], role="OWNER")


def _memory(who, at):
    def snap(symbol, structure, last_price, support, resistance, event):
        payload = {"last_price": last_price, "structure": {"trend": structure, "last_event": event},
                   "support": support, "resistance": resistance}
        return MarketSnapshotRecord(tenant_id=who["tenant_id"], kind="SYMBOL", symbol=symbol, exchange="NSE", timeframe="5min", source="test",
                                    last_price=last_price, change_pct=0.4, bias="NEUTRAL", regime="TRENDING_UP", higher_regime="TRENDING_UP",
                                    structure=structure, payload_json=json.dumps(payload), captured_at=at)
    return [snap("NIFTY 50", "UPTREND", 23050.0, {"low": 22900.0, "high": 22950.0}, {"low": 23300.0, "high": 23350.0},
                 {"event": "BOS", "direction": "BULLISH", "level": 23000.0}),
            snap("NIFTY BANK", "DOWNTREND", 49100.0, {"low": 48000.0, "high": 48200.0}, {"low": 49000.0, "high": 49200.0},
                 {"event": "CHoCH", "direction": "BEARISH", "level": 49500.0})]


def _trade(who, symbol, *, entry, stop, mark, qty=10, direction="LONG", at=None, exit_at=None, pnl=None):
    return TradeRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", symbol=symbol, strategy_id="manual",
                       direction=direction, entry_time=at or _at(DAY, 9, 30), entry_price=entry, quantity=qty, stop_loss=stop,
                       mark_price=mark, mark_time=_at(DAY, 10), exit_time=exit_at, exit_price=None if exit_at is None else mark,
                       pnl=pnl, charges=0.0)


def _signal(who, symbol, at, reasons, direction="LONG"):
    return SignalHistoryRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], strategy_id="pullback_custom", symbol=symbol,
                               direction=direction, signal_time=at, entry=101.5, stop_loss=99.0, target1=106.0, risk_reward=1.8,
                               score=80, grade="High Quality", reasons_json=json.dumps(reasons), timeframe_combo="15m", created_at=at)


def _lines(brief):
    return [line for s in brief["sections"] for line in s["lines"]]


def _assert_signal_language(brief):
    for s in brief["sections"]:
        assert call_words(s["title"]) == []
        for line in s["lines"] + ([s["empty"]] if s["empty"] else []):
            assert call_words(line) == [], line
            assert words.clean(line) == line, line
    assert call_words(brief["note"]) == []


def _build(who, kind, now):
    async def go():
        async with _session_factory() as session:
            return await build.build(session, _me(who), kind, now)
    return _run(go())


# --- schedule --------------------------------------------------------------------------------------------------------

def test_phase_follows_the_session_and_the_calendar():
    assert schedule.phase(_at(DAY, 8, 0)) == "pre_market"
    assert schedule.phase(_at(DAY, 11, 0)) == "mid_session"
    assert schedule.phase(_at(DAY, 16, 0)) == "closing"
    assert schedule.phase(_at(HOLIDAY, 11, 0), {HOLIDAY}) == "pre_market"        # a weekday holiday: the next session's brief
    assert schedule.phase(_at(date(2031, 6, 14), 11, 0)) == "weekly"           # Saturday


def test_telegram_windows_are_on_trading_days_only():
    assert schedule.due_kinds(_at(DAY, 8, 45)) == ["pre_market"]
    assert schedule.due_kinds(_at(DAY, 8, 10)) == []
    assert schedule.due_kinds(_at(DAY, 12, 45)) == ["mid_session"]
    assert schedule.due_kinds(_at(DAY, 14, 0)) == []
    assert schedule.due_kinds(_at(DAY, 16, 0)) == ["closing"]
    assert schedule.due_kinds(_at(DAY, 20, 0)) == []
    for hh in (8, 12, 16):                                                      # the holiday: nothing at all
        assert schedule.due_kinds(_at(HOLIDAY, hh, 45), {HOLIDAY}) == []
    assert schedule.due_kinds(_at(date(2031, 6, 14), 10)) == ["weekly"]          # Saturday
    assert schedule.due_kinds(_at(date(2031, 6, 15), 18)) == ["weekly"]          # Sunday
    assert schedule.due_kinds(_at(date(2031, 6, 14), 8)) == []
    assert schedule.period_key("closing", _at(DAY, 16)) == "2031-06-11"
    assert schedule.period_key("weekly", _at(date(2031, 6, 14), 10)) == schedule.period_key("weekly", _at(date(2031, 6, 15), 18)) == "2031-W24"
    assert schedule.next_session(_at(DAY, 16), {HOLIDAY}) == date(2031, 6, 13)


# --- words -----------------------------------------------------------------------------------------------------------

def test_the_guard_drops_call_words_and_advice():
    for bad in ("Buy RELIANCE now", "Target 23,400", "Stop loss below the low", "SL at 99", "You should exit", "Book profits here",
                "sell the rally"):
        assert words.clean(bad) is None, bad
    assert words.clean("The pullback reached a seller area; a handover is forming.") is not None
    assert words.signal_line("NIFTY 50: a buyer-side setup.", ["NIFTY 50"]) is not None
    assert words.signal_line("RELIANCE: a setup near 2,450.", ["RELIANCE"]) is None


def test_structure_words():
    assert words.area_words(105.0, {"low": 100, "high": 102}, {"low": 120, "high": 121}) == "price is closer to the buyer area below than to the seller area above"
    assert words.area_words(101.0, {"low": 100, "high": 102}, None) == "price is inside a buyer area"
    assert words.area_words(119.0, {"low": 100, "high": 102}, {"low": 120, "high": 121}) == "price is closer to the seller area above than to the buyer area below"
    assert words.area_words(None, None, None) is None
    assert "change of character" in words.event_words({"event": "CHoCH", "direction": "BULLISH"})


# --- the digests -----------------------------------------------------------------------------------------------------

def test_pre_market_brief_from_the_users_own_data():
    who = _who("mo4-pre")
    morning = _at(DAY, 8, 45)
    _add(*_memory(who, morning - timedelta(hours=15)),
         MarketEventRecord(tenant_id=who["tenant_id"], event_date=DAY, kind="RBI_POLICY", action="BLOCK", start_time="10:00", end_time="10:30",
                           description="Policy announcement"),
         MarketEventRecord(tenant_id=who["tenant_id"], event_date=DAY, kind="OTHER", description="Buy the dip day"),   # advice: dropped
         _signal(who, "MO4INFY", _at(DAY - timedelta(days=1), 11), ["Price is 2.5% above VWAP", "The pullback reached a buyer area"]),
         _signal(who, "MO4TCS", _at(DAY - timedelta(days=1), 12), ["Breakout above 3,400"], direction="SHORT"),
         _trade(who, "MO4NEAR", entry=100.0, stop=90.0, mark=91.5),
         _trade(who, "MO4FAR", entry=100.0, stop=90.0, mark=104.0))
    brief = _build(who, "pre_market", morning)
    assert brief["kind"] == "pre_market" and brief["title"] == "Before the open" and brief["has_content"]
    by_id = {s["id"]: s for s in brief["sections"]}
    assert [s["id"] for s in brief["sections"]] == ["mood", "events", "structure", "setups", "positions", "budget"]
    assert by_id["events"]["lines"] == ["RBI policy 10:00-10:30 - Policy announcement. New entries pause in its window; exits always work."]
    structure = " ".join(by_id["structure"]["lines"])
    assert "NIFTY 50: higher highs and higher lows" in structure and "BANK NIFTY: lower highs and lower lows" in structure
    assert "change of character downward" in structure and "seller area" in structure
    assert not any(ch.isdigit() for ch in structure.replace("NIFTY 50", "").replace("Tue 10 Jun", ""))   # no level is shown
    setups = by_id["setups"]["lines"]
    assert setups == ["MO4TCS: a seller-side setup from your strategy \"pullback_custom\".",
                      "MO4INFY: a buyer-side setup from your strategy \"pullback_custom\" - the pullback reached a buyer area."]
    positions = by_id["positions"]["lines"]
    assert any(line.startswith("MO4NEAR (long): open P&L -₹85") and "near the invalidation you set" in line for line in positions)
    assert any(line.startswith("MO4FAR (long): open P&L +₹40") and "invalidation" not in line for line in positions)
    # No risk settings saved: no made-up loss budget, a plain next step instead.
    assert by_id["budget"]["lines"] == [] and "Risk limits" in by_id["budget"]["empty"]
    _assert_signal_language(brief)


def test_nothing_made_up_when_there_is_no_data():
    who = _who("mo4-empty")
    brief = _build(who, "pre_market", _at(DAY, 8, 45))
    assert not brief["has_content"]
    for s in brief["sections"]:
        assert s["lines"] == [] and s["empty"]
    _assert_signal_language(brief)


def test_mid_session_pulse_says_what_changed(monkeypatch):
    who = _who("mo4-pulse")
    noon = _at(DAY, 12, 40)
    _add(*_memory(who, noon - timedelta(minutes=10)),
         _trade(who, "MO4NEAR2", entry=200.0, stop=180.0, mark=183.0, direction="LONG"),
         _trade(who, "MO4OK", entry=200.0, stop=220.0, mark=195.0, direction="SHORT"),
         _signal(who, "MO4SBIN", _at(DAY, 11, 5), ["A handover is forming at the seller area"], direction="SHORT"))
    brief = _build(who, "mid_session", noon)
    by_id = {s["id"]: s for s in brief["sections"]}
    assert by_id["structure"]["title"] == "What changed in the indices" and len(by_id["structure"]["lines"]) == 2
    assert [line.split(" ")[0] for line in by_id["positions"]["lines"]] == ["MO4NEAR2"]
    assert by_id["setups"]["lines"] == ["MO4SBIN: a seller-side setup from your strategy \"pullback_custom\" - a handover is forming at the seller area."]
    _assert_signal_language(brief)


def test_closing_wrap_with_market_moves_and_the_users_day(monkeypatch):
    who = _who("mo4-close")
    evening = _at(DAY, 16, 10)

    async def closes(ctx, name, days=20):
        base = {"NIFTY 50": 23000.0, "NIFTY BANK": 49000.0, "NIFTY IT": 30000.0, "NIFTY METAL": 9000.0}.get(name)
        if base is None:
            return []
        step = {"NIFTY IT": 1.02, "NIFTY METAL": 0.985}.get(name, 1.005)
        return [(DAY - timedelta(days=1), base), (DAY, base * step)]

    async def tile(ctx, name):
        return {"status": "ok", "advances": 1200, "declines": 800, "counted": 2050, "as_of": DAY.isoformat()} if name == "breadth" else {}
    monkeypatch.setattr(build, "_closes", closes)
    monkeypatch.setattr(build, "_tile", tile)
    _add(_trade(who, "MO4WIN", entry=100.0, stop=95.0, mark=104.0, exit_at=_at(DAY, 14), pnl=400.0),
         MarketEventRecord(tenant_id=who["tenant_id"], event_date=date(2031, 6, 13), kind="EXPIRY", underlying="NIFTY", description="Weekly expiry"))
    brief = _build(who, "closing", evening)
    by_id = {s["id"]: s for s in brief["sections"]}
    market = by_id["market"]["lines"]
    assert market[0] == "NIFTY 50 +0.50%, BANK NIFTY +0.50%."
    assert market[1] == "Breadth: 1200 shares rose and 800 fell."
    assert market[2] == "Strongest sector: IT (+2.00%); weakest: Metal (-1.50%)."
    assert by_id["day"]["lines"][0] == "Booked P&L today: +₹400."
    # The next session skips the holiday and names its events.
    nxt = by_id["next"]["lines"]
    assert nxt[0] == "Next session: Fri 13 Jun." and "Thu 12 Jun: market holiday." in nxt
    assert "Fri 13 Jun: Expiry (NIFTY) - Weekly expiry." in nxt
    _assert_signal_language(brief)


def test_closing_wrap_without_closing_data_says_so(monkeypatch):
    who = _who("mo4-close-empty")

    async def none(*_a, **_k):
        return []

    async def no_tile(*_a, **_k):
        return {}
    monkeypatch.setattr(build, "_closes", none)
    monkeypatch.setattr(build, "_tile", no_tile)
    brief = _build(who, "closing", _at(DAY, 16, 10))
    by_id = {s["id"]: s for s in brief["sections"]}
    assert by_id["market"]["lines"] == [] and "not in yet" in by_id["market"]["empty"]
    assert by_id["day"]["lines"] == [] and "fine" in by_id["day"]["empty"]       # never a nudge to trade more
    assert by_id["next"]["lines"] == ["Next session: Fri 13 Jun.", "Thu 12 Jun: market holiday."]   # the calendar is still known


def test_discipline_is_rewarded_never_trade_count(monkeypatch):
    who = _who("mo4-discipline")
    _flags(discipline_engine=True)

    async def none(*_a, **_k):
        return []
    monkeypatch.setattr(build, "_closes", none)
    _add(_trade(who, "MO4D1", entry=100.0, stop=95.0, mark=101.0, exit_at=_at(DAY, 11), pnl=10.0, at=_at(DAY, 10)))
    brief = _build(who, "closing", _at(DAY, 16, 10))
    day = {s["id"]: s for s in brief["sections"]}["day"]
    text = " ".join(day["lines"])
    assert "Discipline score for today:" in text and "not the number of trades" in text
    assert day["link"] == {"label": "Open the Journal", "to": "/journal"}
    for word in ("great job", "keep trading", "trade more", "streak"):
        assert word not in text.lower()


def test_weekly_review(monkeypatch):
    who = _who("mo4-week")
    saturday = _at(date(2031, 6, 14), 10)

    async def closes(ctx, name, days=20):
        if name not in ("NIFTY 50", "NIFTY BANK"):
            return []
        return [(DAY - timedelta(days=i), 23000.0 + 50 * (6 - i)) for i in range(6, -1, -1)]
    monkeypatch.setattr(build, "_closes", closes)
    _add(*_memory(who, _at(date(2031, 6, 13), 15, 20)),
         _trade(who, "MO4W1", entry=100.0, stop=95.0, mark=104.0, exit_at=_at(DAY, 14), pnl=400.0),
         _trade(who, "MO4W2", entry=100.0, stop=95.0, mark=96.0, exit_at=_at(date(2031, 6, 13), 11), pnl=-150.0, at=_at(date(2031, 6, 13), 10)),
         MarketEventRecord(tenant_id=who["tenant_id"], event_date=date(2031, 6, 18), kind="FED", description="FOMC decision"))
    brief = _build(who, "weekly", saturday)
    assert brief["subtitle"] == "Week of Mon 09 Jun"
    by_id = {s["id"]: s for s in brief["sections"]}
    assert by_id["market"]["lines"][0].startswith("Over the week: NIFTY 50 +")
    assert by_id["you"]["lines"] == ["2 closed, 1 won and 1 did not; net +₹250."]
    assert by_id["ahead"]["lines"] == ["Wed 18 Jun: US Fed decision - FOMC decision."]
    _assert_signal_language(brief)


# --- routes ----------------------------------------------------------------------------------------------------------

def test_flags_off_means_nothing():
    who = _who("mo4-off")
    _flags(daily_rhythm=False)
    for path in ("/api/rhythm/today", "/api/rhythm/settings"):
        got = client.get(path, headers=who["headers"])
        assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "daily_rhythm"
    _flags(daily_rhythm=True, ui_v2=False)
    assert client.get("/api/rhythm/today", headers=who["headers"]).status_code == 404


def test_today_route_answers_the_phase_and_any_digest():
    who = _who("mo4-route")
    got = client.get("/api/rhythm/today", headers=who["headers"])
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["phase"] in body["kinds"] and body["brief"]["kind"] == body["phase"]
    weekly = client.get("/api/rhythm/today", params={"kind": "weekly"}, headers=who["headers"]).json()
    assert weekly["brief"]["kind"] == "weekly"
    assert client.get("/api/rhythm/today", params={"kind": "lunch"}, headers=who["headers"]).status_code == 422


def test_settings_default_off_and_saved_per_user():
    who = _who("mo4-settings")
    got = client.get("/api/rhythm/settings", headers=who["headers"]).json()
    assert got["prefs"] == {"telegram": False, "pre_market": True, "mid_session": False, "closing": True, "weekly": True}
    assert got["telegram"] == {"connected": False, "reaches_you": False}
    put = client.put("/api/rhythm/settings", headers=who["headers"], json={"telegram": True, "mid_session": True})
    assert put.status_code == 200, put.text
    assert put.json()["prefs"]["telegram"] is True and put.json()["prefs"]["mid_session"] is True
    assert client.put("/api/rhythm/settings", headers=who["headers"], json={"telegram": "yes"}).status_code == 422
    other = _who("mo4-settings-other")
    assert client.get("/api/rhythm/settings", headers=other["headers"]).json()["prefs"]["telegram"] is False


# --- Telegram --------------------------------------------------------------------------------------------------------

class _Telegram:
    def __init__(self):
        self.sent: list = []

    def client(self):
        def handler(request: httpx.Request):
            self.sent.append(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _connect_telegram(who, chat="777001", by=None):
    async def go():
        async with _session_factory() as session:
            await ensure_tenant_key(session, who["tenant_id"])
            session.add(AlertChannelRecord(tenant_id=who["tenant_id"], channel_type="TELEGRAM", created_by=by or who["user_id"],
                                           encrypted_config=encrypt_config(TelegramConfig(chat_id=chat, platform_bot=True), who["tenant_id"]),
                                           enabled=True, min_severity="WARNING"))
            session.add(NotificationLinkRecord(tenant_id=who["tenant_id"], user_id=by or who["user_id"], channel="tg_platform", status="used",
                                               chat_id=chat, created_at=datetime.now(timezone.utc)))
            await session.commit()
    _run(go())


def _opt_in(who, **values):
    got = client.put("/api/rhythm/settings", headers=who["headers"], json={"telegram": True, **values})
    assert got.status_code == 200, got.text
    return got.json()


def _send(now):
    tg = _Telegram()

    async def go():
        async with _session_factory() as session:
            async with tg.client() as c:
                return await deliver.send_due(session, now, client=c)
    count = _run(go())
    return count, tg.sent


def _mine(sent, chat):
    return [m for m in sent if m.get("chat_id") == chat]


def test_telegram_opt_in_is_respected_and_sent_once():
    who = _who("mo4-tg")
    _add(*_memory(who, _at(DAY, 8, 30)))
    _connect_telegram(who, chat="777101")
    morning = _at(DAY, 8, 50)
    _, sent = _send(morning)
    assert _mine(sent, "777101") == []                                   # default OFF: nothing
    settings = _opt_in(who)
    assert settings["telegram"] == {"connected": True, "reaches_you": True}
    _, sent = _send(morning)
    mine = _mine(sent, "777101")
    assert len(mine) == 1
    text = mine[0]["text"]
    assert text.startswith("<b>Before the open</b>") and "NIFTY 50: higher highs and higher lows" in text
    assert call_words(text) == []
    _, again = _send(morning + timedelta(minutes=5))
    assert _mine(again, "777101") == []                                  # once per day
    _, holiday = _send(_at(HOLIDAY, 8, 50))
    assert _mine(holiday, "777101") == []                                # never on a holiday
    _, pulse = _send(_at(DAY, 12, 45))
    assert _mine(pulse, "777101") == []                                  # the pulse is optional and off by default


def test_telegram_flag_off_sends_nothing():
    who = _who("mo4-tg-off")
    _add(*_memory(who, _at(DAY, 8, 30)))
    _connect_telegram(who, chat="777201")
    _opt_in(who)
    _flags(daily_rhythm=False)
    _, sent = _send(_at(DAY, 8, 50))
    assert _mine(sent, "777201") == []


def test_telegram_never_reaches_another_members_chat():
    owner = _who("mo4-tg-owner")
    member = _invite(owner, "USER")
    _add(*_memory(owner, _at(DAY, 8, 30)))
    _connect_telegram(owner, chat="777301")                             # the owner's own chat
    got = _opt_in(member)
    assert got["telegram"] == {"connected": True, "reaches_you": False}
    _, sent = _send(_at(DAY, 8, 50))
    assert _mine(sent, "777301") == []


def test_telegram_skips_an_empty_digest_but_marks_it():
    who = _who("mo4-tg-empty")
    _connect_telegram(who, chat="777401")
    _opt_in(who)
    _, sent = _send(_at(DAY, 8, 50))
    assert _mine(sent, "777401") == []

    async def marks():
        async with _session_factory() as session:
            row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.user_id == who["user_id"],
                                                                     UserPrefsRecord.namespace == deliver.SENT))
            return json.loads(row.value_json)
    assert _run(marks()) == {"pre_market": DAY.isoformat()}


def test_render_keeps_a_digest_short():
    brief = {"title": "Closing wrap", "subtitle": "Wed 11 Jun · after the close", "note": build.NOTE,
             "sections": [{"title": "A <b>", "lines": [f"line {i} & more" for i in range(9)]}, {"title": "Empty", "lines": []}]}
    text = deliver.render_html(brief)
    assert "A &lt;b&gt;" in text and "line 3 &amp; more" in text and "line 4" not in text and "Empty" not in text
    assert deliver.open_button("https://aialgodesk.tech") == {"inline_keyboard": [[{"text": "Open in app", "url": "https://aialgodesk.tech/"}]]}
    assert deliver.open_button(None) is None
