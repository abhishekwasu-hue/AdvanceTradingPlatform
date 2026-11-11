"""M97 PR-4 (docs/design/M97_SUPPLY_DEMAND.md, Z3 + M97-Q4): the "Supply & Demand zones" chart layer and the 10m / 2h
timeframes.

* the layer's pure part per formation (DBR, RBR, RBD, DBD): every field the chart draws and taps (lines, base / leg
  times, the leg / base numbers, the label);
* violated zones hidden unless asked; no look-ahead: the rows on the bars cut at t equal the full run's zones known by t
  with their state at t;
* 10m / 2h built from stored 5m / 1h bars by close time: a built bar exists only once its last finer bar has closed;
  ScreenQL reads @10m / @2h, refused on a screen timeframe that does not divide them;
* the route: flag `sd_pack` off -> 503; zones per chosen timeframe; `at` cuts the bars read; input checks.
Synthetic bars only - no broker, no network. Nothing here touches an order.
"""
from __future__ import annotations

import asyncio
import uuid

import numpy as np
import pandas as pd
import pytest

from app.charts import price_action_layer as L
from app.charts import sd_zones_layer as S
from app.platform import controls
from app.price_action import sd_zones as sd
from app.screener import nodes as n
from app.screener import parse
from app.screener.runtime import SymbolData, bar_close, evaluate, resample
from app.screener.validator import validate
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_m97_sd_zones import BASE, LEG_DOWN, OUT_UP, STATE_PATH, _frame, _mirror, _rbr
from tests.test_phase_l_ai import _owner

URL = "/api/charts/layers/sd-zones"
FORMATIONS = {"DBR": [LEG_DOWN, *BASE, OUT_UP], "RBR": _rbr(), "RBD": _mirror([LEG_DOWN, *BASE, OUT_UP]), "DBD": _mirror(_rbr())}


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------------------------------- the pure part
@pytest.mark.parametrize("formation", list(FORMATIONS))
def test_each_formation_gives_one_zone_with_every_field_the_chart_needs(formation):
    df = _frame(FORMATIONS[formation])
    rows = S.zone_rows(df, "15m")
    assert rows["bars"] == len(df) and rows["as_of_bar"] == int(df.index[-1].timestamp())
    (z,) = rows["zones"]
    last = len(df) - 1
    t = [int(x.timestamp()) for x in df.index]
    side = sd.FORMATION_SIDE[formation]
    assert (z["formation"], z["side"], z["state"], z["tests"], z["timeframe"]) == (formation, side, "fresh", 0, "15m")
    assert (z["base_start"], z["base_end"], z["formed"]) == (t[last - 2], t[last - 1], t[last])
    assert z["leg_in"]["time"] == t[last - 3] and z["leg_out"]["time"] == t[last]
    assert z["base"]["times"] == [t[last - 2], t[last - 1]] and z["base"]["candles"] == 2
    detected = sd.detect(df)[0]
    assert (z["proximal"], z["distal"]) == pytest.approx((detected.proximal, detected.distal))
    # the numbers the detector judged the candles by: a leg >= 1.5 x the average and >= 75 % body, a base <= 0.7 x and <= 50 %
    assert z["leg_in"]["mult"] >= 1.5 and z["leg_in"]["body_pct"] >= 75
    assert z["leg_out"]["mult"] >= 1.5 and z["leg_out"]["of"] == "average range"
    assert z["base"]["max_mult"] <= 0.7 and z["base"]["max_body_pct"] <= 50
    assert z["label"] == f"{'Demand' if side == 'demand' else 'Supply'} · {formation} · Fresh · 15m"
    assert z["id"].startswith("sd-15m-") and z["id"] == S.zone_rows(df, "15m")["zones"][0]["id"]        # stable


def test_labels_name_the_state_the_tests_and_the_timeframe():
    assert S.label_text("demand", "DBR", "fresh", 0, "1d") == "Demand · DBR · Fresh · D"
    assert S.label_text("supply", "RBD", "tested", 2, "2h") == "Supply · RBD · Tested 2 · 2H"
    assert S.label_text("demand", "RBR", "violated", 3, "10m") == "Demand · RBR · Violated · 10m"
    for word in ("buy", "sell", "target", "stop"):
        assert word not in S.label_text("supply", "DBD", "tested", 1, "1w").lower()


def test_violated_zones_are_hidden_unless_asked():
    df = _frame(STATE_PATH)
    hidden = S.zone_rows(df, "5m")
    assert hidden["zones"] == [] and hidden["hidden_violated"] == 1
    (z,) = S.zone_rows(df, "5m", include_violated=True)["zones"]
    assert z["state"] == "violated" and z["violated_at"] == int(df.index[-1].timestamp()) and z["tests"] == 3


def test_no_look_ahead_rows_on_cut_bars_equal_the_full_run_as_of_each_bar():
    df = _frame(STATE_PATH)
    full = sd.track(df, sd.detect(df))
    for t in range(len(df) - 12, len(df)):
        cut = S.zone_rows(df.iloc[:t + 1], "5m", include_violated=True)
        want = [(z.formation, z.state_at(t), z.visits_by(t), z.proximal, z.distal) for z in full if z.state_at(t) is not None]
        got = [(z["formation"], z["state"], z["tests"], z["proximal"], z["distal"]) for z in cut["zones"]]
        assert got == want, t


def test_the_nearest_zones_per_side_are_kept_and_the_rest_counted():
    one = [LEG_DOWN, *BASE, OUT_UP, (100.8, 101.7, 100.5, 101.5)]
    df = _frame(one * 4)
    all_rows = S.zone_rows(df, "5m", per_side=10)
    assert len(all_rows["zones"]) >= 2
    kept = S.zone_rows(df, "5m", per_side=1)
    assert len([z for z in kept["zones"] if z["side"] == "demand"]) == 1
    assert kept["more"] == len(all_rows["zones"]) - len(kept["zones"])


def test_too_few_bars_give_no_zones():
    assert S.zone_rows(pd.DataFrame(), "1d")["zones"] == []
    assert S.zone_rows(_frame([])[:2], "1d")["zones"] == []


# --------------------------------------------------------------------------------------- 10m / 2h by close time
def _session(day: str, step: str) -> pd.DataFrame:
    """One NSE session of `step` bars (09:15 IST on, the last bar cut at 15:30), oldest first, UTC bar-start index."""
    start = pd.Timestamp(f"{day} 09:15", tz="Asia/Kolkata")
    idx = pd.date_range(start, pd.Timestamp(f"{day} 15:29", tz="Asia/Kolkata"), freq=step).tz_convert("UTC")
    k = pd.Series(range(len(idx)), index=idx, dtype=float)
    return pd.DataFrame({"open": 100 + k, "high": 101 + k, "low": 99 + k, "close": 100.5 + k, "volume": 10.0}, index=idx)


def test_a_10m_bar_exists_only_after_its_last_5m_bar_closed():
    five = _session("2026-11-10", "5min")
    for cut in range(1, 9):
        part = five.iloc[:cut]
        ten = S.built(part, "5m", "10m", "NSE")
        last_close = bar_close(part.index[-1:], "5m", "NSE")[0]
        assert len(ten) == cut // 2, cut                                 # an odd 5m count: the open 10m bucket is left out
        if len(ten):
            assert bar_close(ten.index[-1:], "10m", "NSE")[0] <= last_close
            assert ten["high"].iloc[0] == five["high"].iloc[:2].max() and ten["close"].iloc[0] == five["close"].iloc[1]
    full = S.built(five, "5m", "10m", "NSE")
    assert len(full) == 38                                                # 37 whole buckets + 15:25 cut at the close
    assert bar_close(full.index[-1:], "10m", "NSE")[0] == pd.Timestamp("2026-11-10 15:30", tz="Asia/Kolkata").tz_convert("UTC")


def test_a_2h_bar_exists_only_after_its_last_1h_bar_closed_and_the_last_one_ends_at_the_close():
    hour = _session("2026-11-10", "60min")                                # 09:15 .. 15:15 (7 bars, the last 15 minutes)
    starts = [t.tz_convert("Asia/Kolkata").strftime("%H:%M") for t in S.built(hour, "1h", "2h", "NSE").index]
    assert starts == ["09:15", "11:15", "13:15", "15:15"]
    assert len(S.built(hour.iloc[:3], "1h", "2h", "NSE")) == 1             # 11:15 bucket waits for its 12:15 bar
    assert len(S.built(hour.iloc[:4], "1h", "2h", "NSE")) == 2
    two = S.built(hour, "1h", "2h", "NSE")
    assert bar_close(two.index[-1:], "2h", "NSE")[0] == pd.Timestamp("2026-11-10 15:30", tz="Asia/Kolkata").tz_convert("UTC")
    assert two["open"].iloc[1] == hour["open"].iloc[2] and two["close"].iloc[1] == hour["close"].iloc[3]


def test_screenql_reads_10m_and_2h_and_refuses_a_base_that_does_not_divide_them():
    assert "10m" in n.TIMEFRAMES and "2h" in n.TIMEFRAMES and n.TF_MINUTES["10m"] == 10 and n.TF_MINUTES["2h"] == 120
    five = _session("2026-11-10", "5min")
    got = evaluate(parse("close@10m > 0"), SymbolData("S", {"5m": five}, exchange="NSE"), base_tf="5m")
    ten = resample(five, "5m", "10m", exchange="NSE")
    # each 5m bar sees the last 10m bar closed by its own close (never one still forming)
    assert bool(got.iloc[0]) is False and bool(got.iloc[1]) is True
    assert len(ten) == 38
    hour = _session("2026-11-10", "60min")
    assert evaluate(parse("close@2h > 0"), SymbolData("S", {"1h": hour}, exchange="NSE"), base_tf="1h").iloc[-1]
    ok = validate(parse("close@10m > close"), base_tf="5m")
    assert ok.ok, ok.problems
    bad = validate(parse("close@10m > close"), base_tf="3m")
    assert not bad.ok and "divides it" in bad.problems[0].message
    assert validate(parse("close@2h > close"), base_tf="30m").ok


# --------------------------------------------------------------------------------------------------- the route
def _flag(on: bool):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            stored[S.FLAG] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def owner():
    from app.core import rate_limit
    rate_limit.reset("chart_sd_zones")
    _flag(True)
    yield _owner(f"m97-chart-{uuid.uuid4().hex[:10]}@example.com")
    _flag(False)


def _loader_frame(df: pd.DataFrame, step: pd.Timedelta) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": df.index, "bar_end": df.index + step, **{c: df[c].to_numpy() for c in ("open", "high", "low", "close", "volume")}})


@pytest.fixture
def lake(monkeypatch):
    """The loader returns the DBR frame as 15m bars and the RBD frame as daily bars, closed by `end` only."""
    frames = {"15m": (_frame(FORMATIONS["DBR"]), pd.Timedelta(minutes=15)), "1d": (_frame(FORMATIONS["RBD"]), pd.Timedelta(days=1)),
              "1h": (_frame(STATE_PATH), pd.Timedelta(hours=1))}
    for tf, (df, step) in list(frames.items()):
        frames[tf] = (df.set_axis(pd.date_range("2026-03-02 03:45", periods=len(df), freq=step, tz="UTC")), step)
    asked: list[tuple[str, object]] = []

    async def fake_load(session, key, tf, start, end, max_bars=None, private=None, provisional=False):
        asked.append((tf, end))
        if tf not in frames:
            return pd.DataFrame(columns=L.FRAME_COLS), None
        df, step = frames[tf]
        out = _loader_frame(df, step)
        return out[pd.to_datetime(out["bar_end"], utc=True) <= pd.Timestamp(end)].reset_index(drop=True), f"lake:{tf}"
    monkeypatch.setattr(L, "load_frame", fake_load)
    return frames, asked


def test_flag_off_answers_503():
    _flag(False)
    headers, _ = _owner(f"m97-off-{uuid.uuid4().hex[:10]}@example.com")
    assert client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "1d"}, headers=headers).status_code == 503


def test_zones_per_chosen_timeframe_and_violated_hidden(owner, lake):
    headers, _ = owner
    at = "2026-12-31T00:00:00+00:00"
    body = client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "1d,15m,1h", "at": at}, headers=headers)
    assert body.status_code == 200, body.text
    data = body.json()
    assert [t["timeframe"] for t in data["timeframes"]] == ["15m", "1h", "1d"]           # finest first
    by_tf = {t["timeframe"]: t for t in data["timeframes"]}
    assert [z["formation"] for z in by_tf["15m"]["zones"]] == ["DBR"]
    assert [z["formation"] for z in by_tf["1d"]["zones"]] == ["RBD"]
    assert by_tf["1h"]["zones"] == [] and by_tf["1h"]["hidden_violated"] == 1
    assert data["settings_note"] == sd.REFERENCE_LABEL and data["settings"]["avg_n"] == 20
    shown = client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "1h", "at": at, "include_violated": 1}, headers=headers).json()
    assert [z["state"] for z in shown["timeframes"][0]["zones"]] == ["violated"]


def test_at_cuts_the_bars_read_no_zone_before_its_leg_out_closed(owner, lake):
    frames, asked = lake
    headers, _ = owner
    df, step = frames["15m"]
    formed_end = df.index[-1] + step
    before = (formed_end - pd.Timedelta(minutes=1)).isoformat()
    got = client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "15m", "at": before}, headers=headers).json()
    assert got["timeframes"][0]["zones"] == [] and got["timeframes"][0]["bars"] == len(df) - 1
    assert asked[-1] == ("15m", pd.Timestamp(before).to_pydatetime())
    after = client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "15m", "at": formed_end.isoformat()}, headers=headers).json()
    assert [z["formation"] for z in after["timeframes"][0]["zones"]] == ["DBR"]


def test_10m_and_2h_read_the_stored_5m_and_1h_bars(owner, lake):
    _, asked = lake
    headers, _ = owner
    r = client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "10m,2h,1w", "at": "2026-12-31T00:00:00+00:00"}, headers=headers)
    assert r.status_code == 200, r.text
    assert [tf for tf, _ in asked[-3:]] == ["5m", "1h", "1d"]


def test_input_checks(owner, lake):
    headers, _ = owner
    assert client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "7m"}, headers=headers).status_code == 422
    assert client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "1m,5m,15m,1h,1d"}, headers=headers).status_code == 422
    assert client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "1d", "at": "2026-11-10T10:00:00"}, headers=headers).status_code == 422
    odd = client.get(URL, params={"symbol": "NSE:BAD|KEY", "timeframes": "1d"}, headers=headers).json()
    assert odd["timeframes"][0]["zones"] == [] and odd["timeframes"][0]["reason"] == S.NO_BARS


# ------------------------------------------------------------------------------- M98 F1: the exact candles, end to end
@pytest.mark.parametrize("formation", list(FORMATIONS))
@pytest.mark.parametrize("tf, step", [("15m", pd.Timedelta(minutes=15)), ("1h", pd.Timedelta(hours=1)), ("1d", pd.Timedelta(days=1))])
def test_leg_in_base_and_leg_out_times_are_the_detector_bars_own_start_times(formation, tf, step):
    """The detector's bar numbers -> the layer's times: each candle's `time` is the start of exactly that bar (the
    chart stamps its candles by their start) and `end` the start of the next bar of that timeframe."""
    df = _frame(FORMATIONS[formation])
    df.index = pd.date_range("2026-03-02 03:45", periods=len(df), freq=step, tz="UTC")
    (z,) = sd.detect(df)
    (row,) = S.zone_rows(df, tf)["zones"]
    start = [int(t.timestamp()) for t in df.index]
    assert row["leg_in"]["time"] == start[z.leg_in]
    assert row["base"]["times"] == start[z.base_start:z.base_end + 1] == [b["time"] for b in row["base"]["bars"]]
    assert row["leg_out"]["time"] == start[z.formed] == row["formed"]
    assert row["base_start"] == start[z.base_start] and row["base_end"] == start[z.base_end]
    for c, i in [(row["leg_in"], z.leg_in), *zip(row["base"]["bars"], range(z.base_start, z.base_end + 1)), (row["leg_out"], z.formed)]:
        assert c["end"] - c["time"] == int(step.total_seconds())
        assert (c["high"], c["low"]) == (pytest.approx(df["high"].iloc[i]), pytest.approx(df["low"].iloc[i]))
    # the bars named: leg-in = the strong candle just before the base, leg-out = the strong candle just after it
    assert z.leg_in + 1 == z.base_start and z.base_end + 1 == z.formed


def test_a_monthly_candle_spans_its_calendar_month_in_india():
    nums = {"range": [1.0], "body_pct": [50.0], "avg": [1.0], "ref": [1.0]}
    frame = pd.DataFrame({"high": [2.0], "low": [1.0]})
    times = pd.DatetimeIndex([pd.Timestamp("2026-09-30T18:30:00Z")])                  # 1 Oct 2026 00:00 IST
    c = S._candle(frame, {k: np.array(v) for k, v in nums.items()}, times, 0, tf="1M")
    assert c["end"] == int(pd.Timestamp("2026-10-31T18:30:00Z").timestamp())          # 1 Nov 2026 00:00 IST


# ------------------------------------------------------------------- M98 T1: higher-timeframe zones on closed bars only
def test_a_higher_timeframe_zone_on_a_lower_chart_waits_for_its_own_leg_out_to_close(owner, monkeypatch):
    """A 15m chart with 1H zones added: the 1H zone exists only once its 1H leg-out bar has closed - at any time inside
    that hour the 1H rows have no zone (and no bar of it), while the 15m rows are read up to the same time."""
    headers, _ = owner
    hour = _frame(FORMATIONS["DBR"]).set_axis(pd.date_range("2026-03-02 03:45", periods=len(_frame(FORMATIONS["DBR"])), freq="1h", tz="UTC"))
    quarter = _frame(FORMATIONS["RBD"]).set_axis(pd.date_range("2026-03-02 03:45", periods=len(_frame(FORMATIONS["RBD"])), freq="15min", tz="UTC"))
    frames = {"1h": (hour, pd.Timedelta(hours=1)), "15m": (quarter, pd.Timedelta(minutes=15))}

    async def fake_load(session, key, tf, start, end, max_bars=None, private=None, provisional=False):
        df, step = frames[tf]
        out = _loader_frame(df, step)
        return out[pd.to_datetime(out["bar_end"], utc=True) <= pd.Timestamp(end)].reset_index(drop=True), f"lake:{tf}"
    monkeypatch.setattr(L, "load_frame", fake_load)
    leg_out_start = hour.index[-1]
    for minutes in (0, 15, 45, 59):                     # inside the 1H leg-out bar: not closed yet
        at = (leg_out_start + pd.Timedelta(minutes=minutes)).isoformat()
        got = client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "15m,1h", "at": at}, headers=headers).json()
        one_h = next(t for t in got["timeframes"] if t["timeframe"] == "1h")
        assert one_h["zones"] == [] and one_h["bars"] == len(hour) - 1
        assert pd.Timestamp(one_h["as_of_bar"], unit="s", tz="UTC") + pd.Timedelta(hours=1) <= pd.Timestamp(at)
    closed = (leg_out_start + pd.Timedelta(hours=1)).isoformat()
    got = client.get(URL, params={"symbol": "NSE:SDTEST", "timeframes": "15m,1h", "at": closed}, headers=headers).json()
    one_h = next(t for t in got["timeframes"] if t["timeframe"] == "1h")
    assert [z["formation"] for z in one_h["zones"]] == ["DBR"]
