"""REV-1 M2 (owner order 30 Oct): the built-in strategies' signal text and their no-op reward:risk parameter.

- REV-B1: "Why this signal" keeps only reason lines in the signal language (signal_language_v2: no digit, no buy / sell /
  target / stop-loss wording). Every built-in now writes its reasons in structure words, so the guard keeps them. Proved
  on the REAL reason lines: each of the 13 strategies runs on session-shaped bars (app.quality.lookahead.synthetic_bars,
  higher timeframes resampled and closed as the worker feeds them) until it signals.
- REV-B2: the built-ins carry no `min_rr` (it compared two fixed constants of the same strategy and never acted); the
  trades on fixture bars are the same with the old value put back.
- REV-B3: VWAP + Supertrend names its line truthfully: a VWAP where the session has volume, "the average price line (no
  volume on an index)" where it has none.
"""
import copy
import re
from typing import Dict, List

import pandas as pd
import pytest

from app.backtest import engine as underlying_engine
from app.core.enums import SignalDirection
from app.core.models import RiskConfig
from app.core.resampling import resample_ohlc
from app.market_data.freshness import closed_frames
from app.quality.lookahead import synthetic_bars
from app.signals import language as signal_language
from app.strategy_engine.combo_strategies import AVERAGE_LINE, session_has_volume
from app.strategy_engine.registry import registry

SYMBOL = "NIFTY 50"
BARS = {"1min": "1m", "5min": "5m", "day": "1d"}
SESSION_OPEN = pd.Timestamp("2026-01-05 03:45", tz="UTC")          # 09:15 IST: 30 / 60-minute bars start at the open
# (seed, bar) where each built-in first signals on synthetic_bars(SIZE) - found by scanning every bar from the warm-up on;
# the test asserts the strategy signals there, so a change to the bars or the entry logic fails loudly here.
SIZE = {"1min": 3000, "5min": 3000, "day": 600}
FIRST_SIGNAL = {
    "mtf_1m_5m_trend_pullback": (0, 944), "mtf_1m_15m_trend_pullback": (0, 944), "mtf_5m_30m_trend_pullback": (1, 1926),
    "mtf_5m_60m_trend_pullback": (0, 827), "ema_rsi_scalper_1m": (0, 61), "supertrend_adx_scalper_1m": (0, 68),
    "rsi_adx_momentum_5m": (0, 244), "macd_ema_trend_5m": (0, 208), "bb_rsi_reversion_5m": (0, 99),
    "vwap_supertrend_5m": (0, 62), "orb_15m_5m": (0, 94), "swing_ema_pullback_d": (0, 61), "swing_breakout_d": (0, 143),
}
# The removed no-op, at the value each built-in carried before (REV-B2).
OLD_MIN_RR = {sid: (1.5 if sid.startswith("swing_") else 1.2) for sid in FIRST_SIGNAL}
DIGIT = re.compile(r"[0-9]")


def _bars(strategy, seed: int) -> pd.DataFrame:
    tf = strategy.timeframes[0]
    return synthetic_bars(SIZE[tf], tf=BARS[tf], seed=seed)


def _frames(strategy, bars: pd.DataFrame, t: int) -> Dict[str, pd.DataFrame]:
    """What the worker hands the strategy at bar t: the base bars up to t, each higher timeframe resampled from them,
    closed bars only."""
    cut = bars.iloc[: t + 1]
    base = strategy.timeframes[0]
    frames = {tf: cut if tf == base else resample_ohlc(cut, tf, origin=SESSION_OPEN) for tf in strategy.timeframes}
    step = cut.index[-1] - cut.index[-2]
    return closed_frames(frames, (cut.index[-1] + step).to_pydatetime())


def _signal_at(strategy, seed: int, t: int):
    return strategy.analyze(_frames(strategy, _bars(strategy, seed), t), SYMBOL)


def _kept(lines: List[str]) -> List[str]:
    return signal_language.shown_lines(lines, "signal_own", own_symbols=[SYMBOL])


def test_the_table_covers_all_thirteen_built_ins():
    assert sorted(registry.ids()) == sorted(FIRST_SIGNAL)


@pytest.mark.parametrize("sid", sorted(FIRST_SIGNAL))
def test_why_this_signal_keeps_the_real_reason_lines(sid):
    strategy = registry.get(sid)
    signal = _signal_at(strategy, *FIRST_SIGNAL[sid])
    assert signal.direction != SignalDirection.NO_TRADE, signal.reasons
    kept = _kept(signal.reasons)
    assert kept, f"{sid}: no reason line survives the signal language: {signal.reasons}"
    assert kept == signal.reasons, f"{sid}: a reason line is dropped: {set(signal.reasons) - set(kept)}"
    for line in kept:
        assert not DIGIT.search(line), (sid, line)
        assert signal_language.is_clean(line), (sid, line)                    # no buy / sell / target / stop wording
        assert not signal_language.check(line, "signal_own"), (sid, line)     # clean even without the symbol exemption


@pytest.mark.parametrize("sid", sorted(FIRST_SIGNAL))
def test_no_setup_reasons_carry_no_periods_or_prices(sid):
    """The "why no trade" lines of the same strategies, over the bars before their first signal."""
    strategy = registry.get(sid)
    seed, first = FIRST_SIGNAL[sid]
    bars = _bars(strategy, seed)
    for t in range(max(2, first - 60), first):
        for line in strategy.analyze(_frames(strategy, bars, t), SYMBOL).reasons:
            assert not DIGIT.search(line), (sid, t, line)


def _index_bars(seed: int = 0) -> pd.DataFrame:
    bars = synthetic_bars(1500, tf="5m", seed=seed)
    return bars.assign(volume=0.0)


def test_vwap_supertrend_names_the_average_price_line_on_an_index():
    strategy = registry.get("vwap_supertrend_5m")
    bars = _index_bars()
    assert not session_has_volume(bars) and session_has_volume(synthetic_bars(100, tf="5m"))
    signal = next(s for s in (strategy.analyze({"5min": bars.iloc[: t + 1]}, SYMBOL) for t in range(20, len(bars)))
                  if s.direction != SignalDirection.NO_TRADE)
    text = " ".join(signal.reasons)
    assert AVERAGE_LINE in text and "VWAP" not in text, signal.reasons
    assert _kept(signal.reasons) == signal.reasons
    assert "average price line" in strategy.description and "VWAP" in strategy.description


def test_vwap_supertrend_keeps_the_vwap_name_with_volume():
    strategy = registry.get("vwap_supertrend_5m")
    signal = _signal_at(strategy, *FIRST_SIGNAL["vwap_supertrend_5m"])
    assert "VWAP" in " ".join(signal.reasons) and AVERAGE_LINE not in " ".join(signal.reasons)


def test_the_swing_pullback_description_names_the_downtrend_mirror():
    text = registry.get("swing_ema_pullback_d").description
    assert "downtrend" in text and "option" in text and "skipped on shares" in text


# --- REV-B2: the no-op minimum reward:risk ---------------------------------------------------------------------------------

def test_no_built_in_names_a_min_rr():
    for s in registry.list_all():
        assert "min_rr" not in s.params and "min_rr" not in s.info().default_params, s.id


def _with_old_min_rr(strategy):
    old = copy.deepcopy(strategy)
    old.params["min_rr"] = OLD_MIN_RR[strategy.id]
    return old


def _trades(result) -> List[dict]:
    """Every field of every trade except the signal's reason lines (the only thing REV-B1 changed on purpose)."""
    rows = [t.model_dump() if hasattr(t, "model_dump") else dict(vars(t)) for t in result.trades]
    return [{k: v for k, v in row.items() if k != "reasons"} for row in rows]


@pytest.mark.parametrize("sid", sorted(FIRST_SIGNAL))
def test_the_trades_are_the_same_without_the_removed_min_rr(sid):
    strategy = registry.get(sid)
    bars = _bars(strategy, FIRST_SIGNAL[sid][0]).iloc[: FIRST_SIGNAL[sid][1] + 400]
    cfg = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0, max_consecutive_losses=50, max_trades_per_day=50, max_open_positions=50)
    base_tf = strategy.timeframes[0]
    new = underlying_engine.run_backtest(strategy, bars, SYMBOL, base_tf, cfg)
    old = underlying_engine.run_backtest(_with_old_min_rr(strategy), bars, SYMBOL, base_tf, cfg)
    assert new.trades, f"{sid}: no trade on the fixture bars - the comparison would prove nothing"
    assert _trades(new) == _trades(old)
