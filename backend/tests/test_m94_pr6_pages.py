"""M94 PR-6 (docs/design/M94_INTRADAY_PACK.md F10 / F15 / F16; OPEN_QUESTIONS M94-Q6 / Q10): a family-H template's "how it
works" screen, its chart overlay, and the level exit (TEMPLATE_LEVEL).

* How it works: behind `intraday_pack` (404 while off, and for families A-G); 3-5 short lines, each from the template's
  own data; the schematic is the engine's own first signal on the template tests' synthetic walk (setup bar, trigger bar
  and invalidation exactly the engine's, prices rescaled: no real market, no price); the setup scanner is the compiled
  setup itself; the footer is the order's exact sentence.
* Overlay: each compiled part through the ONE shared M91 evaluation - the route's marks and bars equal `screen_marks`
  run on the part, the setup's bars equal the engine's own setup series (list = chart), the invalidation's bars the
  closes beyond the level, the signals the engine's.
* Level exit: one level per deployment (M94-Q6, no partial exits), read on the signal bar; not set by default (every
  run before is unchanged); a level not beyond the entry is no target, never a refusal; scan = backtest = worker; the
  worker reads it from the deployment row; in PAPER the trade closes at the level with every entry gate refusing
  (ADR-0004: exits are never blocked); the signal text never carries it.
* The migration: additive, rollback-safe, reversible, on the one head chain.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.screener import routes as R
from app.screener import watch_screen as W
from app.screener.parser import parse
from app.screener.runtime import SymbolData, evaluate
from app.screener.validator import validate_study
from app.strategy_templates import compile as C
from app.strategy_templates import engine, language, library, pages
from app.strategy_templates import exits as X
from app.strategy_templates.adapter import TemplateStrategy
from tests.breakout_fixtures import history, orb_day, sessions_frame

H_IDS = tuple(t.id for t in library.templates() if t.family == "H")
IST = ZoneInfo("Asia/Kolkata")
VENUE_24X7 = "BTCINR"                       # as tests/test_tpl10_exits_settings.py: no session cut-off in the backtest
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5, min_risk_reward=0.01)
ADVICE = ("buy", "sell", "stop", "stoploss", "target", "profit", "recommend")


def _api():
    from tests.test_contract_rules import _load_master
    from tests.test_tpl2_routes import FLAG, _org, client
    _load_master()
    return FLAG, _org, client


def _walk(tf, seed):
    return synthetic_bars(pages.WALK_BARS[tf], tf=tf, seed=seed)


def _strategy(tid, tf="5m", choice=None, **params):
    t = library.get(tid)
    s = TemplateStrategy(t, C.resolve_settings(t, tf, params=params))
    return s.use_exits(choice) if choice is not None else s


# ======================================================================================================== how it works

def _needs(tid: str) -> tuple[str, ...]:
    """The flags a family H template needs on top of the pack: H4 / H5 read PatternBreakout, a BK4 function (as in
    tests/test_m94_pr5_templates.py); without it the template is not available and its page has no scanner."""
    return ("screener_breakouts", "intraday_pack") + (("screener_breakouts_bk4",) if tid in ("H4", "H5") else ())

def test_how_it_works_is_behind_the_intraday_pack_and_only_for_family_h():
    FLAG, _org, client = _api()
    off = _org("hiw-off", FLAG, "screener_breakouts")
    assert client.get("/api/strategy-templates/H6/how-it-works", headers=off["headers"]).status_code == 404
    on = _org("hiw-on", FLAG, "screener_breakouts", "intraday_pack")
    assert client.get("/api/strategy-templates/D1/how-it-works", headers=on["headers"]).status_code == 404
    assert client.get("/api/strategy-templates/Z9/how-it-works", headers=on["headers"]).status_code == 404
    assert client.post("/api/strategy-templates/D1/overlay", headers=on["headers"], json={"symbol": "X"}).status_code == 404
    assert client.post("/api/strategy-templates/H6/overlay", headers=off["headers"], json={"symbol": "X"}).status_code == 404


@pytest.mark.parametrize("tid", H_IDS)
def test_how_it_works_is_the_templates_own_data_with_the_footer(tid):
    FLAG, _org, client = _api()
    org = _org(f"hiw-{tid}", FLAG, *_needs(tid))
    r = client.get(f"/api/strategy-templates/{tid}/how-it-works", headers=org["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    t = library.get(tid)
    assert body["footer"] == "Information and education only, not investment advice." == pages.FOOTER
    assert body["reference_label"] == "Reference values - edit before use" and body["template"]["id"] == tid
    lines = body["lines"]
    assert 3 <= len(lines) <= 5 and [x["key"] for x in lines][:1] == ["idea"]
    by = {x["key"]: x["text"] for x in lines}
    assert by["idea"] in t.idea and by["reads"] == (body["template"]["reads"]["long"] or body["template"]["reads"]["short"])
    if "wrong" in by:
        assert by["wrong"][1:] in t.idea                                     # the idea's own sentence (its first letter capitalised)
    assert all(language.TIMEFRAME_WORDS[tf] in by["runs"] for tf in t.allowed_timeframes)
    assert "session" in by["runs"]                                           # session_bound: never outlives its session
    for x in lines:                                                          # signals only: no advice words, no number
        words = {w.lower() for w in x["text"].replace("-", " ").replace("/", " ").split()}
        assert not words & set(ADVICE), x
        assert not any(ch.isdigit() for ch in re.sub(r"\b\d+-minute\b", "", x["text"])), x
    # the setup scanner: the compiled setup itself on the first timeframe, with the default settings
    # (per side the template's default settings run: H6-H10 both, the one-direction H1-H5 (M94 PR-5) one; the other is None)
    recipe = C.compile_recipe(t, C.resolve_settings(t, t.allowed_timeframes[0]))
    sides = recipe.settings.side_list()
    assert sides and all(body["scanner"][side] is None for side in ("long", "short") if side not in sides)
    for side in sides:
        part = recipe.part("setup", side)
        assert body["scanner"][side] == {"text": part.text, "params": part.params, "base_tf": t.allowed_timeframes[0]}
        check = validate_study(parse(part.text), base_tf=t.allowed_timeframes[0], params=part.params)
        assert check.validated.ok and check.kind == "bool"


@pytest.mark.parametrize("tid", H_IDS)
def test_the_schematic_is_the_engines_own_signal_on_the_tests_synthetic_walk(tid):
    t = library.get(tid)
    drawing = pages.schematic(t)
    assert drawing is not None, f"{tid}: no signal on {pages.WALK_SEEDS} walks"
    tf = drawing["timeframe"]
    df = _walk(tf, drawing["seed"])
    series = engine.compute(C.compile_recipe(t, C.resolve_settings(t, tf)), {tf: df})
    trig = pages._pick(series, t.trigger is not None, len(df))
    lo = max(0, min(int(series.setup_bar[trig]), trig) - pages.BEFORE)
    assert drawing["trigger"] == trig - lo and drawing["setup"] == int(series.setup_bar[trig]) - lo
    assert series.direction[trig] != 0 and series.signal_bars()[0] <= trig
    assert drawing["side"] == ("long" if series.direction[trig] > 0 else "short")
    window = df.iloc[lo:trig + pages.AFTER + 1]
    assert len(drawing["candles"]) == len(window)
    top = max(window["high"].max(), series.invalidation[trig])
    bottom = min(window["low"].min(), series.invalidation[trig])
    assert drawing["invalidation"] == pytest.approx((series.invalidation[trig] - bottom) / (top - bottom) * 100, abs=0.01)
    closes = [(c - bottom) / (top - bottom) * 100 for c in window["close"]]
    assert [c[3] for c in drawing["candles"]] == pytest.approx(closes, abs=0.01)
    flat = [v for c in drawing["candles"] for v in c] + [drawing["invalidation"]]
    assert min(flat) >= 0 and max(flat) <= 100                                 # rescaled: no real price is drawn
    # the invalidation is on the losing side of the trigger close (the engine's own geometry rule)
    close = drawing["candles"][drawing["trigger"]][3]
    assert (drawing["invalidation"] < close) if drawing["side"] == "long" else (drawing["invalidation"] > close)


# ============================================================================================================ overlay
def _overlay_frame(tf="5m", seed=None):
    return _walk(tf, 3 if seed is None else seed)


@pytest.fixture
def overlay_on(monkeypatch):
    FLAG, _org, client = _api()
    org = _org("ovl", FLAG, "screener_breakouts", "intraday_pack")
    frames = {}

    async def fake_load(session, user, ast, validated, params, symbols, exchange, base_tf, tier=None, **kw):
        fetched = frames.setdefault("fetch", [])
        fetched.append(dict(validated.fetch_lookback))
        return [SymbolData(s, {base_tf: frames[s]}, exchange=exchange) for s in symbols if s in frames], \
            {s: "not stored yet" for s in symbols if s not in frames}, "lake:intraday", {}
    monkeypatch.setattr(R, "load_universe", fake_load)
    yield client, org, frames


@pytest.mark.parametrize("tid, params", [("H9", {}), ("H7", {}), ("H10", {"tol": 0.2}), ("H6", {"vol_mult": 0.0})])
def test_the_overlay_marks_equal_the_shared_screen_evaluation_and_the_engine(overlay_on, tid, params):
    client, org, frames = overlay_on
    df = _overlay_frame()
    frames["OVL"] = df
    r = client.post(f"/api/strategy-templates/{tid}/overlay", headers=org["headers"], json={"symbol": "ovl", "timeframe": "5m", "params": params})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] and body["footer"] == pages.FOOTER and body["base_tf"] == "5m"
    t = library.get(tid)
    recipe = C.compile_recipe(t, C.resolve_settings(t, "5m", params=params))
    parts = pages.overlay_parts(recipe)
    assert [(p["part"], p["side"]) for p in body["parts"]] == [(p.name, p.side) for p in parts]
    assert frames["fetch"][-1]["5m"] >= recipe.lookback["5m"]                       # the bars every part reads
    data = SymbolData("OVL", {"5m": df})
    tail = df.index[-W.OVERLAY_BARS:]
    series = engine.compute(recipe, {"5m": df})
    setup_rows = 0
    for got, p in zip(body["parts"], parts):
        if p.part is None:                                                          # kept by the engine since the setup bar
            assert got["kept"] and t.invalidation is not None
            continue
        check = validate_study(p.part.ast, base_tf="5m", params=p.part.params).validated
        want = W.screen_marks(p.part.ast, check, data, base_tf="5m", params=p.part.params, bars=W.OVERLAY_BARS)
        assert got["matched"] == want["matched"]
        assert W.marks_of(got["rows"]) == W.marks_of(want["rows"]) and [r["value"] for r in got["rows"]] == [r["value"] for r in want["rows"]]
        hits = [pd.Timestamp(x) for x in got["hits"]]
        assert hits == [t_ for t_, h in zip(tail, want["hits"]) if h]
        if p.name == "setup":                                                       # list = chart: the engine's own setup series
            own = evaluate(p.part.ast, data, base_tf="5m", params=p.part.params)
            on = own.reindex(df.index).fillna(False).astype(bool)
            assert [t_ for t_ in tail[:-1] if on.loc[t_]] == [h for h in hits if h != tail[-1]]
            setup_rows += 1
        if p.name == "invalidation":                                                # the closes beyond the level
            level = evaluate(recipe.part("invalidation", p.side).ast, data, base_tf="5m", params=recipe.part("invalidation", p.side).params)
            beyond = (df["close"] < level) if p.side == "long" else (df["close"] > level)
            assert [t_ for t_ in tail[:-1] if bool(beyond.loc[t_])] == [h for h in hits if h != tail[-1]]
    assert setup_rows == len(recipe.settings.side_list())
    want_signals = [(df.index[i], "long" if series.direction[i] > 0 else "short", float(series.invalidation[i]))
                    for i in series.signal_bars() if i >= len(df) - W.OVERLAY_BARS]
    assert [(pd.Timestamp(s["bar"]), s["side"]) for s in body["signals"]] == [(a, b) for a, b, _ in want_signals]
    assert [s["invalidation"] for s in body["signals"]] == pytest.approx([c for _, _, c in want_signals])


def test_the_overlay_without_bars_says_why_and_refuses_bad_settings(overlay_on):
    client, org, _ = overlay_on
    missing = client.post("/api/strategy-templates/H9/overlay", headers=org["headers"], json={"symbol": "nope"}).json()
    assert missing["available"] is False and missing["reason"] == "not stored yet" and missing["footer"] == pages.FOOTER
    bad = client.post("/api/strategy-templates/H6/overlay", headers=org["headers"], json={"symbol": "x", "timeframe": "15m", "params": {"minutes": 20}})
    assert bad.status_code == 422 and bad.json()["detail"]["problems"]
    unknown = client.post("/api/strategy-templates/H9/overlay", headers=org["headers"], json={"symbol": "x", "params": {"nope": 1}})
    assert unknown.status_code == 422


# ========================================================================================================= level exits
def test_the_level_target_by_value():
    assert X.target_level("TEMPLATE_LEVEL", 1, 100.0, 98.0, level=103.0) == (103.0, 1.5)
    assert X.target_level("TEMPLATE_LEVEL", -1, 100.0, 104.0, level=92.0) == (92.0, 2.0)
    # a level that is not beyond the entry on the winning side, or missing: no target, never a refusal
    for level in (99.0, 100.0, float("nan"), None):
        assert X.target_level("TEMPLATE_LEVEL", 1, 100.0, 98.0, level=level) == (None, None)
    assert X.target_level("TEMPLATE_LEVEL", -1, 100.0, 104.0, level=101.0) == (None, None)
    assert X.target_level("R_MULTIPLE", 1, 100.0, 98.0, target_r=2.0, level=150.0) == (104.0, 2.0)  # the level is LEVEL's only


def test_the_levels_each_template_offers_and_the_refusals_in_plain_words():
    h6, h9, d1 = _strategy("H6"), _strategy("H9"), _strategy("D1", "15m")
    assert h6.capabilities().levels == ("VWAP", "SESSION_EXTREME", "PREV_CLOSE", "OR_FAR_SIDE")     # its own opening range
    assert h9.capabilities().levels == ("VWAP", "SESSION_EXTREME", "PREV_CLOSE")
    assert d1.capabilities().levels == ()                                                           # families A-G: none
    caps = h9.capabilities()
    pick = X.TemplateExits(target_rule="TEMPLATE_LEVEL", target_level="VWAP")
    assert pick.problems(X.UNDERLYING, caps) == [] and pick.problems(X.OPTION_SINGLE, caps) == []
    assert X.TemplateExits(target_rule="TEMPLATE_LEVEL").problems(X.UNDERLYING, caps) == ["Pick the level to take profit at from the list"]
    assert X.TemplateExits(target_rule="TEMPLATE_LEVEL", target_level="OR_FAR_SIDE").problems(X.UNDERLYING, caps) == [
        "Pick the level to take profit at from the list"]
    assert X.TemplateExits(target_level="VWAP").problems(X.UNDERLYING, caps) == ["The level is for the level target only"]
    assert pick.problems(X.UNDERLYING, d1.capabilities()) == ["This template offers no level to take profit at"]
    assert pick.problems(X.OPTION_STRUCTURE, caps) == ["A spread takes profit at its own target % of the credit, or on the template's exit signal"]
    assert pick.describe() == "target at the session VWAP" and X.TemplateExits().is_default


LEVEL_CASES = [("H9", "VWAP"), ("H9", "SESSION_EXTREME"), ("H8", "PREV_CLOSE"), ("H6", "OR_FAR_SIDE")]


def _level_values(tid, level, df, params):
    t = library.get(tid)
    settings = C.resolve_settings(t, "5m", params=params)
    data = SymbolData("S", {"5m": df})
    out = {}
    for side, text in zip(("long", "short"), X.LEVELS[level]):
        part = C.compile_part(text, what="t", want="num", template=t, settings=settings)
        out[side] = evaluate(part.ast, data, base_tf="5m", params=part.params).reindex(df.index).to_numpy(dtype=float)
    return out


@pytest.mark.parametrize("tid, level", LEVEL_CASES)
def test_the_level_is_read_on_the_signal_bar_and_never_changes_the_signals(tid, level):
    params = {"vol_mult": 0.0} if tid == "H6" else {}
    choice = X.TemplateExits(target_rule="TEMPLATE_LEVEL", target_level=level)
    for seed in range(6):
        df = _walk("5m", seed)
        plain, chosen = _strategy(tid, **params), _strategy(tid, choice=choice, **params)
        s0, s1 = plain._compute(df), chosen._compute(df)
        if len(s1.signal_bars()) >= 3:
            break
    assert np.array_equal(s0.direction, s1.direction) and np.array_equal(s0.invalidation, s1.invalidation, equal_nan=True)
    values = _level_values(tid, level, df, params)
    with_level = without = 0
    for i in s1.signal_bars():
        d = int(s1.direction[i])
        entry, stop = float(df["close"].iloc[i]), float(s1.invalidation[i])
        lv = values["long" if d > 0 else "short"][i]
        got = chosen.target_at(s1, i, d, entry, stop)
        if np.isfinite(lv) and ((d > 0 and lv > entry) or (d < 0 and lv < entry)):
            assert got == (round(lv, 2), round(abs(lv - entry) / abs(entry - stop), 2))
            with_level += 1
        else:
            assert got == (None, None)                                         # no target, still the same signal
            without += 1
        assert plain.target_at(s0, i, d, entry, stop) == (None, None)         # not set: no target, as before
    assert with_level + without == len(s1.signal_bars())


def test_level_exit_scan_equals_backtest_equals_worker():
    from app.backtest.engine import run_backtest
    choice = X.TemplateExits(target_rule="TEMPLATE_LEVEL", target_level="SESSION_EXTREME")
    df = _walk("5m", 1)
    chosen = _strategy("H9", choice=choice)
    series = chosen._compute(df)
    scan = {df.index[i]: chosen.target_at(series, i, int(series.direction[i]), float(df["close"].iloc[i]), float(series.invalidation[i]))[0]
            for i in series.signal_bars()}
    result = run_backtest(_strategy("H9", choice=choice), df, VENUE_24X7, "5min", RISK)
    assert result.trades
    for trade in result.trades:
        assert trade.target1 == (pytest.approx(scan[pd.Timestamp(trade.entry_time)]) if scan[pd.Timestamp(trade.entry_time)] is not None else None)
        worker = _strategy("H9", choice=choice).analyze({"5min": df.loc[:trade.entry_time]}, "S")
        assert worker.target1 == (pytest.approx(trade.target1) if trade.target1 is not None else None)
    assert any(t.exit_reason == "Target 1" for t in result.trades), "the level must be reached on this walk"
    plain = run_backtest(_strategy("H9"), df, VENUE_24X7, "5min", RISK)
    assert all(t.target1 is None for t in plain.trades) and not any(t.exit_reason == "Target 1" for t in plain.trades)


def test_the_signal_text_never_carries_the_level():
    df = _walk("5m", 1)
    chosen = _strategy("H9", choice=X.TemplateExits(target_rule="TEMPLATE_LEVEL", target_level="SESSION_EXTREME"))
    series = chosen._compute(df)
    with_target = [i for i in series.signal_bars()
                   if chosen.target_at(series, i, int(series.direction[i]), float(df["close"].iloc[i]), float(series.invalidation[i]))[0]]
    assert with_target
    i = with_target[0]
    sig = chosen.analyze({"5min": df.iloc[:i + 1]}, "S")
    plain = _strategy("H9").analyze({"5min": df.iloc[:i + 1]}, "S")
    assert sig.target1 is not None and plain.target1 is None and sig.direction in (SignalDirection.LONG, SignalDirection.SHORT)
    assert sig.reasons == plain.reasons and all(language.guard(r) == [] for r in sig.reasons)


def test_adopt_offers_the_level_exit_on_family_h_only():
    FLAG, _org, client = _api()
    org = _org("lvl-adopt", FLAG, "screener_breakouts", "intraday_pack")
    exits = client.post("/api/strategy-templates/H6/adopt", headers=org["headers"], json={"timeframe": "5m"}).json()["exits"]
    assert "TEMPLATE_LEVEL" in exits["precedence"]["UNDERLYING"]["targets"] and "TEMPLATE_LEVEL" in exits["precedence"]["OPTION_SINGLE"]["targets"]
    assert "TEMPLATE_LEVEL" not in exits["precedence"]["OPTION_STRUCTURE"]["targets"]
    assert [x["value"] for x in exits["levels"]] == ["VWAP", "SESSION_EXTREME", "PREV_CLOSE", "OR_FAR_SIDE"]
    assert all(not any(ch.isdigit() for ch in x["label"]) for x in exits["levels"])
    d1 = client.post("/api/strategy-templates/D1/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()["exits"]
    assert "TEMPLATE_LEVEL" not in d1["precedence"]["UNDERLYING"]["targets"] and d1["levels"] == []


@pytest.mark.usefixtures("redis_required", "no_open_positions_from_earlier_tests")
def test_a_paper_trade_closes_at_the_level_and_no_entry_refusal_stops_it(monkeypatch):
    """H6 end to end with the level exit: adopt -> the deployment route stores the level (a user setting) -> the worker
    reads it from the row and the PAPER trade carries the session high of the signal bar as its level -> with the kill
    switch engaged and every entry check refusing, the next cycle closes the trade at that level (ADR-0004)."""
    from sqlalchemy import select

    from app.db.models import StrategyDeploymentRecord
    from app.execution import signal_execution
    from app.trading import position_monitor as pm
    from app.workers import strategy_bars
    from tests.test_auth_api import _session_factory
    from tests.test_deployments_api import _upgrade_plan
    from tests.test_tpl2_routes import _set_flag
    from tests.test_trading_worker import _FakeBroker, _tenant, _trades, _worker
    FLAG, _, client = _api()
    monkeypatch.setattr(pm, "FILL_POLL_DELAY_SECONDS", 0)
    t = _tenant(f"m94-lvl-{datetime.now().timestamp():.0f}@example.com")
    _upgrade_plan(t["tenant_id"])
    for flag in (FLAG, "screener_breakouts", "intraday_pack"):
        _set_flag(flag, t["tenant_id"])
    pre = client.post("/api/strategy-templates/H6/adopt", headers=t["headers"], json={"timeframe": "5m"}).json()["deployment"]
    made = client.post("/api/deployments", headers=t["headers"], json={
        **pre, "symbol": "reliance", "instrument_kind": "UNDERLYING",
        "template_exits": {"target_rule": "TEMPLATE_LEVEL", "target_level": "SESSION_EXTREME"}})
    assert made.status_code == 201, made.text
    dep = made.json()
    assert dep["template_exits"] == {"target_rule": "TEMPLATE_LEVEL", "target_level": "SESSION_EXTREME"}
    assert "target at the session high / low" in dep["contract_rules"]
    bad = client.post("/api/deployments", headers=t["headers"], json={
        **pre, "symbol": "tcs", "instrument_kind": "UNDERLYING", "template_exits": {"target_rule": "TEMPLATE_LEVEL", "target_level": "NOPE"}})
    assert bad.status_code == 422 and bad.json()["detail"]["problems"] == ["Pick the level to take profit at from the list"]

    last = pd.Timestamp("2026-09-25")
    first = pd.bdate_range(end=last, periods=22)[0].strftime("%Y-%m-%d")
    df = sessions_frame(history(21) + [orb_day()[:8]], first=first)          # the breaking bar (09:45) and one forming
    signal_bar = df.index[-2]
    session_high = float(df.loc[df.index.normalize() == signal_bar.normalize()].loc[:signal_bar, "high"].max())
    assert session_high == pytest.approx(101.8) and df.loc[signal_bar, "close"] < session_high

    async def frames(session, user, market_data, symbol, exchange, base_timeframe, timeframes, *, now=None, store=None):
        return {"5min": df.copy()}
    monkeypatch.setattr(strategy_bars, "frames", frames)
    broker = _FakeBroker(ltp=101.6)
    worker = _worker(monkeypatch, broker)
    now = datetime(2026, 9, 25, 9, 51, tzinfo=IST)
    report = asyncio.run(worker.run_cycle(now=now))
    assert report.signals_executed == 1, report
    trade = next(x for x in _trades(t["tenant_id"]) if x.deployment_id == dep["id"])
    assert trade.mode == "PAPER" and trade.direction == "LONG" and trade.exit_time is None
    assert trade.target1 == pytest.approx(round(session_high, 2)) and trade.stop_loss == pytest.approx(99.9)

    # every entry gate refuses now: the kill switch and the entry checks
    asked = []

    async def refuse(*a, **k):
        asked.append("entry")
        return ["Daily loss limit reached"]
    monkeypatch.setattr(signal_execution, "entry_refusals", refuse)
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "m94 level exit proof"}).status_code == 200
    try:
        broker.ltp = 101.85                                                   # beyond the level
        second = asyncio.run(worker.run_cycle(now=now + timedelta(seconds=40)))
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])
    closed = next(x for x in _trades(t["tenant_id"]) if x.id == trade.id)
    assert closed.exit_time is not None and closed.exit_reason == "Target 1", (closed.exit_reason, second)
    assert closed.exit_price == pytest.approx(session_high, abs=0.1) and closed.mode == "PAPER"
    assert second.signals_executed == 0 and broker.placed == []                # PAPER: nothing reached the broker

    async def row():
        async with _session_factory() as session:
            return await session.scalar(select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.id == dep["id"]))
    record = asyncio.run(row())
    assert record.target_rule == "TEMPLATE_LEVEL" and record.target_level == "SESSION_EXTREME" and record.mode == "PAPER"


def test_the_level_is_not_set_by_default():
    from app.db.models import StrategyDeploymentRecord
    assert X.TemplateExits.from_record(StrategyDeploymentRecord()).target_level is None
    table = StrategyDeploymentRecord.__table__
    assert table.c["target_level"].nullable and table.c["target_level"].type.length == 24 and table.c["target_level"].server_default is None
    assert X.TemplateExitsBody().to_exits() == X.DEFAULT


# ======================================================================================================== the migration
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "f2b4d6e8a0c1_m94_template_level_exit.py"


def _module(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_is_additive_rollback_safe_and_on_the_one_head_chain():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations
    module = _module("m94_level_migration")
    assert module.revision == "f2b4d6e8a0c1" and module.ROLLBACK_SAFE is True and module.COLUMNS == ("target_level",)
    source = MIGRATION.read_text(encoding="utf-8")
    assert risky_operations(source) == [] and not hasattr(module, "ROLLBACK_SAFE_REASON")
    assert not [p for p in lint() if p.startswith(MIGRATION.name)]
    assert source.count("nullable=True") == 1 and "server_default" not in source and "nullable=False" not in source
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1 and "f2b4d6e8a0c1" in {r.revision for r in script.walk_revisions("base", heads[0])}


def test_the_migration_runs_up_and_down_on_sqlite(tmp_path):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    module = _module("m94_level_migration_run")
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE strategy_deployments (id INTEGER PRIMARY KEY, target_rule VARCHAR(24))"))
        conn.execute(sa.text("INSERT INTO strategy_deployments (id, target_rule) VALUES (1, 'R_MULTIPLE')"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()
        assert "target_level" in {c["name"] for c in sa.inspect(conn).get_columns("strategy_deployments")}
        assert tuple(conn.execute(sa.text("SELECT target_rule, target_level FROM strategy_deployments")).one()) == ("R_MULTIPLE", None)
        with Operations.context(ctx):
            module.downgrade()
        assert {c["name"] for c in sa.inspect(conn).get_columns("strategy_deployments")} == {"id", "target_rule"}


# ================================================================================ the frontend's samples, the scanner
FRONTEND = ROOT.parent / "frontend" / "src" / "strategyTemplates"


@pytest.mark.parametrize("tid, name", [("H6", "howItWorksSample.json"), ("H8", "howItWorksSampleH8.json")])
def test_the_frontends_samples_are_what_the_route_serves(tid, name):
    import json
    FLAG, _org, client = _api()
    org = _org(f"hiw-sample-{tid}", FLAG, *_needs(tid))
    body = client.get(f"/api/strategy-templates/{tid}/how-it-works", headers=org["headers"]).json()
    assert json.loads((FRONTEND / name).read_text(encoding="utf-8")) == json.loads(json.dumps(body))


@pytest.mark.parametrize("tid", H_IDS)
def test_the_setup_scanner_opens_as_builder_stages(tid):
    from tests.test_s1d_screener_api import _flag as _screener_flag
    from tests.test_tpl2_routes import _set_flag
    FLAG, _org, client = _api()
    org = _org(f"hiw-stages-{tid}", FLAG, *_needs(tid))
    _screener_flag(True)
    _set_flag("screener_builder_u2", org["tenant_id"])
    try:
        how = client.get(f"/api/strategy-templates/{tid}/how-it-works", headers=org["headers"]).json()
        sides = [side for side in ("long", "short") if how["scanner"][side] is not None]
        assert sides                                                     # every family H template has at least one side
        for side in sides:
            src = how["scanner"][side]
            r = client.post("/api/screener/stages", headers=org["headers"], json={"source": src["text"], "base_tf": src["base_tf"], "params": src["params"]})
            assert r.status_code == 200, r.text
            assert r.json()["stages"] and r.json()["base_tf"] == src["base_tf"]
    finally:
        _screener_flag(False)


def test_a_template_whose_function_flag_is_off_has_no_scanner_on_its_page():
    """H4 reads PatternBreakout (BK4): with the pack on but BK4 off the page is served, the template is not available and
    no setup scanner is offered (the same gate as the library, never a scanner the Screener would refuse)."""
    FLAG, _org, client = _api()
    org = _org("hiw-h4-no-bk4", FLAG, "screener_breakouts", "intraday_pack")
    how = client.get("/api/strategy-templates/H4/how-it-works", headers=org["headers"]).json()
    assert how["scanner"] == {"long": None, "short": None}
