"""SCR-AUDIT fix (M93 audit, Legends group): Darvas box breakout, Weinstein stage 2 breakout and Livermore pivotal breakout.

The audit found all three passing their volume clause on an index: daily index bars from the NSE files carry volume 0, so
`volume >= vol_mult * SMA(volume, n)[1]` read `0 >= 0` = true and the index was listed (the cards say "on volume above /
well above its recent average"). The owner's M93-Q3 rule: a 0 / missing volume is no volume data - the screen is
"not checked: volume not available", never a match from `0 >= 0` and never a silent "did not match". The Weinstein
screen also read the week before last after a Friday close (M93-Q1: a week closes at its last session's close).

Each screen here, on synthetic bars (own numbers, no real symbol or price), on every path the audit harness reads:
the live answer (`run_screen`, row and panel paths) and the full causal series (`evaluate`, what screen history and the
audit's hit counts read):
  * positive: a stock breakout on volume above the card's multiple matches;
  * negative: the same breakout on ordinary volume does not match (reason None: checked, did not qualify);
  * negative: the same bars on an index with volume 0 never match on ANY bar and the last bar is "not checked".
"""
import numpy as np
import pandas as pd
import pytest

from app.screener import compile_screen, reasons
from app.screener import legends as lg
from app.screener.runtime import SymbolData, evaluate, resample, run_screen

DARVAS, WEINSTEIN, LIVERMORE = ("legends.darvas_box_breakout", "legends.weinstein_stage2_breakout",
                                "legends.livermore_pivotal_breakout")


def _card(card_id):
    card = lg.load().card(card_id)
    ast, validated = compile_screen(card.source, base_tf=card.base_tf, params=card.defaults())
    assert validated.ok, validated.problems
    return ast, validated, card.defaults(), card.base_tf


def _days(n):
    """n weekday bars, each stamped at its session's 00:00 IST (as the store's daily bars)."""
    return pd.bdate_range("2025-01-06", periods=n, tz="UTC") - pd.Timedelta(hours=5, minutes=30)


def _frame(close, volume, index):
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": c - 0.3, "high": c + 0.5, "low": c - 0.5, "close": c,
                         "volume": np.asarray(volume, dtype=float)}, index=index)


def _darvas_daily(last_volume):
    """A rise, then a 25-bar box at most 4% deep near the high, then a close out of its top to a new period high."""
    close = list(np.linspace(80.0, 128.0, 270)) + [126.0 + (i % 5) for i in range(25)] + [133.0]
    volume = [2000.0] * (len(close) - 1) + [last_volume]
    return _frame(close, volume, _days(len(close)))


def _livermore_daily(last_volume):
    """A year-long rise, then a 60-bar sideways base about 5% deep, then a close above the base and the year's high."""
    close = list(np.linspace(60.0, 95.0, 250)) + [93.0 + (i % 4) * 0.8 for i in range(60)] + [99.0]
    volume = [5000.0] * (len(close) - 1) + [last_volume]
    return _frame(close, volume, _days(len(close)))


def _weinstein_daily(last_daily_volume):
    """Daily bars, five a week: 30 weeks falling, 40 weeks flat (the base), then one week closing above the base high."""
    weekly_close = list(np.linspace(70.0, 50.0, 30)) + [50.0 + (w % 3) * 0.4 for w in range(40)] + [55.0]
    close = np.repeat(weekly_close, 5)
    volume = [300.0] * (len(close) - 5) + [last_daily_volume] * 5
    return _frame(close, volume, _days(len(close)))


def _weekly(daily):
    return resample(daily, "1d", "1w", keep_forming=False, exchange="NSE")


# positive volume (above the card's multiple), ordinary volume (below it)
SHAPES = {
    DARVAS: (lambda: _darvas_daily(4000.0), lambda: _darvas_daily(2100.0)),
    LIVERMORE: (lambda: _livermore_daily(9000.0), lambda: _livermore_daily(5200.0)),
    WEINSTEIN: (lambda: _weekly(_weinstein_daily(900.0)), lambda: _weekly(_weinstein_daily(330.0))),
}


def _live(card_id, symbol, frame, vectorized):
    ast, validated, params, tf = _card(card_id)
    [m] = run_screen(ast, validated, [SymbolData(symbol, {tf: frame}, exchange="NSE")], base_tf=tf, params=params,
                     vectorized=vectorized)
    return m


def _series(card_id, symbol, frame):
    ast, _, params, tf = _card(card_id)
    s = evaluate(ast, SymbolData(symbol, {tf: frame.copy()}, exchange="NSE"), base_tf=tf, params=params)
    return pd.Series(s, index=frame.index) if not isinstance(s, pd.Series) else s.reindex(frame.index)


def _hits(series):
    return [t for t, v in series.items() if v is not None and not (isinstance(v, float) and np.isnan(v)) and bool(v)]


@pytest.mark.parametrize("card_id", [DARVAS, WEINSTEIN, LIVERMORE])
@pytest.mark.parametrize("vectorized", [False, True])
def test_a_stock_breakout_on_volume_matches(card_id, vectorized):
    frame = SHAPES[card_id][0]()
    m = _live(card_id, "STOCKA", frame, vectorized)
    assert m.matched and m.reason is None and m.note is None, m
    assert _hits(_series(card_id, "STOCKA", frame)) == [frame.index[-1]]          # the breakout bar only


@pytest.mark.parametrize("card_id", [DARVAS, WEINSTEIN, LIVERMORE])
@pytest.mark.parametrize("vectorized", [False, True])
def test_the_same_breakout_on_ordinary_volume_does_not_match(card_id, vectorized):
    frame = SHAPES[card_id][1]()
    m = _live(card_id, "STOCKA", frame, vectorized)
    assert not m.matched and m.reason is None, m                                   # checked: did not qualify
    assert _hits(_series(card_id, "STOCKA", frame)) == []


@pytest.mark.parametrize("card_id", [DARVAS, WEINSTEIN, LIVERMORE])
@pytest.mark.parametrize("index_name", ["NIFTY50", "NIFTYBANK", "NIFTY 50"])
def test_an_index_with_volume_zero_never_matches_on_any_bar_and_says_volume_not_available(card_id, index_name):
    """The audit's failing shape: the positive breakout's own prices, on an index whose every volume is 0."""
    frame = SHAPES[card_id][0]().assign(volume=0.0)
    if card_id == WEINSTEIN:                     # a weekly bucket of daily zeros: built from the daily bars, as the store does
        frame = _weekly(_weinstein_daily(900.0).assign(volume=0.0))
    for vectorized in (False, True):
        m = _live(card_id, index_name, frame, vectorized)
        assert not m.matched and m.reason is not None, (vectorized, m)
        assert reasons.as_reason(m.reason).code == reasons.NO_VOLUME and "volume not available" in m.reason
    assert _hits(_series(card_id, index_name, frame)) == []                        # never "0 >= 0" on any bar


@pytest.mark.parametrize("card_id", [DARVAS, WEINSTEIN, LIVERMORE])
def test_the_price_part_of_the_screen_still_holds_on_the_index(card_id):
    """Only the volume clause lacks data: the card's price conditions alone match the index's breakout bar, so the
    "not checked" answer is about volume, not a hidden price failure."""
    card = lg.load().card(card_id)
    price_only = " AND ".join(p for p in card.source.split(" AND ") if "volume" not in p)
    params = {k: v for k, v in card.defaults().items() if f"${k}" in price_only}
    ast, validated = compile_screen(price_only, base_tf=card.base_tf, params=params)
    assert validated.ok, validated.problems
    frame = SHAPES[card_id][0]().assign(volume=0.0)
    [m] = run_screen(ast, validated, [SymbolData("NIFTY50", {card.base_tf: frame}, exchange="NSE")], base_tf=card.base_tf,
                     params=params)
    assert m.matched and m.reason is None


def test_weinstein_reads_the_week_that_closed_on_friday():
    """M93-Q1 on the weekly screen: the daily bars end on a Friday; the run after that close reads that week (not the
    week before), and the breakout week is the one listed."""
    daily = _weinstein_daily(900.0)
    assert daily.index[-1].tz_convert("Asia/Kolkata").dayofweek == 4                 # the fixture ends on a Friday
    weekly = _weekly(daily)
    last_week_monday = daily.index[-5]
    assert weekly.index[-1] == last_week_monday and weekly["volume"].iloc[-1] == 5 * 900.0
    m = _live(WEINSTEIN, "STOCKA", weekly, False)
    assert m.matched
