"""N4 CH3: the price-action chart layer (GET /api/charts/layers/price-action).

* golden: the layer on the frozen synthetic 15m fixture is byte-identical to tests/fixtures/charts/pa_layer_golden.json,
  and the frontend's copy (frontend/src/charting/__fixtures__/) is byte-identical to it;
* no look-ahead: the layer at bar N never changes when bars after N are added or altered (new bars, higher versions of
  later bars, another source) - checked through the API on the lake; and the engine itself is truncation-invariant (the
  layer over bars[:n] == the layer over all bars filtered to what was confirmed by bar n), at every 7th n;
* provenance: shared-licensed sources only (never stream: / broker:), source before version, corrections point-in-time;
  reads capped (1-minute rebuild days, rows), times before 1990 refused, a per-user rate limit, the conversion's guards;
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


def _lake_rows(key: str, timeframe: str, bars: pd.DataFrame, source: str = "vendor:test", version: int = 1):
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


@pytest.fixture(autouse=True)
def _fresh():
    from app.core import rate_limit
    L.cache_clear()
    rate_limit.reset("chart_price_action")
    yield
    L.cache_clear()


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
    body = _get(headers, at=_iso(golden_cut()))
    assert body["source"] == "lake:15m" and body["instrument"] == NATIVE and body["reason"] is None and "not signals" in body["note"]
    assert dumps(_content(body)) == dumps({k: v for k, v in want["default"].items() if k != "fingerprint"})
    assert body["fingerprint"] == want["default"]["fingerprint"] == L.fingerprint(_content(body))
    dbg = _get(headers, at=_iso(golden_cut()), debug="true")
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
    # altered later bars (a higher version), the same bars from a less preferred source, and brand-new bars after the fixture
    moved = later.assign(high=later["high"] * 1.03, low=later["low"] * 0.97, close=later["close"] * 1.02)
    tail = F.tail(60).assign(timestamp=F.tail(60)["timestamp"] + pd.Timedelta(days=60), bar_end=F.tail(60)["bar_end"] + pd.Timedelta(days=60))
    _run(_put(_lake_rows(NATIVE, "15m", _native(moved), version=2) + _lake_rows(NATIVE, "15m", _native(later.assign(close=later["close"] * 0.9)), source="r2:other")
              + _lake_rows(NATIVE, "15m", _native(tail))))
    L.cache_clear()                                                                # what the lake now holds, not a cached answer
    try:
        for c in cuts[:1]:
            assert _get(headers, at=_iso(c)) == before[c]
        # the later cuts do see the altered bars at and before them - only bars after `at` are invisible
        assert _get(headers, at=_iso(last))["fingerprint"] != before[last]["fingerprint"]
    finally:
        async def undo():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == NATIVE,
                                                                   (MdCandleRecord.version == 2) | (MdCandleRecord.source == "r2:other")
                                                                   | (MdCandleRecord.ts > F["bar_end"].iloc[-1].to_pydatetime())))
                await session.commit()
        _run(undo())
    L.cache_clear()
    assert all(_get(headers, at=_iso(c)) == before[c] for c in cuts)


def _known(body, cutoff: int, searched=None):
    """What a layer says was known by `cutoff` (epoch s): confirmed swings, the legs between them, breaks confirmed by
    then (of the swings both runs searched)."""
    swings = [s for s in body["swings"] if s["status"] == "confirmed" and s["confirmed_time"] <= cutoff]
    ends = {(s["degree"], s["time"]) for s in swings}
    legs = [g for g in body["legs"] if g["status"] == "confirmed" and (g["degree"], g["to"]["time"]) in ends]
    breaks = [b for b in body["breaks"] if b["confirmed_time"] <= cutoff and (searched is None or (b["degree"], b["pivot_time"]) in searched)]
    return swings, legs, breaks


def _searched(body):
    """(degree, pivot time) of the swings whose breaks a layer looked for: the newest MAX_BREAK_PIVOTS per degree."""
    out = set()
    for d in {s["degree"] for s in body["swings"]}:
        conf = [s for s in body["swings"] if s["degree"] == d and s["status"] == "confirmed"]
        out |= {(d, s["time"]) for s in conf[-L.MAX_BREAK_PIVOTS:]}
    return out


def test_truncation_invariance_of_swings_legs_and_breaks():
    """No look-ahead in the engine itself: the layer over bars[:n] equals the layer over every bar, filtered to what was
    confirmed by bar n's end - for swings (every degree), the legs between them and the real breaks, at every 7th n.
    A mutation that lets the swing threshold read the next bar (causal_swings ZigZag `thr[i + 1]`) fails it."""
    full = L.build(F, "15m", L.DEFAULT_DEGREE, True)
    for n in range(40, len(F), 7):
        cutoff = int(pd.Timestamp(F["bar_end"].iloc[n - 1]).timestamp())
        part = L.build(F.iloc[:n], "15m", L.DEFAULT_DEGREE, True)
        searched = _searched(full) & _searched(part)
        assert _known(part, cutoff, searched) == _known(full, cutoff, searched), n
        assert all(s["confirmed_time"] <= cutoff for s in part["swings"] if s["status"] == "confirmed")


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
        native, rebuilt = _get(headers, NATIVE, at=at), _get(headers, FROM_1M, at=at)
        assert rebuilt["source"] == "lake:1m" and native["source"] == "lake:15m"
        assert rebuilt["fingerprint"] == native["fingerprint"] and _content(rebuilt) == _content(native)
    # mid-bar: the forming 15m bar (only some of its minutes are in) is not read
    mid = _iso(F["bar_end"].iloc[99] + pd.Timedelta(minutes=7))
    assert _get(headers, FROM_1M, at=mid)["fingerprint"] == _get(headers, NATIVE, at=_iso(F["bar_end"].iloc[99]))["fingerprint"]


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
    assert r.status_code == 422 and "more than" in r.text                          # refused, never silently cut
    monkeypatch.undo()
    for key in (EMPTY, "NFO:BANKNIFTY 26OCT 52000 CE"):
        body = _get(headers, key, at=at)
        assert body["bars"] == 0 and body["swings"] == [] and body["reason"] == L.NO_BARS and body["as_of"] is None
    # a future `at` reads as now; the default window is DEFAULT_DAYS before it
    body = client.get(URL, params={"instrument": EMPTY, "at": "2999-01-01T00:00:00+00:00"}, headers=headers).json()
    assert pd.Timestamp(body["at"]) - pd.Timestamp(body["start"]) == pd.Timedelta(days=L.DEFAULT_DAYS["15m"])
    assert pd.Timestamp(body["at"]).year < 2999


def test_only_shared_licensed_sources_are_served(owner):
    """BT1b / data terms: a `stream:<broker>` or `broker:<name>` row (one organisation's own broker session) is never
    read, even as the only row for a bar or with a higher version; the preferred shared source wins over a less
    preferred one with a higher version (source first, then version)."""
    headers, _ = owner
    at = F["bar_end"].iloc[300]
    before = _get(headers, at=_iso(at))
    window = F[(F["bar_end"] > F["bar_end"].iloc[250]) & (F["bar_end"] <= at)]
    spiky = window.assign(high=window["high"] * 1.2, low=window["low"] * 0.8)
    lone = window.head(6)                                                            # bars only a stream row has (between the 15m ENDs)
    newer = lone.assign(timestamp=lone["timestamp"] + pd.Timedelta(minutes=7), bar_end=lone["bar_end"] + pd.Timedelta(minutes=7),
                        high=lone["high"] * 1.3)
    _run(_put(_lake_rows(NATIVE, "15m", _native(spiky), source="stream:upstox", version=5) + _lake_rows(NATIVE, "15m", _native(spiky), source="broker:kite")
              + _lake_rows(NATIVE, "15m", _native(newer), source="stream:upstox")
              + _lake_rows(NATIVE, "15m", _native(spiky), source="r2:other", version=9)))     # a less preferred source loses whatever its version
    try:
        L.cache_clear()
        assert _get(headers, at=_iso(at)) == before
    finally:
        async def undo():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == NATIVE,
                                                                   MdCandleRecord.source.in_(["stream:upstox", "broker:kite", "r2:other"])))
                await session.commit()
        _run(undo())
    assert L.SHARED_SOURCES == ("nse_bhav:", "vendor:", "r2:")


def test_a_correction_counts_once_it_was_ingested(owner):
    """Point-in-time corrections: a later version of a bar at or before `at` changes the layer only for an `at` after it
    was ingested; the bar's first version counts by its END (imported history replays)."""
    headers, _ = owner
    at = F["bar_end"].iloc[320]
    before = _get(headers, at=_iso(at))
    early = F.iloc[200:300]
    fix = _lake_rows(NATIVE, "15m", _native(early.assign(high=early["high"] * 1.05)), version=7)
    for r in fix:
        r.ingested_at = pd.Timestamp(at).to_pydatetime() + timedelta(hours=1)          # ingested after `at`
    _run(_put(fix))
    try:
        L.cache_clear()
        assert _get(headers, at=_iso(at)) == before
        L.cache_clear()
        later = _iso(pd.Timestamp(at) + pd.Timedelta(hours=2))
        assert _get(headers, at=later)["fingerprint"] != _get(headers, at=_iso(at))["fingerprint"]
    finally:
        async def undo():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == NATIVE, MdCandleRecord.version == 7))
                await session.commit()
        _run(undo())


def test_reads_are_capped_and_old_times_refused(owner, monkeypatch):
    headers, _ = owner
    at = _iso(F["bar_end"].iloc[-1])
    # a rebuild from 1-minute bars over more than REBUILD_MAX_DAYS is refused before reading them
    far = _iso(F["bar_end"].iloc[-1] - pd.Timedelta(days=L.REBUILD_MAX_DAYS + 5))
    r = client.get(URL, params={"instrument": FROM_1M, "tf": "1h", "at": at, "start": far}, headers=headers)
    assert r.status_code == 422 and "1-minute" in r.text
    # the row cap: the read stops at the cap + 1 and refuses
    monkeypatch.setattr(L, "ROWS_PER_BAR", 0.01)
    r = client.get(URL, params={"instrument": FROM_1M, "tf": "15m", "at": at, "start": _start()}, headers=headers)
    assert r.status_code == 422 and "lake rows" in r.text
    monkeypatch.undo()
    for bad in ("0001-01-01T00:00:00+00:00", "1989-12-31T23:59:00+00:00"):
        assert client.get(URL, params={"instrument": NATIVE, "at": bad}, headers=headers).status_code == 422
        assert client.get(URL, params={"instrument": NATIVE, "at": at, "start": bad}, headers=headers).status_code == 422


def test_requests_are_rate_limited_per_user(owner, monkeypatch):
    headers, _ = owner
    other, _ = _owner(f"ch3-rate-{uuid.uuid4().hex[:10]}@example.com")
    params = {"instrument": EMPTY, "at": _iso(F["bar_end"].iloc[-1])}
    codes = [client.get(URL, params=params, headers=headers).status_code for _ in range(122)]
    assert codes.count(200) == 120 and codes[-1] == 429
    assert client.get(URL, params=params, headers=other).status_code == 200


def test_to_frame_guards_each_on_its_own():
    """The conversion keeps closed bars only: an over-read row (END after `end`) and a forming bucket (rebuilt from
    1-minute bars that do not reach its end) are each dropped."""
    from datetime import datetime as dt, timezone as tz
    end = pd.Timestamp(F["bar_end"].iloc[30]).to_pydatetime()
    rows = [(pd.Timestamp(b).to_pydatetime(), 1, o, h, lo, c, 0) for b, o, h, lo, c in
            zip(F["bar_end"].iloc[:32], F["open"].iloc[:32], F["high"].iloc[:32], F["low"].iloc[:32], F["close"].iloc[:32])]
    native = L._to_frame(rows, "15m", NATIVE, end, False)                           # rows 31, 32 end after `end`
    assert len(native) == 31 and pd.Timestamp(native["bar_end"].iloc[-1]) == pd.Timestamp(end)
    s15 = F.assign(timestamp=F["timestamp"].dt.tz_convert(IST).dt.tz_localize(None))
    minutes = _minutes_from_15m(s15.iloc[:3])
    m_rows = [(pd.Timestamp(m.ts).to_pydatetime(), 1, m.open, m.high, m.low, m.close, 0) for m in minutes.itertuples()]
    mid = pd.Timestamp(F["bar_end"].iloc[1]).to_pydatetime() + timedelta(minutes=7)  # bar 3 has 7 of its 15 minutes
    rebuilt = L._to_frame([r for r in m_rows if r[0] <= mid], "15m", FROM_1M, mid, True)
    assert len(rebuilt) == 2 and pd.Timestamp(rebuilt["bar_end"].iloc[-1]) == pd.Timestamp(F["bar_end"].iloc[1])
    assert dt(1990, 1, 1, tzinfo=tz.utc) == L.MIN_TIME
