import numpy as np

from app.indicators.directional import adx
from app.indicators.momentum import rsi
from app.indicators.trend import ema, sma
from app.indicators.volatility import atr, supertrend
from tests.utils import decline_then_rally, make_series


def test_ema_tracks_close_and_warms_up():
    df = make_series(decline_then_rally())
    result = ema(df["close"], 9)
    assert result.iloc[:8].isna().all()
    assert not result.iloc[-1:].isna().any()
    assert abs(result.iloc[-1] - df["close"].iloc[-1]) < df["close"].iloc[-1] * 0.2


def test_sma_matches_manual_average():
    df = make_series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    result = sma(df["close"], 3)
    assert result.iloc[2] == 2.0
    assert result.iloc[-1] == 9.0


def test_rsi_bounded_between_0_and_100():
    df = make_series(decline_then_rally())
    result = rsi(df["close"], 14)
    valid = result.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()
    # a strong sustained rally should push RSI well above the midline
    assert valid.iloc[-1] > 55


def test_atr_is_positive_after_warmup():
    df = make_series(decline_then_rally())
    result = atr(df, 14)
    valid = result.dropna()
    assert (valid > 0).all()


def test_supertrend_flips_direction_on_v_shaped_move():
    prices = decline_then_rally(decline_len=30, rally_len=30)
    df = make_series(prices)
    result = supertrend(df, period=10, multiplier=3.0)
    trends = result["trend"].dropna().to_numpy()
    assert -1 in trends and 1 in trends


def test_adx_rises_during_strong_directional_move():
    df = make_series(decline_then_rally(decline_len=10, rally_len=40))
    result = adx(df, 14)
    valid = result["adx"].dropna()
    assert valid.iloc[-1] > valid.iloc[0]
    assert not np.isnan(valid.iloc[-1])
