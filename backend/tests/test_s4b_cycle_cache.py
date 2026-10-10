"""S4b-1: one worker cycle shares its work - rules on the same symbol, timeframe and bar fetch once (a longer
lookback fetches again), the same screen over the same symbols and bar is evaluated once, and nothing is carried
into the next cycle."""
import asyncio
import json

from sqlalchemy import update

from app.alerts import barclose
from app.db.models import AlertRuleRecord, ScreenRecord
from tests.test_auth_api import _session_factory
from tests.test_phase_l_ai import _owner
from tests.test_s3a_notification_service import _flag
from tests.test_s4a_barclose import TUE, FakeFetch, _bars, _events, _rule, ist


def _run(coro):
    return asyncio.run(coro)


def _only(tenant_id):
    async def go():
        async with _session_factory() as session:
            await session.execute(update(AlertRuleRecord).where(AlertRuleRecord.tenant_id != tenant_id).values(status="paused"))
            await session.commit()
    _run(go())


def _due(now, fetch):
    async def go():
        async with _session_factory() as session:
            return await barclose.run_due(session, now, fetch=fetch, holidays=set())
    return _run(go())


def test_rules_on_the_same_symbol_and_bar_fetch_once():
    _, me = _owner("s4b-fetch@example.com")
    _only(me["tenant_id"])
    a = _rule(me["tenant_id"], name="a", condition="close > 100")
    b = _rule(me["tenant_id"], name="b", condition="volume > 10")
    longer = _rule(me["tenant_id"], name="c", condition="close > SMA(close, 3)")
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 100, 101, 105, 50])})        # 09:35 forming
    _flag(True)
    try:
        out = _due(ist(TUE, 9, 36), fetch)
        assert (out.rules, out.evaluated, out.fired) == (3, 3, 3)
        assert fetch.calls == 2 and out.cache.fetched_symbols == 2 and out.cache.reused_symbols == 1   # the SMA rule needs more bars
        assert [len(_events(r)) for r in (a, b, longer)] == [1, 1, 1]
        nxt = _due(ist(TUE, 9, 36), fetch)                                                        # same bar: nothing to do
        assert (nxt.evaluated, fetch.calls) == (0, 2)
        fetch.frames["TCS"] = _bars(ist(TUE, 9, 15), [99, 100, 101, 105, 102, 50])
        later = _due(ist(TUE, 9, 41), fetch)                                                      # a new cycle starts empty
        assert later.evaluated == 3 and later.cache.fetched_symbols >= 1 and fetch.calls >= 3
    finally:
        _flag(False)


def test_the_same_screen_over_the_same_symbols_is_evaluated_once(monkeypatch):
    _, me = _owner("s4b-screen@example.com")
    _only(me["tenant_id"])

    async def screen():
        async with _session_factory() as session:
            from app.screener import compile_screen, nodes
            ast, _ = compile_screen("close > 100", base_tf="5m")
            row = ScreenRecord(tenant_id=me["tenant_id"], name="s", source_text="close > 100", ast_json=json.dumps(nodes.to_json(ast)), base_tf="5m",
                               params_json="{}")
            session.add(row)
            await session.commit()
            return row.id
    sid = _run(screen())
    universe = json.dumps(["TCS", "INFY"])
    r1 = _rule(me["tenant_id"], name="one", kind="screen", symbol=None, condition=None, screen_id=sid, universe_json=universe)
    r2 = _rule(me["tenant_id"], name="two", kind="screen", symbol=None, condition=None, screen_id=sid, universe_json=universe)
    calls = []
    real = barclose.run_screen

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)
    monkeypatch.setattr(barclose, "run_screen", counting)
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 105]), "INFY": _bars(ist(TUE, 9, 15), [99, 90])})
    _flag(True)
    try:
        out = _due(ist(TUE, 9, 26), fetch)
        assert (out.evaluated, out.fired, len(calls), out.cache.reused_results) == (2, 2, 1, 1)
        assert [e.symbol for e in _events(r1)] == ["TCS"] and [e.symbol for e in _events(r2)] == ["TCS"]   # each rule gets its own event
        assert fetch.calls == 1
    finally:
        _flag(False)
