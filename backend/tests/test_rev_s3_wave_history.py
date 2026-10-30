"""REV-S3 (owner order 30 Oct): the wave templates G1-G11 gave 0 trades on three months of 15-minute bars.

The root cause, pinned here on the look-ahead harness's NSE-session bars (synthetic, public):

1. the recipe reads more history than a three-month window holds. A wave count on the default 1h wave chart needs 250
   hourly bars (36 sessions: 894 of 1550 fifteen-minute bars are warm-up), and the default higher trend
   `DowTrend(1)@1d` (G1, G2, G8: "Only with the higher trend") reads as missing on the daily frame's first 250 bars - a
   year - so on 61 daily bars every bar of the window is warm-up;
2. R6 under-sized that need: the recipe's lookback carried DowTrend's refusal minimum (1 bar), not its fetch need (250
   bars), so `history_days` asked a deployment for 175 days where 250 sessions are needed.

The fix (no rule of any template changes): the recipe carries the fetch need (`Recipe.fetch_lookback`, `parity.needs`);
the backtest route fetches that history before the window from the window's own source, used as warm-up only and never
traded (`app.backtest.warmup`); where it cannot, the result says so in one plain sentence. Signals only, PAPER only.
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
import pandas as pd
import pytest

from app.backtest import warmup
from app.backtest.engine import run_backtest
from app.core.models import RiskConfig
from app.quality.lookahead import synthetic_bars
from app.strategy_templates import adapter, engine, library, parity
from app.strategy_templates import compile as C
from app.strategy_templates.adapter import TemplateStrategy

RISK = RiskConfig(capital=10_000_000, risk_per_trade_pct=0.5)
WINDOW = 1550                                   # about 62 sessions of 25 fifteen-minute bars: three months
G_IDS = [f"G{i}" for i in range(1, 12)]


def _strategy(gid: str, **params) -> TemplateStrategy:
    t = library.get(gid)
    return TemplateStrategy(t, C.resolve_settings(t, "15m", params=params or None))


# ======================================================================================================= the root cause
@pytest.mark.parametrize("gid", G_IDS)
def test_three_months_is_mostly_or_wholly_warm_up_for_every_g_template(gid):
    strat = _strategy(gid)
    df = synthetic_bars(WINDOW, tf="15m", seed=1)
    series = engine.compute(strat.recipe, {"15m": df})
    with_trend = strat.settings.params.get("trend_relation") == "with"
    if with_trend:                              # DowTrend(1)@1d never starts on 61 daily bars: every bar is warm-up
        assert series.warmup_bars == WINDOW and set(series.reason) == {engine.REASON_WARMUP}
        assert parity.needs(strat.recipe)["1d"] == 250 and strat.recipe.lookback["1d"] == 1   # refusal unchanged
        assert parity.need_sessions(strat.recipe) == 250
    else:                                       # 250 hourly bars of swings: 36 sessions of the 62
        assert series.warmup_bars > WINDOW // 2
        assert parity.need_sessions(strat.recipe) == 36


def test_r6_now_asks_for_the_daily_trend_history():
    strat = _strategy("G1")
    # 250 daily sessions in the calendar days fetched (weekends and the holiday margin taken off)
    days = strat.history_days()
    assert days == adapter.MAX_HISTORY_DAYS
    assert math.floor((days - adapter.HOLIDAY_MARGIN_DAYS) / adapter.DAYS_PER_SESSION) >= 250
    assert strat.history_bars() >= 250 * 25
    # without the higher trend the 1h wave count decides, as before
    assert _strategy("G1", trend_relation="any").history_days() == 175


def test_a_year_of_history_ends_the_daily_trend_warm_up():
    strat = _strategy("G1")
    df = synthetic_bars(8000, tf="15m", seed=1)
    series = engine.compute(strat.recipe, {"15m": df})
    assert 250 * 25 <= series.warmup_bars < len(df) - WINDOW // 2


# ==================================================================================================== the fix: warm-up
def _split(n: int = 8000, seed: int = 1):
    df = synthetic_bars(n, tf="15m", seed=seed)
    return df.iloc[:-WINDOW], df.iloc[-WINDOW:]


def test_warm_up_is_read_never_traded_and_trades_start_at_the_window():
    history, window = _split()
    strat = _strategy("G4")
    whole = engine.compute(strat.recipe, {"15m": pd.concat([history, window])})
    assert np.count_nonzero(whole.direction[:len(history)]) > 0      # the recipe DOES signal inside the warm-up bars ...
    prepared = warmup.prepare(strat, window, history)
    assert prepared.window_start == window.index[0] and prepared.note is None
    result = run_backtest(prepared.strategy, prepared.frame, "S", "15min", RISK)
    assert result.total_trades >= 1                                    # ... and in the window once it has its history
    assert all(pd.Timestamp(t.entry_time) >= window.index[0] for t in result.trades)   # never before the window
    # every trade sits on a bar where the whole-frame series signals (scan = backtest), with its invalidation as the stop
    at = {pd.Timestamp(ts): i for i, ts in enumerate(whole.index)}
    for trade in result.trades:
        i = at[pd.Timestamp(trade.entry_time)]
        assert whole.direction[i] != 0 and trade.stop_loss == pytest.approx(float(whole.invalidation[i]))


def test_without_the_history_the_same_window_gives_no_trade_and_says_why():
    _, window = _split()
    prepared = warmup.prepare(_strategy("G4"), window, None)
    assert prepared.strategy is not None and len(prepared.frame) == WINDOW
    assert run_backtest(prepared.strategy, prepared.frame, "S", "15min", RISK).total_trades == 0
    assert prepared.note == ("This template needs about 36 trading days of candles before your test window; this test had 0, "
                             "so its first 36 trading days could not give a signal.")
    g1 = warmup.prepare(_strategy("G1"), window, None)
    assert g1.note == ("This template needs about 250 trading days of candles before your test window; this test had 0, "
                       "so no day of this test could give a signal.")
    assert g1.history() == {"note": g1.note, "needed_days": 250, "warmup_days": 0}


def test_the_warm_up_is_causal():
    """The answers on the window's bars come from the bars up to each bar: the series of the frame cut at a window bar
    equals the whole frame's series up to that bar (no later bar - and no bar of the window - is read early)."""
    history, window = _split()
    strat = _strategy("G4")
    frame = pd.concat([history, window])
    whole = engine.compute(strat.recipe, {"15m": frame})
    for cut in (len(history) + 10, len(history) + 700, len(frame) - 1):
        part = engine.compute(strat.recipe, {"15m": frame.iloc[:cut + 1]})
        assert np.array_equal(part.direction, whole.direction[:cut + 1])
        assert np.array_equal(part.reason, whole.reason[:cut + 1])
    # only bars strictly before the window's first bar are taken from the history (overlap and later bars are dropped)
    overlap = pd.concat([history, window.iloc[:100], synthetic_bars(8100, tf="15m", seed=1).iloc[-100:]])
    prepared = warmup.prepare(strat, window, overlap)
    assert len(prepared.frame) == len(frame) and prepared.frame.index.is_monotonic_increasing
    assert prepared.frame.iloc[len(history):].equals(window.tz_convert("UTC") if window.index.tz else window)


def test_a_strategy_that_is_not_a_template_is_unchanged():
    from app.strategy_engine.indicator_strategies import EmaRsiScalper
    builtin = EmaRsiScalper()
    window = synthetic_bars(300, tf="5m", seed=3)
    prepared = warmup.prepare(builtin, window, synthetic_bars(600, tf="5m", seed=3))
    assert prepared.frame is window and prepared.strategy is builtin and prepared.history() is None


# ======================================================================================================== the route
def _candles(df: pd.DataFrame) -> List[dict]:
    return [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close),
             "volume": float(r.volume)} for ts, r in df.iterrows()]


def test_the_backtest_route_fetches_the_history_from_the_windows_source(monkeypatch):
    from app.backtest import bar_source
    from tests.test_tpl2_routes import _org, _set_flag, client
    org = _org("revs3", "strategy_templates", "screener_waves", "screener_breakouts")
    _set_flag("screener_waves", org["tenant_id"])
    history, window = _split()
    asked = {}

    async def resolve(session, tenant_id, symbol, exchange, timeframe, *, lookback_days=30, user=None, broker=None, **_):
        asked.update(symbol=symbol, timeframe=timeframe, lookback_days=lookback_days, broker=broker, tenant=tenant_id)
        return bar_source.BacktestBars(pd.concat([history, window]), "broker:upstox", "broker", "your broker")

    monkeypatch.setattr(bar_source, "resolve", resolve)
    sid = client.post("/api/strategy-templates/G4/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()["strategy_id"]
    body = {"strategy_id": sid, "symbol": "BANKNIFTY", "base_timeframe": "15min", "candles": _candles(window),
            "risk_config": {"capital": 10_000_000, "risk_per_trade_pct": 0.5}}
    fetched = client.post("/api/backtest", headers=org["headers"], json={**body, "data_source": "broker:upstox"})
    assert fetched.status_code == 200, fetched.text
    out = fetched.json()
    assert asked["broker"] == "upstox" and asked["tenant"] == org["tenant_id"] and asked["lookback_days"] >= 175
    assert out["total_trades"] >= 1 and out["history"]["note"] is None and out["history"]["warmup_days"] > 36
    start = pd.Timestamp(window.index[0])
    assert all(pd.Timestamp(t["entry_time"]) >= start for t in out["trades"])
    # uploaded / sample candles: nothing is fetched, and the result says what the test lacked
    asked.clear()
    sample = client.post("/api/backtest", headers=org["headers"], json={**body, "data_source": "sample"})
    assert sample.status_code == 200 and not asked
    assert sample.json()["total_trades"] == 0
    assert sample.json()["history"]["note"].startswith("This template needs about 36 trading days of candles before your test window")
