"""S4a: the bar-close engine - the latest CLOSED bar on the NSE clock (short last bucket, daily close at 15:30,
weekends and holidays), forming bars never evaluated, stale data waits (with a retry pause) instead of firing,
one evaluation per bar, matches recorded with trigger values, screen rules over their own symbols, the flag, and
today's daily bar built from intraday bars."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select, update

from app.alerts import barclose
from app.alerts.barclose import closed_frame, expected_bar, with_today
from app.db.models import AlertEventRecord, AlertRuleRecord, ScreenRecord
from app.market_data.calendar import IST
from app.screener.runtime import SymbolData
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner
from tests.test_s3a_notification_service import _flag

TUE = date(2026, 3, 10)


def _run(coro):
    return asyncio.run(coro)


def ist(d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


def utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def test_expected_bar_follows_the_exchange_clock():
    assert expected_bar(ist(TUE, 9, 22), "5m") == utc(ist(TUE, 9, 15))
    assert expected_bar(ist(TUE, 9, 19), "5m") == utc(ist(date(2026, 3, 9), 15, 25))           # nothing closed yet today
    assert expected_bar(ist(TUE, 15, 20), "1h") == utc(ist(TUE, 14, 15))
    assert expected_bar(ist(TUE, 15, 31), "1h") == utc(ist(TUE, 15, 15))                        # the short last bucket
    assert expected_bar(ist(TUE, 15, 29), "1d") == utc(ist(date(2026, 3, 9), 9, 15))
    assert expected_bar(ist(TUE, 15, 30), "1d") == utc(ist(TUE, 9, 15))
    monday = date(2026, 3, 9)
    assert expected_bar(ist(monday, 8, 0), "1d") == utc(ist(date(2026, 3, 6), 9, 15))          # over the weekend
    assert expected_bar(ist(monday, 16, 0), "1d", holidays={monday}) == utc(ist(date(2026, 3, 6), 9, 15))
    assert expected_bar(ist(TUE, 12, 0), "1w") is None and expected_bar(ist(TUE, 12, 0), "1M") is None


def _bars(start: datetime, closes, minutes=5) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(utc(start + timedelta(minutes=minutes * i))) for i in range(len(closes))])
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000.0] * len(closes)}, index=idx)


def test_closed_frame_drops_the_forming_bar_and_refuses_stale_data():
    df = _bars(ist(TUE, 9, 15), [100, 101, 102, 103])                                          # 09:15 .. 09:30 (09:30 forming)
    kept = closed_frame(df, "5m", utc(ist(TUE, 9, 25)))
    assert kept is not None and kept.index[-1] == pd.Timestamp(utc(ist(TUE, 9, 25))) and len(kept) == 3
    assert closed_frame(df.iloc[:2], "5m", utc(ist(TUE, 9, 25))) is None                          # data not in yet
    daily = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.DatetimeIndex([pd.Timestamp(utc(ist(date(2026, 3, 9), 0, 0))), pd.Timestamp(utc(ist(TUE, 0, 0)))]))
    assert closed_frame(daily, "1d", utc(ist(TUE, 9, 15))) is not None                            # matched by IST date
    assert closed_frame(daily.iloc[:1], "1d", utc(ist(TUE, 9, 15))) is None


class FakeFetch:
    def __init__(self, frames):
        self.frames, self.calls = frames, 0

    async def __call__(self, session, tenant_id, symbols, exchange, base_tf, lookback):
        self.calls += 1
        return [SymbolData(s, {base_tf: self.frames[s]}) for s in symbols if s in self.frames], {}, "test"


def _rule(tenant_id, **kw):
    async def go():
        async with _session_factory() as session:
            row = AlertRuleRecord(tenant_id=tenant_id, name=kw.pop("name", "r"), kind=kw.pop("kind", "instrument"), symbol=kw.pop("symbol", "TCS"),
                                  condition_text=kw.pop("condition", "close > 100"), base_tf=kw.pop("tf", "5m"), priority="normal",
                                  cooldown_minutes=0, mode="instant", digest_every="hourly", status="active", created_at=ist(TUE, 9, 0),
                                  updated_at=ist(TUE, 9, 0), **kw)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _evaluate(rule_id, now, fetch):
    async def go():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rule_id)
            result = await barclose.evaluate_rule(session, rule, now, fetch)
            await session.commit()
            return result, rule.last_problem
    return _run(go())


def _events(rule_id):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rule_id).order_by(AlertEventRecord.id)))
    return _run(go())


def test_an_instrument_rule_fires_once_per_closed_bar_and_never_on_the_forming_bar():
    _, me = _owner("s4a-instrument@example.com")
    rid = _rule(me["tenant_id"])
    # 09:15 99, 09:20 105 (closed at 09:25), 09:25 forming at 50: the forming bar must not decide anything.
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 105, 50])})
    assert _evaluate(rid, ist(TUE, 9, 27), fetch) == (("done", 1), None)
    ev = _events(rid)
    assert len(ev) == 1 and ev[0].bar_time.replace(tzinfo=timezone.utc) == utc(ist(TUE, 9, 20))
    assert json.loads(ev[0].values_json) == {"as_of": pd.Timestamp(utc(ist(TUE, 9, 20))).isoformat(), "close": 105.0, "volume": 1000.0}
    assert _evaluate(rid, ist(TUE, 9, 28), fetch)[0] == ("skipped", 0) and fetch.calls == 1          # same bar: nothing to do

    # 09:30 bar expected at 09:36, but the broker still ends at 09:25 -> wait, record why, and pause before retrying.
    assert _evaluate(rid, ist(TUE, 9, 36), fetch) == (("waiting", 0), "waiting for the closed bar from the broker")
    assert _evaluate(rid, ist(TUE, 9, 36) + timedelta(seconds=30), fetch)[0] == ("waiting", 0) and fetch.calls == 2
    fetch.frames["TCS"] = _bars(ist(TUE, 9, 15), [99, 105, 50, 101, 40])
    assert _evaluate(rid, ist(TUE, 9, 37) + timedelta(seconds=30), fetch) == (("done", 1), None)
    assert [e.bar_time.replace(tzinfo=timezone.utc) for e in _events(rid)] == [utc(ist(TUE, 9, 20)), utc(ist(TUE, 9, 30))]


def test_a_screen_rule_runs_on_its_own_symbols_and_reports_a_missing_screen():
    _, me = _owner("s4a-screen@example.com")

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
    rid = _rule(me["tenant_id"], kind="screen", symbol=None, condition=None, screen_id=sid, universe_json=json.dumps(["TCS", "INFY", "WIPRO"]))
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 105]), "INFY": _bars(ist(TUE, 9, 15), [99, 90]), "WIPRO": _bars(ist(TUE, 9, 15), [99])})
    assert _evaluate(rid, ist(TUE, 9, 26), fetch)[0] == ("done", 1)                                # WIPRO is behind: skipped, not evaluated
    assert [e.symbol for e in _events(rid)] == ["TCS"]

    async def archive():
        async with _session_factory() as session:
            (await session.get(ScreenRecord, sid)).archived = True
            await session.commit()
    _run(archive())
    state, problem = _evaluate(rid, ist(TUE, 9, 31), fetch)
    assert state == ("error", 0) and "gone or archived" in problem


def test_run_due_respects_the_flag_and_expiry():
    _, me = _owner("s4a-flag@example.com")

    async def clean():
        async with _session_factory() as session:
            await session.execute(update(AlertRuleRecord).where(AlertRuleRecord.tenant_id != me["tenant_id"]).values(status="paused"))
            await session.commit()
    _run(clean())
    live = _rule(me["tenant_id"], name="live")
    _rule(me["tenant_id"], name="expired", expires_at=utc(ist(TUE, 9, 0)))
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [101, 102])})

    async def due(now):
        async with _session_factory() as session:
            return await barclose.run_due(session, now, fetch=fetch, holidays=set())
    assert _run(due(ist(TUE, 9, 26))).rules == 0                                                   # flag off: nothing runs
    _flag(True)
    try:
        async def no_time(now):
            async with _session_factory() as session:
                return await barclose.run_due(session, now, fetch=fetch, holidays=set(), budget_seconds=-1)
        boxed = _run(no_time(ist(TUE, 9, 26)))
        assert (boxed.rules, boxed.evaluated, boxed.deferred, fetch.calls) == (1, 0, 1, 0)             # out of time: still due next cycle
        out = _run(due(ist(TUE, 9, 26)))
        assert (out.rules, out.evaluated, out.fired) == (1, 1, 1) and [e.symbol for e in _events(live)] == ["TCS"]
    finally:
        _flag(False)


def test_todays_daily_bar_is_built_from_intraday_bars():
    daily = pd.DataFrame({"open": [10.0], "high": [12.0], "low": [9.0], "close": [11.0], "volume": [5.0]},
                         index=pd.DatetimeIndex([pd.Timestamp(utc(ist(date(2026, 3, 9), 9, 15)))]))
    intraday = _bars(ist(TUE, 9, 15), [20, 22, 21], minutes=15)
    out = with_today(daily, intraday, TUE)
    assert len(out) == 2 and out.iloc[-1]["close"] == 21 and out.iloc[-1]["high"] == 22 and out.iloc[-1]["volume"] == 3000
    assert closed_frame(out, "1d", utc(ist(TUE, 9, 15))) is not None
    assert with_today(out, intraday, TUE) is out                                                    # never twice


def test_screen_rules_need_their_symbols_in_the_api():
    headers, me = _owner("s4a-api@example.com")
    _flag(True)
    try:
        sid = client.post("/api/screener/screens", headers=headers, json={"name": "s", "source": "close > 100", "base_tf": "1d"}).json()["id"]
        assert client.post("/api/alerts/rules", headers=headers, json={"name": "r", "kind": "screen", "screen_id": sid}).status_code == 422
        made = client.post("/api/alerts/rules", headers=headers, json={"name": "r", "kind": "screen", "screen_id": sid, "symbols": ["tcs", "INFY", "tcs"]})
        assert made.status_code == 201 and made.json()["symbols"] == ["TCS", "INFY"] and made.json()["last_bar_at"] is None
    finally:
        _flag(False)
