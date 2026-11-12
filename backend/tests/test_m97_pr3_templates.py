"""M97 PR-3 (docs/design/M97_SUPPLY_DEMAND.md Z5): strategy template family I "Supply & Demand" - I1 Demand zone long and
I2 Supply zone short (its mirror), behind `sd_pack`, added the way family H was.

T1: the signal is the first touch of a fresh zone's near (proximal) line; the invalidation is the zone's far (distal) line
beyond by the user's ATR buffer; the target at the opposing zone's near line is a TEMPLATE_LEVEL level (no level beyond
the entry = no target, never a refusal); the optional reward:risk check `min_rr` is off by default; mirror; scan =
backtest = worker (the parity harness); no look-ahead; the flag hides family I. Signals only: nothing here places an
order."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener import sd_scanners as sds
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import compile as C
from app.strategy_templates import engine, ids, language, library, pages, parity
from app.strategy_templates import exits as X
from app.strategy_templates.adapter import ENGINE_LABEL, TemplateStrategy
from app.strategy_templates.routes import FAMILY_FLAGS, FAMILY_NAMES, REFERENCE_LABEL
from app.strategy_templates.schema import Template
from tests.test_m97_sd_zones import AWAY, BASE, LEG_DOWN, OUT_UP

I_IDS = ("I1", "I2")
T = {tid: library.get(tid) for tid in I_IDS}
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
AXIS = 201.0                                       # the supply zone is the demand shapes mirrored around this
K = 400.0                                          # mirror axis of a whole frame
NEUTRAL = ((99.4, 101.0, 99.0, 100.6), (100.6, 101.0, 99.0, 99.4))     # range 2, body 60 %: neither leg nor base
VISIT = (101.0, 101.2, 97.5, 98.5)                 # low 97.5 <= the demand zone's near line 97.7
LOOSE = {"leg_mult": 1.0, "out_mult": 1.0, "leg_body_pct": 50.0, "base_body_pct": 90.0, "base_mult": 0.9}


def _m(c, axis=AXIS):
    return (axis - c[0], axis - c[2], axis - c[1], axis - c[3])


def frame(supply: bool = True, visit: bool = True, tf: str = "1d", after: int = 3) -> pd.DataFrame:
    """110 neutral candles, a fresh supply zone (an RBD, near line 103.3, far line 104.4) when `supply`, five neutral
    candles, a fresh demand zone (a DBR, near line 97.7, far line 96.6), one candle away and its first visit."""
    rows = [NEUTRAL[i % 2] for i in range(110)]
    if supply:
        rows += [_m(c) for c in (LEG_DOWN, *BASE, OUT_UP)]
    rows += [NEUTRAL[i % 2] for i in range(5)] + [LEG_DOWN, *BASE, OUT_UP, AWAY] + ([VISIT] if visit else []) + [AWAY] * after
    freq = "D" if tf == "1d" else f"{int(tf[:-1])}min"
    idx = pd.date_range("2025-01-01 03:45", periods=len(rows), freq=freq, tz="UTC")
    o, h, lo, c = (np.array(col, dtype=float) for col in zip(*rows))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0}, index=idx)


def mirror(df, axis=K):
    out = df.copy()
    out["open"], out["close"] = axis - df["open"], axis - df["close"]
    out["high"], out["low"] = axis - df["low"], axis - df["high"]
    return out


def run(tid, df, tf="1d", sides=None, **params):
    t = T[tid]
    return engine.compute(C.compile_recipe(t, C.resolve_settings(t, tf, sides, params)), {tf: df})


def at(s):
    return [(t, int(s.direction[t]), round(float(s.invalidation[t]), 4)) for t in s.signal_bars()]


VISIT_BAR = len(frame()) - 4                       # the visit, then three candles away


# ================================================================================= the schema and the library
def test_family_i_is_a_schema_change_and_the_other_families_are_unchanged():
    for t in library.templates():
        if t.family == "I":
            assert t.id in I_IDS and t.reference_values and t.style == "Both"
        else:
            assert t.id[0] == t.family and t.family in "ABCDEFGHJKLM", t.id       # M99 J, K, L and M are their own families
    assert FAMILY_NAMES["I"] == "Supply & Demand" and FAMILY_FLAGS == {"G": "screener_waves", "H": "intraday_pack", "I": "sd_pack",
                                                                         "J": "vsa_pack", "K": "wyckoff_pack",
                                                                         "L": "fib_ew_pack", "M": "pa_pack"}
    assert ids.COMPACT_FAMILIES == {"G", "H", "I"} and pages.PAGE_FAMILIES == {"H", "I"}
    assert X.LEVEL_FAMILIES == {"H", "I"} and X.FAMILY_LEVELS["I"] == ("OPPOSING_ZONE",)
    assert X.FAMILY_LEVELS["H"] == ("VWAP", "SESSION_EXTREME", "PREV_CLOSE", "OR_FAR_SIDE")        # family H as before
    assert X.SD_ZONE_KWARGS == sds.KWARGS                                     # the scanners' and the templates' settings agree
    data = T["I1"].model_dump(by_alias=True)
    for bad in ({"id": "H99"}, {"family": "H"}, {"id": "Z1"}, {"family": "Z"}):
        with pytest.raises(ValueError):
            Template.model_validate({**data, **bad})
    assert (T["I1"].sides_default, T["I2"].sides_default) == ("long", "short")
    one, two = (T[i].model_dump(by_alias=True) for i in I_IDS)
    differ = {k for k in one if one[k] != two[k]}
    assert differ == {"id", "name", "idea", "sides_default"}                 # the mirror: the same recipe, the other side


@pytest.mark.parametrize("tid", I_IDS)
def test_the_recipe_compiles_on_every_timeframe_and_its_text_passes_the_guard(tid):
    t = T[tid]
    assert C.validate_template(t) == []
    assert set(t.allowed_timeframes) <= set(ENGINE_LABEL) and t.allowed_timeframes[0] == "1d"
    for tf in t.allowed_timeframes:
        for confirm in ("none", "close_back", "candle"):
            recipe = C.compile_recipe(t, C.resolve_settings(t, tf, "both", {"confirm": confirm, "min_rr": 2.0, "buf": 0.5}))
            assert recipe.required_flags == {"sd_pack"}
        for side in ("long", "short"):
            text = library.signal_text(t, C.resolve_settings(t, tf, "both"), side)
            assert text and language.guard(text) == [], text
    for p in t.params:
        if p.type in ("int", "float"):
            assert p.min is not None and p.max is not None and p.min <= p.default <= p.max
        if p.choices:
            assert len(p.choice_labels) == len(p.choices) and all(language.guard(c) == [] for c in p.choice_labels)
    words = " ".join([t.idea, *(p.label + " " + p.help for p in t.params)]).lower()
    for w in ("buy", "sell", "target", "stop", "profit", "guarantee"):
        assert w not in words, w


def test_every_detector_number_is_a_setting_with_the_reference_value():
    t = T["I1"]
    by = {p.key: p for p in t.params}
    from app.price_action import sd_zones as sd
    for name, _, ref, rule in sd.SETTINGS_TABLE:
        assert by[name].default == ref, name
        if by[name].type in ("int", "float"):
            assert (by[name].min, by[name].max) == rule, name
        elif by[name].type == "bool":                     # M98-P1 Q2: skip_flat_bars on; M99 P1: engulf on, swap off
            assert rule is None and by[name].default is ref, name
        else:
            assert tuple(by[name].choices) == rule, name
    assert by["buf"].default == 0.0 and by["min_rr"].default == 0.0 and by["confirm"].default == "none"
    assert "Reference 2 (2:1) - reference, edit before use" in by["min_rr"].help
    assert all("Reference value - edit before use" in by[k].help for k in ("leg_mult", "base_mult", "out_mult", "edge",
                                                                    "leg_body_pct", "skip_flat_bars"))
    with pytest.raises(C.TemplateError):
        C.resolve_settings(t, "1d", None, {"base_mult": 1.0, "leg_mult": 1.0})              # the constraint, in words


# ================================================================================= T1: entry, stop, target, min_rr
def test_the_signal_is_the_first_touch_of_the_fresh_demand_zone():
    df = frame()
    s = run("I1", df)
    assert at(s) == [(VISIT_BAR, 1, 96.6)]                                     # buffer 0: the far line itself
    assert df["low"].iloc[VISIT_BAR] <= 97.7 < df["low"].iloc[VISIT_BAR - 1]
    assert not at(run("I1", frame(visit=False)))                               # no visit: no signal
    near = evaluate(parse(f'SDZoneEdge("demand", "proximal", {X.SD_ZONE_KWARGS})'), SymbolData("S", {"1d": df}), base_tf="1d",
                    params={k: v for k, v in C.resolve_settings(T["I1"], "1d").params.items()})
    assert near.iloc[VISIT_BAR - 1] == 97.7 and np.isnan(near.iloc[VISIT_BAR])   # fresh before the visit, tested on it
    strat = TemplateStrategy(T["I1"], C.resolve_settings(T["I1"], "1d"))
    sig = strat.analyze({"day": df.iloc[:VISIT_BAR + 1]}, "S")
    assert sig.is_tradeable and sig.entry == 98.5 and sig.stop_loss == pytest.approx(96.6)
    assert sig.target1 is None and sig.risk_reward is None                     # no target unless he picks one
    assert all(language.guard(r) == [] for r in sig.reasons)
    assert strat.analyze({"day": df.iloc[:VISIT_BAR]}, "S").is_tradeable is False


def test_the_invalidation_is_beyond_the_far_line_by_his_atr_buffer():
    df = frame()
    atr = evaluate(parse("ATR(14)[1]"), SymbolData("S", {"1d": df}), base_tf="1d").iloc[VISIT_BAR]
    s = run("I1", df, buf=1.5)
    assert at(s) == [(VISIT_BAR, 1, round(96.6 - 1.5 * atr, 4))]
    assert at(run("I1", df, buf=1.5, atr_n=5))[0][2] != at(s)[0][2]           # his ATR period


def test_the_target_is_the_opposing_zones_near_line_as_a_level():
    df = frame()
    strat = TemplateStrategy(T["I1"], C.resolve_settings(T["I1"], "1d"))
    assert strat.capabilities().levels == ("OPPOSING_ZONE",)
    choice = X.TemplateExits(target_rule="TEMPLATE_LEVEL", target_level="OPPOSING_ZONE")
    assert choice.problems(X.UNDERLYING, strat.capabilities()) == []
    assert "the opposing zone's near line" in choice.describe()
    sig = strat.use_exits(choice).analyze({"day": df.iloc[:VISIT_BAR + 1]}, "S")
    assert sig.target1 == pytest.approx(103.3) and sig.risk_reward == pytest.approx(round(4.8 / 1.9, 2))
    # no fresh supply zone above: no target, the signal stays (never a refusal)
    lone = frame(supply=False)
    b = len(lone) - 4
    strat2 = TemplateStrategy(T["I1"], C.resolve_settings(T["I1"], "1d")).use_exits(choice)
    sig2 = strat2.analyze({"day": lone.iloc[:b + 1]}, "S")
    assert sig2.is_tradeable and sig2.target1 is None and sig2.risk_reward is None
    # the other families do not offer it; family H's levels are unchanged
    h6 = library.get("H6")
    assert "OPPOSING_ZONE" not in X.offered_levels(h6, C.resolve_settings(h6, "5m"))
    with pytest.raises(C.TemplateError):
        X.level_parts(T["I1"], C.resolve_settings(T["I1"], "1d"), "VWAP")


def test_the_reward_to_risk_check_is_off_by_default_and_only_his_value_skips():
    df = frame()                                                               # reward 103.3 - 98.5 = 4.8, risk 1.9: 2.53
    assert at(run("I1", df)) == at(run("I1", df, min_rr=0.0)) == [(VISIT_BAR, 1, 96.6)]
    assert at(run("I1", df, min_rr=2.0)) == [(VISIT_BAR, 1, 96.6)]
    assert at(run("I1", df, min_rr=2.6)) == []                                  # below his minimum: skipped
    lone = frame(supply=False)                                                 # no opposing zone: nothing is measured
    assert len(at(run("I1", lone, min_rr=5.0))) == 1


def test_the_optional_confirmation_is_none_by_default():
    df = frame()
    assert T["I1"].confirmations and T["I1"].confirmations[0].by == "confirm"
    assert at(run("I1", df, confirm="close_back")) == at(run("I1", df))        # 98.5 closes back above 97.7
    assert at(run("I1", df, confirm="candle")) == []                           # no reversal candle on the visit


# ================================================================================= mirror, parity, no look-ahead
def test_i2_is_the_mirror_of_i1_on_mirrored_prices():
    df = frame()
    long_ = at(run("I1", df))
    for tid in I_IDS:
        short = at(run(tid, mirror(df), sides="short"))
        assert [(t, -d, round(K - inv, 4)) for t, d, inv in short] == long_, tid
    assert at(run("I2", mirror(df))) == [(VISIT_BAR, -1, round(K - 96.6, 4))]
    strat = TemplateStrategy(T["I2"], C.resolve_settings(T["I2"], "1d")).use_exits(
        X.TemplateExits(target_rule="TEMPLATE_LEVEL", target_level="OPPOSING_ZONE"))
    sig = strat.analyze({"day": mirror(df).iloc[:VISIT_BAR + 1]}, "S")
    assert sig.target1 == pytest.approx(K - 103.3) and sig.risk_reward == pytest.approx(round(4.8 / 1.9, 2))


def _walk(tf, seed):
    return synthetic_bars({"1d": 900, "15m": 1500}[tf], tf=tf, seed=seed)


@pytest.mark.parametrize("tid, tf, params", [("I1", "1d", LOOSE), ("I2", "15m", {**LOOSE, "buf": 0.5, "min_rr": 1.0}),
                                             ("I1", "15m", {**LOOSE, "confirm": "close_back"})])
def test_scan_equals_backtest_equals_worker(tid, tf, params):
    t = T[tid]
    strat = TemplateStrategy(t, C.resolve_settings(t, tf, "both", params))
    strat.use_exits(X.TemplateExits(target_rule="TEMPLATE_LEVEL", target_level="OPPOSING_ZONE"))
    df = _walk(tf, 1)
    window = df.iloc[-max(strat.recipe.min_bars + 250, 600):].copy()
    scan = parity.scan_answers(strat.recipe, window)
    assert sum(1 for a in scan.values() if a.direction) >= 3
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    assert parity.compare(scan, answers, "backtest") == []
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    marks = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 50))
                   | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", marks)
    assert parity.compare(scan, worker, "worker") == []


@pytest.mark.parametrize("tf", ["1d", "15m"])
def test_no_look_ahead_a_cut_series_equals_the_full_run(tf):
    df = _walk(tf, 2)
    full = run("I1", df, tf, "both", **LOOSE, min_rr=1.0)
    assert len(full.signal_bars()) > 5
    for cut in (300, 451, 777, len(df) - 1):
        part = run("I1", df.iloc[:cut], tf, "both", **LOOSE, min_rr=1.0)
        np.testing.assert_array_equal(part.direction, full.direction[:cut])
        np.testing.assert_array_equal(part.invalidation, full.invalidation[:cut])
    hand = frame()
    for cut in range(VISIT_BAR - 3, len(hand) + 1):
        assert at(run("I1", hand.iloc[:cut])) == [x for x in at(run("I1", hand)) if x[0] < cut]


def test_a_supply_zone_never_holds_the_long_side_back():
    """The opposing zone's line is a sparse series: the long side signals before any supply zone has formed."""
    assert "SDZoneEdge" in engine.SPARSE_SERIES
    assert len(at(run("I1", frame(supply=False)))) == 1


def test_ids_round_trip_and_fit():
    for t in T.values():
        for tf in t.allowed_timeframes:
            sid, settings = ids.adopt(t, tf)
            assert ids.decode(sid) == (t, settings)
        mine = {**LOOSE, "out_ref": "atr", "edge": "body", "confirm": "candle", "buf": 0.5, "min_rr": 2.5, "atr_n": 21, "avg_n": 30,
                "base_max": 3}
        sid, settings = ids.adopt(t, "15m", "both", mine)
        assert len(sid) <= ids.MAX_LENGTH and ids.decode(sid) == (t, settings), sid


# ================================================================================= the how-it-works page
@pytest.mark.parametrize("tid, side", [("I1", "long"), ("I2", "short")])
def test_the_schematic_is_drawn_on_synthetic_zones(tid, side):
    t = T[tid]
    assert pages.has_page(t)
    drawing = pages.schematic(t)
    assert drawing is not None and drawing["side"] == side and drawing["same_bar"] and drawing["timeframe"] == "1d"
    flat = [v for c in drawing["candles"] for v in c]
    assert min(flat) >= 0 and max(flat) <= 100                               # rescaled: no real price
    lines = pages.lines(t, {"long": "x", "short": "x"}, None)
    assert [ln["key"] for ln in lines][:3] == ["idea", "reads", "wrong"]


# ================================================================================= the flag and the API
def _api():
    from tests.test_contract_rules import _load_master
    from tests.test_tpl2_routes import FLAG, _org, client
    _load_master()
    return FLAG, _org, client


def test_family_i_is_hidden_with_the_flag_off_and_listed_with_it_on():
    FLAG, _org, client = _api()
    off = _org("i-off", FLAG)
    cards = client.get("/api/strategy-templates", headers=off["headers"]).json()["templates"]
    assert cards and not [c for c in cards if c["family"] == "I"]
    assert client.post("/api/strategy-templates/I1/adopt", headers=off["headers"], json={}).status_code == 404
    assert client.get("/api/strategy-templates/I1/how-it-works", headers=off["headers"]).status_code == 404
    on = _org("i-on", FLAG, "sd_pack")
    listed = {c["id"]: c for c in client.get("/api/strategy-templates", headers=on["headers"]).json()["templates"]}
    assert {tid for tid, c in listed.items() if c["family"] == "I"} == set(I_IDS)
    for tid in I_IDS:
        card = listed[tid]
        assert card["family_name"] == "Supply & Demand" and card["reference_values"] is True and card["available"], tid
        assert card["default_timeframe"] == "1d"
    assert listed["I1"]["reads"]["long"] and listed["I2"]["reads"]["short"]
    body = client.post("/api/strategy-templates/I1/adopt", headers=on["headers"], json={"timeframe": "1d"}).json()
    assert body["deployment"]["strategy_id"] == body["strategy_id"] and body["deployment"]["timeframe"] == "day"
    assert f"{REFERENCE_LABEL}." in body["notes"]
    how = client.get("/api/strategy-templates/I1/how-it-works", headers=on["headers"]).json()
    assert how["schematic"] is not None and how["window_note"] == "" and how["footer"] == pages.FOOTER
    exits_line = next(ln for ln in how["lines"] if ln["key"] == "exits")["text"]
    assert "the opposing zone's near line" in exits_line
    assert how["scanner"]["long"]["text"].startswith('SDZoneEntry("demand", $entry')     # M99 P1: the entry type


def test_a_family_i_strategy_id_runs_only_with_the_flag_on():
    from app.custom_strategies.resolver import load_template_strategy
    from app.db.models import User
    from tests.test_tpl2_routes import _set_flag
    FLAG, _org, client = _api()
    org = _org("i-resolve", FLAG)
    sid = ids.adopt(T["I1"], "1d")[0]

    async def resolve():
        from tests.test_auth_api import _session_factory
        async with _session_factory() as session:
            user = User(tenant_id=org["tenant_id"], email="x@example.com")
            return await load_template_strategy(sid, user, session)
    with pytest.raises(KeyError):
        asyncio.run(resolve())
    _set_flag("sd_pack", org["tenant_id"])
    assert asyncio.run(resolve()).template.id == "I1"


def test_the_new_files_are_english():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "strategy_templates"
    for rel in ("templates/I1.json", "templates/I2.json", "pages.py", "exits.py"):
        assert (root / rel).read_text(encoding="utf-8").isascii(), rel
