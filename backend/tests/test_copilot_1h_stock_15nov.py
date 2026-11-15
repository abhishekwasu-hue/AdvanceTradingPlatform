"""Owner 15 Nov ("Copilot mnto 1 hr data nahi"): asked to read a stock chart on 1H, the Copilot said there is no 1-hour data.

Root cause (code): `get_chart_structure` reads `price_action_layer`, whose bars are the shared lake (EOD exchange files)
and the reader's own private store - and ATP's server stores intraday bars only for indices (the history owner's own
index rows, M86 D4). A stock's 1-hour / 15-minute frame was therefore empty, and the tool answered "1-hour: data not
ready". The Charts page draws the same 1H chart from the user's OWN broker session (Tier A). The fix reads the short
intraday timeframes from that same own session (own credential only, inside its API budget), builds CLOSED buckets only
with the lake's one resampler (no look-ahead), and - when his broker cannot be read - says plainly why and which
timeframes were read instead.
"""
import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from app.ai import copilot_chat as chat
from app.ai import evidence
from app.ai.tools import ToolContext, ToolResult
from app.ai.tools import copilot as tools
from app.charts import price_action_layer as pal
from app.market_lake import own_bars

IST_OFFSET = timedelta(hours=5, minutes=30)
# Tuesday 10 Nov 2026, 11:40 IST: the 10:15-11:15 bucket has closed, the 11:15 one is still forming
NOW = datetime(2026, 11, 10, 6, 10, tzinfo=UTC)


def _run(coro):
    return asyncio.run(coro)


def _sessions(last: date, n: int) -> list[date]:
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _minutes(days: list[date], now: datetime, *, extra_outside: bool = True) -> pd.DataFrame:
    """1-minute START-stamped bars (UTC index) of whole sessions 09:15-15:29 IST, the last day up to `now` - plus, as a
    broker may send, a forming minute and a minute after the close (both must never reach a closed bar)."""
    rows, i = [], 0
    for d in days:
        start = datetime(d.year, d.month, d.day, 9, 15, tzinfo=UTC) - IST_OFFSET
        for m in range(375):
            ts = start + timedelta(minutes=m)
            if ts >= now:
                break
            price = 1000 + 30 * ((i // 40) % 2 and -1 or 1) * ((i % 40) / 40) + i * 0.01
            rows.append((ts, price, price + 1.5, price - 1.5, price + 0.3, 100.0))
            i += 1
        if extra_outside:
            close = datetime(d.year, d.month, d.day, 15, 45, tzinfo=UTC) - IST_OFFSET
            if close < now:
                rows.append((close, 9999.0, 9999.0, 9999.0, 9999.0, 1.0))   # a post-close minute: dropped
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).set_index("timestamp")
    df.index = pd.DatetimeIndex(df.index, tz=UTC)
    return df


def test_broker_frame_keeps_closed_session_buckets_only():
    days = _sessions(NOW.date(), 3)
    frame = tools.broker_frame(_minutes(days, NOW), "1h", NOW)
    ends = pd.to_datetime(frame["bar_end"], utc=True)
    assert (ends <= pd.Timestamp(NOW)).all()                             # no look-ahead: nothing ends after the read time
    assert frame["high"].max() < 9999                                    # the post-close minute never reaches a bar
    ist_starts = [t.tz_convert("Asia/Kolkata").strftime("%H:%M") for t in pd.to_datetime(frame["timestamp"], utc=True)]
    assert ist_starts[:7] == ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
    last_full_day_close = [e.tz_convert("Asia/Kolkata").strftime("%H:%M") for e in ends][6]
    assert last_full_day_close == "15:30"                                # the session's short last bucket ends at the close
    # today: 09:15 and 10:15 closed; the 11:15 bucket is forming at 11:40 IST and is left out
    assert ist_starts[-2:] == ["09:15", "10:15"]
    assert len(frame) == 2 * 7 + 2


def test_broker_frame_15m_and_empty():
    days = _sessions(NOW.date(), 2)
    frame = tools.broker_frame(_minutes(days, NOW), "15m", NOW)
    assert len(frame) == 25 + 9                                          # a full session, then 09:15 .. 11:15 today
    assert tools.broker_frame(pd.DataFrame(), "1h", NOW).empty


def _short_layer(tf: str, bars: int = 0) -> dict:
    rows = [{"tf": "1w", "bars": 150, "direction": "up"}, {"tf": "1d", "bars": 700, "direction": "up"},
            {"tf": "1h", "bars": 0, "direction": "none"}, {"tf": "15m", "bars": 0, "direction": "none"}]
    return {"instrument": "NSE:RELCHEMQ", "tf": tf, "bars": bars, "as_of": None, "last_close": None, "source": None,
            "reason": pal.NO_BARS, "mtf": rows, "levels": [], "structure_state": None}


def _ctx():
    return ToolContext(None, 1, SimpleNamespace(id=7, tenant_id=1), NOW)       # type: ignore[arg-type]


def test_fill_intraday_reads_his_own_broker_bars(monkeypatch):
    calls = []

    async def own(ctx, symbol):
        calls.append(symbol)
        return _minutes(_sessions(NOW.date(), 20), NOW), "broker:upstox"
    monkeypatch.setattr(tools, "own_minute_bars", own)
    layer, note = _run(tools.fill_intraday(_ctx(), "RELCHEMQ", "1h", _short_layer("1h")))
    assert note is None and calls == ["RELCHEMQ"]                         # one broker read serves both intraday rows
    assert layer["source"] == "broker:upstox" and layer["reason"] is None
    assert layer["bars"] >= tools.STRUCTURE_MIN_BARS and layer["tf"] == "1h"
    assert pd.Timestamp(layer["as_of"]) <= pd.Timestamp(NOW)
    rows = {r["tf"]: r for r in layer["mtf"]}
    assert rows["1h"]["bars"] >= tools.STRUCTURE_MIN_BARS and rows["15m"]["bars"] > rows["1h"]["bars"]
    assert rows["1h"]["source"] == "broker:upstox"
    assert rows["1d"] == {"tf": "1d", "bars": 700, "direction": "up"}  # the stored daily row is untouched


def test_no_broker_call_when_the_store_holds_the_intraday_bars(monkeypatch):
    async def own(ctx, symbol):
        raise AssertionError("no broker call when nothing is short")
    monkeypatch.setattr(tools, "own_minute_bars", own)
    layer = _short_layer("1d", bars=700)
    for r in layer["mtf"]:
        r["bars"] = 500
    out, note = _run(tools.fill_intraday(_ctx(), "NIFTY", "1d", layer))
    assert out is layer and note is None


def test_without_his_broker_the_answer_says_why_and_what_was_read(monkeypatch):
    async def own(ctx, symbol):
        raise HTTPException(status_code=409, detail="No usable broker session today. Connect it on the Broker page.")
    monkeypatch.setattr(tools, "own_minute_bars", own)
    layer, note = _run(tools.fill_intraday(_ctx(), "relchemq", "1h", _short_layer("1h")))
    assert layer["bars"] == 0
    assert note == ("1-hour / 15-minute bars of RELCHEMQ are not stored on ATP's server (it stores intraday history for "
                    "indices) and could not be read from your own broker: No usable broker session today. Connect it on "
                    "the Broker page")


def test_an_exception_class_never_reaches_the_answer(monkeypatch):
    async def own(ctx, symbol):
        raise HTTPException(status_code=502, detail="Could not fetch RELCHEMQ candles from upstox: BudgetUsed: no token")
    monkeypatch.setattr(tools, "own_minute_bars", own)
    _layer, note = _run(tools.fill_intraday(_ctx(), "RELCHEMQ", "1h", _short_layer("1h")))
    assert "BudgetUsed" not in note and note.endswith("your broker did not answer just now")


def _tool(monkeypatch, own):
    async def layer(**kw):
        return _short_layer(kw["tf"])

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(pal, "price_action_layer", layer)
    monkeypatch.setattr(own_bars, "official_close", nothing)
    monkeypatch.setattr(own_bars, "scope_for", nothing)
    monkeypatch.setattr(tools, "own_minute_bars", own)
    return _run(tools.get_chart_structure(_ctx(), tools._ChartArgs(symbol="RELCHEMQ", timeframe="1h")))


def test_chart_structure_on_1h_for_a_stock_reads_the_broker_bars(monkeypatch):
    async def own(ctx, symbol):
        return _minutes(_sessions(NOW.date(), 20), NOW), "broker:upstox"
    res = _tool(monkeypatch, own)
    assert res.ok and res.source == "price_action_layer:broker:upstox"
    assert res.data["timeframe"] == "1h" and res.data["bars"] >= tools.STRUCTURE_MIN_BARS
    one_hour = next(r for r in res.data["timeframes"] if r["tf"] == "1h")
    assert not one_hour["short"]


def test_chart_structure_without_broker_says_plainly_why(monkeypatch):
    async def own(ctx, symbol):
        raise HTTPException(status_code=409, detail="No usable broker session today")
    res = _tool(monkeypatch, own)
    assert not res.ok
    assert res.error.startswith("1-hour: data not ready")
    assert "not stored on ATP's server" in res.error and res.error.endswith(tools.ASK_DAILY)
    # the chat answer carries the plain reason (no internal words)
    reason = chat.plain_reason(res.error, res.source)
    assert "not stored on ATP's server" in reason and "None" not in reason


def test_chat_head_names_the_reason_when_the_read_tf_is_short():
    data = {"symbol": "RELCHEMQ", "timeframe": "1h", "bars": 400, "last_close": 1000.0, "as_of": NOW.isoformat(),
            "official_close": None, "chart_state": None, "levels": [], "intraday_note": "X not stored. Y",
            "timeframes": [{"tf": "1h", "short": True, "note": "1-hour: data not ready (more history is needed)", "bars": 3}]}
    res = ToolResult(True, data, NOW.isoformat(), "price_action_layer:lake")
    zones = ToolResult(False, None, None, "market_zones", error="no zones for this instrument")

    async def run(tool, args):
        return {"get_chart_structure": res, "get_market_zones": zones}[tool]
    out = _run(chat.answer("Read the RELCHEMQ chart on 1h", run))
    assert any("X not stored. Y." in line for line in out.summary), out.summary


def test_server_frame_budgets_every_broker_call(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import api_budget, candles_routes, intervals, live_access
    seen = {}

    async def off(*a, **k):
        return False

    async def pick(session, tenant_id, broker, kind, **kw):
        seen["scope"] = kw
        return SimpleNamespace(id=11, broker_name="upstox")

    async def bars(service, symbol, exchange, timeframe, **kw):
        seen["broker"] = service.broker
        return []
    monkeypatch.setattr(live_access, "byob_on", off)
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: SimpleNamespace(name="upstox"))
    monkeypatch.setattr(intervals, "get_bars", bars)
    user = SimpleNamespace(id=7, tenant_id=1)
    with pytest.raises(HTTPException):
        _run(evidence.server_frame(None, 1, "RELCHEMQ", "NSE", "1min", user=user, own_only=True, budgeted=True))
    assert isinstance(seen["broker"], api_budget.BudgetedAdapter) and seen["scope"] == {"user_id": 7}
    with pytest.raises(HTTPException):
        _run(evidence.server_frame(None, 1, "RELCHEMQ", "NSE", "1min", user=user, own_only=True))
    assert not isinstance(seen["broker"], api_budget.BudgetedAdapter)                 # unchanged for every other caller
