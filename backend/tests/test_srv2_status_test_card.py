"""Owner 25 Nov: "Terminal var SRV2 bot status dakhav" and "tyachi atach test ghe, mla telegram war vision through chart yayla pahije".
- the bot's status (ON, stepping, Telegram, Vision) from the loop's heartbeat; owner only, names only;
- the `candles` host adapter (Trade's fetch_candles shape) the bot's Vision chart needs - it was not registered, so a chart could not
  be drawn;
- `python -m app.srv2bot.test_card` runs the private package's TEST card with the same adapters, owner only, bot ON."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from app.platform import live_owner
from app.platform import routes as platform_routes
from app.srv2bot import io as bot_io
from app.srv2bot import job, loader, status
from app.srv2bot import test_card as TC
from tests.test_auth_api import _session_factory

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset():
    yield
    loader.set_for_tests(loader._UNSET)


def _flag(monkeypatch, on=True):
    async def flag(session, name, tenant_id=None):
        return on
    monkeypatch.setattr("app.platform.controls.flag_enabled", flag)


async def _read(now):
    async with _session_factory() as s:
        return await status.read(s, 1, now)


async def _beat(names, at):
    async with _session_factory() as s:
        await status.beat(s, names, at)
        await s.commit()


# --- status ------------------------------------------------------------------------------------------------------------

def test_the_status_reads_running_with_telegram_and_vision_from_the_heartbeat(monkeypatch):
    loader.set_for_tests(NS(host=None, runner=None))
    _flag(monkeypatch)
    asyncio.run(_beat(("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"), T0))
    got = asyncio.run(_read(T0 + timedelta(minutes=3)))
    assert got["state"] == "running" and got["telegram"] and got["vision"] and got["last_step_at"] == T0.isoformat()
    assert not any("TOKEN" in str(v) or "KEY" in str(v) for v in got.values())          # names never leave as values


def test_no_step_for_too_long_reads_not_running_and_no_telegram_says_so(monkeypatch):
    loader.set_for_tests(NS(host=None, runner=None))
    _flag(monkeypatch)
    asyncio.run(_beat(("ANTHROPIC_API_KEY",), T0))
    assert asyncio.run(_read(T0 + timedelta(minutes=4)))["state"] == "no_telegram"
    assert asyncio.run(_read(T0 + status.STALE_AFTER + timedelta(minutes=1)))["state"] == "not_running"


def test_off_and_not_installed_are_their_own_states(monkeypatch):
    loader.set_for_tests(NS(host=None, runner=None))
    _flag(monkeypatch, on=False)
    assert asyncio.run(_read(T0))["state"] == "off"
    loader.set_for_tests(None)
    assert asyncio.run(_read(T0))["state"] == "not_installed"


def test_the_route_is_the_owners_only_and_another_admin_gets_hidden_without_an_audit_row(monkeypatch):
    loader.set_for_tests(NS(host=None, runner=None))
    _flag(monkeypatch)
    monkeypatch.setattr(live_owner, "is_owner", lambda user: getattr(user, "id", None) == 1)

    async def go(user):
        async with _session_factory() as s:
            return await platform_routes.get_srv2_status(user=user, session=s)
    assert asyncio.run(go(NS(id=7, tenant_id=1))) == {"state": "hidden"}
    assert asyncio.run(go(NS(id=1, tenant_id=1)))["state"] in ("running", "not_running", "no_telegram")


def test_the_loops_refresh_writes_the_heartbeat_and_a_failed_write_never_stops_it(monkeypatch):
    seen = []

    async def values(session, who):
        return {"TELEGRAM_BOT_TOKEN": "t"}

    async def beat(session, names, now):
        seen.append(names)
        raise RuntimeError("db down")
    from app.srv2bot import dashboard_env as D
    monkeypatch.setattr(D, "values", values)
    monkeypatch.setattr(D, "apply", lambda found: tuple(found))
    monkeypatch.setattr(status, "beat", beat)
    monkeypatch.setattr(job, "_settings", {"at": None, "names": None})
    assert asyncio.run(job.refresh_settings(None, None, T0)) is True and seen == [("TELEGRAM_BOT_TOKEN",)]


# --- candles ------------------------------------------------------------------------------------------------------------

def test_candle_args_take_trades_call_shapes():
    assert bot_io.candle_args(("tok", "nifty", 0), {"interval": "1minute", "lookback_days": 21}) == ("NIFTY", "1minute", 21)
    assert bot_io.candle_args(("tok", "SENSEX"), {"underlying_price": 0, "interval": "15minute"}) == ("SENSEX", "15minute", 20)
    assert bot_io.candle_args(("tok", "NIFTY", 0, "2minute"), {}) == ("NIFTY", "30minute", 60)          # Trade: unknown -> 30minute
    assert bot_io.candle_args(("tok", "NIFTY", 0), {"interval": "day"}) == ("NIFTY", "day", 400)


def _bar(ts, o, h, low, c, v=1.0):
    return NS(timestamp=pd.Timestamp(ts, tz="Asia/Kolkata").to_pydatetime(), open=o, high=h, low=low, close=c, volume=v)


def test_candle_frame_is_trades_shape_sorted_deduplicated_and_resampled():
    bars = [_bar("2026-11-25 09:16", 2, 3, 1, 2), _bar("2026-11-25 09:15", 1, 2, 0.5, 1.5),
            _bar("2026-11-25 09:16", 2, 4, 1, 3)]                                       # repeated: the newest fetch wins
    f = bot_io.candle_frame(bars, "1minute")
    assert list(f.columns) == bot_io.CANDLE_COLUMNS and len(f) == 2 and f["high"].iloc[-1] == 4
    m = bot_io.candle_frame([_bar(f"2026-11-25 09:{15 + i}", 10 + i, 11 + i, 9 + i, 10.5 + i) for i in range(10)], "5minute")
    assert len(m) == 2 and m["timestamp"].iloc[0] == pd.Timestamp("2026-11-25 09:15", tz="Asia/Kolkata")
    assert (m["open"].iloc[0], m["high"].iloc[0], m["low"].iloc[0], m["close"].iloc[0]) == (10, 15, 9, 14.5)
    assert list(bot_io.candle_frame([], "1minute").columns) == bot_io.CANDLE_COLUMNS


def test_the_candles_adapter_is_registered_and_reads_history_and_today(monkeypatch):
    calls = []

    class Broker:
        async def get_historical_data(self, name, exchange, interval, start, end):
            calls.append(("hist", name, exchange, interval))
            return [_bar("2026-11-24 15:29", 1, 2, 0.5, 1.5)]

        async def get_intraday_candles(self, name, exchange, interval):
            calls.append(("today", name, exchange, interval))
            return [_bar("2026-11-25 09:15", 2, 3, 1, 2.5)]

    async def usable(session, tenant_id, broker, user_id, now):
        return Broker()

    loop = asyncio.new_event_loop()
    import threading
    th = threading.Thread(target=loop.run_forever, daemon=True)
    th.start()
    try:
        impls = bot_io.build(loop, _session_factory, bot_io.Owner(1, NS(id=1)), usable)
        assert "candles" in impls
        f = impls["candles"]("tok", "NIFTY", 0, interval="1minute", lookback_days=21)
        assert len(f) == 2 and f.attrs["failed_chunks"] == 0
        assert ("hist", "NIFTY 50", "NSE", "1min") in calls and ("today", "NIFTY 50", "NSE", "1min") in calls
        calls.clear()
        d = impls["candles"]("tok", "SENSEX", 0, interval="day", lookback_days=120)
        assert len(d) == 1 and calls == [("hist", "SENSEX", "BSE", "day")]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        th.join(timeout=5)


# --- the TEST card command -------------------------------------------------------------------------------------------------

def test_the_command_says_why_when_the_bot_or_its_test_card_is_missing(capsys):
    loader.set_for_tests(None)
    assert TC.main([]) == 5
    loader.set_for_tests(NS(host=None, runner=None, test_card=None))
    assert TC.main([]) == 5 and "no TEST card yet" in capsys.readouterr().out


def test_the_command_refuses_with_the_bot_off(monkeypatch, capsys):
    loader.set_for_tests(NS(host=None, runner=None, test_card=NS(main=lambda argv: pytest.fail("no card with the bot OFF"))))

    async def owner(session):
        return bot_io.Owner(1, NS(id=1))
    monkeypatch.setattr(job, "owner", owner)
    _flag(monkeypatch, on=False)
    monkeypatch.setattr(TC, "_session_factory", _session_factory, raising=False)
    import app.db.session as dbs
    monkeypatch.setattr(dbs, "_session_factory", _session_factory)
    assert TC.main([]) == 6 and "OFF" in capsys.readouterr().out


def test_the_command_registers_the_adapters_and_runs_the_private_card(monkeypatch):
    seen = {}

    class Host:
        def register(self, **impls):
            seen["names"] = sorted(impls)
    def card_main(argv, calendar=None):
        seen.update(argv=argv, calendar=calendar)
        return 0
    loader.set_for_tests(NS(host=Host(), runner=None, test_card=NS(main=card_main)))

    async def owner(session):
        return bot_io.Owner(1, NS(id=1))

    async def calendar_for(session, now):
        return "atp-calendar"
    monkeypatch.setattr(job, "owner", owner)
    monkeypatch.setattr(job, "calendar_for", calendar_for)
    _flag(monkeypatch)
    import app.db.session as dbs
    monkeypatch.setattr(dbs, "_session_factory", _session_factory)
    assert TC.main(["--symbol", "NIFTY"]) == 0
    assert seen["argv"] == ["--symbol", "NIFTY"] and {"candles", "send", "settings", "token"} <= set(seen["names"])
    assert seen["calendar"] == "atp-calendar"          # owner VPS 25 Nov: the card reads market hours under ATP's calendar


def test_an_older_private_card_without_a_calendar_is_called_as_before():
    seen = []
    assert TC._run_card(NS(main=lambda argv: seen.append(argv) or 0), ["--symbol", "NIFTY"], "cal") == 0
    assert seen == [["--symbol", "NIFTY"]]


# --- owner VPS 26 Nov: the Analytics Token serves the bot's data without the daily login ----------------------------------------------

def _patch_tokens(monkeypatch, *, session_ok, analytics_ok):
    import app.brokers.token_lifecycle as TL
    import app.brokers.upstox_analytics as UA
    import app.secrets_store.envelope as EV
    record = NS(id=1)

    async def get_record(session, tenant_id, broker, account_label="primary"):
        return record

    async def key(session, tenant_id):
        return None
    monkeypatch.setattr(TL, "get_credential_record", get_record)
    monkeypatch.setattr(TL, "token_is_usable", lambda r, now=None: session_ok)
    monkeypatch.setattr(TL, "data_is_usable", lambda r, now=None: session_ok or analytics_ok)
    monkeypatch.setattr(TL, "load_credentials", lambda r: NS(access_token="daily", analytics_token="analytics"))
    monkeypatch.setattr(TL, "build_adapter", lambda r: "analytics-adapter")
    monkeypatch.setattr(UA, "analytics_usable", lambda r, now=None: analytics_ok)
    monkeypatch.setattr(EV, "ensure_tenant_key", key)


def _impls(usable):
    loop = asyncio.new_event_loop()
    import threading
    th = threading.Thread(target=loop.run_forever, daemon=True)
    th.start()
    return loop, th, bot_io.build(loop, _session_factory, bot_io.Owner(1, NS(id=1)), usable)


@pytest.mark.parametrize("session_ok,analytics_ok,want", [(True, True, "daily"), (False, True, "analytics"), (False, False, None)])
def test_the_bots_token_is_the_daily_one_else_the_analytics_token(monkeypatch, session_ok, analytics_ok, want):
    _patch_tokens(monkeypatch, session_ok=session_ok, analytics_ok=analytics_ok)

    async def usable(session, tenant_id, broker, user_id, now):
        return "session-adapter" if session_ok else None
    loop, th, impls = _impls(usable)
    try:
        assert impls["token"]() == want
    finally:
        loop.call_soon_threadsafe(loop.stop)
        th.join(timeout=5)


def test_data_reads_fall_back_to_the_analytics_token_but_funds_never_do(monkeypatch):
    _patch_tokens(monkeypatch, session_ok=False, analytics_ok=True)
    seen = []

    async def usable(session, tenant_id, broker, user_id, now):
        return None

    class Adapter:
        async def get_ltp_by_key(self, keys):
            seen.append("ltp")
            return {"k": 1.0}
    import app.brokers.token_lifecycle as TL
    monkeypatch.setattr(TL, "build_adapter", lambda r: Adapter())
    loop, th, impls = _impls(usable)
    try:
        assert impls["ltp"]("tok", ["k"]) == ({"k": 1.0}, None) and seen == ["ltp"]
        assert impls["funds"]("tok") is None                       # funds need the daily session: no analytics fallback
    finally:
        loop.call_soon_threadsafe(loop.stop)
        th.join(timeout=5)
