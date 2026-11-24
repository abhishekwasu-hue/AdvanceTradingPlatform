"""SRV2 (owner 18 Nov, DECISIONS 18 Nov SRV2): ATP's side of the owner's 15M SRv2 bot (app/srv2bot) on a test double of the private
package - absent package or flag OFF => nothing runs and no IO is registered; ON for the owner => ATP's IO is registered and the bot's
schedule is called with the IST clock and ATP's calendar; the settings rows round-trip; the IO reads through the owner's broker session
(raw option chain by expiry index, prices by instrument key, funds); nothing here reaches an ATP order path."""
import ast
import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.srv2bot import io as bot_io
from app.srv2bot import job, loader, store
from tests.test_auth_api import _session_factory

PACKAGE = Path(__file__).resolve().parents[1] / "app" / "srv2bot"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh():
    loader.set_for_tests(loader._UNSET)
    yield
    loader.set_for_tests(loader._UNSET)


class FakeBot:
    def __init__(self):
        self.registered, self.ticks = {}, []
        self.host = NS(register=lambda **kw: self.registered.update(kw))
        self.runner = NS(tick=lambda now, cal: (self.ticks.append((now, cal)) or ["exits"]))


def test_the_package_absent_means_no_step_and_no_database_read():
    loader.set_for_tests(None)

    def factory():
        raise AssertionError("no session while the bot is absent")
    assert _run(job.run_once(factory, None)) is None


def test_flag_off_or_no_owner_means_no_step(monkeypatch):
    bot = FakeBot()
    loader.set_for_tests(bot)

    async def no_owner(session):
        return None
    monkeypatch.setattr(job, "owner", no_owner)
    assert _run(job.run_once(_session_factory, None)) is None and bot.ticks == [] and bot.registered == {}

    async def an_owner(session):
        return bot_io.Owner(10**6, NS(id=1))

    async def flag(session, name, tenant_id=None):
        return False
    monkeypatch.setattr(job, "owner", an_owner)
    monkeypatch.setattr("app.platform.controls.flag_enabled", flag)
    assert _run(job.run_once(_session_factory, None)) is None and bot.ticks == []


def test_flag_on_registers_atps_io_and_runs_one_step_on_the_ist_clock(monkeypatch):
    bot = FakeBot()
    loader.set_for_tests(bot)

    async def an_owner(session):
        return bot_io.Owner(10**6, NS(id=1))

    async def flag(session, name, tenant_id=None):
        return name == job.FLAG

    async def cal(session, now):
        return "calendar"
    monkeypatch.setattr(job, "owner", an_owner)
    monkeypatch.setattr("app.platform.controls.flag_enabled", flag)
    monkeypatch.setattr(job, "calendar_for", cal)
    ran = _run(job.run_once(_session_factory, None, now=datetime(2030, 1, 7, 4, 30, tzinfo=UTC)))
    assert ran == ["exits"]
    assert bot.ticks == [(datetime(2030, 1, 7, 10, 0), "calendar")]          # 04:30 UTC = 10:00 IST, naive
    assert set(bot.registered) == {"settings", "token", "option_expiries", "option_chain", "ltp", "funds", "send", "live_confirmed"}
    assert bot.registered["live_confirmed"]("15m_dynamic_sr", "NIFTY", "LIVE") is False   # LIVE is not wired


def test_settings_rows_round_trip_as_trades_json_merge():
    async def go():
        async with _session_factory() as s:
            await store.save_row(s, "15m_dynamic_sr", "NIFTY", {"lots": 2, "max_hits_per_zone": 4})
            await store.save_row(s, "15m_dynamic_sr", "NIFTY", {"lots": 3})
            await store.save_row(s, "__signal_symbols__", "ALL", {"signal_symbols": ["NIFTY"]})
            await s.commit()
        async with _session_factory() as s:
            return (await store.get_row(s, "15m_dynamic_sr", "NIFTY"), await store.get_row(s, "__signal_symbols__", "ALL"),
                    await store.get_row(s, "15m_dynamic_sr", "SENSEX"))
    row, symbols, none = _run(go())
    assert row == {"lots": 3, "max_hits_per_zone": 4} and symbols == {"signal_symbols": ["NIFTY"]} and none is None


class FakeBroker:
    def __init__(self):
        self.asked = []

    async def get_option_chain_raw(self, underlying, expiry):
        self.asked.append(("chain", underlying, expiry))
        return [{"strike_price": 24000, "underlying_spot_price": 24010.0}]

    async def get_ltp_by_key(self, keys):
        self.asked.append(("ltp", tuple(keys)))
        return {k: 1.5 for k in keys}

    async def get_margins(self):
        return NS(available_margin=100000.0, used_margin=2500.0)


def test_the_io_reads_through_the_owners_broker_session(monkeypatch):
    broker = FakeBroker()

    async def usable(session, tenant_id, name, user_id, at):
        assert name == "upstox" and tenant_id == 7 and user_id == 3
        return broker

    async def expiries(session, underlying, *, on_or_after=None, **kw):
        return [date(2030, 1, 9), date(2030, 1, 16)]
    monkeypatch.setattr("app.instruments.master.expiries", expiries)

    async def go():
        loop = asyncio.get_running_loop()
        io = bot_io.build(loop, _session_factory, bot_io.Owner(7, NS(id=3)), usable)
        return await asyncio.to_thread(lambda: (io["option_expiries"]("tok", "NIFTY"), io["option_chain"]("tok", "NIFTY", 1),
                                                io["ltp"]("tok", ["NSE_FO|A", "NSE_FO|B"]), io["funds"]("tok")))
    exps, chain, ltp, funds = _run(go())
    assert exps == ["2030-01-09", "2030-01-16"]
    assert chain == ([{"strike_price": 24000, "underlying_spot_price": 24010.0}], "") and ("chain", "NIFTY", date(2030, 1, 16)) in broker.asked
    assert ltp == ({"NSE_FO|A": 1.5, "NSE_FO|B": 1.5}, None)
    assert funds == {"available_margin": 100000.0, "used_margin": 2500.0}


def test_a_broker_without_a_usable_session_gives_trades_failure_results():
    async def usable(*a):
        return None

    async def go():
        io = bot_io.build(asyncio.get_running_loop(), _session_factory, bot_io.Owner(7, NS(id=3)), usable)
        return await asyncio.to_thread(lambda: (io["ltp"]("tok", ["X"]), io["funds"]("tok")))
    (prices, why), funds = _run(go())
    assert prices == {} and "not usable" in why and funds is None


def test_only_the_loader_names_the_private_package_and_nothing_reaches_an_order_path():
    banned = ("app.execution", "app.paper", "app.trading", "app.live", "app.deployments", "app.portfolio")
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        assert "atp_private" not in text or path.name in ("loader.py", "__init__.py"), path.name
        tree = ast.parse(text)
        names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [x for x in names if x.startswith(banned)], (path.name, names)
        for word in ("place_order", "modify_order", "cancel_order", "submit_order", "square_off"):
            assert word not in text, (path.name, word)


def test_the_worker_starts_the_bots_loop_once():
    from app.workers.trading_worker import TradingWorker

    async def go():
        worker = TradingWorker.__new__(TradingWorker)
        worker._srv2_task = None
        worker.session_factory = _session_factory
        loader.set_for_tests(None)
        worker._spawn_srv2_bot()
        first = worker._srv2_task
        worker._spawn_srv2_bot()
        assert worker._srv2_task is first and first is not None
        first.cancel()
    _run(go())


def test_the_worker_gets_the_bots_own_telegram_and_vision_settings_from_srv2_names():
    """Owner 24 Nov ("srv2 bot functional jhala ka"): the copied Trade code reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID /
    TELEGRAM_APPROVER_IDS / VISION_CALLBACK_SECRET (its ✅ cards and their taps) and VISION_SIGNAL_MODEL / ANTHROPIC_API_KEY
    (Vision); compose passed none, so its cards had no buttons and "Ask me first" could place no entry. Now the worker - only
    the worker - gets them from SRV2_* in .env, and .env.example names each one."""
    from pathlib import Path

    import yaml
    root = Path(__file__).resolve().parents[2]
    services = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    names = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_APPROVER_IDS", "VISION_CALLBACK_SECRET", "VISION_SIGNAL_MODEL",
             "ANTHROPIC_API_KEY")
    wanted = {name: "SRV2_" + name for name in names}
    worker = services["worker"]["environment"]
    for name, source in wanted.items():
        assert worker[name] == "${" + source + ":-}", name
        assert name not in (services["backend"].get("environment") or {}), name      # the web process never holds them
    example = (root / ".env.example").read_text()
    for source in wanted.values():
        assert f"\n{source}=\n" in example, source
