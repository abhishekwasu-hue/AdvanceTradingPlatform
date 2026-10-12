"""U6 BK1 (docs/specs/ATP_BREAKOUT_SCREENERS_ADDENDUM.md): breakout screens B1 / B2 / B4 / B7 / B13 / B14, the quality
overlay (grade A / B / C with "why it matched", follow-up status) and the style / timeframe selector.

  params.py     every threshold: default, doc and a one-line research note (docs/research/breakouts.md)
  style.py      Intraday / Swing styles, signal timeframe -> context timeframes, closed-candle alignment
  screens.py    the screens as causal per-bar series, one signal per leg
  quality.py    the overlay and `run_breakout` (one screen over one symbol's closed bars)
  templates.py  pre-built templates, the builder block families, a template's ScreenQL text
  gate.py       the `screener_breakouts` operator flag (off by default) at the API layer

Pure functions; research only - nothing here places, changes or blocks an order (PAPER-only beta; exits never blocked).
ScreenQL functions over the same series: RangeBreakout, SwingBreakout, SwingBreakType, NRBreakout, NewHighBreakout,
RangeExpansion, VolumeBreakout, RelVolume, ATRPercentile, DowTrend (app/screener/registry.py).
"""
from app.screener.breakouts.params import DEFAULTS, settings, validate
from app.screener.breakouts.quality import DISCLAIMER, BreakoutRun, run_breakout
from app.screener.breakouts.screens import SCREENS, evaluate_screen
from app.screener.breakouts.style import STYLES, context_timeframes, style_of

__all__ = ["DEFAULTS", "settings", "validate", "run_breakout", "BreakoutRun", "DISCLAIMER", "SCREENS", "evaluate_screen", "STYLES",
           "context_timeframes", "style_of"]
