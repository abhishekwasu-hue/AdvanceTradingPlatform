"""REV-B5 (owner 30 Oct): RSI with no gains and no losses over the period is the neutral 50, not 100."""
import numpy as np
import pandas as pd

from app.indicators.momentum import rsi


def _closes(values) -> pd.Series:
    idx = pd.date_range("2026-10-26 09:15", periods=len(values), freq="1min", tz="Asia/Kolkata")
    return pd.Series(values, index=idx, dtype=float)


def test_a_flat_period_is_neutral():
    out = rsi(_closes([22600.0] * 30), 14).dropna()
    assert len(out) and (out == 50.0).all()


def test_only_gains_is_still_100_and_only_losses_0():
    assert rsi(_closes(np.arange(30) + 22600.0), 14).dropna().iloc[-1] == 100.0
    assert rsi(_closes(22600.0 - np.arange(30)), 14).dropna().iloc[-1] == 0.0


def test_a_flat_open_then_moves_reads_the_moves():
    """A session that opens on unchanged prints (an index before the first trade), then trades: neutral while flat,
    then the usual reading - never a false 100 on the flat stretch."""
    values = [22600.0] * 16 + [22600.0 + 5 * ((-1) ** k) * (k % 3) for k in range(40)]
    out = rsi(_closes(values), 14)
    assert out.iloc[14] == 50.0 and out.iloc[15] == 50.0
    later = out.iloc[20:].dropna()
    assert ((later > 0) & (later < 100)).all()


def test_warm_up_stays_empty():
    assert rsi(_closes([22600.0] * 30), 14).iloc[:13].isna().all()
