"""N4 CH4a: the trades layer (GET /api/charts/layers/trades) - the caller's own PAPER trades on a chart, as of a time.

* own trades only: never a teammate's (same organisation) nor another organisation's; never a LIVE row;
* the instrument: its own trades at their prices; derived contracts (an option on it) at their times only;
* as of `at`: a trade entered later is not shown, an exit after `at` is not shown (open then, no P&L) - replay shows
  nothing before it happened; the window keeps trades active in (start, at];
* flag `chart_trades_layer`: off by default (503), per organisation; read-only, it never writes a row.
No broker, no order: the rows are written directly as a PAPER fill would store them.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.charts import trades_layer as T
from app.db.models import TradeRecord
from app.platform import controls
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_z10c_tenancy_fixes import _member

URL = "/api/charts/layers/trades"
T0 = datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _flag(on, tenants=()):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            stored[T.FLAG] = {"on": on, "tenants": list(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def _trade(tenant_id, user_id, symbol="CH4ATEST", *, hours=0, exit_hours=None, mode="PAPER", direction="LONG", price=100.0, **extra):
    entry = T0 + timedelta(hours=hours)
    return TradeRecord(tenant_id=tenant_id, user_id=user_id, mode=mode, symbol=symbol, strategy_id="manual", direction=direction,
                       entry_time=entry, entry_price=price, quantity=10, stop_loss=price * 0.98, target1=price * 1.04,
                       exit_time=None if exit_hours is None else T0 + timedelta(hours=exit_hours),
                       exit_price=None if exit_hours is None else price * 1.02, exit_reason=None if exit_hours is None else "TARGET",
                       pnl=None if exit_hours is None else 20.0, **extra)


def _put(rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _uid(email):
    return client.get("/api/auth/me", headers=email).json()["id"]


@pytest.fixture
def world():
    """An owner with trades, a teammate with trades on the same symbol, another organisation with trades on it."""
    _flag(True)
    tag = uuid.uuid4().hex[:8]
    owner, me = _owner(f"ch4a-{tag}@example.com")
    mate = _member(f"ch4a-mate-{tag}@example.com", me["tenant_id"], "USER")
    other, them = _owner(f"ch4a-other-{tag}@example.com")
    tid, uid = me["tenant_id"], me["id"]
    _put([
        _trade(tid, uid, hours=0, exit_hours=30, exchange="NSE"),                       # closed
        _trade(tid, uid, hours=50, direction="SHORT", price=110.0),                    # open
        _trade(tid, uid, hours=70, mode="LIVE"),                                        # never drawn (not PAPER)
        _trade(tid, uid, "CH4ATEST26OCT100CE", hours=5, exit_hours=8, price=4.5, instrument_kind="OPTION",
               underlying_symbol="CH4ATEST", expiry=date(2026, 10, 27), option_position="LONG", lot_size=50),
        _trade(tid, uid, "OTHERSYM", hours=1),                                          # another instrument
        _trade(tid, uid, hours=2, exchange="BSE"),                                      # the same name on another exchange
        _trade(tid, _uid(mate), hours=3),                                               # a teammate's
        _trade(them["tenant_id"], them["id"], hours=4),                                 # another organisation's
    ])
    yield owner, mate, other
    _flag(False)


def _get(headers, **params):
    r = client.get(URL, params={"instrument": "NSE:CH4ATEST", "start": (T0 - timedelta(days=1)).isoformat(),
                                "at": (T0 + timedelta(days=5)).isoformat(), **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_only_the_callers_own_paper_trades_on_this_instrument(world):
    owner, mate, other = world
    body = _get(owner)
    assert body["schema"] == "trades/1" and body["mode"] == "PAPER" and "places no orders" in body["note"]
    syms = sorted((t["symbol"], t["side"], t["on_price_axis"]) for t in body["trades"])
    assert syms == [("CH4ATEST", "long", True), ("CH4ATEST", "short", True), ("CH4ATEST26OCT100CE", "long", False)]
    assert body["open"] == 1
    closed = next(t for t in body["trades"] if t["symbol"] == "CH4ATEST" and t["side"] == "long")
    assert closed["exit"]["price"] == pytest.approx(102.0) and closed["pnl"] == 20.0 and closed["stop_loss"] is None
    short = next(t for t in body["trades"] if t["side"] == "short")
    assert short["open"] and short["exit"] is None and short["stop_loss"] == pytest.approx(107.8) and short["target"] == pytest.approx(114.4)
    option = next(t for t in body["trades"] if not t["on_price_axis"])
    assert option["contract"] == {"symbol": "CH4ATEST26OCT100CE", "kind": "OPTION", "expiry": "2026-10-27", "position": "LONG", "lot_size": 50}
    assert option["stop_loss"] is None and option["target"] is None                     # a premium level never on the underlying's axis
    assert _get(mate)["trades"] and all(t["entry"]["time"] == int((T0 + timedelta(hours=3)).timestamp()) for t in _get(mate)["trades"])
    assert [t["entry"]["time"] for t in _get(other)["trades"]] == [int((T0 + timedelta(hours=4)).timestamp())]
    on_contract = _get(owner, instrument="NFO:CH4ATEST26OCT100CE")["trades"]           # the contract's own chart: its premium
    assert [(t["symbol"], t["on_price_axis"]) for t in on_contract] == [("CH4ATEST26OCT100CE", True)]


def test_as_of_no_trade_and_no_exit_before_it_happened(world):
    owner, _, _ = world
    mid = _get(owner, at=(T0 + timedelta(hours=10)).isoformat())                       # before the long's exit, before the short
    assert sorted(t["side"] for t in mid["trades"] if t["on_price_axis"]) == ["long"]
    long = next(t for t in mid["trades"] if t["on_price_axis"])
    assert long["open"] and long["exit"] is None and long["pnl"] is None and long["stop_loss"] == pytest.approx(98.0)
    assert next(t for t in mid["trades"] if not t["on_price_axis"])["exit"] is not None  # the option closed at +8h, before `at`
    window = _get(owner, start=(T0 + timedelta(hours=40)).isoformat())                 # the long closed before the window
    assert [t["side"] for t in window["trades"]] == ["short"]


def test_flag_refusals_and_the_layer_writes_nothing(world):
    owner, _, _ = world

    async def count():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(TradeRecord))
    before = _run(count())
    assert client.get(URL, params={"instrument": "NSE:CH4ATEST", "at": "2026-09-01T10:00:00"}, headers=owner).status_code == 422
    assert client.get(URL, params={"instrument": "NSE:CH4ATEST", "at": T0.isoformat(), "start": (T0 + timedelta(hours=1)).isoformat()},
                      headers=owner).status_code == 422
    assert client.get(URL, params={"instrument": "NSE:CH4ATEST"}).status_code == 401
    _flag(False)
    assert client.get(URL, params={"instrument": "NSE:CH4ATEST"}, headers=owner).status_code == 503
    assert _run(count()) == before
    assert T.FLAG in controls.DEFAULT_OFF_FLAGS and "places no orders" in controls.FEATURE_FLAGS[T.FLAG]


def test_the_flag_is_per_organisation():
    headers, me = _owner(f"ch4a-flag-{uuid.uuid4().hex[:8]}@example.com")
    other, _ = _owner(f"ch4a-flag-b-{uuid.uuid4().hex[:8]}@example.com")
    _flag(False, tenants=[me["tenant_id"]])
    try:
        assert client.get(URL, params={"instrument": "NSE:X"}, headers=headers).status_code == 200
        assert client.get(URL, params={"instrument": "NSE:X"}, headers=other).status_code == 503
    finally:
        _flag(False)


def test_split_instrument():
    assert T.split_instrument(" nse:reliance ") == ("NSE", "RELIANCE")
    assert T.split_instrument("NIFTY  50") == (None, "NIFTY 50")


def _me(headers):
    return client.get("/api/auth/me", headers=headers).json()


def test_replay_draws_the_initial_stop_not_a_stop_trailed_after_at(world):
    """position_monitor overwrites `stop_loss` as the stop trails: as of a past `at` the row's stop is a later one."""
    owner, _, _ = world
    me = _me(owner)
    trailed = _trade(me["tenant_id"], me["id"], "CH4ATRAIL", hours=1, price=100.0)
    trailed.initial_stop_loss, trailed.stop_loss = 97.0, 101.5               # trailed above the entry later in the week
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    live = _trade(me["tenant_id"], me["id"], "CH4ATRAIL", price=100.0)
    live.entry_time, live.initial_stop_loss, live.stop_loss = recent, 96.0, 99.0
    legacy = _trade(me["tenant_id"], me["id"], "CH4ATRAIL", hours=2, price=200.0)   # no initial stop recorded: its stop
    _put([trailed, live, legacy])
    past = _get(owner, instrument="NSE:CH4ATRAIL", at=(T0 + timedelta(hours=3)).isoformat())["trades"]
    by_price = {t["entry"]["price"]: t for t in past}
    assert by_price[100.0]["stop_loss"] == pytest.approx(97.0) and by_price[100.0]["stop_label"] == "initial stop"
    assert by_price[200.0]["stop_loss"] == pytest.approx(196.0) and by_price[200.0]["stop_label"] == "initial stop"
    r = client.get(URL, params={"instrument": "NSE:CH4ATRAIL"}, headers=owner)          # live: the stop in force now
    assert r.status_code == 200, r.text
    by_entry = {t["entry"]["time"]: t for t in r.json()["trades"]}                  # all three are still open now
    assert by_entry[int(recent.timestamp())]["stop_loss"] == pytest.approx(99.0) and by_entry[int(recent.timestamp())]["stop_label"] == "stop"
    assert by_entry[int((T0 + timedelta(hours=1)).timestamp())]["stop_loss"] == pytest.approx(101.5)


def test_the_exchange_filter_is_applied_before_the_cap(world, monkeypatch):
    owner, _, _ = world
    monkeypatch.setattr(T, "MAX_TRADES", 3)
    body = _get(owner)                                       # newest first: short, option, (BSE row), closed long
    assert len(body["trades"]) == 3 and body["capped"] is True
    assert sorted(t["symbol"] for t in body["trades"]) == ["CH4ATEST", "CH4ATEST", "CH4ATEST26OCT100CE"]


def test_an_index_chart_shows_option_trades_named_by_the_fo_underlying(world):
    owner, _, _ = world
    me = _me(owner)
    _put([_trade(me["tenant_id"], me["id"], "NIFTY26OCT25000CE", hours=6, price=120.0, instrument_kind="OPTION",
                 underlying_symbol="NIFTY", exchange="NFO", expiry=date(2026, 10, 27), option_position="LONG", lot_size=75)])
    trades = _get(owner, instrument="NSE:NIFTY 50")["trades"]
    assert [(t["symbol"], t["on_price_axis"]) for t in trades] == [("NIFTY26OCT25000CE", False)]
    assert T.underlying_names("NIFTY 50") == ["NIFTY", "NIFTY 50"] and T.underlying_names("RELIANCE") == ["RELIANCE"]
