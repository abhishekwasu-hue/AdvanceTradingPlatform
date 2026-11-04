"""Pre-open charts from ATP's own job (owner 4 Nov, app/workers/preopen_charts.py, flag preopen_charts): once a trading
day inside the send window, per organisation with the flag ON and stored engine runner settings, the private package's
charts go to the runner user's own Telegram chat. A made-up private module and engine only (no method here)."""
from __future__ import annotations

import asyncio
import json
import types
import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select

from app.db.models import PlatformControlRecord, RuntimeCounterRecord, User
from app.engine_provider import frames as F
from app.engine_provider import loader
from app.workers import engine_runner as R
from app.workers import preopen_charts as P
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory

IST = F.IST
D1 = date(2030, 1, 7)                       # a made-up Monday
KEY = "NSE:TESTINDEX"


def _run(coro):
    return asyncio.run(coro)


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=IST)


class FakeEngine:
    name, version, timeframes = "test-double-engine", "0.0.0-test", ("15m", "1d")


def _bar(day: date, close: float):
    start = datetime.combine(day, time(15, 15), tzinfo=IST)
    return types.SimpleNamespace(start=start, end=start + timedelta(minutes=15), close=close)


class Captured:
    def __init__(self):
        self.photos: list[tuple[str, str, bytes, str]] = []
        self.built: list[tuple[str, object]] = []

    async def send(self, token, chat, png, caption):
        self.photos.append((token, chat, png, caption))

    def module(self, problem=None):
        def build(request, official_close=None):
            self.built.append((request.symbol, official_close))
            if problem:
                return {"symbol": request.symbol, "problem": problem}
            return {"symbol": request.symbol, "errors": [], "items": [(tf, b"\x89PNG-" + tf.encode(), f"{request.symbol} · {tf}")
                                                                     for tf in ("Daily", "1H", "15M")]}
        return types.SimpleNamespace(build=build)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    async def forget():
        async with _session_factory() as session:
            await session.execute(delete(PlatformControlRecord).where(PlatformControlRecord.key == R.SETTINGS_KEY))
            await session.execute(delete(RuntimeCounterRecord).where(RuntimeCounterRecord.name == P.DAY_SENT))
            await session.commit()
    loader.reset_for_tests()
    _run(forget())
    from app.ai.tools import engine_read
    from app.market_lake import own_bars
    from app.screener import evidence_store

    async def build_request(ctx, cfg, watch, engine):
        frames = {"15m": types.SimpleNamespace(bars=[_bar(D1 - timedelta(days=3), 101.5)])}
        return types.SimpleNamespace(symbol=watch.key, frames=frames), True, 101.5

    async def scope_for(session, user, key):
        return None

    async def official_close(session, key, scope):
        return {"close": 101.25, "date": (D1 - timedelta(days=3)).isoformat(), "source": "NSE index close file"}

    async def target(session, user):
        return "chat-1", "bot-token", None
    monkeypatch.setattr(engine_read, "build_request", build_request)
    monkeypatch.setattr(own_bars, "scope_for", scope_for)
    monkeypatch.setattr(own_bars, "official_close", official_close)
    monkeypatch.setattr(evidence_store, "_telegram_target", target)
    yield
    loader.reset_for_tests()


def _org(flag: bool = True) -> int:
    email = f"preopen-{uuid.uuid4().hex[:8]}@example.com"
    _register(email)

    async def go():
        async with _session_factory() as session:
            user = (await session.scalars(select(User).where(User.email == email))).one()
            await R.save_settings(session, user.tenant_id, {"user_id": user.id, "symbols": [{"key": KEY, "exchange": "NSE", "name": "TESTINDEX"}]},
                                  None)
            return user.tenant_id
    tenant_id = _run(go())
    if flag:
        _set_flag(P.FLAG, tenant_id)
    return tenant_id


def _due(cap: Captured, now: datetime, module=None, monkeypatch=None):
    loader.set_for_tests(FakeEngine())
    monkeypatch.setattr(P, "_private", lambda: module if module is not None else cap.module())
    return _run(P.run_due(_session_factory, now, send=cap.send))


def _day_row(tenant_id: int) -> dict:
    async def go():
        async with _session_factory() as session:
            row = (await session.scalars(select(RuntimeCounterRecord).where(RuntimeCounterRecord.name == P.DAY_SENT,
                                                                            RuntimeCounterRecord.key == f"{tenant_id}:{D1.isoformat()}"))).first()
            return json.loads(row.value) if row is not None else {}
    return _run(go())


def test_window_is_operational_time_only():
    assert P.in_window(at(D1, 8, 59)) is None
    assert P.in_window(at(D1, 9, 0)) == D1 and P.in_window(at(D1, 9, 59)) == D1
    assert P.in_window(at(D1, 10, 0)) is None


def test_official_close_only_when_it_is_the_newest_session_and_not_provisional():
    d = date(2030, 1, 4)
    assert P.usable_close({"close": 10.5, "date": "2030-01-04"}, d) == (10.5, "2030-01-04")
    assert P.usable_close({"close": 10.5, "date": "2030-01-03"}, d) is None
    assert P.usable_close({"close": 10.5, "date": "2030-01-04", "provisional": True}, d) is None
    assert P.usable_close(None, d) is None


def test_sends_the_three_charts_once_a_day_to_the_runner_users_own_chat(monkeypatch):
    tenant_id = _org()
    cap = Captured()
    report = _due(cap, at(D1, 9, 1), monkeypatch=monkeypatch)
    assert report.sent == 3 and [c for _, _, _, c in cap.photos] == [f"{KEY} · Daily", f"{KEY} · 1H", f"{KEY} · 15M"]
    assert {(t, ch) for t, ch, _, _ in cap.photos} == {("bot-token", "chat-1")}
    assert cap.built == [(KEY, (101.25, (D1 - timedelta(days=3)).isoformat()))]       # the official close of the newest session
    row = _day_row(tenant_id)
    assert row["state"] == "done" and row["sent"] == 3 and row["symbols"]["TESTINDEX"]["official_close"] is True
    again = Captured()
    assert _due(again, at(D1, 9, 20), monkeypatch=monkeypatch).sent == 0 and again.photos == []   # once a day


def test_flag_off_or_outside_the_window_sends_nothing(monkeypatch):
    _org(flag=False)
    cap = Captured()
    assert _due(cap, at(D1, 9, 1), monkeypatch=monkeypatch).sent == 0 and cap.photos == []
    _org()
    assert _due(cap, at(D1, 11, 0), monkeypatch=monkeypatch).skipped == "outside the send window" and cap.photos == []


def test_no_private_module_is_a_quiet_no_op(monkeypatch):
    _org()
    loader.set_for_tests(FakeEngine())
    monkeypatch.setattr(P, "_private", lambda: None)
    cap = Captured()
    assert _run(P.run_due(_session_factory, at(D1, 9, 1), send=cap.send)).skipped == "private package not installed"
    assert cap.photos == []


def test_no_telegram_chat_or_an_engine_problem_is_recorded_never_sent(monkeypatch):
    from app.screener import evidence_store

    async def none(session, user):
        return None, None, "not_linked"
    tenant_id = _org()
    monkeypatch.setattr(evidence_store, "_telegram_target", none)
    cap = Captured()
    assert _due(cap, at(D1, 9, 1), monkeypatch=monkeypatch).sent == 0 and cap.photos == []
    assert _day_row(tenant_id)["reason"] == "not_linked"


def test_engine_problem_is_recorded_per_symbol(monkeypatch):
    tenant_id = _org()
    cap = Captured()
    assert _due(cap, at(D1, 9, 1), module=cap.module(problem="insufficient_data"), monkeypatch=monkeypatch).sent == 0
    assert _day_row(tenant_id)["symbols"]["TESTINDEX"] == "engine: insufficient_data"


def test_no_order_or_execution_import():
    import inspect
    src = inspect.getsource(P)
    for word in ("app.execution", "app.trading.orders", "place_order", "app.brokers"):
        assert word not in src
