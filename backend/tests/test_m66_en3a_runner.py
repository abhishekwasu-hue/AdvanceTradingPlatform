"""M66 EN-3a: the signal engine runner (app/workers/engine_runner.py) - signals only, on a synthetic test double.

Covered: flag off => no-op; engine absent => no-op (no database read); one closed bar => one signal row in the existing
signals table (signal_history) with no entry / stop / target on it; a replay of the same bar => no duplicate (also with
the run state gone: the dedupe row); a running bar is never read (no look-ahead); a blocking gap => no signal and the
reason with the gap lines on the run record; the card text passes the signal-language check (a card ATP has no phrase
for is stored without text); the per-symbol lock; bounded retries and the grace window; the settings; the worker hook;
nothing in the runner reaches an order path. The engine is a made-up double (`loader.set_for_tests`), the bars are
made up (prices around 100), and the dates are a made-up future week.
"""
import ast
import asyncio
import json
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.db.models import RuntimeCounterRecord, SignalHistoryRecord, User
from app.engine_provider import contract as c
from app.engine_provider import frames as F
from app.engine_provider import loader
from app.signals import language
from app.signals.language import SignalStructure
from app.signals_feed import service as feed
from app.workers import engine_runner as R
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory

IST = F.IST
D1 = date(2030, 1, 7)                       # a made-up Monday
KEY = "NSE:TESTSTOCK"
M = timedelta(minutes=1)


def _run(coro):
    return asyncio.run(coro)


def at(day: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm, ss), tzinfo=IST)


# --- the test double and the synthetic data --------------------------------------------------------------------------

class FakeEngine:
    """A made-up engine: one signal on the read bar (generic tokens of ATP's public signal language), or what the test
    sets. Nothing here is a reading rule."""
    name = "test-double-engine"
    version = "0.0.0-test"
    interface = c.INTERFACE_VERSION
    timeframes = ("5m",)

    def __init__(self, *, card=None, problems=(), raises=None, signal=True):
        self.calls: list[c.EngineRequest] = []
        self.card = card or SignalStructure(trend="up", steps=("pullback_reached_area",), setup="trend_continuation", evidence=("bos",))
        self.problems, self.raises, self.signal = tuple(problems), raises, signal

    def read(self, request: c.EngineRequest) -> c.EngineResult:
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        area = c.Area("area-1", "buyer", "state-a", c.PriceRange(99.0, 100.0), "5m", request.as_of)
        signals = (c.Signal(model_id="model-test", timeframe="5m", bar_end=request.as_of, side="buyer", entry_zone=c.PriceRange(99.2, 99.8),
                            card=self.card, area_id="area-1", invalidation=98.765, evidence=(c.Evidence("token-a", True),)),) if self.signal else ()
        return c.EngineResult(interface=c.INTERFACE_VERSION, version=self.version, areas=(area,), signals=signals,
                              data_problems=self.problems)


def minutes(day: date, upto: datetime, skip=()) -> F.SourceBars:
    out, t, i = [], at(day, 9, 15), 0
    while t < upto:
        if t not in skip:
            p = 100.0 + (i % 5) * 0.25
            out.append(F.RawBar(t, t + M, p, p + 0.5, p - 0.5, p + 0.1, 1000.0))
        t += M
        i += 1
    return F.SourceBars("test source", "1m", tuple(out))


class FakeCollect:
    """Stands in for EN-2's `sources.collect`: the made-up 1-minute bars a broker has delivered up to `upto`."""

    def __init__(self, upto: datetime, skip=()):
        self.upto, self.skip, self.calls = upto, set(skip), 0

    async def __call__(self, session, *, key, exchange, timeframes, starts, now, cal, scope=None, broker=None, broker_symbol=None):
        self.calls += 1
        return F.SymbolData(symbol=key, exchange=exchange, minutes=minutes(D1, min(self.upto, now.astimezone(IST)), self.skip)), []


# --- fixtures --------------------------------------------------------------------------------------------------------

async def _forget_settings():
    from app.db.models import PlatformControlRecord
    async with _session_factory() as session:
        await session.execute(delete(PlatformControlRecord).where(PlatformControlRecord.key == R.SETTINGS_KEY))
        await session.commit()


@pytest.fixture(autouse=True)
def _fresh():
    """Each test configures only its own organisations (the runner settings row is platform-wide)."""
    loader.reset_for_tests()
    R._held.clear()
    _run(_forget_settings())
    yield
    loader.reset_for_tests()
    R._held.clear()


def _org(flag: bool = True, **settings) -> tuple[int, int]:
    """A new organisation with its user; the flag on for it (allow-list) and the runner settings stored."""
    email = f"en3a-{uuid.uuid4().hex[:8]}@example.com"
    _register(email)

    async def go():
        async with _session_factory() as session:
            user = (await session.scalars(select(User).where(User.email == email))).one()
            values = {"user_id": user.id, "symbols": [{"key": KEY, "exchange": "NSE", "name": "TESTSTOCK"}], "intraday_days": 1, **settings}
            await R.save_settings(session, user.tenant_id, values, None)
            return user.tenant_id, user.id
    tenant_id, user_id = _run(go())
    if flag:
        _set_flag(R.FLAG, tenant_id)
    return tenant_id, user_id


def _signals(tenant_id: int) -> list[SignalHistoryRecord]:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(SignalHistoryRecord).where(SignalHistoryRecord.tenant_id == tenant_id)
                                              .order_by(SignalHistoryRecord.id)))
    return _run(go())


def _records(tenant_id: int) -> list[dict]:
    async def go():
        async with _session_factory() as session:
            return await R.run_records(session, tenant_id)
    return _run(go())


def _due(engine, collect, now, **kw):
    loader.set_for_tests(engine)
    return _run(R.run_due(_session_factory, now, collect=collect, **kw))


# --- no-op paths -------------------------------------------------------------------------------------------------------

def test_flag_off_is_a_no_op():
    tenant_id, _ = _org(flag=False)
    engine, collect = FakeEngine(), FakeCollect(at(D1, 10, 0))
    _due(engine, collect, at(D1, 10, 0, 10))
    assert collect.calls == 0 and engine.calls == [] and _signals(tenant_id) == [] and _records(tenant_id) == []


def test_engine_absent_is_a_no_op_without_a_database_read():
    loader.set_for_tests(None)

    def factory():
        raise AssertionError("the runner must not open a session while the engine is absent")
    report = _run(R.run_due(factory, at(D1, 10, 0, 10)))
    assert report.skipped == "engine not installed" and report.runs == 0


def test_the_worker_starts_no_task_while_the_engine_is_absent():
    from app.workers.trading_worker import TradingWorker
    loader.set_for_tests(None)
    worker = TradingWorker.__new__(TradingWorker)
    worker._engine_task = None
    worker._spawn_engine_runner(at(D1, 10, 0))
    assert worker._engine_task is None


# --- one closed bar, replay, look-ahead -----------------------------------------------------------------------------------

def test_one_closed_bar_gives_one_signal_in_the_signals_table():
    tenant_id, user_id = _org()
    engine, collect = FakeEngine(), FakeCollect(at(D1, 10, 0))
    _due(engine, collect, at(D1, 10, 0, 10))
    assert [r.as_of for r in engine.calls] == [at(D1, 10, 0)]
    rows = _signals(tenant_id)
    assert len(rows) == 1
    row = rows[0]
    assert (row.user_id, row.strategy_id, row.symbol, row.direction, row.timeframe_combo) == (user_id, R.STRATEGY_ID, "TESTSTOCK", "LONG", "5m")
    assert row.entry is None and row.stop_loss is None and row.target1 is None and row.target2 is None and row.risk_reward is None
    assert row.signal_time.replace(tzinfo=None) == at(D1, 10, 0).astimezone(F.timezone.utc).replace(tzinfo=None)
    [text] = json.loads(row.reasons_json)
    assert text.startswith("TESTSTOCK · Buyer area: 99–100.")
    assert language.check(text, "signal_own", own_symbols=["TESTSTOCK"]) == []
    assert "98.765" not in text and "98,765" not in text                    # the invalidation stays internal
    [record] = _records(tenant_id)
    assert record["status"] == R.SIGNAL and record["runs"][-1]["signals"] == [row.id] and record["final"] is True
    key = R.dedupe_key(tenant_id, KEY, "5m", at(D1, 10, 0), "model-test")

    async def internal():
        async with _session_factory() as session:
            return await R.internal_signal(session, key)
    stored = _run(internal())
    assert stored["history_id"] == row.id and stored["invalidation"] == 98.765 and stored["entry_zone"] == [99.2, 99.8]


def test_a_replay_of_the_same_bar_never_writes_a_second_signal():
    tenant_id, _ = _org()
    engine, collect = FakeEngine(), FakeCollect(at(D1, 10, 0))
    _due(engine, collect, at(D1, 10, 0, 10))
    _due(engine, collect, at(D1, 10, 0, 40))                               # the same bar on the next cycle: already read
    assert len(engine.calls) == 1
    assert len(_signals(tenant_id)) == 1

    async def forget_state():                                            # a restart that lost the run state
        async with _session_factory() as session:
            await session.execute(delete(RuntimeCounterRecord).where(RuntimeCounterRecord.name == R.STATE,
                                                                     RuntimeCounterRecord.key == R.state_key(tenant_id, "5m", KEY)))
            await session.commit()
    _run(forget_state())
    report = _due(engine, collect, at(D1, 10, 1))
    assert len(_signals(tenant_id)) == 1 and report.duplicates == 1 and len(engine.calls) == 2
    assert _records(tenant_id)[0]["runs"][-1]["duplicates"] == 1


def test_a_running_bar_is_never_read():
    assert R.due_bar(F.market_calendar("NSE", (), D1, D1), "5m", at(D1, 10, 2, 30), 3) == at(D1, 10, 0)
    assert R.due_bar(F.market_calendar("NSE", (), D1, D1), "5m", at(D1, 10, 5, 1), 3) == at(D1, 10, 0)   # close + delay not reached
    assert R.due_bar(F.market_calendar("NSE", (), D1, D1), "5m", at(D1, 10, 5, 3), 3) == at(D1, 10, 5)
    assert R.due_bar(F.market_calendar("NSE", (), D1, D1), "5m", at(D1, 9, 16), 3) == at(date(2030, 1, 4), 15, 30)  # the last close
    tenant_id, _ = _org()
    engine, collect = FakeEngine(), FakeCollect(at(D1, 10, 2))            # the broker already has minutes of the running bar
    _due(engine, collect, at(D1, 10, 1, 50))
    [req] = engine.calls
    assert req.as_of == at(D1, 10, 0)
    assert all(b.end <= at(D1, 10, 0) for frame in req.frames.values() for b in frame.bars)
    assert req.frames["5m"].bars[-1].start == at(D1, 9, 55)


# --- data gaps ------------------------------------------------------------------------------------------------------------

def test_a_blocking_gap_gives_no_signal_and_says_why():
    tenant_id, _ = _org(max_attempts=1)
    hole = {at(D1, 9, 30) + i * M for i in range(5)}
    engine, collect = FakeEngine(), FakeCollect(at(D1, 10, 0), skip=hole)
    _due(engine, collect, at(D1, 10, 0, 10))
    assert engine.calls == []                                              # never read on gapped data
    assert _signals(tenant_id) == []
    [record] = _records(tenant_id)
    run = record["runs"][-1]
    assert record["status"] == R.DATA_GAP and run["final"] is True
    assert "data gaps" in run["reason"] and "missing_bars:5m" in run["problems"]
    assert any(line.startswith(f"{KEY} 5m: 1 bar missing") for line in run["gaps"])


def test_a_data_problem_the_engine_reports_gives_no_signal():
    tenant_id, _ = _org(max_attempts=1)
    engine = FakeEngine(problems=("problem-token",))
    _due(engine, FakeCollect(at(D1, 10, 0)), at(D1, 10, 0, 10))
    assert _signals(tenant_id) == []
    run = _records(tenant_id)[0]["runs"][-1]
    assert run["status"] == R.DATA_GAP and run["problems"] == ["problem-token"] and "engine reported" in run["reason"]


def test_a_late_bar_is_retried_and_then_written():
    tenant_id, _ = _org()
    engine = FakeEngine()
    _due(engine, FakeCollect(at(D1, 9, 59)), at(D1, 10, 0, 10))           # the last minute has not arrived yet
    assert _signals(tenant_id) == [] and _records(tenant_id)[0]["final"] is False
    _due(engine, FakeCollect(at(D1, 10, 0)), at(D1, 10, 1, 10))           # it has now, within the grace window
    assert len(_signals(tenant_id)) == 1
    record = _records(tenant_id)[0]
    assert [r["attempt"] for r in record["runs"]] == [1, 2] and record["status"] == R.SIGNAL


# --- the card text ----------------------------------------------------------------------------------------------------------

def test_the_card_text_follows_the_signal_language():
    result = c.EngineResult(interface=1, version="x", areas=(c.Area("a", "seller", "s", c.PriceRange(23000, 23100), "5m", at(D1, 10, 0)),))
    sig = c.Signal(model_id="m", timeframe="5m", bar_end=at(D1, 10, 0), side="seller", entry_zone=c.PriceRange(23010, 23090), area_id="a",
                   card=SignalStructure(trend="down", steps=("rally_reached_area", "handover_forming"), evidence=("bearish_divergence",)),
                   invalidation=23150.0)
    text, note = R.card_line(sig, result, "TESTSTOCK")
    assert note is None and text is not None
    assert language.check(text, "signal_own", own_symbols=["TESTSTOCK"]) == []
    assert language.call_words(text) == [] and "23,150" not in text and "Seller area: 23,000–23,100" in text
    unknown = c.Signal(model_id="m", timeframe="5m", bar_end=at(D1, 10, 0), side="buyer", entry_zone=c.PriceRange(1, 2),
                       card=SignalStructure(steps=("token-without-a-phrase",)))
    text, note = R.card_line(unknown, result, "TESTSTOCK")
    assert text is None and "card text not shown" in note


def test_a_card_without_a_phrase_is_stored_without_text():
    tenant_id, _ = _org()
    engine = FakeEngine(card=SignalStructure(steps=("token-without-a-phrase",)))
    _due(engine, FakeCollect(at(D1, 10, 0)), at(D1, 10, 0, 10))
    [row] = _signals(tenant_id)
    assert json.loads(row.reasons_json) == []
    assert any("card text not shown" in n for n in _records(tenant_id)[0]["runs"][-1]["notes"])


# --- lock, retries, grace ------------------------------------------------------------------------------------------------------

def test_the_per_symbol_lock_keeps_a_second_run_out():
    tenant_id, _ = _org()
    engine, collect = FakeEngine(), FakeCollect(at(D1, 10, 0))
    R._held.add(R.lock_name(tenant_id, KEY))
    report = _due(engine, collect, at(D1, 10, 0, 10))
    assert report.busy == 1 and _signals(tenant_id) == [] and _records(tenant_id) == []   # not an attempt
    R._held.clear()
    _due(engine, collect, at(D1, 10, 0, 40))
    assert len(_signals(tenant_id)) == 1


def test_retries_are_bounded():
    tenant_id, _ = _org(max_attempts=3, grace_seconds=600)
    engine, collect = FakeEngine(raises=RuntimeError("made up")), FakeCollect(at(D1, 10, 0))
    for second in (10, 70, 130, 190, 250):
        _due(engine, collect, at(D1, 10, 0) + timedelta(seconds=second))
    record = _records(tenant_id)[0]
    assert [r["attempt"] for r in record["runs"]] == [1, 2, 3] and record["final"] is True
    assert record["status"] == R.FAILED and "RuntimeError" in record["runs"][-1]["reason"]
    assert _signals(tenant_id) == []


def test_the_grace_window_ends_the_retries_and_a_late_bar_is_not_read():
    tenant_id, _ = _org(max_attempts=10, grace_seconds=120)
    engine = FakeEngine()
    _due(engine, FakeCollect(at(D1, 9, 59)), at(D1, 10, 0, 10))           # data short: attempt 1
    _due(engine, FakeCollect(at(D1, 10, 0)), at(D1, 10, 3, 0))            # past close + delay + grace: given up, not read
    assert _signals(tenant_id) == []
    run = _records(tenant_id)[0]["runs"][-1]
    assert run["final"] is True and "given up" in run["reason"] and "grace" in run["reason"]
    third, _ = _org(grace_seconds=600)
    _due(engine, FakeCollect(at(D1, 9, 59)), at(D1, 10, 0, 10))           # attempt 1 of the 10:00 bar ...
    _due(engine, FakeCollect(at(D1, 10, 5)), at(D1, 10, 5, 10))           # ... and the 10:05 bar closed meanwhile
    runs = _records(third)[0]["runs"]
    assert runs[0]["final"] is True and "a newer bar closed" in runs[0]["reason"] and runs[-1]["status"] == R.SIGNAL
    other, _ = _org()
    _due(FakeEngine(), FakeCollect(at(D1, 10, 0)), at(D1, 10, 4, 0))       # first seen after its grace window
    assert _signals(other) == [] and _records(other)[0]["status"] == R.MISSED


# --- settings, the feed, the boundary ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,words", [
    ({}, "user_id"), ({"user_id": 1, "symbols": [{}]}, "no key"), ({"user_id": 1, "signal_timeframe": "2m"}, "timeframe"),
    ({"user_id": 1, "delay_seconds": -1}, "delay_seconds"), ({"user_id": 1, "max_attempts": 0}, "max_attempts"),
    ({"user_id": 1, "symbols": [{"key": "A"}, {"key": "A"}]}, "twice"), ({"user_id": 1, "engine_settings": [1]}, "engine settings"),
])
def test_settings_that_cannot_be_used_are_refused(raw, words):
    with pytest.raises(R.SettingsProblem, match=words):
        R.parse_settings(1, raw)


def test_settings_defaults_are_operational_only():
    cfg = R.parse_settings(3, {"user_id": 5, "symbols": [{"key": "NSE_INDEX:TEST INDEX", "broker_symbol": "TEST INDEX"}]})
    assert (cfg.signal_timeframe, cfg.delay_seconds, cfg.grace_seconds, cfg.max_attempts) == ("5m", 3.0, 120.0, 3)
    assert cfg.symbols[0].shown == "TEST INDEX" and cfg.engine_settings == {}


def test_the_inbox_shows_the_engine_by_name_never_its_id():
    assert (feed.ENGINE_STRATEGY_ID, feed.ENGINE_DISPLAY_NAME) == (R.STRATEGY_ID, R.DISPLAY_NAME)
    assert feed.strategy_name(R.STRATEGY_ID) == "Signal engine"


def test_the_runner_reaches_no_order_path():
    text = Path(R.__file__).read_text()
    assert "atp_private" not in text and "place_order" not in text and "submit_order" not in text
    tree = ast.parse(text)
    imported = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    banned = ("app.execution", "app.paper", "app.live", "app.brokers", "app.deployments", "app.ai", "app.risk_engine")
    assert not [m for m in imported if m.startswith(banned)]
    trading = [(n.module, [a.name for a in n.names]) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app.trading")]
    assert trading == [("app.trading.persistence", ["persist_signal_history"])]
