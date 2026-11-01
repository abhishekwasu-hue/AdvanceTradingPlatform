"""M71 W2-G: the rest of the Screener family reads the own store first (flag `market_store_reads`): breakouts, library
screens, ranking and the Copilot screener tool (explain_results; run_screen is a stored Screener run, PL-3).

Flag on: `fetch_frames(store=True)` and every broker call that path still makes (the newest bars, a symbol not stored)
takes a token from the credential's API budget first (`budget_out`, api_budget.BudgetedAdapter - the paced adapter).
Flag off: exactly as before (store off, no budget). Synthetic store rows and a spy broker only - no network.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.tools import ToolContext, run_tool
from app.ai.tools import screener as COPILOT_SCREENER
from app.core.models import OHLCVBar
from app.db.models import User
from app.market_data import api_budget
from app.market_data.calendar import IST
from app.market_store import models as M
from app.market_store import reader as R
from app.screener import library_routes, ranking_routes
from app.screener import routes as SR
from app.screener.breakouts import routes as BR
from tests.test_auth_api import _session_factory, client
from tests.test_m71_pl3_read_service import Spy, _clear, _minutes, _rest, _run, _store_minutes, _weekdays
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant

NIFTY = [f"M71G{i:02d}" for i in range(50)]


@pytest.fixture(autouse=True)
def _fresh():
    R.reset_cache()
    api_budget.reset()
    yield
    R.reset_cache()


def _org(tag, store):
    t = _tenant(f"m71-w2g-{tag}@example.com", with_credentials=False)
    for name in ("screener_v2", COPILOT_SCREENER.FLAG, ranking_routes.RK.FLAG):
        _flag(name, t["tenant_id"])
    _flag(M.FLAG, t["tenant_id"], on=store)
    return t


class _Recorder:
    """Stands in for fetch_frames / load_universe: keeps the keyword arguments, then answers "no session" (409)."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        raise HTTPException(status_code=409, detail="connect a broker")


def _user_of(t):
    return SimpleNamespace(id=t["user_id"], tenant_id=t["tenant_id"])


def _session_call(fn):
    async def go():
        async with _session_factory() as session:
            return await fn(session)
    return _run(go())


# --- the call sites pass the flag through (and a budget only with the flag on) ---------------------------------------------

@pytest.mark.parametrize("store", [False, True])
def test_breakouts_library_ranking_and_the_copilot_tool_pass_the_store_flag(monkeypatch, store):
    t = _org(f"wire-{int(store)}-{datetime.now(UTC).timestamp():.0f}", store)
    rec = _Recorder()
    monkeypatch.setattr(BR, "fetch_frames", rec)
    monkeypatch.setattr(library_routes, "fetch_frames", rec)
    monkeypatch.setattr(ranking_routes, "fetch_frames", rec)
    monkeypatch.setattr(SR, "load_universe", rec)
    user = _user_of(t)
    for call in (lambda s: BR._frames(s, user, ["A"], "NSE", "1d", ("1w",)),
                 lambda s: BR._frames(s, user, ["A"], "NSE", "1h", ()),
                 lambda s: library_routes._universe(s, user, ["A"], "NSE", "1d", {"1d": 50})):
        with pytest.raises(HTTPException):
            _session_call(call)
    ranked = client.post("/api/screener/rankings/run", headers=t["headers"], json={"system": "platform:Momentum", "symbols": ["A"]})
    assert ranked.status_code == 409, ranked.text

    async def explain(session):
        me = await session.get(User, t["user_id"])
        return await run_tool("explain_results", {"text": "close > SMA(close, 20)", "symbols": ["A"]},
                              ToolContext(session, me.tenant_id, me, datetime.now(UTC)))
    res = _session_call(explain)
    assert not res.ok and "connect a broker" in res.error
    assert len(rec.calls) == 5                                   # 2 breakouts, library, ranking, the Copilot tool
    assert all(c["store"] is store for c in rec.calls)
    budgets = [c.get("budget_out", c.get("budget")) for c in rec.calls]
    assert all((b == {}) if store else (b is None) for b in budgets)


# --- the store behind the family -------------------------------------------------------------------------------------------

def _stocked(user_id, tenant_id, symbols):
    async def calendar(session):
        return await R.holidays_for(session, "NSE")
    hol = _session_call(calendar)
    end = R.last_closed_end(datetime.now(UTC), 1, hol)
    days = [d for d in _weekdays(4, end.astimezone(IST).date()) if d not in hol][-2:]
    rows = [r for d in days for r in _minutes(d, end.astimezone(IST))]
    for s in symbols:
        _store_minutes(f"NSE:{s}", rows, tenant_id, f"broker:upstox:u{user_id}")


@pytest.fixture
def stocked():
    t = _org(f"store-{datetime.now(UTC).timestamp():.0f}", True)
    _stocked(t["user_id"], t["tenant_id"], NIFTY)
    yield t
    _clear(*[f"NSE:{s}" for s in NIFTY])


def test_a_nifty_50_1h_breakout_and_library_read_has_no_unread_row_and_a_second_run_makes_zero_history_calls(monkeypatch, stocked):
    t = stocked
    spy = Spy(upto=datetime.now(UTC))
    rest = _rest(spy)

    async def own_rest(session, tenant_id, user, symbols, exchange, wait_seconds):
        return rest, set(symbols)                                # the reader's own session (M71Q-2), a spy
    monkeypatch.setattr(SR, "_store_rest", own_rest)
    user = _user_of(t)
    frames, problems, source = _session_call(lambda s: BR._frames(s, user, NIFTY, "NSE", "1h", (), bars=6))
    assert not problems and sorted(frames) == NIFTY and source.startswith("lake:intraday")
    assert all(len(f["1h"]) > 0 for f in frames.values())
    first = spy.history
    assert first <= len(NIFTY)                                   # at most the newest bars, once per symbol - never the history
    universe, lib_problems, meta = _session_call(lambda s: library_routes._universe(s, user, NIFTY, "NSE", "1h", {"1h": 6}))
    again, again_problems, _ = _session_call(lambda s: BR._frames(s, user, NIFTY, "NSE", "1h", (), bars=6))
    assert not lib_problems and len(universe) == len(NIFTY) and meta["data_source"].startswith("lake:intraday")
    assert not again_problems and spy.history == first           # the second run: zero history calls
    assert all(again[s]["1h"].equals(frames[s]["1h"]) for s in NIFTY)


def test_a_symbol_not_stored_is_a_plain_gap_row_never_zero_filled(monkeypatch, stocked):
    t = stocked

    async def no_rest(session, tenant_id, user, symbols, exchange, wait_seconds):
        return None, set()
    monkeypatch.setattr(SR, "_store_rest", no_rest)
    user = _user_of(t)
    frames, problems, _ = _session_call(lambda s: BR._frames(s, user, [NIFTY[0], "M71GNONE"], "NSE", "1h", (), bars=6))
    assert list(frames) == [NIFTY[0]] and "M71GNONE" in problems     # reported, no frame made up for it
    f = frames[NIFTY[0]]["1h"]
    assert (f[["open", "high", "low", "close"]] > 0).all().all()


def test_the_same_bars_give_the_same_frames_through_the_store_and_the_broker(monkeypatch, stocked):
    """Same bars -> same scan: the store's 1h frame handed to the broker path (flag off) as its bars gives the same frame."""
    t = stocked

    async def no_rest(session, tenant_id, user, symbols, exchange, wait_seconds):
        return None, set()
    monkeypatch.setattr(SR, "_store_rest", no_rest)
    user = _user_of(t)
    sym = NIFTY[1]
    stored, _, _ = _session_call(lambda s: BR._frames(s, user, [sym], "NSE", "1h", (), bars=6))
    frame = stored[sym]["1h"]
    bars = [OHLCVBar(timestamp=ts.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
            for ts, r in frame.iterrows()]
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label, **_k):
        return SimpleNamespace(id=1, broker_name="upstox")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None, fresh=False):
        return list(bars)
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: SimpleNamespace(name="upstox"))
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)
    _flag(M.FLAG, t["tenant_id"], on=False)
    broker, problems, source = _session_call(lambda s: BR._frames(s, user, [sym], "NSE", "1h", (), bars=6))
    assert not problems and source == "broker:upstox"
    got = broker[sym]["1h"]
    common = got.index.intersection(frame.index)
    assert len(common) == len(frame)
    assert got.loc[common, ["open", "high", "low", "close", "volume"]].equals(frame.loc[common, ["open", "high", "low", "close", "volume"]])


def test_a_daily_ranking_on_the_store_makes_no_broker_call(monkeypatch):
    """Ranking / daily breakouts with the flag on: the NSE daily files, no broker session needed (zero calls)."""
    from tests.test_m71_pl3_read_service import _store_days
    t = _org(f"rank-{datetime.now(UTC).timestamp():.0f}", True)
    today = datetime.now(UTC).astimezone(IST).date()
    keys = {"M71GUP": 1.0, "M71GDN": -1.0}
    for s in keys:
        _store_days(f"NSE:{s}", _weekdays(300, today - timedelta(days=1)))

    async def no_broker(*a, **k):
        raise AssertionError("the broker must not be called for Daily bars on the store")
    from app.market_data import candles_routes
    monkeypatch.setattr(candles_routes, "_pick_record", no_broker)
    try:
        r = client.post("/api/screener/rankings/run", headers=t["headers"], json={"system": "platform:Momentum", "symbols": list(keys)})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["data_source"] == "lake:daily" and len(out["ranked"]) == 2
        frames, problems, source = _session_call(lambda s: BR._frames(s, _user_of(t), list(keys), "NSE", "1d", ("1w",)))
        assert not problems and source == "lake:daily" and set(frames) == set(keys)
    finally:
        _clear(*[f"NSE:{s}" for s in keys])
