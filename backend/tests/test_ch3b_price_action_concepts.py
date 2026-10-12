"""N4 CH3b: the price-action layer's leg classes, structure (BOS / CHoCH), liquidity, trendlines / channels and the
top-down W / D / 1H / 15m panel through GET /api/charts/layers/price-action.

* every concept is in the response with its numbers and texts; default view small, debug complete;
* the top-down panel reads each timeframe's own bars as of the same `at` (the weekly from daily bars), and nothing after
  `at`: altering and adding later bars on every timeframe leaves the whole response at `at` byte-identical;
* replay only adds structure events, and a confirmed leg keeps its class at every later step;
* the weekly bars leave the forming week out.
Synthetic bars only (tests/fixtures/zones/session_15m.csv) - no broker, no network, no NIFTY data. No order path.
"""
import asyncio
import uuid
from datetime import timedelta

import pandas as pd
import pytest
from sqlalchemy import delete

from app.charts import price_action_concepts as PC
from app.charts import price_action_layer as L
from app.db.models import MdCandleRecord
from app.platform import controls
from tests import zones_golden as G
from tests.ch3_pa_cases import frame
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

KEY = "NSE:PALAYERM"
URL = "/api/charts/layers/price-action"
IST = "Asia/Kolkata"
F = frame()


def _run(coro):
    return asyncio.run(coro)


def _bars(f: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Closed bars of a coarser timeframe from the 15m fixture: start label, end = the last 15m bar's end."""
    local = f.assign(day=f["timestamp"].dt.tz_convert(IST).dt.tz_localize(None))
    g = local.set_index("day").resample(rule, label="left", closed="left")
    out = pd.DataFrame({"timestamp": g["timestamp"].first(), "bar_end": g["bar_end"].last(), "open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last()}).dropna().reset_index(drop=True)
    return out


H1, D1 = _bars(F, "1h"), _bars(F, "1D")


def _rows(key, tf, f, version=1, source="test"):
    return [MdCandleRecord(instrument_key=key, timeframe=tf, ts=pd.Timestamp(b.bar_end).to_pydatetime(), source=source, version=version,
                           open=float(b.open), high=float(b.high), low=float(b.low), close=float(b.close), volume=0,
                           ingested_at=pd.Timestamp(b.bar_end).to_pydatetime() + timedelta(seconds=30)) for b in f.itertuples()]


async def _put(rows):
    async with _session_factory() as session:
        session.add_all(rows)
        await session.commit()


async def _drop(cond=None):
    async with _session_factory() as session:
        q = delete(MdCandleRecord).where(MdCandleRecord.instrument_key == KEY)
        await session.execute(q if cond is None else q.where(cond))
        await session.commit()


@pytest.fixture(scope="module", autouse=True)
def _lake():
    _run(_drop())
    _run(_put(_rows(KEY, "15m", F) + _rows(KEY, "60m", H1) + _rows(KEY, "1d", D1)))
    yield
    _run(_drop())


@pytest.fixture
def headers():
    async def flag(on):
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            stored[L.FLAG] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(flag(True))
    yield _owner(f"ch3b-{uuid.uuid4().hex[:10]}@example.com")[0]
    _run(flag(False))


def _iso(t) -> str:
    return pd.Timestamp(t).tz_convert("UTC").isoformat()


START = _iso(F["timestamp"].iloc[0] - pd.Timedelta(hours=1))


def _get(headers, **params):
    r = client.get(URL, params={"instrument": KEY, "tf": "15m", "start": START, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_every_concept_is_served_with_numbers_and_texts(headers):
    at = _iso(F["bar_end"].iloc[-1])
    body = _get(headers, at=at)
    assert set(L.CONCEPTS) <= set(body["concepts"]) and body["schema"] == "pa-layer/2"
    legs = body["legs"]
    assert legs and all(g["class"] in ("STRONG_IMPULSE", "WEAK_IMPULSE", "HEALTHY_PULLBACK", "MIXED_PULLBACK", "DANGEROUS_PULLBACK", "REVERSAL", "RANGE")
                        and g["role"] in ("impulse", "pullback") and g["text"].startswith(g["class_text"]) for g in legs)
    assert 0 < len(body["structure"]) <= PC.MAX_STRUCTURE_EVENTS and {e["type"] for e in body["structure"]} <= set(PC.STRUCTURE_EVENTS)
    assert all(e["text"].startswith(e["name"]) and e["price_text"] in e["text"] for e in body["structure"])
    st = body["structure_state"]
    assert st["tf"] == "15m" and st["state"] and (st["protected"] is None or st["protected_text"] in st["protected_label"])
    sides = [p["side"] for p in body["liquidity"]]
    assert sides.count("buy") <= 3 and sides.count("sell") <= 3 and all(p["price_text"] in p["text"] for p in body["liquidity"])
    assert body["trendlines"] and all(t["a"]["time"] < t["b"]["time"] <= t["end"]["time"] and "channel" in t for t in body["trendlines"])
    dbg = _get(headers, at=at, debug="true")
    assert len(dbg["structure"]) > len(body["structure"]) and len(dbg["liquidity"]) >= len(body["liquidity"])
    assert len({g["degree"] for g in dbg["legs"]}) == 4 and any(g["status"] == "tentative" and "class" in g for g in dbg["legs"])


def test_top_down_panel_is_as_of_the_same_time(headers):
    at = F["bar_end"].iloc[400]
    body = _get(headers, at=_iso(at))
    rows = {r["tf"]: r for r in body["mtf"]}
    assert list(rows) == list(PC.MTF_TFS)
    assert rows["15m"]["source"] == "lake:15m" and rows["1h"]["source"] == "lake:60m" and rows["1d"]["source"] == "lake:1d"
    assert rows["1w"]["source"] == "lake:1d"                                  # weekly from the daily bars
    for r in rows.values():
        assert r["bars"] > 0 and pd.Timestamp(r["as_of"]) <= pd.Timestamp(at) and r["direction"] in (
            "up", "down", "up (weak)", "down (weak)", "range", "none"), r
    assert rows["15m"]["state"] == body["structure_state"]["state"]          # the chart's own timeframe agrees with the panel
    # later bars on every timeframe - altered (a higher version), from another source, and new - change nothing at `at`
    later = lambda f: f[f["bar_end"] > at]                                    # noqa: E731
    bump = lambda f: f.assign(high=f["high"] * 1.04, close=f["close"] * 1.03)  # noqa: E731
    extra = []
    for tf, f in (("15m", F), ("60m", H1), ("1d", D1)):
        extra += _rows(KEY, tf, bump(later(f)), version=2) + _rows(KEY, tf, later(f).assign(low=later(f)["low"] * 0.9), source="zz-other")
    _run(_put(extra))
    try:
        assert _get(headers, at=_iso(at)) == body
    finally:
        _run(_drop((MdCandleRecord.version == 2) | (MdCandleRecord.source == "zz-other")))
    assert _get(headers, at=_iso(at)) == body


def test_replay_adds_structure_events_and_keeps_leg_classes(headers):
    prev = None
    for i in range(200, 420, 11):
        body = _get(headers, at=_iso(F["bar_end"].iloc[i]), debug="true", mtf="false")
        events = [(e["type"], e["time"], e["price"]) for e in body["structure"]]
        legs = {(g["degree"], g["from"]["time"], g["to"]["time"]): g["class"] for g in body["legs"] if g["status"] == "confirmed"}
        if prev is not None:
            assert events[:len(prev[0])] == prev[0], i                         # the earlier events, unchanged, in order
            assert all(legs[k] == v for k, v in prev[1].items()), i            # a confirmed leg keeps its class
        prev = (events, legs)


def test_weekly_bars_leave_the_forming_week_out():
    d = D1.copy()
    weeks = PC.weekly_from_daily(d)
    days = d["timestamp"].dt.tz_convert(IST)
    last_day = days.iloc[-1]
    if last_day.weekday() == 4:
        d = d.iloc[:-1]                                                       # end the data mid-week
        days = days.iloc[:-1]
    weeks = PC.weekly_from_daily(d)
    last_week_start = pd.Timestamp(weeks["timestamp"].iloc[-1]).tz_convert(IST)
    assert last_week_start.to_period("W-FRI") < days.iloc[-1].to_period("W-FRI")
    assert (weeks["bar_end"].diff().dropna() > pd.Timedelta(days=3)).all() and weeks["high"].max() == d[
        d["timestamp"] < weeks["bar_end"].iloc[-1] + pd.Timedelta(minutes=1)]["high"].max()
    assert len(G.load("session_15m")) == len(F)
