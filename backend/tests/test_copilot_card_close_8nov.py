"""Owner 8 Nov (production, Copilot, "Read the NIFTY chart"): the chart card showed "LAST CLOSE · 30 OCT 23,053.10",
"TREND · DAILY Down", "Supply 23,020.95 - 23,162.70, Price is inside" while the stored Daily bars of NSE_INDEX:NIFTY50
ran to 6 Nov (NSE's index file `nse_bhav:index` and his own `broker:upstox:u<id>` rows, close 25,030.35).

What the code shows:
1. A fresh read of exactly those rows states the 6 Nov close and reads the structure through 6 Nov (test 1) - the card on
   his screen was not a fresh read. It is the 2 Nov answer of the same card (tests/test_data_fix_2nov.py holds its
   numbers) kept in his open conversation (CU6 / CH-2) and re-rendered as stored (test 3); the page now says a kept turn
   was asked earlier and brings a new question's own turn into view at once (frontend, e2e/copilot-open-conversation-v2).
2. One shared-function gap found on the way: the chart layer (`price_action_layer._load_frame`) and the zones job read
   the FIRST lake label holding any row ("1d" before "day"), while `own_bars.official_close` reads both - a "1d" history
   that stopped at 30 Oct hid the "day" bars of 2-6 Nov, so the card could state the 6 Nov close beside a reading to
   30 Oct (test 2, fails before the fix). The label whose bars run newest is read now (a tie keeps the earlier label).
"""
import asyncio
import math
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import delete

from app.ai import chat_history
from app.ai import copilot_chat as chat
from app.ai.tools import ToolContext, ToolResult, run_tool
from app.charts import price_action_layer as pal
from app.db.models import CopilotChatSessionRecord, CopilotChatTurnRecord, MdCandleRecord, MdTenantCandleRecord, User
from app.market_lake import index_history as dh
from app.market_lake import own_bars
from tests.test_auth_api import _session_factory
from tests.test_copilot_v2_chat import _flag
from tests.test_data_h_lake_backfill import _user

KEY = "NSE_INDEX:NIFTY50"
NOW = datetime(2026, 11, 8, 6, 0, tzinfo=UTC)               # Sunday 8 Nov, 11:30 IST: the last session is Fri 6 Nov
OCT30, NOV6 = date(2026, 10, 30), date(2026, 11, 6)
OWN_NOV = {date(2026, 11, 2): 23440.80, date(2026, 11, 3): 23810.85, date(2026, 11, 4): 23960.60, date(2026, 11, 5): 24536.60,
           NOV6: 25030.35}
FIRST_NSE = date(2026, 10, 1)                                # NSE's index files in the shared lake began recently


def _run(coro):
    return asyncio.run(coro)


def _sessions(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1) if (first + timedelta(days=i)).weekday() < 5]


def _history() -> dict[date, float]:
    """Three years of Daily closes to 30 Oct (a plain wave; 30 Oct = 23,053.10), then the owner's 2-6 Nov closes."""
    days = _sessions(OCT30 - timedelta(days=3 * 365), OCT30)
    closes = {d: round(21000 + 1500 * math.sin(i / 23) + 600 * math.sin(i / 7) + i * 1.5, 2) for i, d in enumerate(days)}
    closes[OCT30] = 23053.10
    return {**closes, **OWN_NOV}


def _bar(model, day: date, close: float, **kw):
    return model(instrument_key=KEY, ts=dh.session_close(day), version=1, open=close - 40, high=close + 60, low=close - 80, close=close,
                 volume=0, **kw)


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):                                   # noqa: ANN001, ANN206 - datetime.now's own signature
        return NOW if tz is not None else NOW.replace(tzinfo=None)


def _seed(user: User, *, stopped_1d: bool) -> None:
    closes = _history()

    async def go():
        async with _session_factory() as session:
            await _clean(session)
            holder = f"broker:upstox:u{user.id}"
            # his own store: the long broker Daily history to 30 Oct and the 2-6 Nov rows (label "day", as written)
            session.add_all([_bar(MdTenantCandleRecord, d, c, tenant_id=user.tenant_id, timeframe="day", source=holder) for d, c in closes.items()])
            # the shared lake: NSE's index close file, newest row 6 Nov 15:30 IST (10:00 UTC), 25,030.35
            session.add_all([_bar(MdCandleRecord, d, c, timeframe="day", source="nse_bhav:index") for d, c in closes.items() if d >= FIRST_NSE])
            if stopped_1d:
                # a licensed "1d" history of the same key that stopped at 30 Oct (an import / vendor label)
                session.add_all([_bar(MdCandleRecord, d, c, timeframe="1d", source="vendor:import") for d, c in closes.items() if d <= OCT30])
            await session.commit()
    _run(go())


async def _clean(session) -> None:
    await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == KEY, MdCandleRecord.timeframe.in_(("1d", "day"))))
    await session.execute(delete(MdTenantCandleRecord).where(MdTenantCandleRecord.instrument_key == KEY))


def _read(user_id: int):
    """What the Copilot's chart answer reads: the real `get_chart_structure` tool, then the card's headline row."""
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            res = await run_tool("get_chart_structure", {"symbol": "NIFTY", "timeframe": "1d"}, ToolContext(session, user.tenant_id, user, NOW))
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            scope = await own_bars.scope_for(session, user, KEY)
            frame, source = await pal.load_frame(session, KEY, "1d", NOW - timedelta(days=pal.DEFAULT_DAYS["1d"]), NOW, private=scope)
        return res, frame, source
    return _run(go())


def _with(monkeypatch, email: str, *, stopped_1d: bool):
    monkeypatch.setattr(pal, "datetime", _Clock)
    pal.cache_clear()
    user = _user(email)
    _seed(user, stopped_1d=stopped_1d)
    _run(_flag("chart_price_action", True))
    try:
        return _read(user.id)
    finally:
        _run(_flag("chart_price_action", False))
        pal.cache_clear()

        async def tidy():
            async with _session_factory() as session:
                await _clean(session)
                await session.commit()
        _run(tidy())


def _assert_card_is_the_6_nov_close(res: ToolResult) -> None:
    assert res.ok, res.error
    d = res.data
    assert d["official_close"] == {"close": 25030.35, "date": "2026-11-06", "source": "NSE index close file"}
    head = chat.chart_headline("NIFTY", "1d", res, ToolResult(False, None, None, "market_zones", error="not read"))
    assert head["price"] == 25030.35 and head["as_of"] == "2026-11-06" and head["price_label"] == "Last close"
    # the reading itself runs through that session, never 30 Oct
    assert d["last_close"] == 25030.35 and str(d["as_of"]).startswith("2026-11-06"), (d["last_close"], d["as_of"])
    assert "30 Oct" not in chat._close_head("NIFTY", d, "1d")                       # noqa: SLF001


def test_a_fresh_read_of_the_owners_stored_rows_states_the_6_nov_close_and_reads_through_it(monkeypatch):
    res, frame, source = _with(monkeypatch, "cpfix-fresh@example.com", stopped_1d=False)
    _assert_card_is_the_6_nov_close(res)
    assert source == "own:day" and float(frame["close"].iloc[-1]) == 25030.35


def test_a_stopped_1d_history_never_hides_the_newer_day_bars(monkeypatch):
    """Before the fix: source "lake:1d", the frame and the card's reading ended 30 Oct (23,053.10) beside the 6 Nov close."""
    res, frame, source = _with(monkeypatch, "cpfix-1d@example.com", stopped_1d=True)
    assert source == "own:day", source
    assert float(frame["close"].iloc[-1]) == 25030.35
    assert pd_day(frame["bar_end"].iloc[-1]) == NOV6
    _assert_card_is_the_6_nov_close(res)


def pd_day(ts) -> date:
    return pd.Timestamp(ts).tz_convert("Asia/Kolkata").date()


def test_a_kept_turn_comes_back_exactly_as_it_was_stored_with_when_it_was_asked():
    """The mechanism behind the screenshot: an earlier chart answer kept in the open conversation is re-rendered as
    stored (its own close and date) - it is not read again. `at` goes with it; the page shows "Asked earlier · <when>"."""
    user = _user("cpfix-kept@example.com")
    old = {"job": "chart", "summary": ["NIFTY closed at 23,053.1 on 30 Oct."],
           "headline": {"symbol": "NIFTY", "price": 23053.10, "price_label": "Last close", "as_of": "2026-10-30", "trend": "down",
                        "area": {"side": "supply", "from": 23020.95, "to": 23162.70, "where": "inside", "distance": 0.0}}}

    async def go():
        async with _session_factory() as session:
            convo = await chat_history.remember(session, user.tenant_id, user.id, "Read the NIFTY chart", old, new=True)
            await session.commit()
        async with _session_factory() as session:
            turns = await chat_history.recent(session, user.tenant_id, user.id)
            current = await chat_history.current(session, user.tenant_id, user.id)
            await session.execute(delete(CopilotChatTurnRecord).where(CopilotChatTurnRecord.user_id == user.id))
            await session.execute(delete(CopilotChatSessionRecord).where(CopilotChatSessionRecord.user_id == user.id))
            await session.commit()
        return convo, turns, current
    convo, turns, current = _run(go())
    assert current == convo
    assert [t["question"] for t in turns] == ["Read the NIFTY chart"]
    assert turns[0]["answer"]["headline"] == old["headline"]                           # as stored: 30 Oct, 23,053.10
    assert turns[0]["at"]                                                              # when it was asked
