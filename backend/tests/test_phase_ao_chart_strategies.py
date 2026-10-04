"""Phase AO: indicator-combination strategies and the chart-run endpoint."""
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.backtest.engine import run_backtest
from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.strategy_engine.combo_strategies import (
    BollingerRsiReversion, MacdEmaTrend, OpeningRangeBreakout, VwapSupertrend, session_vwap_or_mean,
)
from tests.test_auth_api import client

UTC = timezone.utc


def _frame(closes, start=datetime(2026, 9, 28, 3, 45, tzinfo=UTC), minutes=5, volume=1000.0, spread=0.4):
    idx = pd.DatetimeIndex([start + timedelta(minutes=minutes * i) for i in range(len(closes))])
    opens = [closes[0]] + list(closes[:-1])
    return pd.DataFrame({
        "open": opens, "close": closes,
        "high": [max(o, c) + spread for o, c in zip(opens, closes)],
        "low": [min(o, c) - spread for o, c in zip(opens, closes)],
        "volume": [volume] * len(closes),
    }, index=idx)


def _trend_with_waves(n=420, drift=0.08, amp=3.0, period=24, base=100.0, sign=1):
    return [base + sign * drift * i + amp * math.sin(2 * math.pi * i / period) for i in range(n)]


def _directions(strategy, df):
    result = run_backtest(strategy, df, "TEST", "5min", RiskConfig(capital=1_000_000, max_trades_per_day=100, max_consecutive_losses=100))
    return {t.direction for t in result.trades}, result


def test_macd_ema_trend_only_trades_with_the_trend():
    up, _ = _directions(MacdEmaTrend(), _frame(_trend_with_waves()))
    down, _ = _directions(MacdEmaTrend(), _frame(_trend_with_waves(base=200.0, sign=-1)))
    assert up == {SignalDirection.LONG}
    assert down == {SignalDirection.SHORT}


def test_bollinger_rsi_reversion_fades_extremes():
    closes = [100.0 + 0.3 * math.sin(i / 3) for i in range(60)]
    closes += [closes[-1] - 1.5 * k for k in range(1, 7)]          # flush below the band, RSI collapses
    closes += [closes[-1] + 2.5, closes[-1] + 3.0, closes[-1] + 3.2] + [closes[-1] + 3.0] * 20
    dirs, result = _directions(BollingerRsiReversion(), _frame(closes, spread=0.2))
    assert SignalDirection.LONG in dirs, result


def test_vwap_supertrend_trades_trends():
    up, _ = _directions(VwapSupertrend(), _frame(_trend_with_waves(n=300, drift=0.15, amp=4.0, period=40)))
    assert SignalDirection.LONG in up


def test_session_vwap_falls_back_to_typical_mean_without_volume():
    df = _frame([100.0, 102.0, 104.0], volume=0.0, spread=0.0)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    running_mean = list(typical.expanding().mean().round(4))
    assert list(session_vwap_or_mean(df).round(4)) == running_mean        # no volume: running mean
    df_vol = _frame([100.0, 102.0, 104.0], volume=10.0, spread=0.0)
    assert list(session_vwap_or_mean(df_vol).round(4)) == running_mean    # equal volume: same numbers
    df_vol.loc[df_vol.index[2], "volume"] = 30.0
    vw = session_vwap_or_mean(df_vol).iloc[-1]
    assert round(vw, 4) == round(float((typical * df_vol["volume"]).sum() / df_vol["volume"].sum()), 4)


def test_orb_takes_only_the_first_close_beyond_the_range():
    # 09:15-09:30 IST (03:45-04:00 UTC): three 5m bars inside 100-101, then a close at 102 breaks out.
    closes = [100.5, 100.2, 100.8, 102.0, 103.0, 101.5, 103.5]
    df = _frame(closes, volume=0.0, spread=0.2)
    s = OpeningRangeBreakout()
    first = s.analyze({"5min": df.iloc[:4]}, "NIFTY")
    assert first.direction == SignalDirection.LONG and "index" in " ".join(first.reasons)
    assert first.stop_loss is not None and first.stop_loss < first.entry
    later = s.analyze({"5min": df.iloc[:7]}, "NIFTY")
    assert later.direction == SignalDirection.NO_TRADE      # not the first close beyond the range
    early = s.analyze({"5min": df.iloc[:3]}, "NIFTY")
    assert early.direction == SignalDirection.NO_TRADE      # range not complete yet


def _bars(df):
    return [{"timestamp": ts.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
            for ts, r in df.iterrows()]


def test_chart_run_draws_trades_and_last_signal():
    df = _frame(_trend_with_waves())
    r = client.post("/api/strategies/macd_ema_trend_5m/chart-run", json={"symbol": "nifty", "timeframe": "5min", "candles": _bars(df)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compatible"] is True and body["bars_used"] == len(df)
    assert body["total_trades"] == len(body["trades"]) > 0
    assert {t["direction"] for t in body["trades"]} == {"LONG"}
    assert body["last_signal"]["strategy_id"] == "macd_ema_trend_5m" and body["last_signal"]["symbol"] == "NIFTY"


def test_chart_run_refuses_a_finer_strategy_on_coarser_candles():
    df = _frame(_trend_with_waves(n=50))
    r = client.post("/api/strategies/ema_rsi_scalper_1m/chart-run", json={"symbol": "NIFTY", "timeframe": "5min", "candles": _bars(df)})
    assert r.status_code == 200
    body = r.json()
    assert body["compatible"] is False and "1m" in body["reason"] and body["trades"] == []


def test_chart_run_resamples_a_coarser_strategy_from_finer_candles():
    df = _frame(_trend_with_waves(n=2100), minutes=1)
    r = client.post("/api/strategies/macd_ema_trend_5m/chart-run", json={"symbol": "NIFTY", "timeframe": "1min", "candles": _bars(df)})
    assert r.status_code == 200 and r.json()["compatible"] is True


def test_chart_run_unknown_strategy_is_404():
    df = _frame([100.0] * 10)
    assert client.post("/api/strategies/nope/chart-run", json={"symbol": "X", "timeframe": "5min", "candles": _bars(df)}).status_code == 404


def test_chart_run_says_how_many_candles_are_missing():
    df = _frame(_trend_with_waves(n=60))
    body = client.post("/api/strategies/macd_ema_trend_5m/chart-run", json={"symbol": "NIFTY", "timeframe": "5min", "candles": _bars(df)}).json()
    assert body["compatible"] is True and body["last_signal"] is None and body["trades"] == []
    assert "205 x 5min (have 60)" in body["reason"]
