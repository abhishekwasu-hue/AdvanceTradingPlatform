"""N7 C2b (docs/design/C2_MODELS.md PR 2): the `next_open` fill, `spread_half` / `volume_share` slippage and the
`fixed_ms` latency (flag `backtest_models` at the API, off by default).

- next_open never fills on the signal bar: it fills at the next bar's OPEN, and a gap through the stop there exits at
  that open;
- a latency that crosses bar boundaries moves the fill to the bar trading when the order arrives;
- volume_share grows with the order's share of the signal bar's volume; spread_half is half the spread;
- no look-ahead: changing the fill bar's high / low / close, or cutting the bars anywhere, never changes an entry
  already decided (only the fill bar's open is read; impact uses the signal bar's volume);
- the defaults stay byte-identical (tests/test_c2_models.py golden); a non-default set enters the fingerprint;
- the API refuses the new models while the flag is off, and a bad parameter with 422."""
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

from app.backtest import engine
from app.backtest.models import FLAG, ModelError, ModelSet, SpreadHalfSlippage, VolumeShareSlippage
from app.backtest.repro import result_hash
from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.strategy_engine.base import BaseStrategy
from tests.test_auth_api import client
from tests.test_deployments_api import _auth
from tests.test_exit_rules_and_backtest_depth import _candles

RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5, max_trades_per_day=100, max_consecutive_losses=100, max_daily_loss_pct=50)


class _At(BaseStrategy):
    """LONG at the close of the bars whose start is in `at`: stop 5 below, targets 7.5 / 12.5 above."""
    id = "c2b_stub"
    name = "C2b stub"
    timeframes = ["1min"]

    def __init__(self, at: List[pd.Timestamp]) -> None:
        super().__init__()
        self.at = set(at)

    def min_history(self) -> Dict[str, int]:
        return {"1min": 1}

    def analyze(self, data, symbol):
        frame = data["1min"]
        last = frame.index[-1]
        if last not in self.at:
            return self.no_trade(symbol, last.to_pydatetime(), ["no"])
        close = float(frame["close"].iloc[-1])
        return self.build_signal(symbol, last.to_pydatetime(), SignalDirection.LONG, close, close - 5.0, 80, ["test"], target_rr=(1.5, 2.5))


def _bars(opens, highs, lows, closes, volume=1000.0):
    idx = pd.date_range("2025-01-06 09:15", periods=len(opens), freq="1min")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}, index=idx)


def _flat(n=12, price=100.0):
    p = np.full(n, price)
    return _bars(p.copy(), p + 0.5, p - 0.5, p.copy())


def _run(df, at_i, models=None):
    return engine.run_backtest(_At([df.index[i] for i in at_i]), df, "NIFTY 50", "1min", RISK, models=models)


NO_SLIP = {"slippage": {"pct": 0.0}}


# --- the models ------------------------------------------------------------------------------------------------------------------

def test_the_flag_is_off_by_default_and_new_models_need_it():
    assert FLAG in DEFAULT_OFF_FLAGS
    assert ModelSet().flagged() == {} and ModelSet(params={"slippage": {"pct": 0.1}}).flagged() == {}
    assert ModelSet(fill="next_open", slippage="volume_share", latency="fixed_ms").flagged() == {
        "fill": "next_open", "slippage": "volume_share", "latency": "fixed_ms"}


def test_next_open_fills_at_the_next_bar_s_open_never_on_the_signal_bar():
    df = _flat()
    df.iloc[4, df.columns.get_loc("open")] = 101.0                  # the bar after the signal opens higher
    touch = _run(df, [3], ModelSet(params=NO_SLIP))
    nxt = _run(df, [3], ModelSet(fill="next_open", params=NO_SLIP))
    assert touch.trades[0].entry_time == df.index[3] and touch.trades[0].entry_price == 100.0
    assert nxt.trades[0].entry_time == df.index[4] and nxt.trades[0].entry_price == 101.0
    assert nxt.trades[0].expected_price == 100.0                    # what the signal expected vs what filled


def test_a_gap_through_the_stop_at_the_fill_bar_exits_at_that_open():
    df = _flat()
    df.iloc[5, df.columns.get_loc("open")] = 90.0                     # far below the stop (95) set from the signal close
    df.iloc[5, df.columns.get_loc("low")] = 89.5
    df.iloc[5, df.columns.get_loc("high")] = 90.5
    res = _run(df, [4], ModelSet(fill="next_open", params=NO_SLIP))
    [t] = res.trades
    assert t.entry_price == 90.0 and t.exit_price == 90.0 and t.entry_time == t.exit_time == df.index[5]


def test_a_latency_across_bars_moves_the_fill_to_the_bar_trading_then():
    df = _flat()
    df["open"] = 100.0 + np.arange(len(df))                          # each bar's open names it
    one = _run(df, [3], ModelSet(latency="fixed_ms", params={**NO_SLIP, "latency": {"ms": 250}}))
    two = _run(df, [3], ModelSet(latency="fixed_ms", params={**NO_SLIP, "latency": {"ms": 90_000}}))   # 1.5 bars
    assert one.trades[0].entry_time == df.index[4] and one.trades[0].entry_price == 104.0
    assert two.trades[0].entry_time == df.index[5] and two.trades[0].entry_price == 105.0
    late = _run(df, [len(df) - 1], ModelSet(latency="fixed_ms", params={**NO_SLIP, "latency": {"ms": 250}}))
    assert late.trades == []                                         # no bar left to fill it: no entry


def test_volume_share_grows_with_the_order_and_spread_half_is_half_the_spread():
    m = VolumeShareSlippage(base_pct=0.02, impact_pct=0.1, max_pct=1.0)
    assert m.pct_for(10, 1000) == pytest.approx(0.02 + 0.1 * 1.0) and m.pct_for(50, 1000) == pytest.approx(0.52)
    assert m.pct_for(10_000, 1000) == 1.0 and m.pct_for(1, 0) == 1.0 and m.pct_for(None, None) == 0.02
    assert m.apply(100.0, SignalDirection.LONG, qty=10, volume=1000) == pytest.approx(100.12)
    assert m.apply(100.0, SignalDirection.SHORT, qty=10, volume=1000) == pytest.approx(99.88)
    assert SpreadHalfSlippage(spread_pct=0.1).apply(100.0, SignalDirection.LONG) == pytest.approx(100.05)
    for bad in ({"slippage": "volume_share", "params": {"slippage": {"max_pct": 0.01}}},
                {"slippage": "spread_half", "params": {"slippage": {"spread_pct": -1}}},
                {"latency": "fixed_ms", "params": {"latency": {"ms": 0}}}):
        with pytest.raises((ModelError, TypeError)):
            ModelSet(**bad).built()
    thin, thick = _flat(), _flat()
    thin["volume"], thick["volume"] = 50.0, 1e9
    small = _run(thick, [3], ModelSet(slippage="volume_share")).trades[0].entry_price
    big = _run(thin, [3], ModelSet(slippage="volume_share")).trades[0].entry_price
    assert big > small > 100.0


# --- no look-ahead ---------------------------------------------------------------------------------------------------------------

def test_the_fill_bar_s_range_and_later_bars_never_change_an_entry():
    df = _flat(20)
    models = ModelSet(fill="next_open", slippage="volume_share", latency="fixed_ms", params={"latency": {"ms": 250}})
    base = _run(df, [3], models).trades[0]
    shaken = df.copy()
    shaken.iloc[4, shaken.columns.get_loc("high")] = 150.0          # the fill bar's high / close: never read for the entry
    shaken.iloc[4, shaken.columns.get_loc("close")] = 120.0
    shaken.iloc[4, shaken.columns.get_loc("volume")] = 1.0           # nor its volume (impact uses the signal bar's)
    moved = _run(shaken, [3], models).trades[0]
    assert (moved.entry_time, moved.entry_price, moved.quantity) == (base.entry_time, base.entry_price, base.quantity)


@pytest.mark.parametrize("models", [ModelSet(fill="next_open"), ModelSet(latency="fixed_ms", params={"latency": {"ms": 61_000}}),
                                    ModelSet(fill="next_open", slippage="volume_share"), ModelSet(slippage="spread_half")])
def test_a_run_on_bars_cut_anywhere_agrees_with_the_full_run_up_to_the_cut(models):
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 3.0, 120))
    open_ = close + rng.normal(0, 1.5, 120)
    df = _bars(open_, np.maximum(open_, close) + 1.0, np.minimum(open_, close) - 1.0, close, volume=rng.integers(100, 5000, 120).astype(float))
    signals = list(range(5, 115, 7))
    full = _run(df, signals, models).trades
    assert len(full) > 5
    for cut in (30, 61, 90):
        part = _run(df.iloc[:cut], [i for i in signals if i < cut], models).trades
        done = [t for t in full if t.exit_time < df.index[cut - 1]]
        assert [(t.entry_time, t.entry_price, t.exit_time, t.exit_price) for t in part[:len(done)]] == \
               [(t.entry_time, t.entry_price, t.exit_time, t.exit_price) for t in done]


# --- the fingerprint and the API ------------------------------------------------------------------------------------------------

def test_the_choice_is_part_of_the_result():
    df = _flat(30)
    a = _run(df, [3, 12], ModelSet(fill="next_open"))
    b = _run(df, [3, 12], ModelSet(fill="next_open"))
    assert result_hash(a) == result_hash(b)
    assert ModelSet(fill="next_open").describe() != ModelSet().describe()


def _flag_on(tenant_id):
    from tests.test_n1_e1_execution_core import _flag
    _flag(FLAG, tenant_id)


def test_the_api_needs_the_flag_and_refuses_bad_parameters():
    headers = _auth("n7c2b-api@example.com")
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "nifty", "base_timeframe": "1min", "candles": _candles(), "data_source": "sample"}
    off = client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"fill": "next_open"}})
    assert off.status_code == 403 and FLAG in off.text
    assert client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"params": {"slippage": {"pct": 0.1}}}}).status_code == 200
    me = client.get("/api/auth/me", headers=headers).json()
    _flag_on(me["tenant_id"])
    on = client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"fill": "next_open", "latency": "fixed_ms"}})
    assert on.status_code == 200, on.text
    plain = client.post("/api/backtest", headers=headers, json=body).json()["reproducibility"]
    assert on.json()["reproducibility"]["config_hash"] != plain["config_hash"]
    bad = client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"latency": "fixed_ms", "params": {"latency": {"ms": -5}}}})
    assert bad.status_code == 422 and "ms" in bad.text
    walk = client.post("/api/backtest/monte-carlo?runs=100", headers=_auth("n7c2b-mc@example.com"),
                       json={**body, "execution_models": {"slippage": "volume_share"}})
    assert walk.status_code == 403
