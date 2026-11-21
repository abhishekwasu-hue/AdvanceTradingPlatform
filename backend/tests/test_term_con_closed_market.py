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
