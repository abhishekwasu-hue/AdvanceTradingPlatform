"""Strategy templates step 1: the parity gate (owner order section 8 "scan = backtest = live", S11, R3, R6) and R4's
"determine_exit_price tolerates target1 = None".

The scan series, both backtest engines and the worker's call run on the same fixed trailing window W and must give the
same side, invalidation and setup instance on every bar each of them asked about; every backtest trade opens on a scan
signal bar with that bar's invalidation as its stop. The signals never depend on the trades (R3)."""
import numpy as np
import pytest

from app.backtest.options_engine import OptionBacktestConfig
from app.core.enums import OptionStrategy
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.strategy_templates import compile as C
from app.strategy_templates import library, parity
from app.strategy_templates.adapter import TemplateStrategy
from app.trading.exit_logic import determine_exit_price

D1 = library.get("D1")
RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)


def _strategy(tf="15m", **params):
    return TemplateStrategy(D1, C.resolve_settings(D1, tf, params=params))


def _window(seed, tf="15m", total=700, w=400, scale=1.0):
    df = synthetic_bars(total, tf={"1h": "60m"}.get(tf, tf), seed=seed)
    df[["open", "high", "low", "close"]] *= scale
    return df.iloc[-w:]                                        # the fixed TRAILING window W (R6), not the history's start


CASES = [("15m", {}, 3), ("15m", {"period": 7, "multiplier": 2.0}, 4),
         ("15m", {"use_context": True, "context_timeframe": "1h", "multiplier": 2.0}, 6),
         ("1h", {"use_context": True, "context_timeframe": "1d", "multiplier": 2.0}, 3)]


@pytest.mark.parametrize("tf, params, seed", CASES)
def test_scan_equals_backtest_equals_worker(tf, params, seed):
    strat = _strategy(tf, **params)
    window = _window(seed, tf)
    scan = parity.scan_answers(strat.recipe, window)
    assert sum(1 for a in scan.values() if a.direction) >= 3, "the fixture must carry signals to compare"
    answers, trades = parity.backtest_answers(strat, window, "S", RISK)
    # the backtest asks only while flat; since CP6-15 its losses-in-a-row pause ends at the next session, so it holds more
    # trades and asks on fewer bars than when the pause never ended (one case: 96 of 400) - still a wide comparison
    assert len(answers) > len(window) // 5
    assert parity.compare(scan, answers, "backtest") == []
    assert trades and parity.compare_trades(scan, trades, lambda ts: ts) == []
    # the worker on each signal bar and its neighbours, every 5th bar and the window's last bar (each call computes the
    # bars it is given from scratch, as the worker does)
    signal_at = [i for i, a in enumerate(scan.values()) if a.direction]
    bars = sorted({j for i in signal_at for j in (i - 1, i, i + 1) if 0 <= j < len(window)} | set(range(0, len(window), 5)) | {len(window) - 1})
    worker = parity.worker_answers(strat, window, "S", bars)
    assert parity.compare(scan, worker, "worker") == []
    # every scan signal is seen by the worker on its own bar (the backtest only asks while flat)
    assert {ts for ts, a in scan.items() if a.direction} == {ts for ts, a in worker.items() if a.direction}


def test_scan_equals_the_options_backtest():
    strat = _strategy("15m")
    window = _window(7, scale=240.0)                           # index-like prices for the option ladder
    scan = parity.scan_answers(strat.recipe, window)
    config = OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2)
    answers = parity.options_answers(strat, window, "NIFTY 50", config, RISK)
    assert len(answers) > 20
    assert parity.compare(scan, answers, "options") == []
    assert any(a.direction for a in answers.values())


def test_signals_do_not_depend_on_the_trades():
    """R3: a run that trades (the backtest) and a run that never trades (the scan) give the same series on the bars the
    backtest asked; and a second backtest of the same window asks the same bars and gets the same answers."""
    strat = _strategy("15m", period=7, multiplier=2.0)
    window = _window(8)
    first, trades_a = parity.backtest_answers(strat, window, "S", RISK)
    second, trades_b = parity.backtest_answers(strat, window, "S", RISK)
    assert first == second and trades_a == trades_b


def test_compare_reports_a_difference():
    scan = {1: parity.Answer(1, 10.0, "long@x"), 2: parity.Answer(0)}
    assert parity.compare(scan, {1: parity.Answer(1, 10.0, "long@x"), 2: parity.Answer(0)}, "p") == []
    assert parity.compare(scan, {1: parity.Answer(1, 10.5, "long@x")}, "p")
    assert parity.compare(scan, {1: parity.Answer(1, 10.0, "long@y")}, "p")
    assert parity.compare(scan, {2: parity.Answer(-1, 11.0, "short@z")}, "p")
    assert parity.compare(scan, {3: parity.Answer(0)}, "p") == ["p: 3 is not a bar of the window"]
    assert parity.compare_trades(scan, [(1, 1, 10.0)], lambda t: t) == []
    assert parity.compare_trades(scan, [(1, -1, 10.0)], lambda t: t) and parity.compare_trades(scan, [(2, 1, 9.0)], lambda t: t)


def test_fetch_bars_covers_every_timeframe_the_recipe_reads():
    plain = _strategy("15m").recipe
    assert parity.fetch_bars(plain) == plain.min_bars * parity.SAFETY_FACTOR
    ctx = _strategy("15m", use_context=True, context_timeframe="1d").recipe
    daily_need = ctx.lookback["1d"]
    assert parity.fetch_bars(ctx, safety=1) >= daily_need * 25           # 25 fifteen-minute bars to a session
    assert parity.fetch_bars(ctx) > parity.fetch_bars(plain)
    # what is fetched is enough: on a window of exactly that many bars the recipe is out of its warm-up before the end
    for recipe, tf in ((plain, "15m"), (ctx, "15m")):
        bars = parity.fetch_bars(recipe)
        window = synthetic_bars(bars + 50, tf=tf, seed=2).iloc[-bars:]
        series = parity.engine.compute(recipe, {tf: window})
        assert series.warmup_bars < len(window)


# --- R4: an exit check with no target ---------------------------------------------------------------------------------

@pytest.mark.parametrize("direction, low, high, want", [
    ("LONG", 99.0, 120.0, None), ("LONG", 94.0, 101.0, ("Stop Loss", 95.0)),
    ("SHORT", 80.0, 101.0, None), ("SHORT", 99.0, 106.0, ("Stop Loss", 105.0)),
])
def test_determine_exit_price_without_targets(direction, low, high, want):
    stop = 95.0 if direction == "LONG" else 105.0
    assert determine_exit_price(direction, stop, None, None, low, high) == want
    assert determine_exit_price(direction, stop, None, None, low, high, open_price=100.0) == want


def test_determine_exit_price_with_targets_unchanged():
    assert determine_exit_price("LONG", 95.0, 110.0, None, 99.0, 111.0) == ("Target 1", 110.0)
    assert determine_exit_price("LONG", 95.0, 110.0, 120.0, 99.0, 121.0) == ("Target 2", 120.0)
    assert determine_exit_price("SHORT", 105.0, 90.0, None, 89.0, 101.0) == ("Target 1", 90.0)
    assert determine_exit_price("LONG", 95.0, 110.0, None, 99.0, 115.0, open_price=112.0) == ("Target 1", 112.0)
    assert np.isfinite(determine_exit_price("SHORT", 105.0, None, 80.0, 79.0, 101.0)[1])
