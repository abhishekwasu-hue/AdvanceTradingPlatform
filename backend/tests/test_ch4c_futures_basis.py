"""N4 CH4c: an underlying's futures basis, carry, calendar spread and rollover from the shared lake
(GET /api/charts/contracts/basis).

* near / next / far by expiry per trade date (the expiry day is still the near month's), the roll to the next month; a
  contract with no row on a day is a gap there, not a roll;
* basis, basis %, annualised carry, calendar spread, the three months' OI and rollover %, the index spot for an index future;
* as of `at`: later bars and a correction ingested after `at` change nothing; broker / stream rows are never read; a
  later-stamped vendor row never beats the bhavcopy on the same trade date;
* the futures keys in chunks under the bind-parameter limit;
* flags `chart_futures_basis` and `chart_contracts` off by default (503); refusals.
Synthetic rows only - no network, no broker. Nothing here places an order.
"""
import asyncio
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.charts import contracts as C
from app.charts import futures_basis as B
from app.db.models import MdCandleRecord
from app.platform import controls
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

SYM = "CH4CSTK"
OCT, NOV, DEC = date(2026, 9, 29), date(2026, 10, 27), date(2026, 11, 24)    # (named by position: near, next, far on day 1)
DAYS = [date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]       # the 29th: the near month's expiry day


def _run(coro):
    return asyncio.run(coro)


def _end(d, hh=10):
    return datetime.combine(d, time(hh, 0), tzinfo=timezone.utc)


def _bar(key, d, close, *, oi=1000, source="nse_bhav:fo", version=1, ingested=None, hh=10):
    return MdCandleRecord(instrument_key=key, timeframe="day", ts=_end(d, hh), source=source, version=version, open=close, high=close,
                          low=close, close=close, volume=10, oi=oi, ingested_at=ingested or _end(d) + timedelta(hours=6))


def _fut(exp):
    return C.contract_key(SYM, "FUT", exp)


def _put(rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _flags(**on):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, value in on.items():
                stored[name] = {"on": value, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def _clear():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.like("%CH4C%")))
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _lake():
    _clear()
    rows = []
    for i, d in enumerate(DAYS):
        if d <= OCT:
            rows.append(_bar(_fut(OCT), d, 101.0 + i, oi=1000 - 100 * i))
        rows.append(_bar(_fut(NOV), d, 102.0 + i, oi=200 + 100 * i))
        rows.append(_bar(_fut(DEC), d, 103.0 + i, oi=50))
        rows.append(_bar(f"NSE:{SYM}", d, 100.0 + i, oi=None, source="nse_bhav:cm"))
    _put(rows)
    yield
    _clear()
    _flags(chart_contracts=False, chart_futures_basis=False)


def _read(at=None):
    async def go():
        async with _session_factory() as session:
            when = at or _end(DAYS[-1]) + timedelta(hours=8)
            keys = [_fut(e) for e in B.expiry_candidates(DAYS[0] - timedelta(days=5), when.date())]
            futures = await B.read_daily(session, keys, _end(DAYS[0]) - timedelta(days=5), when)
            spot = (await B.read_daily(session, [B.spot_key(SYM)], _end(DAYS[0]) - timedelta(days=5), when)).get(B.spot_key(SYM), {})
            return B.build(SYM, futures, spot)
    return _run(go())


def test_near_next_far_and_the_roll_after_the_expiry_day():
    out = _read()
    assert out["days"] == [d.isoformat() for d in DAYS]
    assert out["near"]["expiry"] == [OCT.isoformat()] * 3 + [NOV.isoformat()]          # the expiry day is still October's
    assert out["next"]["expiry"][:3] == [NOV.isoformat()] * 3 and out["next"]["expiry"][3] == DEC.isoformat()
    assert out["far"]["expiry"][:3] == [DEC.isoformat()] * 3 and out["far"]["expiry"][3] is None
    assert out["rolls"] == [DAYS[3].isoformat()] and out["dte"] == [4, 1, 0, 27]


def test_basis_carry_calendar_and_rollover():
    out = _read()
    # day 1: near 101 vs spot 100, 4 days to expiry; OI 1000 + 200 + 50
    assert out["basis"][0] == 1.0 and out["basis_pct"][0] == 1.0 and out["carry_pct"][0] == round(1.0 * 365 / 4, 4)
    assert out["calendar"][0] == 1.0 and out["total_oi"][0] == 1250 and out["rollover_pct"][0] == round(250 / 1250 * 100, 4)
    # the expiry day: at least one day of carry
    assert out["carry_pct"][2] == round((103.0 / 102.0 - 1) * 100 * 365 / 1, 4)
    # after the roll: the next month is near (105 vs spot 103), the far month next
    assert out["near"]["close"][3] == 105.0 and out["basis"][3] == 2.0 and out["calendar"][3] == 1.0
    assert out["total_oi"][3] == 500 + 50 and out["rollover_pct"][3] == round(50 / 550 * 100, 4)


def test_a_near_month_with_no_row_on_a_day_is_a_gap_not_a_roll():
    # the bhavcopy leaves an untraded contract out: the near month has no row on the 28th
    async def drop():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == _fut(OCT), MdCandleRecord.ts == _end(DAYS[1])))
            await session.commit()
    _run(drop())
    out = _read()
    assert out["days"] == [d.isoformat() for d in DAYS]
    assert out["near"]["expiry"] == [OCT.isoformat()] * 3 + [NOV.isoformat()]          # still October's on the 28th
    assert out["next"]["expiry"][1] == NOV.isoformat() and out["far"]["expiry"][1] == DEC.isoformat()
    assert out["rolls"] == [DAYS[3].isoformat()]                                       # no spurious roll on the 28th / 29th
    assert out["near"]["close"][1] is None and out["near"]["oi"][1] is None
    assert out["next"]["close"][1] == 103.0 and out["far"]["close"][1] == 104.0        # the other months keep their places
    assert out["spot"][1] == 101.0 and out["dte"][1] == 1
    for k in ("basis", "basis_pct", "carry_pct", "calendar", "total_oi", "rollover_pct"):
        assert out[k][1] is None, k                                                    # nothing built on the next month instead
    assert out["basis"][0] == 1.0 and out["basis"][2] == 1.0                           # the days around it are unchanged


def test_as_of_shared_sources_and_the_trade_date_preference():
    before = _read()
    late = _end(DAYS[-1]) + timedelta(days=5)
    _put([_bar(_fut(OCT), DAYS[0], 999.0, oi=1, version=2, ingested=late),                      # a correction ingested later
          _bar(_fut(NOV), DAYS[0], 555.0, oi=9, source="stream:kite", version=7),               # one organisation's stream
          _bar(f"NSE:{SYM}", DAYS[0], 1.0, oi=None, source="broker:kite", version=7),           # one organisation's broker bars
          _bar(_fut(DEC), DAYS[0], 777.0, oi=77, source="vendor:test", hh=18)])                 # shared, later stamp, less preferred
    assert _read() == before                                                                    # as of the original time
    after = _read(late + timedelta(hours=1))
    assert after["near"]["close"][0] == 999.0 and after["next"]["close"][0] == 102.0 and after["spot"][0] == 100.0
    assert after["far"]["close"][0] == 103.0                                                    # the bhavcopy, not the vendor row


def test_index_futures_read_the_index_close():
    from app.universe.fo_eod import code_for
    assert B.spot_key("NIFTY") == f"NSE_INDEX:{code_for('NIFTY 50')}" and B.spot_key("tcs") == "NSE:TCS"


def test_the_keys_are_read_in_chunks_under_the_bind_parameter_limit():
    from sqlalchemy import event

    from tests.test_auth_api import _engine
    keys = [_fut(date(2000, 1, 1) + timedelta(days=i)) for i in range(12_001)]
    counts = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        counts.append(len(parameters) if isinstance(parameters, (list, tuple, dict)) else 0)
    event.listen(_engine.sync_engine, "before_cursor_execute", capture)
    try:
        async def go():
            async with _session_factory() as session:
                return await B.read_daily(session, keys, _end(DAYS[0]) - timedelta(days=5), _end(DAYS[-1]) + timedelta(hours=8))
        _run(go())
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", capture)
    assert len(counts) == 3 and max(counts) <= B.KEYS_PER_QUERY + 20


def test_the_route_is_off_by_default_then_answers():
    headers, _ = _owner("ch4c-basis@example.com")
    url = "/api/charts/contracts/basis"
    assert "chart_futures_basis" in controls.DEFAULT_OFF_FLAGS and "places no orders" in controls.FEATURE_FLAGS["chart_futures_basis"]
    r = client.get(url, params={"underlying": SYM}, headers=headers)
    assert r.status_code == 503
    _flags(chart_contracts=True)
    r = client.get(url, params={"underlying": SYM}, headers=headers)
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == B.FLAG
    _flags(chart_futures_basis=True)
    at = (_end(DAYS[-1]) + timedelta(hours=8)).isoformat()
    r = client.get(url, params={"underlying": SYM.lower(), "at": at, "start": (_end(DAYS[0]) - timedelta(days=10)).isoformat()}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "basis/1" and body["days"] == [d.isoformat() for d in DAYS] and body["sources"] == ["nse_bhav"]
    assert "recommendations" in body["note"] and "reason" not in body
    assert client.get(url, params={"underlying": "BAD KEY!"}, headers=headers).status_code == 422
    assert client.get(url, params={"underlying": SYM, "at": "2026-10-28T10:00:00"}, headers=headers).status_code == 422
    assert client.get(url, params={"underlying": SYM, "at": at, "start": at}, headers=headers).status_code == 422
    empty = client.get(url, params={"underlying": "CH4CNONE", "at": at}, headers=headers).json()
    assert empty["days"] == [] and "no shared daily futures bars" in empty["reason"]
    assert client.get(url, params={"underlying": SYM}).status_code == 401
