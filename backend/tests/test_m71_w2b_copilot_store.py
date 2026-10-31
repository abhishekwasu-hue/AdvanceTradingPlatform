"""M71 W2-B: the Copilot / AI history reads go through the own store (app/market_store) when the flag
`market_store_reads` is on; OFF = exactly today's broker reads.

Covered: the candles / regime tools, the strategist's frames, the market memory's daily cues. Synthetic store rows and
a spy broker only - no network. A second question on the same symbols makes ZERO history calls; what the store does
not hold is said plainly (`data_gaps`), never zero-filled; another member's broker bars are never read (M71Q-2).
"""
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai import evidence, market_memory
from app.ai.routes import StrategistBody, _strategist_frames
from app.ai.tools import ToolContext, market, run_tool
from app.ai.tools.copilot import lake_key
from app.market_data import api_budget
from app.market_data.calendar import IST
from app.market_store import models as M
from app.market_store import reader as R
from app.market_store import sources
from tests.test_auth_api import _session_factory
from tests.test_copilot_v2_chat import _flag
from tests.test_deployments_api import _auth
from tests.test_m71_pl3_read_service import DAY, Spy, _clear, _minutes, _rest, _run, _store_days, _store_minutes, _user, _weekdays

NOW = datetime.combine(DAY, time(13, 5), IST).astimezone(UTC)          # a trading session, mid-day


@pytest.fixture(autouse=True)
def _fresh():
    R.reset_cache()
    api_budget.reset()
    yield
    R.reset_cache()
    _run(_flag(M.FLAG, False))


def _reader(email):
    return _user(_auth(email), role="OWNER")


def _ctx_call(user, name, args, now=NOW):
    async def go():
        async with _session_factory() as session:
            return await run_tool(name, args, ToolContext(session, user.tenant_id, user, now))
    return _run(go())


def _broker_spy(monkeypatch):
    """`market._frame` (today's broker read) replaced by a counter that fails like a missing session."""
    seen = []

    async def frame(ctx, symbol, exchange, timeframe, days):
        seen.append((symbol, timeframe))
        raise HTTPException(status_code=409, detail="No usable broker session - connect your broker")
    monkeypatch.setattr(market, "_frame", frame)
    return seen


def _own_rest(monkeypatch, spy):
    rest = _rest(spy)
    calls = []

    async def own(session, user, symbol, exchange, *, broker=None):
        calls.append(symbol)
        return rest
    monkeypatch.setattr(sources, "own_rest", own)
    return calls


def _intraday(user, sym, upto):
    """The reader's own stored 1-minute rows of the last sessions up to `upto` (his own broker session's label)."""
    rows = []
    for d in _weekdays(4, DAY):
        rows += _minutes(d, upto if d == DAY else datetime.combine(d, time(15, 30), IST))
    _store_minutes(f"NSE:{sym}", rows, user.tenant_id, f"broker:upstox:u{user.id}")


# --- flag OFF: today's behaviour ---------------------------------------------------------------------------------------------

def test_flag_off_reads_the_broker_exactly_as_before(monkeypatch):
    user = _reader("m71-w2b-off@example.com")
    _store_days("NSE:W2BOFF", _weekdays(300, DAY - timedelta(days=1)))
    seen = _broker_spy(monkeypatch)
    res = _ctx_call(user, "get_candles", {"symbol": "W2BOFF", "timeframe": "day"})
    assert not res.ok and "broker session" in res.error and seen == [("W2BOFF", "day")]   # the store is not asked
    _clear("NSE:W2BOFF")


# --- flag ON: Daily from the NSE files, zero broker calls ---------------------------------------------------------------------

def test_daily_candles_and_regime_come_from_the_nse_files(monkeypatch):
    user = _reader("m71-w2b-daily@example.com")
    _store_days("NSE:W2BDAY", _weekdays(300, DAY - timedelta(days=1)))
    seen = _broker_spy(monkeypatch)
    _run(_flag(M.FLAG, True))
    for name in ("get_candles", "get_market_regime"):
        res = _ctx_call(user, name, {"symbol": "W2BDAY", "timeframe": "day"})
        assert res.ok, res.error
        assert res.source == "lake:daily" and "data_gaps" not in res.data
    assert seen == []                                                       # no broker read at all
    _clear("NSE:W2BDAY")


# --- flag ON: intraday = own store + only the newest bars; a repeat question = zero history calls -----------------------------

def test_intraday_tops_up_only_the_newest_bars_and_a_repeat_makes_zero_calls(monkeypatch):
    user = _reader("m71-w2b-intraday@example.com")
    _intraday(user, "W2BINT", datetime.combine(DAY, time(12, 0), IST))
    spy = Spy(DAY, upto=NOW)
    _own_rest(monkeypatch, spy)
    seen = _broker_spy(monkeypatch)
    _run(_flag(M.FLAG, True))
    first = _ctx_call(user, "get_candles", {"symbol": "W2BINT", "timeframe": "15min", "days": 5})
    assert first.ok, first.error
    assert first.source == "lake:intraday+broker:upstox" and seen == []
    assert 1 <= spy.calls <= 2 and spy.history == 0                         # the newest bars only, never the history
    calls = spy.calls
    again = _ctx_call(user, "get_market_regime", {"symbol": "W2BINT", "timeframe": "15min", "days": 5})
    again2 = _ctx_call(user, "get_candles", {"symbol": "W2BINT", "timeframe": "15min", "days": 5})
    assert again.ok and again2.ok and spy.calls == calls                     # second question: ZERO history calls
    assert again2.data["last_bars"] == first.data["last_bars"]
    _clear("NSE:W2BINT")


def test_missing_newest_bars_are_said_plainly_never_filled(monkeypatch):
    user = _reader("m71-w2b-gap@example.com")
    _intraday(user, "W2BGAP", datetime.combine(DAY, time(12, 0), IST))

    async def none(session, user, symbol, exchange, *, broker=None):
        return None                                                         # no own session for the newest bars
    monkeypatch.setattr(sources, "own_rest", none)
    _broker_spy(monkeypatch)
    _run(_flag(M.FLAG, True))
    res = _ctx_call(user, "get_candles", {"symbol": "W2BGAP", "timeframe": "15min", "days": 5})
    assert res.ok and res.source == "lake:intraday"
    assert res.data["data_gaps"] == [M.CONNECT_FOR_INTRADAY]
    last = datetime.fromisoformat(res.data["to"])
    assert last < datetime.combine(DAY, time(12, 0), IST)                   # ends at the store's last bar: no invented bars
    _clear("NSE:W2BGAP")


def test_nothing_stored_keeps_the_old_path_and_its_plain_error(monkeypatch):
    user = _reader("m71-w2b-cold@example.com")
    seen = _broker_spy(monkeypatch)
    _run(_flag(M.FLAG, True))
    res = _ctx_call(user, "get_candles", {"symbol": "W2BNONE", "timeframe": "5min"})
    assert not res.ok and res.data is None and "broker session" in res.error and seen == [("W2BNONE", "5min")]


def test_another_members_broker_bars_are_never_read(monkeypatch):
    owner = _reader("m71-w2b-owner@example.com")
    _intraday(owner, "W2BMINE", datetime.combine(DAY, time(12, 0), IST))
    member = SimpleNamespace(id=owner.id + 900_000, tenant_id=owner.tenant_id, role="TRADER")
    _own_rest(monkeypatch, Spy(DAY, upto=NOW))

    async def go(user):
        async with _session_factory() as session:
            return await evidence.store_frame(session, owner.tenant_id, "W2BMINE", "NSE", "15min", lookback_days=5, user=user, now=NOW)
    _run(_flag(M.FLAG, True))
    assert _run(go(owner)) is not None
    assert _run(go(member)) is None                                           # the owner's bars never reach the member
    stranger = SimpleNamespace(id=owner.id, tenant_id=owner.tenant_id + 1, role="OWNER")
    assert _run(go(stranger)) is None                                         # nor another organisation
    _clear("NSE:W2BMINE")


# --- the strategist's frames ------------------------------------------------------------------------------------------------

class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def test_strategist_reads_the_store_without_any_broker_and_repeats_with_zero_calls(monkeypatch):
    user = _reader("m71-w2b-strategist@example.com")
    _intraday(user, "W2BSTR", datetime.combine(DAY, time(12, 0), IST))
    _store_days("NSE:W2BSTR", _weekdays(300, DAY - timedelta(days=1)))
    spy = Spy(DAY, upto=NOW)
    _own_rest(monkeypatch, spy)
    monkeypatch.setattr(evidence, "datetime", _Frozen)
    body = StrategistBody(symbol="W2BSTR", exchange="NSE")

    async def go():
        async with _session_factory() as session:
            return await _strategist_frames(session, user, body)
    with pytest.raises(HTTPException):
        _run(go())                                                            # flag off: the broker path (no session -> 409)
    _run(_flag(M.FLAG, True))
    df, day, source = _run(go())
    assert source == "lake:intraday+broker:upstox" and len(day) > 250 and not df.empty
    assert df.index[-1] + timedelta(minutes=1) <= NOW                          # closed bars only
    assert "Older intraday history is not stored yet." in df.attrs["data_gaps"]  # said plainly, never filled
    calls = spy.calls
    assert calls <= 2 and spy.history == 0
    _run(go())
    assert spy.calls == calls                                                  # the same question again: zero history calls
    _clear("NSE:W2BSTR")


# --- the market memory's daily cues ------------------------------------------------------------------------------------------

class _CountingMD:
    calls: list = []

    def __init__(self, broker, lookback_days=5):
        self.lookback_days = lookback_days

    async def get_candles(self, symbol, exchange, interval, now=None):
        _CountingMD.calls.append((symbol, interval))
        return []


def test_market_memory_cues_read_the_nse_files_first():
    user = _reader("m71-w2b-memory@example.com")
    key = lake_key("NIFTY 50")
    _store_days(key, _weekdays(30, DAY - timedelta(days=1)))
    now = datetime.combine(DAY, time(16, 0), IST).astimezone(UTC)

    async def go():
        async with _session_factory() as session:
            return await market_memory.capture(session, user.tenant_id, _CountingMD, SimpleNamespace(name="upstox"), now=now,
                                               symbols=[], include_global=False)
    _CountingMD.calls = []
    _run(go())
    assert ("NIFTY 50", "day") in _CountingMD.calls                             # flag off: the broker, as before
    _run(_flag(M.FLAG, True))
    _CountingMD.calls = []
    report = _run(go())
    assert ("NIFTY 50", "day") not in _CountingMD.calls and report["cues"] >= 1   # the stored cue; no broker call for it
    assert ("SENSEX", "day") in _CountingMD.calls                                # not in the NSE files: the broker as before
    _clear(key)
