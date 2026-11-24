"""TERM-CON fix (owner screenshot 21 Nov, Saturday): the chart reading gets the stored-history key ("NIFTY" ->
"NSE_INDEX:NIFTY50", the reader refused the plain name: "No closed bars"), and with no OI slot today the last session's
banner is shown, marked with its date - never as today's."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.conclusion import gather as G
from app.conclusion import rules as R
from app.conclusion import sections as S
from app.signals import language as L


def test_the_chart_reading_gets_the_lake_key_not_the_plain_name():
    from app.price_action.zones.store import normalise_key
    assert G.chart_key("NIFTY") == "NSE_INDEX:NIFTY50"
    assert normalise_key(G.chart_key("NIFTY")) == "NSE_INDEX:NIFTY50"        # the reader accepts it now
    assert G.chart_key("SENSEX") == "BSE_INDEX:SENSEX"
    assert G.chart_key("RELIANCE") == "NSE:RELIANCE"


class _Session:
    pass


def test_no_oi_slot_today_shows_the_last_session_marked_with_its_date(monkeypatch):
    from app.option_chain import routes as oc
    banner_last = {"underlying": "NIFTY", "date": "2026-11-20", "banner": {"slot": "s", "direction": "BEARISH", "pcr": 0.8}}
    seen = {}

    async def today(symbol, user, session):
        return {"underlying": "NIFTY", "date": "2026-11-21", "banner": None, "message": "Insufficient data"}

    async def day_view(session, user, underlying, day):
        seen["day"] = day
        return ("settings", day, ["slot"], {"stale": False})

    async def exchange(session, tenant_id, underlying):
        return "NSE"

    async def holidays(session, exchange):
        return set()
    monkeypatch.setattr(oc, "banner", today)
    monkeypatch.setattr(oc, "_day_view", day_view)
    monkeypatch.setattr(oc, "_exchange", exchange)
    monkeypatch.setattr(oc, "_banner_from", lambda u, *a: dict(banner_last))
    monkeypatch.setattr("app.market_data.calendar.family_holidays", holidays)

    class U:
        tenant_id = 1
    saturday = datetime(2026, 11, 21, 13, 4, tzinfo=UTC)                     # 18:34 IST, Saturday
    got = asyncio.run(G._oi(_Session(), U(), "NIFTY", saturday))
    assert got["last_session"] == "2026-11-20" and str(seen["day"]) == "2026-11-20"
    sec = S.oi_section(got, None)
    assert sec["status"] == "ok" and "the last session's reading (2026-11-20)" in sec["note"]
    reading = S.oi_reading(got)
    assert reading["label"] == "OI (last session)" and reading["direction"] == "down"
    line = R.line([reading], "NIFTY")
    assert line == "OI (last session) down" and L.check(line, "signal_own", own_symbols=["NIFTY"]) == []


def test_a_banner_today_is_used_as_it_is(monkeypatch):
    from app.option_chain import routes as oc

    async def today(symbol, user, session):
        return {"date": "2026-11-20", "banner": {"slot": "s", "direction": "BULLISH"}}
    monkeypatch.setattr(oc, "banner", today)

    class U:
        tenant_id = 1
    got = asyncio.run(G._oi(_Session(), U(), "NIFTY"))
    assert "last_session" not in got and S.oi_reading(got)["label"] == "OI"


def test_a_busy_build_slot_is_tried_again_and_then_said_as_being_read(monkeypatch):
    """Owner screenshot 24 Nov: "The chart reading is not available" while the Chart panel built the same chart - his one
    build slot answered 503 busy and the panel called it "not available". Now: tried CHART_TRIES times, then "busy"."""
    from fastapi import HTTPException

    from app.charts import read_chart as rc
    calls = {"n": 0}

    async def busy(**kw):
        calls["n"] += 1
        raise HTTPException(status_code=503, detail="busy, retry")

    monkeypatch.setattr(rc, "read_chart", busy)
    assert asyncio.run(G._chart(_Session(), object(), "NIFTY", "15m")) == "busy"        # type: ignore[arg-type]
    assert calls["n"] == G.CHART_TRIES

    calls["n"] = 0

    async def busy_then_ok(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HTTPException(status_code=503, detail="busy, retry")
        return {"bars": 10}

    monkeypatch.setattr(rc, "read_chart", busy_then_ok)
    assert asyncio.run(G._chart(_Session(), object(), "NIFTY", "15m")) == {"bars": 10}   # type: ignore[arg-type]

    async def off(**kw):
        raise HTTPException(status_code=404, detail="Not found")

    monkeypatch.setattr(rc, "read_chart", off)
    assert asyncio.run(G._chart(_Session(), object(), "NIFTY", "15m")) == "off"          # type: ignore[arg-type]

    sec = S.na("chart", "Chart", S.SOURCE_CHART, S.BUSY_NOTE)
    assert sec["note"] == S.BUSY_NOTE and "not available" not in sec["note"]


def test_a_busy_chart_marks_the_section_and_asks_the_panel_to_read_again(monkeypatch):
    async def busy(session, user, symbol, tf):
        return "busy"

    async def nothing(*a, **kw):
        return "na"

    monkeypatch.setattr(G, "_chart", busy)
    for name in ("_oi", "_positions", "_orders", "_signals", "_broker", "_kill", "_guardian", "_events"):
        monkeypatch.setattr(G, name, nothing)
    body = asyncio.run(G.conclusion(_Session(), type("U", (), {"id": 1, "tenant_id": 1})(), "NIFTY", "2026-12-01", "15m"))  # type: ignore[arg-type]
    chart = next(s for s in body["sections"] if s["key"] == "chart")
    assert chart["busy"] is True and chart["note"] == S.BUSY_NOTE
    assert body["retry_after"] == G.RETRY_AFTER_SECONDS


def test_the_oi_section_does_not_repeat_no_clear_movement():
    resp = {"banner": {"slot": "2026-11-24T12:30:00+00:00", "direction": "NEUTRAL", "fact_message": "No clear movement", "pcr": 1.06}}
    texts = [x["text"] for x in S.oi_section(resp, None)["lines"]]
    assert texts[0] == "OI: No clear OI change." and "No clear movement" not in texts
    resp["banner"]["fact_message"] = "Put writing → NIFTY OI readings lean up"
    assert "Put writing → NIFTY OI readings lean up" in [x["text"] for x in S.oi_section(resp, None)["lines"]]
