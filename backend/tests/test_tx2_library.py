"""U9 TX2 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §4): mirror screens, the confluence view and watch-then-trigger
lists (`app/screener/mirror.py`, `app/screener/library.py`, `app/screener/library_routes.py`; flag `screener_tx2`, off).

The mirror is checked, not trusted: every rule is evaluated bar by bar on synthetic bars against the original on the
reflected bars, and so is every generated pre-built mirror."""
import asyncio
import json
import types
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.core.models import OHLCVBar
from app.platform import controls
from app.quality.lookahead import synthetic_bars
from app.screener import library as lib
from app.screener import mirror as mr
from app.screener import nodes as n
from app.screener import taxonomy as tx
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner

STARTERS_FILE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "screener" / "starters.json"
FLAGS = ("screener_v2", "screener_gallery", lib.FLAG)


def _run(coro):
    return asyncio.run(coro)


def _set_flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def tx2_on():
    _set_flags(**{f: True for f in FLAGS})
    yield
    _set_flags(**{f: False for f in FLAGS})


# --- the mirror: every rule exact on reflected bars ------------------------------------------------------------------

def _value(ast, frame, tf):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = evaluate(ast, SymbolData("S", {tf: frame}), base_tf=tf)
    return out if isinstance(out, pd.Series) else pd.Series([out] * len(frame), index=frame.index)


SEEDS = (3, 17, 24)                                  # 17 and 24 open with a swing that differs on reflected bars (warm-up)


def _ticks(frame):
    """Prices as whole ticks (0.05 -> 1): reflecting through a whole-tick level is then exact, so an equal high or low
    stays equal on the reflected bars (floats can break such a tie differently)."""
    out = frame.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = np.round(frame[col] / 0.05)
    return out


def _warmup(frame, reflected):
    """mr.SWING_WARMUP: the swing functions are exact from the third confirmed swing (degrees 0 and 1) on both the bars
    and the reflected bars - before it the zigzag's opening swing can differ."""
    from app.price_action import causal_swings as cs
    from app.price_action import pa_settings
    out = 0
    for f in (frame, reflected):
        raw = pd.DataFrame({k: f[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")})
        raw["timestamp"] = raw["bar_end"] = f.index
        for degree in (0, 1):
            pivots = cs.degree_pivots(raw, degree, pa_settings.settings())
            out = max(out, pivots[mr.SWING_WARMUP - 1].confirmed_idx if len(pivots) >= mr.SWING_WARMUP else len(f))
    return out


def _same_on_reflection(text, tf="5m", bars=600):
    """mirror(e) on the bars, mapped back by its class, equals e on the reflected bars - bar by bar, on whole-tick prices,
    for several seeds, from the swing warm-up on (review of TX2)."""
    for seed in SEEDS:
        _same_on_reflection_seed(text, tf, bars, seed)


def _same_on_reflection_seed(text, tf, bars, seed):
    frame = _ticks(synthetic_bars(bars, tf=tf, seed=seed))
    level = float(frame["high"].max() + frame["low"].min())
    reflected = mr.reflect_bars(frame, level)
    ast = parse(text)
    cls, expr = mr.rewrite(ast)
    mine, theirs = _value(expr, frame, tf), _value(ast, reflected, tf)
    if cls == mr.REFL:
        mine = level - mine
    elif cls == mr.NEG:
        mine = -mine
    elif cls == mr.COMP:
        mine = 100 - mine
    elif cls.startswith("cat:"):
        table = mr.CATEGORIES[cls[4:]]
        mine = mine.map(lambda v: table.get(v, v) if isinstance(v, str) else v)
    skip = _warmup(frame, reflected)
    assert skip < bars - 100, f"seed {seed}: the warm-up leaves too few bars"
    mine, theirs = mine.iloc[skip:], theirs.iloc[skip:]
    if mine.dtype == object or theirs.dtype == object or mine.dtype == bool:
        diff = [skip + i for i, (a, b) in enumerate(zip(mine, theirs)) if not ((pd.isna(a) and pd.isna(b)) or a == b)]
        assert not diff, f"{text} (seed {seed}): differs at bars {diff[:5]}"
    else:
        a, b = mine.to_numpy(float), theirs.to_numpy(float)
        assert (np.isnan(a) == np.isnan(b)).all(), f"{text} (seed {seed}): missing values differ"
        both = ~np.isnan(a) & np.isfinite(a)
        assert both.sum() > 0 and np.allclose(a[both], b[both], atol=1e-9), f"{text} (seed {seed}): values differ"
    return cls, n.to_text(expr)


CASES_5M = [
    "close", "open", "high", "low", "volume", "SMA(close, 10)", "EMA(close, 20)", "Lag(close, 3)", "Max(high, 20)", "Min(low, 20)",
    "Greatest(open, close)", "Least(open, close, low)", "RSI(14)", "ATR(14)", "ADX(14)", "PlusDI(14)", "MinusDI(14)", "VWAP()",
    "SessionVWAP()", "DayOpen()", "PDH()", "PDL()", "PDC()", "ORHigh(30)", "ORLow(15)", "BBUpper(20, 2)", "BBMid(20, 2)",
    "BBLower(20, 2)", "SwingHigh(0)", "SwingLow(1)", "SwingDirection(0)", "ZScore(close, 20)", "MedianRange(20)",
    "RelVolume(20)", "RelVolumeTOD(5)", "Count(10, close > open)", "CountStreak(close > open)", "CrossAbove(close, SMA(close, 10))",
    "CrossBelow(close, EMA(close, 10))", "CrossAbove(RSI(14), 30)", "PctChange(volume, 5)", "high - low", "(close - open) / ATR(14)",
    "-(close - open)", "close - open + ATR(14)", "ATR(14) + (close - open)", "low <= EMA(close, 20) + 0.25 * ATR(14)",
    "RSI(14) <= 30", "RSI(14) BETWEEN 30 AND 50", "close BETWEEN SMA(close, 10) AND EMA(close, 20)", "ZScore(close, 20) > 1",
    'SwingDirection(0) == "UP"', 'SwingDirection(0) IN ("UP")', "NOT close > open",
    'Pattern("bullish_engulfing")', 'Pattern("morning_star")', 'Pattern("inside_bar")', 'Pattern("doji")', 'Pattern("bullish_pin_bar")',
    'Pattern("bullish_rejection")', 'Pattern("bullish_strong_body")', 'Pattern("bullish_gap_reversal")',              # AMW-3 v2
    'Pattern("bullish_engulfing", "confirmed")', 'Pattern("bullish_pin_bar", "failed")', 'Pattern("bearish_rejection", "unconfirmed")',   # AMW-3 status
    'SwingZoneStrength("low", 0)', 'SwingZoneDistance("low", 0)', 'ReversalAt(SwingLow(0), "bullish")',
    'RealBreak(SwingHigh(0), "above", 20)', 'RangeBreakout("up", 20, 0, 0)', 'RangeBreakout("down", 10, 0.1, 0, "auto")',
    'SwingBreakout("up", 0, 0)', 'NRBreakout("up", "nr7", 0, 0)', 'NRBreakout("down", "inside", 0, 0)', 'RangeExpansion("up", 1.5, 0)',
    'VolumeBreakout("up", 1.5)', 'ORBreakout("up", 15, 0, FALSE, 15, 0)', 'DayLevelBreak("up", "prev_day", 0, FALSE, 15, 0)',
    'DayLevelBreak("down", "first_hour", 0, FALSE, 0, 0)', 'DayExtremeBreak("up", 0, FALSE, 15, 0)',
    "Count(1, close > EMA(close, 20))[1] >= 1 AND close > high[1]",
    # TPL-F6: the swing structure mirrors (an equal high is not higher, an equal low not lower)
    "DowTrend(0)", 'DowTrend(0) == "UP"', 'DowTrend(0) IN ("DOWN", "RANGE")', 'SwingBreakType("up", 0, 0)',
    'SwingBreakType("down", 0, 0.2) == "CHOCH"',
    # idea library IDEA-12 / IDEA-14: the channel's trend-side line and the swings before the latest one
    'ChannelLine("up")', 'ChannelLine("down", 2, 2)', 'ReversalAt(ChannelLine("up", 2, 1), "bullish")', 'PriorSwingHigh(0)',
    'PriorSwingLow(1, "two")', 'PriorSwingHigh(0) > SwingHigh(0)',
]
CASES_1D = ['NewHighBreakout("up", 4, 0, 0)', "SwingDirection(1)", 'SwingBreakout("up", 1, 0)', "close@1w > SMA(close, 5)@1w",
            'DowTrend(0)@1w != "DOWN"', 'SwingBreakType("up", 1, 0) == "BOS"']
# Read no bars (reference data as of the run date): direction-free, passed through; nothing to evaluate here.
REFERENCE = {"IsFnO", "IndexMember", "Sector", "Industry", "McapBucket"}
NOT_IN_SYNTHETIC_BARS = {"oi"}                     # open interest: passed through unchanged like volume


@pytest.mark.parametrize("text", CASES_5M)
def test_each_mirror_rule_is_exact_on_reflected_bars(text):
    _same_on_reflection(text)


@pytest.mark.parametrize("text", CASES_1D)
def test_each_daily_mirror_rule_is_exact_on_reflected_bars(text):
    _same_on_reflection(text, "1d", 400)


def test_every_mirror_rule_has_an_exactness_case():
    named = set(mr.FIELD_RULES) | set(mr.LEVELS) | set(mr.LINEAR) | set(mr.EXTREMES) | set(mr.SIDED_FILTERS) | set(mr.CATEGORIES)
    named |= mr.SIDED_LEVELS
    named |= {"Count", "CountStreak", "CrossAbove", "CrossBelow", "ZScore", "PctChange", "RealBreak", "ReversalAt", "SwingZoneStrength",
              "SwingZoneDistance", "Pattern"}
    covered = {getattr(node, "name", None) for text in CASES_5M + CASES_1D for node in n.walk(parse(text))}
    assert named - REFERENCE - NOT_IN_SYNTHETIC_BARS - covered == set()
    swaps = {f'Pattern("{k}")' for k in mr.PATTERN_SWAP if k.startswith("bullish") or k in ("doji", "inside_bar", "morning_star")}
    assert swaps <= set(CASES_5M)


@pytest.mark.parametrize("text,why", [
    ("close > 100", "not a price"),
    ("close >= 0.9 * Max(high, 250)", "ratio or a multiple of a price"),
    ("PctChange(close, 5) > 2", "percentage change of a price"),
    ("ATRPercentile(14, 50) < 20", "price level"),
    ("Supertrend(10, 3) < close", "not checked"),
    ('Pattern("hammer")', "no mirrored twin"),
    ("PCR() > 1", "no mirror rule"),
    ("close + close > open", "not symmetric"),
])
def test_asymmetric_logic_has_no_mirror_and_says_why(text, why):
    got = mr.mirror(parse(text))
    assert got.text is None and why in got.reason and got.reason.startswith("No mirror:")


def test_a_screen_that_reads_both_sides_alike_is_its_own_mirror():
    got = mr.mirror(parse("high - low < Min(high - low, 6)[1]"))                # Crabel NR7: a range, no direction
    assert got.symmetric and got.text is None and "its own mirror" in got.reason
    assert mr.mirror(parse("RSI(14) <= 30")).text == "RSI(14) >= 70"
    assert mr.mirror(parse("RSI(2) < $rsi_max")).text == "RSI(2) > 100 - $rsi_max"


# --- the pre-built library ---------------------------------------------------------------------------------------------

def test_every_pre_built_screen_resolves_to_valid_screenql():
    import app.main  # noqa: F401 - registers the breakouts and Legends sources
    from app.screener import compile_screen
    for screen in tx.load().screens:
        resolved, why = lib.resolve(screen)
        assert resolved is not None, f"{screen.id}: {why}"
        _, validated = compile_screen(resolved.text, base_tf=resolved.base_tf, params=resolved.params)
        assert validated.ok, (screen.id, [p.message for p in validated.problems])


def test_the_starters_text_matches_the_frontend_contract():
    rows = json.loads(STARTERS_FILE.read_text(encoding="utf-8"))["starters"]
    assert {r["id"]: (r["text"], r["base_tf"]) for r in rows} == {k: tuple(v) for k, v in lib.STARTERS.items()}


def test_every_long_screen_has_a_checked_mirror_or_a_reason():
    import app.main  # noqa: F401
    from app.screener import compile_screen
    taxonomy = tx.load()
    entries = {m["mirror_of"]: m for m in lib.mirrors(taxonomy)}
    long_ids = {s.id for s in taxonomy.screens if s.tags.get("direction") == "long"}
    assert set(entries) == long_ids
    frame = synthetic_bars(400, tf="1d", seed=5)
    level = float(frame["high"].max() + frame["low"].min())
    for screen in taxonomy.screens:
        if screen.id not in entries:
            continue
        m = entries[screen.id]
        assert m["id"] == screen.id + ".short" and m["direction"] == "short" and (m["text"] is None) != (m["reason"] is None)
        assert tx.advice_words(m["name"]) == []
        if m["text"] is None:
            continue
        _, validated = compile_screen(m["text"], base_tf=m["base_tf"])
        assert validated.ok, (m["id"], [p.message for p in validated.problems])
        if m["how"] == "template_down_side":
            assert '"down"' in m["text"] and '"up"' not in m["text"]
            continue
        resolved, _ = lib.resolve(screen)
        original = lib.inline_params(parse(resolved.text), resolved.params)
        mine, theirs = _value(parse(m["text"]), frame, "1d"), _value(original, mr.reflect_bars(frame, level), "1d")
        assert mine.fillna(False).astype(bool).tolist() == theirs.fillna(False).astype(bool).tolist(), m["id"]
    # what the reviewed filing expects (OPEN_QUESTIONS TX2-2): the starters and five Legends mirror; two Legends do not
    assert entries["starter.oversold"]["text"] == "RSI(14) >= 70"
    assert entries["legends.turtle_channel_breakout"]["text"] == "low < Min(low, 20)[1]"
    assert entries["legends.minervini_trend_template"]["text"] is None and entries["legends.darvas_box_breakout"]["text"] is None


# --- watch-then-trigger lists ------------------------------------------------------------------------------------------

def test_the_library_lint_passes():
    assert lib.lint() == []


def test_the_transition_is_the_trigger_after_a_ready_bar_and_reads_no_future_bar():
    frame = synthetic_bars(600, tf="5m", seed=7)
    ready, trigger = parse("close > EMA(close, 20)"), parse("close > high[1]")
    r, t = _value(ready, frame, "5m").fillna(False).astype(bool), _value(trigger, frame, "5m").fillna(False).astype(bool)
    hit = _value(lib.transition_ast(ready, trigger), frame, "5m").fillna(False).astype(bool)
    assert hit.tolist() == (t & r.shift(1, fill_value=False)).tolist() and hit.sum() > 0
    cut = 400                                                             # no look-ahead: the past does not change
    assert _value(lib.transition_ast(ready, trigger), frame.iloc[:cut], "5m").fillna(False).astype(bool).tolist() == hit.iloc[:cut].tolist()


def test_watch_split_and_confluence_ranking():
    split = lib.watch_split({"A": (True, None), "B": (True, None), "C": (False, "not enough history on 1d")},
                            {"A": (True, None), "B": (False, None)}, ["A", "B", "C", "D"])
    assert split["triggered"] == ["A"] and split["ready"] == ["B"] and split["neither"] == ["C", "D"]
    assert split["not_evaluated"] == {"C": "not enough history on 1d"}
    s = [lib.Resolved("p", "P", "price_action", "breakout", "long", "x", "close > 1", "1d"),
         lib.Resolved("i", "I", "indicator", "momentum", "long", "x", "close > 1", "1d"),
         lib.Resolved("i2", "I2", "indicator", "pullback", "long", "x", "close > 1", "1d")]
    results = {"p": {"X": (True, None), "Y": (False, None)}, "i": {"Y": (True, None), "Z": (True, None)}, "i2": {"Z": (True, None)}}
    out = lib.confluence(s, results, ["Y", "X", "Z", "W"])
    assert [(r["symbol"], r["method_count"], r["screen_count"]) for r in out["rows"]] == [("Z", 1, 2), ("X", 1, 1), ("Y", 1, 1)]
    results["p"]["Z"] = (True, None)
    out = lib.confluence(s, results, ["Y", "X", "Z", "W"])
    assert out["rows"][0]["symbol"] == "Z" and out["rows"][0]["methods"] == ["indicator", "price_action"]
    assert out["unflagged"] == ["W"] and tx.advice_words(out["note"]) == []


# --- API ---------------------------------------------------------------------------------------------------------------

def _bars(step, days=120):
    idx = pd.date_range("2026-01-01", periods=days, freq="1D", tz="UTC")
    closes = 300 + np.arange(days) * step
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=c - 0.5, high=c + 1, low=c - 1, close=float(c), volume=1000.0) for t, c in zip(idx, closes)]


@pytest.fixture
def fake_bars(monkeypatch):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        return _bars({"UP": 2.0, "DOWN": -2.0}.get(symbol, 0.0))
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


def test_the_routes_are_off_by_default():
    headers, _ = _owner("tx2-off@example.com")
    _set_flags(screener_v2=True, screener_gallery=True)
    try:
        for r in (client.get("/api/screener/library/mirrors", headers=headers),
                  client.get("/api/screener/library/watch-lists", headers=headers),
                  client.post("/api/screener/library/confluence", json={"screens": ["a", "b"], "symbols": ["X"]}, headers=headers),
                  client.post("/api/screener/library/watch-trigger", json={"list_id": "watch.rsi_oversold_turn", "symbols": ["X"]},
                              headers=headers)):
            assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == lib.FLAG
    finally:
        _set_flags(screener_v2=False, screener_gallery=False)
    assert lib.FLAG in controls.DEFAULT_OFF_FLAGS and lib.FLAG in controls.FEATURE_FLAGS


def test_mirrors_and_watch_lists_endpoints(tx2_on):
    headers, _ = _owner("tx2-lists@example.com")
    body = client.get("/api/screener/library/mirrors", headers=headers).json()
    ids = {m["id"] for m in body["mirrors"]}
    assert "starter.momentum.short" in ids and not any(i.startswith(("legends.", "breakouts.")) for i in ids)   # their flags are off
    lists = client.get("/api/screener/library/watch-lists", headers=headers).json()["lists"]
    assert {w["id"] for w in lists} == set(lib.WATCH_BY_ID) and all("Count(1, " in w["transition"] for w in lists)


def test_mirrors_leave_out_cards_whose_own_flag_is_off(tx2_on):
    """A legend card behind a flag of its own (U7 LG2 / LG3) and its mirror are listed only while that flag is on - as the
    taxonomy hides the card itself; running such a mirror is refused anyway (confluence)."""
    headers, _ = _owner("tx2-mirror-flags@example.com")
    own = tx.screen_flags()
    assert own, "the legend slices register their cards' own flags"
    _set_flags(screener_legends=True, **{flag: False for flag in set(own.values())})
    try:
        listed = {m["mirror_of"] for m in client.get("/api/screener/library/mirrors", headers=headers).json()["mirrors"]}
        assert not listed & set(own)                                            # no card whose own flag is off
        assert any(i.startswith("legends.") for i in listed)                    # LG1 cards (no own flag) stay listed
        flag = sorted(set(own.values()))[0]
        _set_flags(**{flag: True})
        listed = {m["mirror_of"] for m in client.get("/api/screener/library/mirrors", headers=headers).json()["mirrors"]}
        long_cards = {s.id for s in tx.load().screens if own.get(s.id) == flag and s.tags.get("direction") == "long"}
        assert long_cards and long_cards <= listed                               # that slice's long cards are back
    finally:
        _set_flags(screener_legends=False, **{flag: False for flag in set(own.values())})


def test_mirrors_leave_out_a_family_whose_further_flag_is_off(tx2_on):
    """U6 BK3 / BK4 families need `screener_breakouts` and a flag of their own (`also`): with only the first on, their
    mirrors are not listed (the same `_hidden` list feeds `_resolve`) - as the taxonomy hides the family itself."""
    headers, _ = _owner("tx2-mirror-also@example.com")
    _set_flags(screener_breakouts=True, screener_breakouts_bk3=False, screener_breakouts_bk4=False)
    try:
        listed = {m["mirror_of"] for m in client.get("/api/screener/library/mirrors", headers=headers).json()["mirrors"]}
        assert any(i.startswith("breakouts.") for i in listed)                  # the base family is listed
        assert not any(i.startswith(("breakouts_bk3.", "breakouts_bk4.")) for i in listed)
        _set_flags(screener_breakouts_bk3=True)
        listed = {m["mirror_of"] for m in client.get("/api/screener/library/mirrors", headers=headers).json()["mirrors"]}
        assert any(i.startswith("breakouts_bk3.") for i in listed)               # back once its own flag is on
        assert not any(i.startswith("breakouts_bk4.") for i in listed)
    finally:
        _set_flags(screener_breakouts=False, screener_breakouts_bk3=False, screener_breakouts_bk4=False)


def test_confluence_ranks_symbols_by_methods_with_each_screens_result(tx2_on, fake_bars):
    headers, _ = _owner("tx2-confluence@example.com")
    saved = client.post("/api/screener/screens", json={"name": "New 10-day high", "source": "close > Max(high, 10)[1]"}, headers=headers).json()
    body = {"screens": ["starter.momentum", "starter.daily_trend", "starter.daily_trend.short", f"saved:{saved['id']}"],
            "symbols": ["up", "DOWN", "FLAT"]}
    r = client.post("/api/screener/library/confluence", json=body, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert [row["symbol"] for row in out["rows"]] == ["UP", "DOWN"]
    up = out["rows"][0]
    assert up["methods"] == ["indicator", "my_screens"] and up["screen_count"] == 3
    assert {s["ref"] for s in up["screens"]} == {"starter.momentum", "starter.daily_trend", f"saved:{saved['id']}"}
    down = out["rows"][1]
    assert [s["ref"] for s in down["screens"]] == ["starter.daily_trend.short"]
    assert out["sources"]["1d"]["data_source"] == "broker:fake" and "recommendation" in out["disclaimer"]
    other, _ = _owner("tx2-confluence-other@example.com")
    assert client.post("/api/screener/library/confluence", json=body, headers=other).status_code == 404     # not their saved screen
    hidden = client.post("/api/screener/library/confluence", json={"screens": ["starter.momentum", "legends.turtle_channel_breakout"],
                                                                   "symbols": ["UP"]}, headers=headers)
    assert hidden.status_code == 422 and "feature is off" in hidden.text                                   # Legends flag off
    nomirror = client.post("/api/screener/library/confluence", json={"screens": ["starter.momentum", "starter.momentum.short.short"],
                                                                     "symbols": ["UP"]}, headers=headers)
    assert nomirror.status_code == 422
    assert client.post("/api/screener/library/confluence", json={"screens": ["starter.momentum", "starter.momentum"], "symbols": ["UP"]},
                       headers=headers).status_code == 422


def test_watch_trigger_lists_ready_and_triggered(tx2_on, fake_bars):
    headers, _ = _owner("tx2-watch@example.com")
    r = client.post("/api/screener/library/watch-trigger", json={"ready": "close > SMA(close, 5)", "trigger": "close > high[1]",
                                                                 "symbols": ["UP", "DOWN"]}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["triggered"] == ["UP"] and out["ready"] == [] and out["neither"] == ["DOWN"]
    assert out["transition_text"] == "Count(1, close > SMA(close, 5))[1] >= 1 AND close > high[1]"
    saved = client.post("/api/screener/screens", json={"name": "alert me", "source": out["transition_text"]}, headers=headers)
    assert saved.status_code == 201                                                # the transition saves as a screen to alert on
    waiting = client.post("/api/screener/library/watch-trigger", json={"ready": "close > SMA(close, 5)", "trigger": "close > high[1] + 100",
                                                                       "symbols": ["UP"]}, headers=headers).json()
    assert waiting["ready"] == ["UP"] and waiting["triggered"] == []
    listed = client.post("/api/screener/library/watch-trigger", json={"list_id": "watch.pullback_ema20", "symbols": ["UP"]}, headers=headers)
    assert listed.status_code == 200 and "$" not in listed.json()["transition_text"]
    assert client.post("/api/screener/library/watch-trigger", json={"list_id": "nope", "symbols": ["UP"]}, headers=headers).status_code == 404
    bad = client.post("/api/screener/library/watch-trigger", json={"ready": "close >", "trigger": "close > 1", "symbols": ["UP"]}, headers=headers)
    assert bad.status_code == 422


# --- review of TX2: the shared /run bucket and one evaluation of `ready` -----------------------------------------------------

@pytest.mark.parametrize("list_id", [w.id for w in lib.WATCH_LISTS] + ["own", "rank"])
def test_watch_eval_equals_running_the_ready_and_transition_screens(list_id):
    """The watch-trigger answer, with `ready` evaluated once, equals run_screen on `ready` and on the transition screen -
    on every prefix of several seeds' bars (so both outcomes, and the short-history reasons, occur)."""
    from app.screener import compile_screen
    from app.screener import library_routes as lr
    from app.screener.runtime import run_screen
    w = lib.WATCH_BY_ID.get(list_id)
    own = {"own": ("close > EMA(close, 20)", "close > high[1]", "5m", {}),
           "rank": ("close > EMA(close, 20)", "Rank(PctChange(close, 5)) <= 2", "5m", {})}      # a trigger ranked across the list
    ready_text, trigger_text, tf, params = (w.ready, w.trigger, w.base_tf, w.params) if w else own[list_id]
    ready = lib.inline_params(parse(ready_text), params)
    trigger = lib.inline_params(parse(trigger_text), params)
    transition = lib.transition_ast(ready, trigger)
    _, ready_v = compile_screen(n.to_json(ready), base_tf=tf)
    _, transition_v = compile_screen(n.to_json(transition), base_tf=tf)
    assert ready_v.ok and transition_v.ok
    seen = {"ready": set(), "hit": set()}
    frames = {f"S{seed}": synthetic_bars(400 if tf == "1d" else 600, tf=tf, seed=seed) for seed in (1, 2, 3, 4)}
    for cut in range(5, 400 if tf == "1d" else 600, 7):
        universe = [SymbolData(sym, {tf: f.iloc[:cut]}) for sym, f in frames.items()] + [SymbolData("E", {tf: frames["S1"].iloc[:0]})]
        ready_got, hits = lr.watch_eval(ready, trigger, ready_v, transition_v, universe, tf, transition)
        want_ready = {m.symbol: (m.matched, m.reason) for m in run_screen(ready, ready_v, universe, base_tf=tf)}
        want_hit = {m.symbol: (m.matched, m.reason) for m in run_screen(transition, transition_v, universe, base_tf=tf)}
        assert ready_got == want_ready and hits == want_hit, (list_id, cut)
        seen["ready"] |= {v[0] for k, v in ready_got.items() if k != "E"}
        seen["hit"] |= {v[0] for k, v in hits.items() if k != "E"}
    assert seen["ready"] == {True, False}, list_id
    if list_id == "rank":
        assert transition_v.cross_sectional and seen["hit"] == {True, False}            # the ranked trigger really fires


def test_confluence_and_watch_trigger_are_charged_on_the_run_bucket(tx2_on, fake_bars):
    """Review of TX2 (MED): a confluence of k screens over f fetches costs k + f - 1 `/run` calls and a watch-trigger 2,
    on the same per-user bucket as `/run` (60 a minute)."""
    from app.core import rate_limit
    from app.screener import library_routes as lr
    assert lr.confluence_units(6, 6) == 11 and lr.confluence_units(2, 1) == 2 and lr.WATCH_UNITS == 2
    headers, _ = _owner("tx2-charge@example.com")
    rate_limit.reset("screener_run")
    try:
        body = {"screens": ["starter.momentum", "starter.daily_trend", "starter.daily_trend.short"], "symbols": ["UP"]}
        codes = [client.post("/api/screener/library/confluence", json=body, headers=headers).status_code for _ in range(21)]
        assert codes[:20] == [200] * 20 and codes[20] == 429                     # 20 x 3 units = the 60 a minute of /run
        run = client.post("/api/screener/run", json={"source": "close > 1", "symbols": ["UP"]}, headers=headers)
        assert run.status_code == 429                                            # the same bucket
        rate_limit.reset("screener_run")
        watch = {"ready": "close > SMA(close, 5)", "trigger": "close > high[1]", "symbols": ["UP"]}
        codes = [client.post("/api/screener/library/watch-trigger", json=watch, headers=headers).status_code for _ in range(31)]
        assert codes[:30] == [200] * 30 and codes[30] == 429
    finally:
        rate_limit.reset("screener_run")


def test_a_failing_trigger_keeps_the_ready_answer():
    """Review of TX2 re-check: when only the trigger cannot be evaluated, the ready list still has the symbol."""
    from app.screener import compile_screen
    from app.screener import library_routes as lr
    ready, trigger = parse("close > 0"), parse('NearZone("demand")')                  # zones not loaded: the trigger fails
    transition = lib.transition_ast(ready, trigger)
    _, ready_v = compile_screen(n.to_json(ready), base_tf="5m")
    _, transition_v = compile_screen(n.to_json(transition), base_tf="5m")
    universe = [SymbolData("S", {"5m": synthetic_bars(200, tf="5m", seed=1)})]
    ready_got, hits = lr.watch_eval(ready, trigger, ready_v, transition_v, universe, "5m", transition)
    assert ready_got["S"] == (True, None) and hits["S"][0] is False and "zones" in hits["S"][1]


def test_the_library_refuses_rankscore_it_does_not_load(tx2_on, fake_bars):
    """Merge of main into S7a: missing_data counts RankScore's systems as loaded by /run; the library runs do not load
    them, so a RankScore condition is refused with the reason rather than ranking every symbol NaN."""
    _set_flags(screener_rankings=True)
    try:
        headers, _ = _owner("tx2-rankscore@example.com")
        r = client.post("/api/screener/library/watch-trigger", json={"ready": 'RankScore("platform:Momentum") > 80', "trigger": "close > high[1]",
                                                                     "symbols": ["UP"]}, headers=headers)
        assert r.status_code == 422 and "RankScore() is not run in confluence or watch lists" in r.text, r.text
    finally:
        _set_flags(screener_rankings=False)


def test_the_library_refuses_ownership_it_does_not_load(tx2_on, fake_bars):
    """Merge of main into S8: ownership / event functions are refused in confluence and watch lists with the reason
    (the library runs load bars only), as screen history and alert rules refuse them."""
    _set_flags(screener_ownership=True)
    try:
        headers, _ = _owner("tx2-ownership@example.com")
        r = client.post("/api/screener/library/watch-trigger", json={"ready": 'Holding("promoter") > 50', "trigger": "close > high[1]",
                                                                     "symbols": ["UP"]}, headers=headers)
        assert r.status_code == 422 and "confluence and watch lists do not read ownership or events" in r.text, r.text
    finally:
        _set_flags(screener_ownership=False)
