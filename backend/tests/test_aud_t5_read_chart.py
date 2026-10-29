"""AUD-T5 (owner audit 28 Oct): "Read this chart" - the existing readers, unchanged, as of the last closed bar.

- the existing functions are called (price_action_layer.compute_layer, level_gate.gate.safe_evaluate), nothing re-read;
- no look-ahead: a forming bar (END after the request time) is never read, later bars never change the reading, and an
  area is never replayed on bars before it was stored;
- the gate's own levels and ratios never reach the answer; plain structure words (no buy / sell / target / stop-loss);
- the route: 404 while `ui_v2` or `read_chart` is off; self-scoped (the caller's own private-bar scope and his
  organisation's zones).
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.charts import price_action_layer as pal
from app.charts import read_chart as R
from app.charts import zones_layer as zl
from app.core import rate_limit
from app.platform import controls, flag_catalog
from app.price_action.level_gate import gate as G
from app.signals.language import is_clean
from tests.ch3_pa_cases import frame as fixture_frame
from tests.test_auth_api import client  # noqa: F401 - the app client
from tests.test_phase_l_ai import _owner
from tests.test_settings_a_connect_ai import _flags

F = fixture_frame()
CUT = pd.Timestamp(F["bar_end"].iloc[400]).to_pydatetime()
CTX = {"ok": True, "trend": "UP", "sides": {G.BULL, G.BEAR}, "band": None, "phase": "impulse", "counter": "pullback", "asof": "2026-01-02"}
STRICT = ("entry", "r:r", "reward", "risk", "profit", "recommend")


def _bands(frame: pd.DataFrame):
    last = float(frame["close"].iloc[-1])
    mk = lambda key, lo, hi, role, state: {"zone_key": key, "low": lo, "high": hi, "low_text": f"{lo:,.2f}", "high_text": f"{hi:,.2f}",
                                           "role": role, "timeframe": "15m", "state": state, "touches": 1, "touch_text": "1 touch"}
    return [mk("z1", round(last * 0.995, 2), round(last * 0.998, 2), "demand", "fresh"),
            mk("z2", round(last * 1.002, 2), round(last * 1.005, 2), "supply", "flipped")]


def _texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _texts(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _texts(v)


def test_the_flag_is_off_by_default_and_depends_on_ui_v2_and_zones():
    assert R.FLAG in controls.FEATURE_FLAGS and R.FLAG in controls.DEFAULT_OFF_FLAGS
    assert flag_catalog.FLAG_INFO[R.FLAG].depends_on == ("ui_v2", "market_zones")


def test_it_calls_the_existing_readers_unchanged(monkeypatch):
    calls = {"layer": 0, "gate": 0}
    real_layer, real_gate = pal.compute_layer, G.safe_evaluate

    def layer(*a, **k):
        calls["layer"] += 1
        return real_layer(*a, **k)

    def gate(*a, **k):
        calls["gate"] += 1
        return real_gate(*a, **k)

    monkeypatch.setattr(pal, "compute_layer", layer)
    monkeypatch.setattr(G, "safe_evaluate", gate)
    cut = F[F["bar_end"] <= CUT]
    known = pd.Timestamp(cut["bar_end"].iloc[-60]).to_pydatetime()
    out = R.read_closed(cut, "15m", CUT, bands=_bands(cut), areas_known=known, ctx=CTX)
    assert calls == {"layer": 1, "gate": 2}                       # one structure read, one gate replay per area
    assert out["structure"] == R.structure_part(real_layer(cut.reset_index(drop=True), "15m"), "15m")


def test_no_lookahead_the_forming_bar_and_later_bars_are_never_read():
    cut = F[F["bar_end"] <= CUT]
    known = pd.Timestamp(cut["bar_end"].iloc[-60]).to_pydatetime()
    before = R.read_closed(cut, "15m", CUT, bands=_bands(cut), areas_known=known, ctx=CTX)
    # the whole fixture (bars after CUT, the next one still forming at CUT) read as of CUT: the same reading
    after = R.read_closed(F, "15m", CUT, bands=_bands(cut), areas_known=known, ctx=CTX)
    assert after == before and before["bars"] == len(cut)
    assert before["as_of"] == pd.Timestamp(CUT).isoformat()


def test_an_area_is_never_replayed_on_bars_before_it_was_stored(monkeypatch):
    seen = []
    monkeypatch.setattr(G, "safe_evaluate", lambda bars, area, ctx, others, start, *a, **k: seen.append((len(bars), start)) or {})
    cut = F[F["bar_end"] <= CUT].reset_index(drop=True)
    known = pd.Timestamp(cut["bar_end"].iloc[-60]).to_pydatetime()
    R.read_closed(cut, "15m", CUT, bands=_bands(cut), areas_known=known, ctx=CTX)
    assert seen and all(start == len(cut) - 59 for _, start in seen)
    seen.clear()
    R.read_closed(cut, "15m", CUT, bands=_bands(cut), areas_known=CUT, ctx=CTX)     # stored at the last bar: nothing after it
    assert seen == []


def test_only_the_commitment_candle_reaches_the_answer_never_the_gate_levels(monkeypatch):
    cut = F[F["bar_end"] <= CUT].reset_index(drop=True)
    n = len(cut)
    fake = {"state": G.DISARMED, "side": G.BULL, "form": "engulf", "confirm_bar": n - 3, "entry_price": 1.0, "sl": 0.5, "target": 9.0,
            "rr": 3.0, "commit_line": "commitment: x", "reason": G.R_RR}
    monkeypatch.setattr(G, "safe_evaluate", lambda *a, **k: dict(fake))
    out = R.read_closed(cut, "15m", CUT, bands=_bands(cut), areas_known=pd.Timestamp(cut["bar_end"].iloc[-10]).to_pydatetime(), ctx=CTX)
    c = out["commitment"]
    assert c["form"] == "bullish engulfing" and c["role"] == "demand" and c["time"] == pal._epoch(cut["timestamp"].iloc[n - 3])
    blob = json.dumps(out)
    for word in ("entry_price", "\"sl\"", "\"target\"", "\"rr\"", "commit_line"):
        assert word not in blob


def test_plain_structure_words_only():
    cut = F[F["bar_end"] <= CUT]
    out = R.read_closed(cut, "15m", CUT, bands=_bands(cut), areas_known=pd.Timestamp(cut["bar_end"].iloc[-60]).to_pydatetime(), ctx=CTX)
    texts = list(_texts(out))
    assert out["lines"] and all(is_clean(t) for t in texts)
    assert not [t for t in texts if any(w in t.lower() for w in STRICT)]
    words = [R.pullback_part({**CTX, "counter": c, "phase": p}) for c, p in
             ((None, "impulse"), ("pullback", "impulse"), ("candidate", "correction"), ("correction", "correction"), (None, "origin_broken"))]
    words += [R.pullback_part(None), R.pullback_part({"pending": True}), R.pullback_part({**CTX, "trend": "RANGE"})]
    assert all(is_clean(w["words"]) for w in words) and words[1]["words"].endswith("a pullback inside the running leg.")
    assert not R.pullback_part(None)["available"]


@pytest.fixture
def member():
    rate_limit.reset("read_chart")
    yield _owner(f"aud-t5-{uuid.uuid4().hex[:10]}@example.com")[0]
    _flags(ui_v2=False, read_chart=False)


def test_route_404_while_off_and_self_scoped(member, monkeypatch):
    seen = {}
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    shift = pd.Timestamp(now) - pd.Timestamp(F["bar_end"].iloc[-1]) - pd.Timedelta(minutes=30)
    recent = F.assign(timestamp=F["timestamp"] + shift, bar_end=F["bar_end"] + shift)
    forming = recent.iloc[[-1]].assign(timestamp=pd.Timestamp(now) - pd.Timedelta(minutes=5), bar_end=pd.Timestamp(now) + pd.Timedelta(minutes=10))
    served = pd.concat([recent, forming], ignore_index=True)

    async def load(session, key, tf, start, end, max_bars=None, private=None):
        seen["load"] = (key, tf, end, private)
        return served, "lake:15m"

    async def zones(**kw):
        seen["zones"] = (kw["instrument"], kw["at"], kw["user"].tenant_id)
        return {"bands": _bands(recent), "as_of": (pd.Timestamp(recent["bar_end"].iloc[-40])).isoformat()}

    async def ctx(session, key, today, *a, **k):
        seen["ctx"] = (key, today)
        return dict(CTX)

    from app.screener.then import trend
    monkeypatch.setattr(pal, "load_frame", load)
    monkeypatch.setattr(zl, "market_zones_layer", zones)
    monkeypatch.setattr(trend, "daily_context", ctx)
    params = {"instrument": "NSE:NIFTY BANK", "tf": "15m"}
    _flags(ui_v2=True, read_chart=False)
    assert client.get("/api/charts/read", params=params, headers=member).status_code == 404
    _flags(ui_v2=False, read_chart=True)
    assert client.get("/api/charts/read", params=params, headers=member).status_code == 404
    _flags(ui_v2=True, read_chart=True)
    r = client.get("/api/charts/read", params=params, headers=member)
    assert r.status_code == 200, r.text
    body = r.json()
    key, tf, end, private = seen["load"]
    assert tf == "15m" and end <= datetime.now(timezone.utc) and private is None        # no private bars of his: the shared lake
    assert seen["zones"][2] is not None and seen["zones"][1] == pd.Timestamp(recent["bar_end"].iloc[-1]).to_pydatetime()
    assert body["bars"] == len(recent)                                                  # the forming bar is not read
    assert body["as_of"] == pd.Timestamp(recent["bar_end"].iloc[-1]).isoformat()
    assert body["answer_key"] is None and body["lines"] and all(is_clean(t) for t in _texts(body))
    assert [a["role"] for a in body["areas"]] == ["demand", "supply"] and body["pullback"]["available"]


def test_the_route_is_classified_for_tenancy():
    from tests.tenancy_route_table import ROUTE_TABLE
    assert ROUTE_TABLE[("GET", "/api/charts/read")].startswith("member")
