"""S4b-2: intrabar alerts - opt-in per rule (`fire_on = "intrabar"`) behind the `screener_intrabar` flag (off by default):
evaluated on the bar still forming, during the session only, at most once per bar and symbol, throttled like bar-close
retries, marked `intrabar: true`; bar-close rules are unchanged."""
import asyncio
import json
from datetime import date, timedelta, timezone

import pandas as pd
from sqlalchemy import update

from app.alerts import barclose
from app.alerts.barclose import forming_bar
from app.db.models import AlertRuleRecord
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.screener.runtime import SymbolData, resample
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner
from tests.test_s4a_barclose import TUE, _bars, _events, _rule, ist, utc


def _run(coro):
    return asyncio.run(coro)


def _flags(**on):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            for name, value in on.items():
                flags[name] = {"on": value, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


class FormingFetch:
    def __init__(self, frames):
        self.frames, self.calls = frames, []

    async def __call__(self, session, tenant_id, symbols, exchange, base_tf, lookback, forming=False):
        self.calls.append(forming)
        return [SymbolData(s, {base_tf: self.frames[s]}) for s in symbols if s in self.frames], {}, "test"


def _evaluate(rule_id, now, fetch, enabled=True):
    async def go():
        async with _session_factory() as session:
            rule = await session.get(AlertRuleRecord, rule_id)
            result = await barclose.evaluate_rule(session, rule, now, fetch, intrabar_enabled=enabled)
            await session.commit()
            return result, rule.last_problem, rule.last_bar_at
    return _run(go())


def test_forming_bar_only_inside_the_session():
    assert forming_bar(ist(TUE, 9, 22), "5m") == utc(ist(TUE, 9, 20))
    assert forming_bar(ist(TUE, 9, 15), "15m") == utc(ist(TUE, 9, 15))
    assert forming_bar(ist(TUE, 15, 29), "1h") == utc(ist(TUE, 15, 15))                         # the short last bucket
    assert forming_bar(ist(TUE, 12, 0), "1d") == utc(ist(TUE, 9, 15))
    assert forming_bar(ist(TUE, 9, 14), "5m") is None and forming_bar(ist(TUE, 15, 30), "5m") is None
    assert forming_bar(ist(date(2026, 3, 14), 11, 0), "5m") is None                              # Saturday
    assert forming_bar(ist(TUE, 11, 0), "5m", holidays={TUE}) is None
    assert forming_bar(ist(TUE, 11, 0), "1w") is None
    assert "screener_intrabar" in DEFAULT_OFF_FLAGS


def test_an_intrabar_rule_fires_on_the_forming_bar_once_and_can_still_match_later_in_it():
    _, me = _owner("s4b2-intrabar@example.com")
    rid = _rule(me["tenant_id"], fire_on="intrabar")
    # 09:20 closed at 99, 09:25 forming at 98 -> no match yet.
    fetch = FormingFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 99, 98])})
    assert _evaluate(rid, ist(TUE, 9, 26), fetch)[0] == ("done", 0) and fetch.calls == [True]
    # Within the retry pause: not looked at again.
    assert _evaluate(rid, ist(TUE, 9, 26) + timedelta(seconds=30), fetch)[0] == ("waiting", 0)
    # Later in the same bar the forming close crosses 100: it fires now, before the close, marked intrabar.
    fetch.frames["TCS"] = _bars(ist(TUE, 9, 15), [99, 99, 105])
    (state, problem, last_bar) = _evaluate(rid, ist(TUE, 9, 27) + timedelta(seconds=30), fetch)
    assert state == ("done", 1) and problem is None and last_bar is None
    ev = _events(rid)
    assert ev[0].bar_time.replace(tzinfo=timezone.utc) == utc(ist(TUE, 9, 25))
    assert json.loads(ev[0].values_json)["intrabar"] is True and json.loads(ev[0].values_json)["close"] == 105.0
    # Still true a minute later in the same bar: no second event for that bar.
    assert _evaluate(rid, ist(TUE, 9, 28) + timedelta(seconds=40), fetch)[0] == ("done", 0) and len(_events(rid)) == 1
    # Outside the session nothing is forming: skipped, no fetch.
    calls = len(fetch.calls)
    assert _evaluate(rid, ist(TUE, 16, 0), fetch)[0] == ("skipped", 0) and len(fetch.calls) == calls


def test_intrabar_needs_the_flag_and_bar_close_rules_are_unchanged():
    _, me = _owner("s4b2-flag@example.com")
    rid = _rule(me["tenant_id"], fire_on="intrabar")
    fetch = FormingFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 105, 50])})
    (state, problem, _) = _evaluate(rid, ist(TUE, 9, 27), fetch, enabled=False)
    assert state == ("unsupported", 0) and "screener_intrabar" in problem and fetch.calls == []
    # A bar-close rule on the same data decides on the CLOSED 09:20 bar (105) and never on the forming 09:25 bar (50).
    closed = _rule(me["tenant_id"], name="closed")
    assert _evaluate(closed, ist(TUE, 9, 27), fetch, enabled=True)[0] == ("done", 1) and fetch.calls == [False]
    assert _events(closed)[0].bar_time.replace(tzinfo=timezone.utc) == utc(ist(TUE, 9, 20))
    assert "intrabar" not in json.loads(_events(closed)[0].values_json)


def test_resample_keeps_the_forming_bucket_only_when_asked():
    base = _bars(ist(TUE, 9, 15), [1, 2, 3, 4], minutes=1)                                     # 09:15..09:18 (1m)
    assert len(resample(base, "1m", "3m")) == 1                                                 # 09:18 bucket not closed: dropped
    kept = resample(base, "1m", "3m", keep_forming=True)
    assert len(kept) == 2 and kept.iloc[-1]["close"] == 4 and kept.index[-1] == pd.Timestamp(utc(ist(TUE, 9, 18)))


def test_the_api_refuses_intrabar_rules_while_the_flag_is_off_and_run_due_uses_the_flag():
    headers, me = _owner("s4b2-api@example.com")
    body = {"name": "r", "kind": "instrument", "symbol": "TCS", "condition": "close > 100", "base_tf": "5m"}
    _flags(screener_v2=True)
    try:
        assert client.post("/api/alerts/rules", headers=headers, json={**body, "fire_on": "intrabar"}).status_code == 409
        plain = client.post("/api/alerts/rules", headers=headers, json=body)
        assert plain.status_code == 201 and plain.json()["fire_on"] == "bar_close"
        _flags(screener_intrabar=True)
        made = client.post("/api/alerts/rules", headers=headers, json={**body, "fire_on": "intrabar"})
        assert made.status_code == 201 and made.json()["fire_on"] == "intrabar"

        async def only_this():
            async with _session_factory() as session:
                await session.execute(update(AlertRuleRecord).where(AlertRuleRecord.id != made.json()["id"]).values(status="paused"))
                await session.commit()
        _run(only_this())
        fetch = FormingFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 99, 105])})

        async def due():
            async with _session_factory() as session:
                return await barclose.run_due(session, ist(TUE, 9, 27), fetch=fetch, holidays=set())
        out = _run(due())
        assert (out.rules, out.evaluated, out.fired) == (1, 1, 1) and fetch.calls == [True]
    finally:
        _flags(screener_v2=False, screener_intrabar=False)
