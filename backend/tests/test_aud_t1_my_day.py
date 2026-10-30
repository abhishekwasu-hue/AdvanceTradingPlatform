"""AUD-T1 (owner audit 28 Oct): "My day in one card" - GET /api/today/my-day.

- 404 while `my_day_card` (or `ui_v2`) is off; the flag is registered OFF by default and depends on `ui_v2`;
- only the caller's own open positions (another member's, another organisation's and closed ones never show);
- the chip comes only from the position's own stop, against the MY_DAY_* settings; no stop -> no chip, "No stop set";
- expiry in sessions on the exchange calendar (holidays skipped); no areas -> one plain sentence, never a made-up area;
- the nearest area is measured from the held strike; its lifecycle note only from the event the zone store holds;
- the gap at the open from the shared daily history; today's events from the events calendar.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete

from app.core import config
from app.db.models import MarketEventRecord, MarketHolidayRecord, MdCandleRecord, TradeRecord
from app.market_data.calendar import IST
from app.platform import controls, flag_catalog
from app.today import my_day
from tests.test_auth_api import _register, _session_factory, client
from tests.test_settings_a_connect_ai import _flags
from tests.test_trading_worker import _run


def _user(email):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _trade(tenant_id, user_id, **kw):
    values = dict(tenant_id=tenant_id, user_id=user_id, mode="PAPER", symbol="RELIANCE", strategy_id="s", direction="LONG",
                  entry_time=datetime.now(timezone.utc), entry_price=100.0, quantity=1.0, stop_loss=90.0)
    values.update(kw)

    async def go():
        async with _session_factory() as session:
            t = TradeRecord(**values)
            session.add(t)
            await session.commit()
            return t.id
    return _run(go())


# --- pure pieces --------------------------------------------------------------------------------------------------------

def test_flag_is_registered_off_by_default_and_depends_on_ui_v2():
    assert "my_day_card" in controls.FEATURE_FLAGS and "my_day_card" in controls.DEFAULT_OFF_FLAGS
    assert flag_catalog.FLAG_INFO["my_day_card"].depends_on == ("ui_v2",)


def test_chip_reads_only_the_users_own_stop_against_the_settings(monkeypatch):
    monkeypatch.setattr(config, "MY_DAY_WATCH_ROOM_LEFT_PCT", 50.0)
    monkeypatch.setattr(config, "MY_DAY_NEAR_ROOM_LEFT_PCT", 25.0)
    assert my_day.chip_for("LONG", 100, 90, 98)["chip"] == "safe"            # 80 % of the room left
    assert my_day.chip_for("LONG", 100, 90, 95)["chip"] == "watch"           # 50 %
    assert my_day.chip_for("LONG", 100, 90, 92)["chip"] == "near"            # 20 %
    assert my_day.chip_for("LONG", 100, 90, 89)["chip"] == "near"            # past the stop
    short = my_day.chip_for("SHORT", 100, 150, 120)                          # a written option: stop above the entry
    assert short["chip"] == "safe" and short["room_left_pct"] == 60.0
    monkeypatch.setattr(config, "MY_DAY_WATCH_ROOM_LEFT_PCT", 90.0)          # the setting moves the chip, nothing else
    assert my_day.chip_for("LONG", 100, 90, 98)["chip"] == "watch"
    none = my_day.chip_for("LONG", 100, 0, 98)
    assert none["chip"] is None and none["text"] == "No stop set"
    assert my_day.chip_for("LONG", 100, 90, None)["chip"] is None             # no mark: no chip, plain words
    trailed = my_day.chip_for("LONG", 100, 105, 110)                          # stop past the entry: room not measured
    assert trailed["chip"] is None and "room not measured" in trailed["text"]


def test_expiry_words_and_the_nearest_area_from_the_strike():
    assert my_day.expiry_text(0) == "Expires today" and my_day.expiry_text(1) == "Expires next session"
    assert my_day.expiry_text(4) == "Expires in 4 sessions"
    since = date(2026, 10, 28)
    rows = [
        {"zone_key": "a", "zone_type": "demand", "zone_low": 24300.0, "zone_high": 24350.0, "state": "fresh", "strength": 1.0},
        {"zone_key": "b", "zone_type": "supply", "zone_low": 24600.0, "zone_high": 24650.0, "state": "tested", "strength": 1.0,
         "last_event": {"event": "TOUCH", "time": datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)}},
        {"zone_key": "c", "zone_type": "demand", "zone_low": 24490.0, "zone_high": 24495.0, "state": "invalidated", "strength": 9.0},
    ]
    area = my_day.nearest_area(rows, 24500.0, since)
    assert area["low"] == 24600.0 and area["where"] == "above" and area["change"]["event"] == "TOUCH"
    assert my_day.area_text(area, "strike").startswith("Nearest area is 0.41% above your strike")
    assert my_day.nearest_area(rows, 24500.0, date(2026, 10, 29))["change"] is None    # an older event is not "since"
    assert my_day.nearest_area([], 24500.0, since) is None


def test_gap_at_the_open_from_the_two_newest_daily_bars():
    bars = [(date(2026, 10, 27), 100.0, 200.0), (date(2026, 10, 28), 202.0, 205.0)]
    gap = my_day.gap_of(bars)
    assert gap == {"session": "2026-10-28", "gap_pct": 1.0}
    assert my_day.gap_text("NIFTY", gap, date(2026, 10, 28)) == "NIFTY opened 1.00% above the previous close today."
    assert my_day.gap_of(bars[:1]) is None


def test_expiry_sessions_skip_weekends_and_holidays():
    t = _user("aud-t1-expiry@example.com")
    now = datetime(2026, 10, 29, 10, 0, tzinfo=IST)                           # a Thursday
    holiday = date(2026, 11, 2)

    async def go():
        async with _session_factory() as session:
            session.add(MarketHolidayRecord(exchange="NSE", holiday_date=holiday, description="test holiday"))
            await session.commit()
        try:
            async with _session_factory() as session:
                return await my_day.build(session, my_day.Viewer(t["user_id"], t["tenant_id"]), now)
        finally:
            async with _session_factory() as session:
                await session.execute(delete(MarketHolidayRecord).where(MarketHolidayRecord.holiday_date == holiday,
                                                                         MarketHolidayRecord.description == "test holiday"))
                await session.commit()
    _trade(t["tenant_id"], t["user_id"], symbol="NIFTY 24500 CE 03 NOV 26", instrument_kind="OPTION", direction="SHORT",
           underlying_symbol="NIFTY", expiry=date(2026, 11, 3), entry_price=100.0, stop_loss=150.0, mark_price=110.0, exchange="NFO")
    out = _run(go())
    p = out["positions"][0]
    assert p["expiry_sessions"] == 2 and p["expiry_text"] == "Expires in 2 sessions"     # Fri, (Mon holiday), Tue
    assert p["chip"] == "safe" and p["room_left_pct"] == 80.0


# --- the route ----------------------------------------------------------------------------------------------------------

def test_route_is_flagged_self_scoped_and_says_no_stop_plainly(monkeypatch):
    t = _user("aud-t1-route@example.com")
    other = _user("aud-t1-other@example.com")
    mine = _trade(t["tenant_id"], t["user_id"], symbol="RELIANCE", stop_loss=0.0, mark_price=101.0)
    _trade(t["tenant_id"], t["user_id"], symbol="TCS", exit_time=datetime.now(timezone.utc), exit_price=99.0)   # closed
    _trade(t["tenant_id"], other["user_id"], symbol="INFY")                    # another member's
    _trade(other["tenant_id"], other["user_id"], symbol="WIPRO")               # another organisation's
    live = _trade(t["tenant_id"], t["user_id"], symbol="SBIN", mode="LIVE", entry_price=100.0, stop_loss=90.0, mark_price=92.0)
    _flags(ui_v2=True, my_day_card=False)
    try:
        assert client.get("/api/today/my-day", headers=t["headers"]).status_code == 404
        _flags(ui_v2=False, my_day_card=True)
        assert client.get("/api/today/my-day", headers=t["headers"]).status_code == 404
        _flags(ui_v2=True, my_day_card=True, market_zones=False)
        res = client.get("/api/today/my-day", headers=t["headers"])
        assert res.status_code == 200
        body = res.json()
        assert {p["id"] for p in body["positions"]} == {mine, live}            # own open ones, PAPER and LIVE alike
        by_id = {p["id"]: p for p in body["positions"]}
        assert by_id[mine]["chip"] is None and by_id[mine]["chip_text"] == "No stop set"
        assert by_id[live]["chip"] == "near" and by_id[live]["chip_label"] == "Near invalidation"
        assert by_id[live]["area_text"] == "Structure areas are not available for this position."
        assert body["thresholds"] == {"watch_room_left_pct": config.MY_DAY_WATCH_ROOM_LEFT_PCT,
                                      "near_room_left_pct": config.MY_DAY_NEAR_ROOM_LEFT_PCT}
        assert client.get("/api/today/my-day", headers=other["headers"]).json()["positions"][0]["symbol"] == "WIPRO"
    finally:
        _flags(ui_v2=False, my_day_card=False)


def test_no_stored_areas_is_one_plain_sentence_and_stored_areas_are_measured(monkeypatch):
    t = _user("aud-t1-zones@example.com")
    _trade(t["tenant_id"], t["user_id"], symbol="NIFTY 24500 CE 03 NOV 26", instrument_kind="OPTION", direction="SHORT",
           underlying_symbol="NIFTY", expiry=date(2026, 11, 3), stop_loss=150.0, mark_price=110.0, exchange="NFO")
    _flags(ui_v2=True, my_day_card=True, market_zones=True)
    try:
        body = client.get("/api/today/my-day", headers=t["headers"]).json()
        assert body["positions"][0]["area"] is None
        assert body["positions"][0]["area_text"] == "No structure areas stored for NIFTY yet."

        async def rows(session, me, key, now):
            assert key == "NSE_INDEX:NIFTY50"
            return [{"zone_key": "z", "zone_type": "demand", "zone_low": 24300.0, "zone_high": 24400.0, "state": "fresh", "strength": 1.0}]
        monkeypatch.setattr(my_day, "_zone_rows", rows)
        p = client.get("/api/today/my-day", headers=t["headers"]).json()["positions"][0]
        assert p["area"]["from"] == "strike" and p["area"]["where"] == "below" and p["area"]["low"] == 24300.0
        assert p["area_text"] == "Nearest area is 0.41% below your strike."
    finally:
        _flags(ui_v2=False, my_day_card=False, market_zones=False)


def test_since_last_session_gap_and_events_today():
    t = _user("aud-t1-since@example.com")
    _trade(t["tenant_id"], t["user_id"], symbol="BANKNIFTY", instrument_kind="UNDERLYING", stop_loss=90.0, mark_price=99.0, exchange="NSE")
    now = datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    key = "NSE_INDEX:NIFTYBANK"

    async def seed():
        async with _session_factory() as session:
            for i, (o, c) in enumerate(((100.0, 200.0), (198.0, 201.0))):
                day = today - timedelta(days=2 - i)
                ts = datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(hour=15, minute=30).astimezone(timezone.utc)
                session.add(MdCandleRecord(instrument_key=key, timeframe="day", ts=ts, source="aud-t1", version=1, open=o, high=max(o, c),
                                           low=min(o, c), close=c, volume=0))
            session.add(MarketEventRecord(tenant_id=None, underlying="BANKNIFTY", event_date=today, kind="RBI_POLICY", action="SIZE_CUT",
                                          description=""))
            session.add(MarketEventRecord(tenant_id=None, underlying="SENSEX", event_date=today, kind="RESULTS", action="SIZE_CUT",
                                          description=""))
            await session.commit()

    async def clean():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.source == "aud-t1"))
            await session.execute(delete(MarketEventRecord).where(MarketEventRecord.event_date == today,
                                                                  MarketEventRecord.underlying.in_(("BANKNIFTY", "SENSEX"))))
            await session.commit()
    _run(seed())
    _flags(ui_v2=True, my_day_card=True)
    try:
        since = client.get("/api/today/my-day", headers=t["headers"]).json()["since_last_session"]
        assert since["gaps"][0]["underlying"] == "BANKNIFTY" and since["gaps"][0]["gap_pct"] == -1.0
        assert "below the previous close" in since["gaps"][0]["text"]
        assert since["events"] == ["Today: RBI policy (BANKNIFTY)."]          # the other underlying's event is left out
        assert since["empty"] is None
    finally:
        _flags(ui_v2=False, my_day_card=False)
        _run(clean())


def test_an_option_is_measured_from_the_master_strike_not_the_text_guess():
    """A compact symbol runs the date into the strike (the parser reads 124500); the master's own strike wins."""
    from types import SimpleNamespace

    from app.today.my_day import reference_of
    t = SimpleNamespace(instrument_kind="OPTION", symbol="NIFTY26O0124500CE", underlying_stop_loss=None, stop_loss=None)
    assert reference_of(t, 24500.0) == (24500.0, "strike")
    assert reference_of(SimpleNamespace(instrument_kind="OPTION", symbol="NIFTY 24500 CE", underlying_stop_loss=None,
                                        stop_loss=None)) == (24500.0, "strike")      # no master row: the parser, as before
