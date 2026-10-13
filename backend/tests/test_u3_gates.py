"""N3 U3 full gates: the gate manifest (every row names tests that exist or a declared OPEN_QUESTIONS gap), the definition
of done per registry entry, price-action parity on the 15m fixture (G1), run replay (G3) and the screener metrics (G7)."""
import asyncio
import hashlib
import json
import types
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from prometheus_client import REGISTRY

from app.core.models import OHLCVBar
from app.db.models import ScreenRunRecord
from app.price_action import candlestick_patterns as cp
from app.price_action import causal_swings as cs
from app.price_action import pa_settings
from app.quality import gates
from app.screener import parse, replay
from app.screener.registry import FUNCTIONS, Spec
from app.screener.runtime import SymbolData, evaluate
from tests import ch3_pa_cases
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_s5a_price_action import EXPECTED


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------------------------------------------- manifest
def test_the_manifest_names_only_existing_tests_and_declared_gaps():
    assert gates.manifest_problems() == []
    assert {r.gate for r in gates.ROWS} == set(gates.GATES)
    for gate in gates.GATES:                                        # every gate has at least one row enforced by tests
        assert any(r.tests for r in gates.ROWS if r.gate == gate), gate


def test_the_manifest_catches_a_missing_test_an_unknown_gap_and_a_met_target(monkeypatch):
    rows = (gates.Row("X.a", "G1", "t", "s", ("backend/tests/test_u3_gates.py::test_no_such_test",)),
            gates.Row("X.b", "G2", "t", "s", (), gap="U3-999"),
            gates.Row("X.c", "G9", "t", "s", ()),
            gates.Row("X.d", "ACC", "t", "s", ("frontend/src/components/dataTier.test.ts::no such title",), gap="U3-2", measure="m"))
    monkeypatch.setitem(gates.MEASURES, "m", lambda: (50, 50))
    problems = gates.manifest_problems(rows)
    assert "X.b: gap U3-999 has no row in docs/OPEN_QUESTIONS.md" in problems
    assert "X.c: unknown gate G9" in problems and "X.c: no enforcing test and no declared gap" in problems
    assert "X.d: m = 50 meets 50; close gap U3-2" in problems
    assert "missing test - X.a: backend/tests/test_u3_gates.py::test_no_such_test" in problems
    assert any(p.startswith("missing test - X.d") for p in problems)


def test_the_report_lists_every_gate_and_is_clean():
    text, ok = gates.report()
    assert ok, text
    for gate in gates.GATES:
        assert f"\n{gate}  " in f"\n{text}"
    assert "ACC.prebuilt" in text and "OPEN U3-2" in text and " of 50]" in text


# ------------------------------------------------------------------------------------------------ definition of done
def test_every_registry_entry_meets_the_definition_of_done():
    assert gates.definition_of_done() == {}


def test_the_definition_of_done_names_what_an_entry_lacks(monkeypatch):
    monkeypatch.setitem(FUNCTIONS, "Planted", Spec("Planted", "factor", "num", "price", flag="planted_flag_not_off"))
    assert gates.definition_of_done() == {"Planted": ["a doc string", "its flag planted_flag_not_off in DEFAULT_OFF_FLAGS",
                                                      "a Z10-a causal case or a named exemption (app/quality/causal_cases.py)"]}
    from app.screener import evidence
    monkeypatch.setattr(evidence, "DRAWN_AS", {name: "series" for name in FUNCTIONS if name != "SMA"}, raising=False)
    assert "a drawn-as declaration (evidence.DRAWN_AS)" in gates.definition_of_done()["SMA"]   # enforced once EV3 declares it


# ---------------------------------------------------------------------------------------------------- G1 price action
def _fixture():
    f = ch3_pa_cases.frame()
    return f.set_index("timestamp")[["open", "high", "low", "close"]].assign(volume=100.0)


def _ev(text, f):
    return evaluate(parse(text), SymbolData("BANKNIFTY_LIKE", {"15m": f}), base_tf="15m", params={})


def test_price_action_functions_match_the_engine_on_the_15m_fixture():
    f = _fixture()
    fired = set()
    for name, (fn, direction) in EXPECTED.items():
        got = _ev(f'Pattern("{name}")', f)
        want = [False, False] + [(m := getattr(cp, fn)(f, i)) is not None and (direction is None or m.direction == direction)
                                 for i in range(2, len(f))]
        assert got.tolist() == want, name
        fired |= {name} if any(want) else set()
    assert len(fired) >= 6, fired                                  # a parity check on all-False would prove nothing
    frame = pd.DataFrame({k: f[k].to_numpy(float) for k in ("open", "high", "low", "close")})
    frame["timestamp"] = frame["bar_end"] = f.index
    for degree in (0, 1):
        pivots = cs.degree_pivots(frame, degree, pa_settings.settings())
        for kind, fn_name in (("H", "SwingHigh"), ("L", "SwingLow")):
            mine = [p for p in pivots if p.kind == kind]
            series = _ev(f"{fn_name}({degree})", f)
            assert len(mine) >= 3, (degree, kind)
            last_per_bar = {p.confirmed_idx: p.price for p in mine}  # two confirmed on one bar: the later one shows
            for idx, price in last_per_bar.items():
                assert series.iloc[idx] == pytest.approx(price), (degree, kind, idx)
            assert np.isnan(series.iloc[: min(last_per_bar)]).all()   # nothing before the first confirmation


# ------------------------------------------------------------------------------------------------------- G3 replay
def _flags(on, only=None):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            for name in (only,) if only else ("screener_v2", replay.REPLAY_FLAG):
                flags[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def flags_on():
    _flags(True)
    yield
    _flags(False)


def _daily(step, days=60, shift=0.0):
    idx = pd.date_range("2026-01-01", periods=days, freq="1D", tz="UTC")
    closes = 100 + np.arange(days) * step + shift
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=c - 0.5, high=c + 1, low=c - 1, close=float(c), volume=1000.0) for t, c in zip(idx, closes)]


@pytest.fixture
def broker(monkeypatch):
    """A fake broker whose bars per symbol the test sets (`bars[symbol]`); a missing symbol raises."""
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service
    bars = {"UP": _daily(1.0), "DOWN": _daily(-1.0)}

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        if symbol not in bars:
            raise RuntimeError("broker said no")
        return bars[symbol]
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)
    return bars


SOURCE = "close > SMA(close, $n) AND PctChange(close, 5) > 0"


def _stored_run(headers):
    out = client.post("/api/screener/run", json={"source": SOURCE, "params": {"n": 20}, "symbols": ["UP", "DOWN", "BROKEN"]}, headers=headers)
    assert out.status_code == 200, out.text
    assert out.json()["matched"] == ["UP"]
    return out.json()["run_id"]


def test_a_run_stores_what_a_replay_needs(flags_on, broker):
    headers, me = _owner("u3-replay-store@example.com")
    run_id = _stored_run(headers)
    stored = client.get(f"/api/screener/runs/{run_id}", headers=headers).json()
    last_close = "2026-03-02T00:00:00+00:00"                        # the 60th daily bar (1 Mar) closes at 2 Mar 00:00 UTC
    assert stored["replayable"] is True and stored["params"] == {"n": 20}
    assert stored["bar_times"] == {"UP": last_close, "DOWN": last_close} and stored["data_as_of"].startswith("2026-03-02T00:00:00")

    async def row():
        async with _session_factory() as session:
            return await session.get(ScreenRunRecord, run_id)
    r = _run(row())
    assert hashlib.sha256(r.ast_json.encode()).hexdigest() == r.ast_sha256 and r.exchange == "NSE"
    assert json.loads(r.params_json) == {"n": 20} and json.loads(r.bar_times_json) == stored["bar_times"]


def test_a_replay_reproduces_the_run_after_later_bars_arrive(flags_on, broker):
    headers, _ = _owner("u3-replay-same@example.com")
    run_id = _stored_run(headers)
    later = _daily(1.0, days=70)
    broker["UP"] = later[:60] + [OHLCVBar(timestamp=b.timestamp, open=50.0, high=51.0, low=49.0, close=50.0, volume=1000.0) for b in later[60:]]
    fresh = client.post("/api/screener/run", json={"source": SOURCE, "params": {"n": 20}, "symbols": ["UP"]}, headers=headers).json()
    assert fresh["matched"] == []                                   # today's bars would answer differently ...
    out = client.post(f"/api/screener/runs/{run_id}/replay", headers=headers)
    assert out.status_code == 200, out.text
    body = out.json()                                              # ... but the replay reads the bars closed by the run's time
    assert body["reproduced"] is True and body["same"] == 3 and body["differences"] == []
    assert body["data_as_of"].startswith("2026-03-02T00:00:00") and body["data_source"] == body["stored_source"] == "broker:fake"
    assert "recommendation" in body["disclaimer"]


def test_a_replay_names_symbols_whose_bars_changed(flags_on, broker):
    headers, _ = _owner("u3-replay-diff@example.com")
    run_id = _stored_run(headers)
    broker["UP"] = _daily(-1.0, shift=200.0)                        # same timestamps, corrected prices: no longer matches
    broker["DOWN"] = _daily(-1.0)[:59]                              # the last bar is gone
    body = client.post(f"/api/screener/runs/{run_id}/replay", headers=headers).json()
    assert body["reproduced"] is False and body["same"] == 1      # BROKEN fails the same way
    diffs = {d["symbol"]: d for d in body["differences"]}
    assert set(diffs) == {"UP", "DOWN"}
    assert diffs["UP"]["stored"]["matched"] is True and diffs["UP"]["replayed"]["matched"] is False
    assert diffs["UP"]["why"].startswith("same last bar, different answer")
    assert diffs["DOWN"]["why"].startswith("the source now returns a different last bar")
    assert diffs["DOWN"]["replayed"]["bar_time"] == "2026-03-01T00:00:00+00:00"


def test_replay_is_flagged_off_owner_scoped_and_refuses_old_runs(flags_on, broker):
    headers, me = _owner("u3-replay-scope@example.com")
    other, _ = _owner("u3-replay-scope-other@example.com")
    run_id = _stored_run(headers)
    assert client.post(f"/api/screener/runs/{run_id}/replay", headers=other).status_code == 404

    async def old_runs():
        async with _session_factory() as session:
            ast_json = json.dumps({"x": 1})
            pre_u3 = ScreenRunRecord(tenant_id=me["tenant_id"], user_id=me["id"], ast_sha256="0" * 64, ast_version="screenql/1", base_tf="1d",
                                     universe_json='["UP"]', data_source="broker:fake")
            tampered = ScreenRunRecord(tenant_id=me["tenant_id"], user_id=me["id"], ast_sha256="0" * 64, ast_version="screenql/1",
                                       base_tf="1d", universe_json='["UP"]', data_source="broker:fake", ast_json=ast_json,
                                       data_as_of=datetime(2026, 3, 2, tzinfo=timezone.utc), bar_times_json="{}")
            session.add_all([pre_u3, tampered])
            await session.commit()
            return pre_u3.id, tampered.id
    pre_u3, tampered = _run(old_runs())
    old = client.post(f"/api/screener/runs/{pre_u3}/replay", headers=headers)
    assert old.status_code == 409 and "before runs kept" in old.json()["detail"]
    assert client.get(f"/api/screener/runs/{pre_u3}", headers=headers).json()["replayable"] is False
    bad = client.post(f"/api/screener/runs/{tampered}/replay", headers=headers)
    assert bad.status_code == 409 and "does not match" in bad.json()["detail"]
    _flags(False, only=replay.REPLAY_FLAG)                         # the screener stays on; only the replay is off
    off = client.post(f"/api/screener/runs/{run_id}/replay", headers=headers)
    assert off.status_code == 503 and off.headers.get("X-Feature-Disabled") == replay.REPLAY_FLAG
    assert client.get(f"/api/screener/runs/{run_id}", headers=headers).status_code == 200


def test_the_replay_flag_is_registered_off_by_default():
    from app.platform.controls import DEFAULT_OFF_FLAGS, FEATURE_FLAGS
    assert replay.REPLAY_FLAG in DEFAULT_OFF_FLAGS and replay.REPLAY_FLAG in FEATURE_FLAGS


def test_compare_and_trim_by_hand():
    idx = pd.date_range("2026-03-02 03:45", periods=4, freq="5min", tz="UTC")
    f = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [1.0, 2.0, 3.0, 4.0], "volume": 1.0}, index=idx)
    d = replay.trim([SymbolData("A", {"5m": f})], datetime(2026, 3, 2, 3, 55, tzinfo=timezone.utc))[0]
    assert list(d.frames["5m"]["close"]) == [1.0, 2.0]               # bars closing after the time are dropped
    assert replay.bar_times([d], "5m") == {"A": "2026-03-02T03:55:00+00:00"}
    assert replay.data_as_of({}) is None
    from app.screener.runtime import Match
    out = replay.compare([{"symbol": "A", "matched": True}], {"A": "t"}, [Match("A", True)], {"A": "t"}, {})
    assert out == {"reproduced": True, "same": 1, "history_not_covered": 0, "differences": [], "more_differences": 0}
    short = replay.compare([{"symbol": "A", "matched": True}], {"A": "t"}, [Match("A", False, "not enough history on 1d")], {"A": "t"}, {})
    assert short["differences"][0]["why"] == replay.HISTORY_GONE and short["history_not_covered"] == 1   # never "a correction"
    gone = replay.compare([{"symbol": "A", "matched": True}], {"A": "t"}, [], {}, {"A": replay.HISTORY_GONE})
    assert gone["differences"][0]["why"] == replay.HISTORY_GONE
    assert replay.reach_back_days(datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 3, 11, 1, tzinfo=timezone.utc)) == 12
    assert replay.reach_back_days(datetime(2026, 3, 2, tzinfo=timezone.utc), datetime(2026, 3, 1, tzinfo=timezone.utc)) == 0
    many = replay.compare([{"symbol": f"S{i}", "matched": True} for i in range(60)], {}, [], {}, {"S0": "gone"})
    assert len(many["differences"]) == replay.MAX_DIFFERENCES and many["more_differences"] == 10
    assert many["differences"][0]["replayed"]["reason"] == "gone"


@pytest.fixture
def window_broker(monkeypatch):
    """A fake broker that serves, like the real service, only the bars of its window: `lookback` calendar days ending at
    its clock (`state["clock"]`, the real time when None) - so a run "then" and its replay "now" read different windows."""
    from datetime import timedelta

    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service
    state = {"clock": None, "series": {}, "asked": []}

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None, fresh=False):
        end = state["clock"] or datetime.now(timezone.utc)
        days = self._lookback(interval)
        state["asked"].append((interval, days))
        start = end - timedelta(days=days)
        return [b for b in state["series"][symbol] if start <= b.timestamp and b.timestamp < end]
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)
    return state


def _rising(stamps):
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=99.0 + i * 0.01, high=101.0 + i * 0.01, low=98.0 + i * 0.01, close=100.0 + i * 0.01,
                     volume=1000.0) for i, t in enumerate(stamps)]


def _five_minute_bars(days_back):
    """NSE sessions (09:15-15:30 IST = 03:45-10:00 UTC, 75 five-minute bars) on the weekdays of the last `days_back` days."""
    today = pd.Timestamp.now(tz="UTC").normalize()
    stamps = [d + pd.Timedelta(minutes=225 + 5 * k) for d in pd.bdate_range(today - pd.Timedelta(days=days_back), today, tz="UTC")
              for k in range(75)]
    return _rising(stamps)


def _replay_then(headers, state, symbol, source, base_tf, days_ago):
    """Run `source` with the broker's clock `days_ago` days back (the run "then"), then replay it now."""
    from datetime import timedelta
    state["clock"] = datetime.now(timezone.utc) - timedelta(days=days_ago)
    out = client.post("/api/screener/run", json={"source": source, "base_tf": base_tf, "symbols": [symbol]}, headers=headers)
    assert out.status_code == 200, out.text
    assert out.json()["matched"] == [symbol], out.json()["results"]
    state["clock"], state["asked"] = None, []
    res = client.post(f"/api/screener/runs/{out.json()['run_id']}/replay", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_a_5m_run_replayed_ten_days_later_reads_the_bars_it_had(flags_on, window_broker):
    """Review MED: the replay's fetch is sized from (now - data time) + the screen's lookback. Sized from now alone, a 5m run
    replayed 10 days later kept almost no bars before its data time and was reported as 'earlier bars changed'."""
    headers, _ = _owner("u3-replay-5m@example.com")
    window_broker["series"]["FIVE"] = _five_minute_bars(120)
    body = _replay_then(headers, window_broker, "FIVE", "close > SMA(close, 300)", "5m", days_ago=10)
    assert body["reproduced"] is True and body["same"] == 1 and body["differences"] == [], body
    asked = [days for interval, days in window_broker["asked"] if interval == "5min"]
    assert asked and min(asked) >= 10 + 10                                # the screen's 10 days, plus the 10 since the run
    # beyond the broker's intraday window (60 days) the bars the run read cannot be fetched any more: said so, never a correction
    late = _replay_then(headers, window_broker, "FIVE", "close > SMA(close, 300)", "5m", days_ago=75)
    assert late["reproduced"] is False and late["history_not_covered"] == 1, late["differences"]
    assert late["differences"][0]["why"] == replay.HISTORY_GONE and "correction" not in late["differences"][0]["why"]


def test_a_daily_250_bar_screen_replayed_200_days_later_reproduces(flags_on, window_broker):
    headers, _ = _owner("u3-replay-1d@example.com")
    today = pd.Timestamp.now(tz="UTC").normalize()
    window_broker["series"]["DAILY"] = _rising(pd.bdate_range(today - pd.Timedelta(days=1200), today, tz="UTC"))
    body = _replay_then(headers, window_broker, "DAILY", "close > SMA(close, 250)", "1d", days_ago=200)
    assert body["reproduced"] is True and body["differences"] == [], body
    assert max(days for interval, days in window_broker["asked"] if interval == "day") >= 400 + 200
    # the source lost the older history since: too few bars before the data time - the distinct reason, not a correction
    cut = today - pd.Timedelta(days=330)
    window_broker["series"]["DAILY"] = [b for b in window_broker["series"]["DAILY"] if b.timestamp >= cut.to_pydatetime()]
    again = client.post(f"/api/screener/runs/{body['run_id']}/replay", headers=headers).json()      # the same stored run
    assert again["reproduced"] is False and again["history_not_covered"] == 1
    diff = again["differences"][0]
    assert diff["why"] == replay.HISTORY_GONE and diff["replayed"]["reason"].startswith("not enough history"), diff


def test_replay_attaches_exactly_what_the_run_attaches():
    """Review LOW: the run and the replay load data beyond the bars through one helper (`attach_run_data`); a data kind the
    run path starts loading (RUN_PROVIDES) must be attached there, or a replay of such a screen would differ."""
    import inspect

    from app.screener import routes
    assert routes.RUN_PROVIDES <= routes.RUN_ATTACHES
    for fn in (routes.run, routes.replay_run):
        src = inspect.getsource(fn)
        assert "attach_run_data(" in src and "attach_zones(" not in src, fn.__name__
    assert "missing_data(" in inspect.getsource(routes.replay_run)       # data the run path does not load: refused (409)


# ------------------------------------------------------------------------------------------------------ G7 metrics
def _sample(name, labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_scan_latency_and_cache_lookups_are_measured(flags_on, broker):
    headers, _ = _owner("u3-metrics@example.com")
    before = _sample("atp_screen_run_duration_seconds_count", {"path": "run"})
    preview_before = _sample("atp_screen_run_duration_seconds_count", {"path": "preview"})
    _stored_run(headers)
    client.post("/api/screener/run", json={"source": SOURCE, "params": {"n": 20}, "symbols": ["UP"], "preview": True}, headers=headers)
    assert _sample("atp_screen_run_duration_seconds_count", {"path": "run"}) == before + 1
    assert _sample("atp_screen_run_duration_seconds_count", {"path": "preview"}) == preview_before + 1
    from app.screener import compile_screen
    from app.screener.result_cache import ResultCache, namespace
    ast, validated = compile_screen("close > 1", base_tf="1d")
    universe = [SymbolData("UP", {"1d": pd.DataFrame({"open": [1.0], "high": [2.0], "low": [1.0], "close": [2.0], "volume": [1.0]},
                                                    index=pd.DatetimeIndex([pd.Timestamp("2026-03-02", tz="UTC")]))})]
    hits, misses = _sample("atp_screen_cache_lookups_total", {"outcome": "hit"}), _sample("atp_screen_cache_lookups_total", {"outcome": "miss"})
    cache = ResultCache()
    for _ in range(2):
        cache.run(ast, validated, universe, namespace=namespace(1, source="t"), base_tf="1d")
    assert _sample("atp_screen_cache_lookups_total", {"outcome": "hit"}) == hits + 1
    assert _sample("atp_screen_cache_lookups_total", {"outcome": "miss"}) == misses + 1
    from app.observability import metrics
    assert "path" in metrics.SCREEN_RUN_SECONDS._labelnames                    # the bar-close engine observes path="barclose"

