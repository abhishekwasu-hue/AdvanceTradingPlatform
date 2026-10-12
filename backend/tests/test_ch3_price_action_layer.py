"""N4 CH3: the price-action chart layer (GET /api/charts/layers/price-action).

* golden: the layer on the frozen synthetic 15m fixture is byte-identical to tests/fixtures/charts/pa_layer_golden.json,
  and the frontend's copy (frontend/src/charting/__fixtures__/) is byte-identical to it;
* no look-ahead: the layer at bar N never changes when bars after N are added or altered (new bars, higher versions of
  later bars, another source) - checked through the API on the lake and on the pure function at every 7th bar;
* replay never repaints: with a fixed `start`, each later step keeps every confirmed swing, break and leg of the step
  before (it only adds), and nothing in a step is later than its `at`;
* the lake's own 15m bars and 15m rebuilt from its 1-minute bars give the same layer;
* flag `chart_price_action` off by default (503), per organisation; input checks (time zones, window, bar cap).
Synthetic bars only - no broker, no network, no NIFTY data. Nothing here touches an order.
"""
import asyncio
import uuid
from datetime import timedelta

import pandas as pd
import pytest
from sqlalchemy import delete

from app.charts import price_action_layer as L
from app.db.models import MdCandleRecord
from app.platform import controls
from tests.ch3_pa_cases import FRONTEND_PATH, GOLDEN_SESSIONS, PATH, dumps, frame, golden, golden_cut
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_zones_store_job import _minutes_from_15m

NATIVE, FROM_1M, EMPTY = "NSE:PALAYERA", "NSE:PALAYERB", "NSE:PALAYERC"
URL = "/api/charts/layers/price-action"
F = frame()
IST = "Asia/Kolkata"


def _run(coro):
    return asyncio.run(coro)


def _lake_rows(key: str, timeframe: str, bars: pd.DataFrame, source: str = "test", version: int = 1):
    return [MdCandleRecord(instrument_key=key, timeframe=timeframe, ts=pd.Timestamp(b.ts).to_pydatetime(), source=source, version=version,
                           open=float(b.open), high=float(b.high), low=float(b.low), close=float(b.close), volume=0,
                           ingested_at=pd.Timestamp(b.ts).to_pydatetime() + timedelta(seconds=30)) for b in bars.itertuples()]


def _native(f: pd.DataFrame) -> pd.DataFrame:
    return f.assign(ts=f["bar_end"])                               # the lake labels a bar by its END


async def _put(rows):
    async with _session_factory() as session:
        session.add_all(rows)
        await session.commit()


async def _drop(keys):
    async with _session_factory() as session:
        await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_(keys)))
        await session.commit()


@pytest.fixture(scope="module", autouse=True)
def _lake():
    _run(_drop([NATIVE, FROM_1M, EMPTY]))
    _run(_put(_lake_rows(NATIVE, "15m", _native(F))))
    s15 = F.assign(timestamp=F["timestamp"].dt.tz_convert(IST).dt.tz_localize(None))
    _run(_put(_lake_rows(FROM_1M, "1m", _minutes_from_15m(s15))))
    yield
    _run(_drop([NATIVE, FROM_1M, EMPTY]))


def _flag(on: bool, tenants=()):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            stored[L.FLAG] = {"on": on, "tenants": list(tenants)}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def owner():
    _flag(True)
    yield _owner(f"ch3-layer-{uuid.uuid4().hex[:10]}@example.com")
    _flag(False)


def _iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").isoformat()


def _start() -> str:
    return _iso(F["timestamp"].iloc[0] - pd.Timedelta(hours=1))


def _get(headers, key=NATIVE, **params):
    params = {"instrument": key, "tf": "15m", "start": _start(), **params}
    r = client.get(URL, params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _content(body):
    """The computed part (what `fingerprint` covers) - the request echo (instrument, at, start, source) left out."""
    return {k: v for k, v in body.items() if k not in ("instrument", "at", "start", "source", "concepts", "note", "reason", "fingerprint")}


def test_flag_is_off_by_default_and_per_organisation():
    assert L.FLAG in controls.DEFAULT_OFF_FLAGS and "places no orders" in controls.FEATURE_FLAGS[L.FLAG]
    headers, me = _owner(f"ch3-flag-{uuid.uuid4().hex[:10]}@example.com")
    other, _ = _owner(f"ch3-flag-b-{uuid.uuid4().hex[:10]}@example.com")
    assert client.get(URL, params={"instrument": NATIVE}, headers=headers).status_code == 503
    _flag(False, tenants=[me["tenant_id"]])
    try:
        params = {"instrument": NATIVE, "start": _start(), "at": _iso(F["bar_end"].iloc[-1])}
        assert client.get(URL, params=params, headers=headers).status_code == 200
        assert client.get(URL, params=params, headers=other).status_code == 503
    finally:
        _flag(False)
    assert client.get(URL, params={"instrument": NATIVE}).status_code == 401


def test_golden_files_are_current_and_byte_identical():
    text = dumps(golden())
    assert PATH.read_text(encoding="utf-8") == text, "python -m tests.ch3_pa_cases --write (after a deliberate change only)"
    assert FRONTEND_PATH.read_bytes() == PATH.read_bytes()


def test_the_api_serves_the_golden_layer(owner):
    headers, _ = owner
    want = golden()
    body = _get(headers, at=_iso(golden_cut()), mtf="false")
    assert body["source"] == "lake:15m" and body["instrument"] == NATIVE and body["reason"] is None and "not signals" in body["note"]
    assert dumps(_content(body)) == dumps({k: v for k, v in want["default"].items() if k != "fingerprint"})
    assert body["fingerprint"] == want["default"]["fingerprint"] == L.fingerprint(_content(body))
    dbg = _get(headers, at=_iso(golden_cut()), debug="true", mtf="false")
    assert dbg["fingerprint"] == want["debug"]["fingerprint"] and dbg["debug_info"] == want["debug"]["debug_info"]
    # default view: the chosen degree only, nearest live levels; debug: every degree, tentative pivots, broken levels
    assert {s["degree"] for s in body["swings"]} == {L.DEFAULT_DEGREE} and all(s["status"] == "confirmed" for s in body["swings"])
    assert {s["degree"] for s in dbg["swings"]} == {0, 1, 2, 3} and any(s["status"] == "tentative" for s in dbg["swings"])
    assert any(lv["status"] == "broken" for lv in dbg["levels"]) and all(lv["status"] == "live" for lv in body["levels"])
    assert [t["degree"] for t in body["trend"]] == [0, 1, 2, 3] and all(t["trend"] in ("up", "down", "range", "none") for t in body["trend"])
    # another degree is another view of the same bars
    d1 = _get(headers, at=_iso(golden_cut()), degree=1)
    assert {s["degree"] for s in d1["swings"]} == {1} and len(d1["swings"]) > len(body["swings"])


def test_no_lookahead_bars_after_at_never_change_the_layer(owner):
    headers, _ = owner
    cuts = [F["bar_end"].iloc[i] for i in (120, 233, 299, 410)]
    before = {c: _get(headers, at=_iso(c)) for c in cuts}
    last = cuts[-1]
    later = F[F["bar_end"] > cuts[0]]
    # altered later bars (a higher version), the same bars from another source, and brand-new bars after the fixture
    moved = later.assign(high=later["high"] * 1.03, low=later["low"] * 0.97, close=later["close"] * 1.02)
    tail = F.tail(60).assign(timestamp=F.tail(60)["timestamp"] + pd.Timedelta(days=60), bar_end=F.tail(60)["bar_end"] + pd.Timedelta(days=60))
    _run(_put(_lake_rows(NATIVE, "15m", _native(moved), version=2) + _lake_rows(NATIVE, "15m", _native(later.assign(close=later["close"] * 0.9)), source="zz-other")
              + _lake_rows(NATIVE, "15m", _native(tail))))
    try:
        for c in cuts[:1]:
            assert _get(headers, at=_iso(c)) == before[c]
        # the later cuts do see the altered bars at and before them - only bars after `at` are invisible
        assert _get(headers, at=_iso(last))["fingerprint"] != before[last]["fingerprint"]
    finally:
        async def undo():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == NATIVE,
                                                                   (MdCandleRecord.version == 2) | (MdCandleRecord.source == "zz-other")
                                                                   | (MdCandleRecord.ts > F["bar_end"].iloc[-1].to_pydatetime())))
                await session.commit()
        _run(undo())
    assert all(_get(headers, at=_iso(c)) == before[c] for c in cuts)


def test_no_lookahead_on_the_pure_layer_at_every_seventh_bar():
    """Layer(bars <= N) == layer(bars <= N of a series whose later bars were replaced), for the debug view (everything)."""
    for n in range(40, len(F), 7):
        noisy = F.copy()
        noisy.loc[n:, ["open", "high", "low", "close"]] = F.loc[n:, ["open", "high", "low", "close"]].to_numpy() * (1.0 + 0.004 * (n % 5 - 2))
        a = L.build(F.iloc[:n], "15m", L.DEFAULT_DEGREE, True)
        b = L.build(noisy.iloc[:n], "15m", L.DEFAULT_DEGREE, True)
        assert L.canonical(a) == L.canonical(b), n


def test_replay_only_adds_and_never_shows_a_later_time(owner):
    headers, _ = owner
    steps = [F["bar_end"].iloc[i] for i in range(150, 330, 9)]
    prev = None
    for c in steps:
        body = _get(headers, at=_iso(c), debug="true")
        at = int(pd.Timestamp(c).timestamp())
        assert pd.Timestamp(body["as_of"]) <= pd.Timestamp(c)
        assert all(s["time"] < at and (s["confirmed_time"] is None or s["confirmed_time"] <= at) for s in body["swings"])
        assert all(b["confirmed_time"] <= at for b in body["breaks"]) and all(leg["to"]["time"] < at for leg in body["legs"])
        if prev is not None:
            def confirmed(x):
                return [(s["degree"], s["kind"], s["price"], s["time"], s["confirmed_time"]) for s in x["swings"] if s["status"] == "confirmed"]
            def legs(x):
                return [(g["degree"], g["from"]["time"], g["to"]["time"]) for g in x["legs"] if g["status"] == "confirmed"]
            def breaks(x):
                return [(b["degree"], b["pivot_time"], b["time"]) for b in x["breaks"]]
            assert set(confirmed(prev)) <= set(confirmed(body)) and set(legs(prev)) <= set(legs(body)), c
            # a break of a swing among the newest MAX_BREAK_PIVOTS stays (the oldest drop out of the search window)
            newest = {(b[0], b[1]) for b in breaks(body)}
            assert {b for b in breaks(prev) if (b[0], b[1]) in newest} <= set(breaks(body)), c
        prev = body


def test_lake_15m_and_15m_rebuilt_from_1m_give_the_same_layer(owner):
    headers, _ = owner
    for i in (99, GOLDEN_SESSIONS * 25 - 1, len(F) - 1):
        at = _iso(F["bar_end"].iloc[i])
        native, rebuilt = _get(headers, NATIVE, at=at, mtf="false"), _get(headers, FROM_1M, at=at, mtf="false")
        assert rebuilt["source"] == "lake:1m" and native["source"] == "lake:15m"
        assert rebuilt["fingerprint"] == native["fingerprint"] and _content(rebuilt) == _content(native)
    # mid-bar: the forming 15m bar (only some of its minutes are in) is not read
    mid = _iso(F["bar_end"].iloc[99] + pd.Timedelta(minutes=7))
    assert _get(headers, FROM_1M, at=mid, mtf="false")["fingerprint"] == _get(headers, NATIVE, at=_iso(F["bar_end"].iloc[99]), mtf="false")["fingerprint"]


def test_input_checks_and_empty_answers(owner, monkeypatch):
    headers, _ = owner
    at = _iso(F["bar_end"].iloc[200])
    naive = pd.Timestamp(F["bar_end"].iloc[200]).tz_localize(None).isoformat()
    assert client.get(URL, params={"instrument": NATIVE, "at": naive}, headers=headers).status_code == 422
    assert client.get(URL, params={"instrument": NATIVE, "at": at, "start": naive}, headers=headers).status_code == 422
    assert client.get(URL, params={"instrument": NATIVE, "at": at, "start": at}, headers=headers).status_code == 422
    far = _iso(F["bar_end"].iloc[200] - pd.Timedelta(days=L.MAX_DAYS["15m"] + 1))
    r = client.get(URL, params={"instrument": NATIVE, "at": at, "start": far}, headers=headers)
    assert r.status_code == 422 and "at most" in r.text
    assert client.get(URL, params={"instrument": NATIVE, "tf": "2m"}, headers=headers).status_code == 422
    assert client.get(URL, params={"instrument": NATIVE, "degree": 4}, headers=headers).status_code == 422
    monkeypatch.setattr(L, "MAX_BARS", 100)
    r = client.get(URL, params={"instrument": NATIVE, "at": at, "start": _start()}, headers=headers)
    assert r.status_code == 422 and "more than 100" in r.text                      # refused, never silently cut
    monkeypatch.undo()
    for key in (EMPTY, "NFO:BANKNIFTY 26OCT 52000 CE"):
        body = _get(headers, key, at=at)
        assert body["bars"] == 0 and body["swings"] == [] and body["reason"] == L.NO_BARS and body["as_of"] is None
    # a future `at` reads as now; the default window is DEFAULT_DAYS before it
    body = client.get(URL, params={"instrument": EMPTY, "at": "2999-01-01T00:00:00+00:00"}, headers=headers).json()
    assert pd.Timestamp(body["at"]) - pd.Timestamp(body["start"]) == pd.Timedelta(days=L.DEFAULT_DAYS["15m"])
    assert pd.Timestamp(body["at"]).year < 2999
