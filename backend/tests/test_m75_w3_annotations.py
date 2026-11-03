"""M75 W-3 (owner order M75 CW-2; M75Q-3 / M75Q-4 defaults confirmed 1 Nov): the Copilot workspace's annotations.

* One reading = areas (side, state, tests, timeframe), swings HH / HL / LH / LL, trend per timeframe, BOS / CHoCH, next
  areas, an RSI divergence when present and the structure level - from ATP's readers called unchanged, or from the
  signal engine; one source per reading, labelled; never mixed.
* No look-ahead: the annotations as of a bar never change when later bars (or the forming one) are handed in.
* The engine path is the Copilot's engine read (the runner's own request builder - the worker's request for the same bar),
  cached per closed bar (one engine read for two opens), and gives public parts only: no model id, instance id, area id,
  evidence, invalidation reference, engine name / version, settings or package-only state token (a synthetic engine).
* Flag `copilot_workspace` off: the read response is exactly as before (no `annotations`).
No test uses the private package or a real provider; nothing here places, blocks or changes an order.
"""
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.charts import price_action_layer as pal
from app.charts import read_chart as R
from app.charts import workspace_reading as WR
from app.charts import zones_layer as zl
from app.core import rate_limit
from app.db.models import User
from app.engine_provider import contract as c
from app.engine_provider import loader
from app.signals.language import SignalStructure, is_clean
from tests.test_aud_t5_read_chart import CTX, CUT, STRICT, F, _bands, _texts
from tests.test_auth_api import _session_factory, client  # noqa: F401 - the app client
from tests.test_m72_pr1a_safety import _org
from tests.test_m72_pr1c_planner import _flag, _run
from tests.test_settings_a_connect_ai import _flags

MTF = [{"tf": "1w", "direction": "up", "bars": 50}, {"tf": "1d", "direction": "down (weak)", "bars": 200},
       {"tf": "1h", "direction": "range", "bars": 300}, {"tf": "15m", "direction": "up", "bars": 400}, {"tf": "5m", "direction": "none", "bars": 0}]
SECRETS = ("model-secret-7", "inst-secret-9", "area-secret-1", "area-secret-2", "evidence-secret-a", "state-secret-x", "98.765",
           "engine-setting-secret", "problem-secret", "0.0.0-secret", "test-double-engine")


def _read(frame, *, mtf=MTF):
    known = pd.Timestamp(frame[frame["bar_end"] <= CUT]["bar_end"].iloc[-60]).to_pydatetime()
    cut = frame[frame["bar_end"] <= CUT]
    return R.read_closed(frame, "15m", CUT, bands=_bands(cut), areas_known=known, ctx=CTX, mtf=mtf)


# --- ATP's annotations (pure) ------------------------------------------------------------------------------------------

def test_without_the_workspace_rows_the_reading_is_unchanged():
    cut = F[F["bar_end"] <= CUT]
    assert "annotations" not in _read(cut, mtf=None)
    with_rows = _read(cut)
    assert {k: v for k, v in with_rows.items() if k != "annotations"} == _read(cut, mtf=None)


def test_one_reading_from_atp_readers_with_every_part():
    cut = F[F["bar_end"] <= CUT]
    a = _read(cut)["annotations"]
    layer = pal.compute_layer(cut.reset_index(drop=True), "15m")
    assert a["source"] == WR.SOURCE_ATP and a["source_words"] and a["timeframe"] == "15m"
    # swings: the layer's own confirmed swings, labelled HH / HL / LH / LL, newest last, at most MAX_SWINGS
    confirmed = [s for s in layer["swings"] if s["status"] == "confirmed" and s["label"] in WR.SWING_LABELS]
    assert [s["time"] for s in a["swings"]] == [s["time"] for s in confirmed[-WR.MAX_SWINGS:]]
    assert {s["label"] for s in a["swings"]} <= set(WR.SWING_LABELS)
    # BOS / CHoCH: the structure tracker's events only
    events = [e for e in layer["structure"] if e["type"] in WR.EVENT_TYPES][-WR.MAX_EVENTS:]
    assert [(e["type"], e["time"]) for e in a["events"]] == [(e["type"], e["time"]) for e in events]
    # trend per timeframe: the top-down rows with bars (a row without bars is not shown), in the rows' order
    assert [t["timeframe"] for t in a["trend"]] == ["1w", "1d", "1h", "15m"]
    assert a["trend"][1]["words"] == "daily: down, weakening"
    # areas: ATP's own states and touch counts, each with its own timeframe; never merged
    assert [(x["side"], x["state"], x["tests"], x["timeframe"]) for x in a["areas"]] == [("buyer", "fresh", 1, "15m"), ("seller", "flipped", 1, "15m")]
    # next areas against the last close
    last = layer["last_close"]
    assert a["next_areas"]["below"]["high"] < last < a["next_areas"]["above"]["low"] and a["next_areas"]["inside"] is None
    # structure level (M75Q-3): the protected swing, in structure words
    state = layer["structure_state"]
    if state["protected"] is not None and state["direction"] not in ("range", "none"):
        assert a["level"]["price"] == state["protected"] and "structure changes beyond" in a["level"]["words"]
    texts = list(_texts(a))
    assert all(is_clean(t) for t in texts) and not [t for t in texts if any(w in t.lower() for w in STRICT)]


def test_no_lookahead_later_bars_never_change_the_annotations():
    cut = F[F["bar_end"] <= CUT]
    before = _read(cut)["annotations"]
    after = _read(F)["annotations"]                          # the whole fixture, read as of CUT
    assert after == before
    last_bar = pal._epoch(cut["timestamp"].iloc[-1])
    times = [s["time"] for s in before["swings"]] + [e["time"] for e in before["events"]]
    assert all(t <= last_bar for t in times)


def test_divergence_is_the_screeners_reader_on_the_charts_swings(monkeypatch):
    from app.screener import runtime as RT
    D = RT._Divergence
    seen = []

    def fake(f, side, degree, period, kind="regular"):
        seen.append((side, degree, period))
        return [D(10, 5, 100.0, False), D(30, 20, 95.0, True)] if side == "bullish" else [D(12, 8, 120.0, False), D(25, 18, 125.0, False)]
    monkeypatch.setattr(RT, "_divergence_swings", fake)
    cut = F[F["bar_end"] <= CUT].reset_index(drop=True)
    d = WR.divergence_part(cut, "15m")
    assert seen == [("bullish", pal.DEFAULT_DEGREE, RT.RSI_DIVERGENCE_PERIOD), ("bearish", pal.DEFAULT_DEGREE, RT.RSI_DIVERGENCE_PERIOD)]
    assert d["side"] == "bullish" and d["role"] == "demand"
    assert d["first"] == {"time": pal._epoch(cut["timestamp"].iloc[5]), "price": 100.0}
    assert d["second"] == {"time": pal._epoch(cut["timestamp"].iloc[20]), "price": 95.0}
    assert is_clean(d["words"]) and "confirmed" not in d
    # the latest swing of neither kind diverges: none
    monkeypatch.setattr(RT, "_divergence_swings", lambda f, side, degree, period, kind="regular": [D(10, 5, 100.0, True), D(30, 20, 95.0, False)])
    assert WR.divergence_part(cut, "15m") is None


def test_next_areas_and_the_level_words():
    rows = [{"low": 90.0, "high": 92.0, "words": "Buyer area x."}, {"low": 105.0, "high": 106.0, "words": "Seller area y."},
            {"low": 99.0, "high": 101.0, "words": "Seller area z."}, {"low": 110.0, "high": 111.0, "words": "Seller area w."}]
    n = WR.next_areas(rows, 100.0)
    assert n["above"]["low"] == 105.0 and n["below"]["high"] == 92.0 and n["inside"]["low"] == 99.0
    assert WR.next_areas(rows, None) == {"above": None, "below": None, "inside": None}
    assert WR.level_part({"protected": 95.5, "direction": "up (weak)"})["side"] == "below"
    assert WR.level_part({"protected": 95.5, "direction": "range"}) is None and WR.level_part(None) is None


# --- the engine's annotations (synthetic engine) ----------------------------------------------------------------------

AS_OF = datetime(2026, 10, 20, 15, 30, tzinfo=UTC)


class FakeEngine:
    """A synthetic test double: areas with a public and a package-only state, swings on two timeframes, the optional
    parts and a signal carrying private references. Nothing here is a reading rule."""
    name, version, interface, timeframes = "test-double-engine", "0.0.0-secret", c.INTERFACE_VERSION, ("15m",)

    def __init__(self, optional=True):
        self.optional = optional
        self.requests: list[c.EngineRequest] = []

    def read(self, request: c.EngineRequest) -> c.EngineResult:
        self.requests.append(request)
        at = request.as_of
        areas = (c.Area("area-secret-1", "buyer", "fresh", c.PriceRange(99.0, 100.0), "15m", at, tests=2),
                 c.Area("area-secret-2", "seller", "state-secret-x", c.PriceRange(104.0, 105.0), "1h", at))
        swings = (c.Swing("HL", at - timedelta(hours=2), 99.5, "15m"), c.Swing("HH", at - timedelta(hours=1), 103.9, "15m"),
                  c.Swing("LL", at - timedelta(hours=5), 97.0, "1h"))
        signal = c.Signal(model_id="model-secret-7", timeframe="15m", bar_end=at, side="buyer", entry_zone=c.PriceRange(99.21, 99.79),
                          card=SignalStructure(trend="up", steps=("pullback_reached_area",)), area_id="area-secret-1", invalidation=98.765,
                          evidence=(c.Evidence("evidence-secret-a", True),), instance_id="inst-secret-9")
        extra = {}
        if self.optional:
            extra = {"trends": (c.TimeframeTrend("down", "1h"), c.TimeframeTrend("up", "15m")),
                     "events": (c.StructureEvent("BOS", "up", at - timedelta(minutes=45), 103.0, "15m"),
                                c.StructureEvent("CHOCH", "down", at - timedelta(hours=3), 101.0, "1h")),
                     "divergences": (c.Divergence("buyer", "15m", at - timedelta(hours=4), 98.0, at - timedelta(hours=2), 97.5),)}
        return c.EngineResult(interface=c.INTERFACE_VERSION, version=self.version, areas=areas, swings=swings,
                              higher_side=c.HigherTimeframeSide("up", "1d"), signals=(signal,), **extra)


def _request():
    bar = c.Bar(AS_OF - timedelta(minutes=15), AS_OF, 101.0, 102.0, 100.5, 101.5, 10.0)
    cal = c.MarketCalendar("Asia/Kolkata", datetime(2026, 1, 1).time(), datetime(2026, 1, 1, 15, 30).time())
    return c.EngineRequest(symbol="NSE:EEESTOCK", as_of=AS_OF, frames={"15m": c.Frame("15m", (bar,))}, calendar=cal,
                           settings={"engine-setting-secret": 1})


def _clean(obj) -> None:
    text = repr(obj)
    for secret in SECRETS:
        assert secret not in text, secret


def test_engine_annotations_public_parts_only():
    result = FakeEngine().read(_request())
    a = WR.engine_annotations(result, tf="15m", signal_tf="15m", last_close=101.5, as_of=AS_OF.isoformat())
    _clean(a)
    assert a["source"] == WR.SOURCE_ENGINE and a["level"] is None             # the internal invalidation is never read
    assert [(x["side"], x["state"], x["tests"], x["timeframe"]) for x in a["areas"]] == [("buyer", "fresh", 2, "15m"), ("seller", None, None, "1h")]
    assert [s["label"] for s in a["swings"]] == ["HL", "HH"]                  # only the chart's own timeframe is drawn
    assert [t["timeframe"] for t in a["trend"]] == ["1d", "1h", "15m"]
    assert [e["type"] for e in a["events"]] == ["BOS"] and a["divergence"]["side"] == "bullish"
    assert a["next_areas"]["above"]["low"] == 104.0 and a["next_areas"]["below"]["high"] == 100.0
    assert a["not_given"] == ["level"]
    assert all(is_clean(t) for t in _texts(a))


def test_engine_interface_1_without_the_optional_parts_says_what_it_lacks():
    result = FakeEngine(optional=False).read(_request())
    a = WR.engine_annotations(result, tf="15m", signal_tf="15m", last_close=101.5, as_of=AS_OF.isoformat())
    assert a["events"] == [] and a["divergence"] is None and a["not_given"] == ["events", "divergence", "level"]
    assert [t["timeframe"] for t in a["trend"]] == ["1d"]


def test_the_optional_contract_parts_validate():
    with pytest.raises(c.ContractViolation):
        c.Area("a", "buyer", "fresh", c.PriceRange(1, 2), "15m", AS_OF, tests=-1)
    with pytest.raises(c.ContractViolation):
        c.StructureEvent("SWEEP", "up", AS_OF, 1.0, "15m")  # type: ignore[arg-type]
    with pytest.raises(c.ContractViolation):
        c.Divergence("buyer", "15m", AS_OF, 1.0, AS_OF - timedelta(minutes=1), 2.0)
    with pytest.raises(c.ContractViolation):
        c.TimeframeTrend("left", "1h")  # type: ignore[arg-type]
    assert c.EngineResult(interface=1, version="x").trends == () and c.Area("a", "buyer", "s", c.PriceRange(1, 2), "5m", AS_OF).tests is None


@pytest.fixture
def engine_org(monkeypatch):
    from app.ai.tools import engine_read
    from app.workers import engine_runner as RR
    WR.cache_clear()
    fake = FakeEngine()
    loader.set_for_tests(fake)
    built = {"calls": 0}

    async def fake_build(ctx, cfg, watch, eng):
        built["calls"] += 1
        return _request(), True, 101.5
    monkeypatch.setattr(engine_read, "build_request", fake_build)
    tenant_id, a, b = _org(f"w3-{datetime.now().timestamp()}")

    async def save():
        async with _session_factory() as session:
            user = await session.get(User, a)
            await RR.save_settings(session, tenant_id, {"user_id": a, "signal_timeframe": "15m",
                                                        "symbols": [{"key": "NSE:EEESTOCK", "name": "EEESTOCK"}],
                                                        "engine_settings": {"engine-setting-secret": 1}}, user)
    _run(save())
    yield fake, built, tenant_id, a, b
    loader.reset_for_tests()
    WR.cache_clear()
    _run(_flag("trade_engine", False))


def _engine_read(user_id, key="NSE:EEESTOCK", tf="15m"):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await WR.engine_reading(session, user, key, tf, datetime.now(UTC))
    return _run(go())


def test_engine_reading_gate_and_one_read_per_closed_bar(engine_org):
    fake, built, tenant_id, a, b = engine_org
    _run(_flag("trade_engine", False))
    assert _engine_read(a) is None and not fake.requests                     # flag off: ATP's readers answer
    _run(_flag("trade_engine", False, [tenant_id]))
    assert _engine_read(b) is None                                            # not the runner's own user
    assert _engine_read(a, key="NSE:OTHER") is None                          # not one of his engine symbols
    first = _engine_read(a)
    second = _engine_read(a)
    assert first == second and first["source"] == WR.SOURCE_ENGINE
    assert len(fake.requests) == 1 and built["calls"] == 2                    # the runner's request each time, one engine read per bar
    _clean(first)
    loader.set_for_tests(None)
    assert _engine_read(a) is None                                            # not installed


# --- the route --------------------------------------------------------------------------------------------------------

@pytest.fixture
def member():
    from tests.test_phase_l_ai import _owner
    rate_limit.reset("read_chart")
    WR.cache_clear()
    yield _owner(f"m75w3-{datetime.now().timestamp()}@example.com")[0]
    _flags(ui_v2=False, read_chart=False, copilot_workspace=False)


def test_route_adds_annotations_only_with_the_workspace_flag(member, monkeypatch):
    seen = {}
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    shift = pd.Timestamp(now) - pd.Timestamp(F["bar_end"].iloc[-1]) - pd.Timedelta(minutes=30)
    recent = F.assign(timestamp=F["timestamp"] + shift, bar_end=F["bar_end"] + shift)

    async def load(session, key, tf, start, end, max_bars=None, private=None, provisional=False):
        return recent, "lake:15m"

    async def zones(**kw):
        return {"bands": _bands(recent), "as_of": (pd.Timestamp(recent["bar_end"].iloc[-40])).isoformat()}

    async def ctx(session, key, today, *a, **k):
        return dict(CTX)

    async def rows(session, key, when, daily=None, *, owner, private=None, provisional=False):
        seen["mtf"] = (key, when)
        return MTF
    from app.screener.then import trend
    monkeypatch.setattr(pal, "load_frame", load)
    monkeypatch.setattr(zl, "market_zones_layer", zones)
    monkeypatch.setattr(trend, "daily_context", ctx)
    monkeypatch.setattr(pal, "mtf_rows", rows)
    params = {"instrument": "NSE:NIFTY BANK", "tf": "15m"}
    _flags(ui_v2=True, read_chart=True, copilot_workspace=False)
    off = client.get("/api/charts/read", params=params, headers=member)
    assert off.status_code == 200 and "annotations" not in off.json() and "mtf" not in seen
    _flags(copilot_workspace=True)
    on = client.get("/api/charts/read", params=params, headers=member)
    assert on.status_code == 200, on.text
    body = on.json()
    assert body["annotations"]["source"] == WR.SOURCE_ATP and body["annotations"]["trend"]
    assert seen["mtf"][1] == pd.Timestamp(recent["bar_end"].iloc[-1]).to_pydatetime()      # the top-down rows as of the last closed bar
    assert {k: v for k, v in body.items() if k != "annotations"} == off.json()
    assert all(is_clean(t) for t in _texts(body["annotations"]))
