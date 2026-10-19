"""U6 BK1 - BK4 (docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md): breakout screens B1 / B2 / B4 / B7 / B13 / B14, the
intraday B16 / B17 / B18, BK3's B3 VCP and indicator breakouts B10 / B11 / B12 / B15, BK4's B5 trendline / channel, B6
flag / pennant / triangle, B8 gap and B9 retest, the quality overlay (grade A / B / C with "why it matched", follow-up status) and the style /
timeframe selector.

  params.py     every threshold: default, doc and a one-line research note (docs/research/breakouts.md)
  style.py      Intraday / Swing styles, signal timeframe -> context timeframes, closed-candle alignment
  screens.py    the screens as causal per-bar series, one signal per leg
  intraday.py   BK2: B16 opening range, B17 first hour / previous day / first candle / pre-market, B18 new high / low of day
  vcp.py        BK3: B3 volatility contraction (VCP-style) breakout and its Watchlist (ready, not yet broken)
  indicator_breaks.py  BK3: B10 Donchian, B11 Bollinger / TTM squeeze, B12 moving average, B15 RSI / ADX regime
  lines.py      BK4: B5 trendline / channel and B6 flag / pennant / triangle (the CH3b trendline rule as of every bar)
  gaps.py       BK4: B8 gap breakout with the gap-and-go / gap-hold / gap-fill class
  retest.py     BK4: B9 retest entry after a range / swing / Donchian / trendline breakout
  fitness.py    BK5: screen fitness - forward returns and failed-break rates of a template's past matches, by grade /
                timeframe / volatility regime (informational)
  fitness_job.py  BK5: the nightly fitness pass over the shared lake's daily bars, and the stored rows' reader
  session.py    BK2: the exchange session clock (calendar, half days, special sessions), session VWAP, relative volume by
                time of day (prior sessions only)
  quality.py    the overlay and `run_breakout` (one screen over one symbol's closed bars)
  templates.py  pre-built templates, the builder block families, a template's ScreenQL text
  gate.py       the `screener_breakouts` operator flag (off by default) at the API layer; BK3 / BK4 also need
                `screener_breakouts_bk3` / `screener_breakouts_bk4`

Pure functions; research only - nothing here places, changes or blocks an order (PAPER-only beta; exits never blocked).
ScreenQL functions over the same series: RangeBreakout, SwingBreakout, SwingBreakType, NRBreakout, NewHighBreakout,
RangeExpansion, VolumeBreakout, RelVolume, ATRPercentile, DowTrend; BK2: ORBreakout, DayLevelBreak, DayExtremeBreak,
RelVolumeTOD, SessionVWAP; BK3: VCPBreakout, VCPReady, DonchianBreakout, SqueezeBreakout, MABreakout, RegimeBreakout;
BK4: TrendlineBreakout, PatternBreakout, GapBreakout, GapType, RetestEntry (app/screener/registry.py).
"""
from app.screener.breakouts.params import DEFAULTS, settings, validate
from app.screener.breakouts.quality import DISCLAIMER, BreakoutRun, run_breakout
from app.screener.breakouts.screens import SCREENS, evaluate_screen
from app.screener.breakouts.style import STYLES, context_timeframes, style_of

__all__ = ["DEFAULTS", "settings", "validate", "run_breakout", "BreakoutRun", "DISCLAIMER", "SCREENS", "evaluate_screen", "STYLES",
           "context_timeframes", "style_of"]
