"""Owner order 23 Oct: backtests without a daily broker login (app/backtest/bar_source.py).

The broker session when usable (unchanged), else the shared lake's licensed Daily history (flag
`backtest_stored_history`), else a plain 409 saying what is missing - never sample data. Lake bars become the very frame a
broker's Daily candles give, so equal bars give an identical backtest result. The owner's private 1-minute store, intraday
bars and another organisation's broker-written bars are never read. Fake lake data only, no network.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.backtest import bar_source
from app.backtest.engine import run_backtest
from app.core import config
from app.core.models import OHLCVBar, RiskConfig, bars_to_dataframe
from app.db.models import MdCandleRecord, MdTenantCandleRecord, User
from app.market_data.calendar import IST
from app.market_lake import ingest
from app.market_lake.bhavcopy_backfill import bar_end
from app.strategy_engine.swing_strategies import SwingBreakout, SwingEmaPullback
from tests.server_evidence import serve_candles
from tests.test_auth_api import _session_factory, client
from tests.test_copilot_v2_chat import _flag
from tests.test_deployments_api import _auth, _me

SYM = "BSHTEST"
KEY = f"NSE:{SYM}"
RISK = RiskConfig(capital=1_000_000.0, risk_per_trade_pct=1.0)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def stored_on():
    _run(_flag("backtest_stored_history", True))
    yield
    _run(_flag("backtest_stored_history", False))


def _days(n: int, last: date):
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _prices(n: int):
    i = np.arange(n, dtype=float)
    close = 1000 + 120 * np.sin(i / 17.0) + 40 * np.sin(i / 5.0) + i * 1.5
    return close


def _rows(days):
    close = _prices(len(days))
    return [(d, float(c - 4), float(c + 9), float(c - 11), float(c), 10_000 + 7 * k) for k, (d, c) in enumerate(zip(days, close))]


def _store(rows, source="nse_bhav:cm", key=KEY, tenant_id=None):
    async def go():
        async with _session_factory() as session:
            model = MdTenantCandleRecord if tenant_id is not None else MdCandleRecord
            await session.execute(delete(model).where(model.instrument_key == key))
            await session.commit()
            bars = [ingest.LakeBar(key, "day", bar_end(d), o, h, lo, c, v) for d, o, h, lo, c, v in rows]
            await ingest.write_candles_bulk(session, bars, source, tenant_id=tenant_id)
            await session.commit()
    _run(go())


def _clear(key=KEY):
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == key))
            await session.execute(delete(MdTenantCandleRecord).where(MdTenantCandleRecord.instrument_key == key))
            await session.commit()
    _run(go())


def _broker_bars(rows):
    """What a broker's Daily candles look like: the trade date at 00:00 India time, as an ISO string with the offset."""
    return [OHLCVBar.model_validate({"timestamp": f"{d.isoformat()}T00:00:00+05:30", "open": o, "high": h, "low": lo, "close": c,
                                     "volume": float(v)}) for d, o, h, lo, c, v in rows]


def _resolve(tenant_id, timeframe="day", symbol=SYM, exchange="NSE", lookback=400, now=None):
    async def go():
        async with _session_factory() as session:
            return await bar_source.resolve(session, tenant_id, symbol, exchange, timeframe, lookback_days=lookback, now=now)
    return _run(go())


def _today():
    return datetime.now(timezone.utc).astimezone(IST).date()


# --- parity: equal bars give an identical frame and an identical result -------------------------------------------------

def test_lake_bars_make_the_same_frame_and_the_same_backtest_as_equal_broker_bars():
    rows = _rows(_days(320, date(2026, 6, 30)))
    lake_rows = [ingest.LakeBar(KEY, "day", bar_end(d), o, h, lo, c, v) for d, o, h, lo, c, v in rows]
    from_lake = bar_source.to_frame(lake_rows)
    from_broker = bars_to_dataframe(_broker_bars(rows))
    pd.testing.assert_frame_equal(from_lake, from_broker)
    traded = 0
    for strategy in (SwingBreakout(), SwingEmaPullback()):
        a = run_backtest(strategy, from_lake, SYM, "day", RISK)
        b = run_backtest(strategy, from_broker, SYM, "day", RISK)
        assert a.model_dump() == b.model_dump()
        traded += a.total_trades
    assert traded > 0                                     # the parity is checked on real trades, not on an empty run


def test_the_resolved_lake_frame_equals_the_broker_frame_for_the_same_window(stored_on):
    me = _me(_auth("bsh-parity@example.com"))
    days = _days(300, _today() - timedelta(days=1))
    rows = _rows(days)
    _store(rows)
    got = _resolve(me["tenant_id"])
    assert got.kind == "stored" and got.source == bar_source.LAKE_SOURCE
    first, last = bar_source.window(400)
    want = bars_to_dataframe(_broker_bars([r for r in rows if first <= r[0] <= last]))
    pd.testing.assert_frame_equal(got.frame, want)
    _clear()


# --- the order of sources ------------------------------------------------------------------------------------------------

def test_a_usable_broker_session_is_used_exactly_as_before_even_with_stored_history(monkeypatch, stored_on):
    me = _me(_auth("bsh-broker@example.com"))
    _store(_rows(_days(50, _today() - timedelta(days=1))))
    candles = [{"timestamp": f"2026-07-{d:02d}T00:00:00+05:30", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1} for d in range(1, 20)]
    calls = serve_candles(monkeypatch, candles, "broker:upstox_analytics")
    got = _resolve(me["tenant_id"])
    assert calls and got.kind == "broker" and got.source == "broker:upstox_analytics" and len(got.frame) == 19
    assert got.info()["line"].startswith("Prices from your broker (Upstox Analytics), ")
    _clear()


def test_without_the_flag_every_path_answers_as_before():
    me = _me(_auth("bsh-flagoff@example.com"))
    _store(_rows(_days(50, _today() - timedelta(days=1))))
    with pytest.raises(HTTPException) as exc:
        _resolve(me["tenant_id"])
    assert exc.value.status_code == 409 and "not stored" not in str(exc.value.detail)
    _clear()


def test_no_broker_stored_history_then_plain_messages(stored_on):
    me = _me(_auth("bsh-plain@example.com"))
    _clear()
    with pytest.raises(HTTPException) as exc:
        _resolve(me["tenant_id"])
    assert exc.value.status_code == 409 and exc.value.detail == f"Daily history for {SYM} is not stored yet."
    with pytest.raises(HTTPException) as exc:
        _resolve(me["tenant_id"], timeframe="15min")
    assert exc.value.status_code == 409 and exc.value.detail == bar_source.INTRADAY_NEEDS_BROKER
    with pytest.raises(HTTPException) as exc:
        _resolve(me["tenant_id"], exchange="MCX")
    assert exc.value.status_code == 409 and "NSE" in exc.value.detail


def test_only_licensed_daily_sources_are_read_never_the_private_store_or_broker_rows(monkeypatch, stored_on):
    me = _me(_auth("bsh-licence@example.com"))
    rows = _rows(_days(40, _today() - timedelta(days=1)))
    _store(rows, source="broker:upstox")                              # another organisation's broker wrote it
    with pytest.raises(HTTPException) as exc:
        _resolve(me["tenant_id"])
    assert "not stored" in exc.value.detail

    async def private():
        async with _session_factory() as session:
            await ingest.write_candles_bulk(session, [ingest.LakeBar(KEY, "day", bar_end(d), o, h, lo, c, v) for d, o, h, lo, c, v in rows],
                                            "r2:upstox", tenant_id=me["tenant_id"])
            await session.commit()
    _run(private())                                                   # the caller's OWN private store: still not read here
    with pytest.raises(HTTPException) as exc:
        _resolve(me["tenant_id"])
    assert "not stored" in exc.value.detail
    _store(rows, source="r2:upstox")                                  # the R2 daily import in the shared lake
    with pytest.raises(HTTPException) as exc:                         # RDY-6 default: the NSE daily files only
        _resolve(me["tenant_id"])
    assert "not stored" in exc.value.detail
    monkeypatch.setattr(config, "BACKTEST_STORED_SOURCES", ("nse_bhav:", "r2:", "broker:"))
    assert _resolve(me["tenant_id"]).kind == "stored"                 # allowed by the operator's setting: read
    _store(rows, source="broker:upstox")
    with pytest.raises(HTTPException) as exc:                         # a setting never widens past the licensed sources
        _resolve(me["tenant_id"])
    assert "not stored" in exc.value.detail
    _clear()


def test_exchange_files_are_preferred_over_the_import_for_the_same_day(monkeypatch, stored_on):
    monkeypatch.setattr(config, "BACKTEST_STORED_SOURCES", ("nse_bhav:", "r2:"))
    me = _me(_auth("bsh-prefer@example.com"))
    days = _days(30, _today() - timedelta(days=1))
    rows = _rows(days)
    _store([(d, o, h, lo, c + 1, v) for d, o, h, lo, c, v in rows], source="r2:upstox")

    async def add():
        async with _session_factory() as session:
            await ingest.write_candles_bulk(session, [ingest.LakeBar(KEY, "day", bar_end(d), o, h, lo, c, v) for d, o, h, lo, c, v in rows],
                                            "nse_bhav:cm")
            await session.commit()
    _run(add())
    got = _resolve(me["tenant_id"])
    assert list(got.frame["close"]) == [r[4] for r in rows]
    _clear()


# --- no look-ahead, Weekly from Daily --------------------------------------------------------------------------------------

def test_the_window_never_reads_today_or_later():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    first, last = bar_source.window(30, now)
    assert last == date(2026, 10, 22) and first == date(2026, 10, 23) - timedelta(days=400)


def test_todays_stored_bar_is_not_in_the_frame(stored_on):
    me = _me(_auth("bsh-today@example.com"))
    days = _days(20, _today())
    _store(_rows(days))
    got = _resolve(me["tenant_id"])
    stamps = {ts.tz_convert("Asia/Kolkata").date() for ts in got.frame.index}
    assert _today() not in stamps and max(stamps) < _today()
    _clear()


def test_weekly_bars_are_built_from_daily_bars_only():
    days = [date(2026, 10, 5) + timedelta(days=k) for k in range(12) if (date(2026, 10, 5) + timedelta(days=k)).weekday() < 5]
    rows = [(d, 10.0 + k, 20.0 + k, 5.0 + k, 15.0 + k, 100) for k, d in enumerate(days)]
    w = bar_source.weekly(bars_to_dataframe(_broker_bars(rows)))
    assert len(w) == 2
    first = w.iloc[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (10.0, 24.0, 5.0, 19.0, 500.0)
    assert w.index[0].tz_convert("Asia/Kolkata").date() == date(2026, 10, 5)
    assert w.index[1].tz_convert("Asia/Kolkata").date() == date(2026, 10, 12)
    assert bar_source.timeframe_kind("week") == "week" and bar_source.timeframe_kind("5min") is None


# --- the route and the Copilot tool -----------------------------------------------------------------------------------------

def test_the_bars_route_says_where_the_prices_came_from_and_the_range(stored_on):
    headers = _auth("bsh-route@example.com")
    body = {"symbol": SYM, "timeframe": "day", "lookback_days": 30}
    assert client.post("/api/backtest/bars", json=body).status_code == 401
    _clear()
    r = client.post("/api/backtest/bars", json=body, headers=headers)
    assert r.status_code == 409 and r.json()["detail"] == f"Daily history for {SYM} is not stored yet."
    days = _days(60, _today() - timedelta(days=1))
    _store(_rows(days))
    out = client.post("/api/backtest/bars", json=body, headers=headers).json()
    assert out["kind"] == "stored" and out["source"] == "lake:daily" and out["bars"] == len(out["candles"]) == 60
    assert out["line"] == f"Prices from {bar_source.STORED_WORDS}, {bar_source.day_words(out['candles'][0]['timestamp'])} to " \
                          f"{bar_source.day_words(out['candles'][-1]['timestamp'])}."
    assert out["candles"][0]["timestamp"].endswith("+05:30") or out["candles"][0]["timestamp"].endswith("+00:00")
    r = client.post("/api/backtest/bars", json={**body, "timeframe": "5min"}, headers=headers)
    assert r.status_code == 409 and r.json()["detail"] == bar_source.INTRADAY_NEEDS_BROKER
    _clear()


def test_the_copilot_backtest_tools_run_on_stored_history_without_a_broker(stored_on):
    from app.ai import strategy_builder as sb
    from app.ai.tools import ToolContext, run_tool
    me = _me(_auth("bsh-copilot@example.com"))
    _store(_rows(_days(300, _today() - timedelta(days=1))))

    def call(name, args):
        async def go():
            async with _session_factory() as session:
                user = await session.get(User, me["id"])
                return await run_tool(name, args, ToolContext(session, user.tenant_id, user, datetime.now(timezone.utc)))
        return _run(go())
    async def account():                      # M72 PR-4 (F12): the capital basis is his own PAPER account (DEF-0)
        from app.paper.service import get_account
        async with _session_factory() as session:
            await get_account(session, await session.get(User, me["id"]))
            await session.commit()
    _run(account())
    res = call("run_backtest", {"strategy_id": "swing_ema_pullback_d", "symbol": SYM, "timeframe": "day", "days": 60})
    assert res.ok and res.source == "lake:daily" and res.data["prices"] == bar_source.STORED_WORDS
    assert res.data["prices_line"].startswith("Prices from ATP's stored")
    idea = ("on the 15-minute chart, go long when the 20 EMA crosses above the 50 EMA on BSHTEST. Stop 1.5 x ATR 14, objective 2R.")
    cfg = sb.config_of(sb.build(idea, SYM, 1.5))
    assert cfg is not None
    draft = call("backtest_draft", {"config": cfg.model_dump(mode="json"), "symbol": SYM})
    assert not draft.ok and draft.data is None and draft.error == bar_source.INTRADAY_NEEDS_BROKER     # never sample data
    _clear()
