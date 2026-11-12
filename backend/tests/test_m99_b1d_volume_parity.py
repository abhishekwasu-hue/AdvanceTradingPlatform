"""M99 B1-d: scan = backtest = live for an index's volume.

- The backtest's bar source (app/backtest/bar_source.py) gives an index frame its near-month future's volume, read
  through the same loader as the index's own bars; no contract or no future bars: the index keeps its own bars.
- The template engine (`engine.compute`) reads an index's 0 volume as no volume data (M93-Q3), as the screener does.
- Parity: one index frame gives the same volume through the screener, the worker, the backtest and the template engine.

Synthetic fixtures with our own numbers."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from app.ai import evidence
from app.backtest import bar_source
from app.core.models import OHLCVBar, bars_to_dataframe
from app.market_data import index_volume
from app.platform import controls
from app.quality.lookahead import synthetic_bars
from app.screener import parse
from app.screener.runtime import SymbolData, evaluate
from app.strategy_templates import engine, library
from app.strategy_templates.compile import Part, compile_recipe, resolve_settings
from app.workers import strategy_bars

NOW = datetime(2026, 3, 2, 4, 10, tzinfo=UTC)
FUT = index_volume.NearFuture("NIFTY", "NIFTY26MARFUT", date(2026, 3, 26), "NFO")


def _index_and_future() -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = pd.date_range("2026-03-02 03:45", periods=4, freq="5min", tz="UTC")
    index_frame = pd.DataFrame({"open": [100.0, 101.0, 102.0, 101.5], "high": [101.0, 102.0, 103.0, 102.5],
                                "low": [99.0, 100.0, 101.0, 100.5], "close": [100.5, 101.5, 102.5, 101.0], "volume": 0.0}, index=idx)
    fut_frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                              "volume": [500.0, 700.0, 900.0]}, index=idx[1:])                    # no bar at the first stamp
    return index_frame, fut_frame


@pytest.fixture
def near(monkeypatch):
    def use(fut: index_volume.NearFuture | None) -> None:
        async def near_future(session, symbol, exchange, on):
            assert isinstance(on, date)
            return fut
        monkeypatch.setattr(index_volume, "near_future", near_future)
    use(FUT)
    return use


@pytest.fixture
def broker(monkeypatch):
    """The broker read every backtest path takes (`evidence.server_frame`), per symbol; the flags off."""
    frames: dict[str, pd.DataFrame] = {}
    reads: list[tuple[str, str, int]] = []

    async def server_frame(session, tenant_id, symbol, exchange, timeframe, *, broker=None, lookback_days=30, user=None, own_only=False):
        reads.append((symbol, exchange, lookback_days))
        if symbol not in frames:
            raise HTTPException(status_code=409, detail=f"No server data for {symbol}")
        return frames[symbol].copy(), "broker:upstox"

    async def flag_enabled(session, flag, tenant_id=None):
        return False

    monkeypatch.setattr(evidence, "server_frame", server_frame)
    monkeypatch.setattr(controls, "flag_enabled", flag_enabled)
    return frames, reads


def _backtest(symbol: str, timeframe: str = "5m", lookback: int = 10) -> bar_source.BacktestBars:
    return asyncio.run(bar_source.resolve(object(), 1, symbol, "NSE", timeframe, lookback_days=lookback, now=NOW))


# ---- the backtest's bar source -----------------------------------------------------------------------------------------

def test_an_index_backtest_frame_gets_its_near_month_future_volume(near, broker):
    frames, reads = broker
    index_frame, fut_frame = _index_and_future()
    frames.update({"NIFTY 50": index_frame, FUT.symbol: fut_frame})
    got = _backtest("NIFTY 50")
    assert got.frame["volume"].tolist() == [0.0, 500.0, 700.0, 900.0]               # 0 where the future has no bar
    assert got.frame[["open", "high", "low", "close"]].equals(index_frame[["open", "high", "low", "close"]])
    assert (got.source, got.kind) == ("broker:upstox", "broker")                     # the index's own label
    assert reads == [("NIFTY 50", "NSE", 10), (FUT.symbol, "NFO", 10)]


def test_the_future_reads_the_broker_intraday_window_and_the_daily_window_as_asked(near, broker):
    frames, reads = broker
    index_frame, fut_frame = _index_and_future()
    frames.update({"NIFTY 50": index_frame, FUT.symbol: fut_frame})
    _backtest("NIFTY 50", "5m", 90)
    _backtest("NIFTY 50", "day", 400)
    assert reads[1] == (FUT.symbol, "NFO", bar_source.BROKER_INTRADAY_DAYS)
    assert reads[3] == (FUT.symbol, "NFO", 400)


def test_a_stock_or_an_unread_future_keeps_its_own_bars(near, broker):
    frames, reads = broker
    index_frame, _ = _index_and_future()
    stock = index_frame.assign(volume=123.0)
    frames.update({"RELIANCE": stock, "NIFTY 50": index_frame})
    assert _backtest("RELIANCE").frame.equals(stock) and reads == [("RELIANCE", "NSE", 10)]
    assert _backtest("NIFTY 50").frame.equals(index_frame)                          # the future's read failed: as before
    near(None)
    assert _backtest("NIFTY 50").frame.equals(index_frame)                          # no contract in the master: as before


# ---- the template engine -----------------------------------------------------------------------------------------------

def _b1(timeframe: str = "15m"):
    template = library.get("B1")
    settings = resolve_settings(template, timeframe, params={"vol_mult": 1.5, "range_mode": "fixed"})
    return template, settings, compile_recipe(template, settings)


def test_a_zero_volume_index_bar_is_no_volume_data_in_the_template_engine():
    _, _, recipe = _b1()
    df = synthetic_bars(900, tf="15m", seed=1)
    df = df.assign(volume=np.where(np.arange(len(df)) % 3 == 0, 0.0, df["volume"].to_numpy(float)))
    before = df.copy()
    stock = engine.compute(recipe, {"15m": df}, "S")
    index = engine.compute(recipe, {"15m": df}, "NIFTY 50")
    screened = engine.compute(recipe, {"15m": index_volume.screen_frame(df)}, "S")
    assert np.array_equal(index.direction, screened.direction)                     # read as the screener reads it
    assert (stock.direction != 0).any() and not np.array_equal(stock.direction, index.direction)
    assert df.equals(before)                                                        # the caller's frame is not changed


def test_the_adapter_hands_the_symbol_to_the_engine():
    from app.strategy_templates.adapter import TemplateStrategy
    template, settings, _ = _b1()
    strategy = TemplateStrategy(template, settings)
    df = synthetic_bars(900, tf="15m", seed=1)
    df = df.assign(volume=np.where(np.arange(len(df)) % 3 == 0, 0.0, df["volume"].to_numpy(float)))
    stock, _ = strategy.series(df, "S")
    index, _ = strategy.series(df, "NIFTY 50")
    assert np.array_equal(index.direction, engine.compute(strategy.recipe, {"15m": df}, "NIFTY 50").direction)
    assert not np.array_equal(stock.direction, index.direction)


# ---- parity: screener = worker = backtest = template ---------------------------------------------------------------

class _Market:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    async def get_frames(self, symbol, exchange, base_timeframe, timeframes):
        if symbol not in self.frames:
            raise RuntimeError("no bars")
        return {tf: self.frames[symbol].copy() for tf in timeframes}


def _bars(frame: pd.DataFrame) -> list[OHLCVBar]:
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
            for t, r in frame.iterrows()]


def test_one_index_frame_gives_the_same_volume_on_every_path(near, broker):
    index_frame, fut_frame = _index_and_future()
    frames, _ = broker
    frames.update({"NIFTY 50": index_frame, FUT.symbol: fut_frame})
    # screener: its index bars take the future's volume bar by bar (`replace_volume`), then are read by ScreenQL
    screen_bars = bars_to_dataframe(index_volume.replace_volume(_bars(index_frame), _bars(fut_frame)))
    screener = evaluate(parse("volume"), SymbolData("NIFTY 50", {"5m": screen_bars}), base_tf="5m")
    # worker: the deployment's frames
    worker = asyncio.run(strategy_bars.frames(None, None, _Market({"NIFTY 50": index_frame, FUT.symbol: fut_frame}), "NIFTY 50",
                                              "NSE", "5m", ["5m"], now=NOW, store=False))["5m"]
    # backtest: the bar source
    backtest = _backtest("NIFTY 50").frame
    # template engine: `volume` read on the backtest's / worker's bars through the engine
    _, _, recipe = _b1("5m")
    extra = {"v": Part("volume", parse("volume"), "num", {}, {})}             # the bars' own volume, read by the engine
    tpl_backtest = engine.compute(recipe, {"5m": backtest}, "NIFTY 50", extras=extra).extras["v"]
    tpl_worker = engine.compute(recipe, {"5m": worker}, "NIFTY 50", extras=extra).extras["v"]
    want = [np.nan, 500.0, 700.0, 900.0]
    np.testing.assert_array_equal(screener.to_numpy(float), want)
    np.testing.assert_array_equal(index_volume.screen_frame(worker)["volume"].to_numpy(float), want)
    np.testing.assert_array_equal(index_volume.screen_frame(backtest)["volume"].to_numpy(float), want)
    np.testing.assert_array_equal(tpl_backtest, want)
    np.testing.assert_array_equal(tpl_worker, want)
    assert worker["volume"].tolist() == backtest["volume"].tolist() == screen_bars["volume"].tolist()
