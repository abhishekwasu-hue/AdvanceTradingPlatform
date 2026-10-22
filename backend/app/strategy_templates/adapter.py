"""Strategy templates: the adapter to BaseStrategy - a template as a strategy the backtest / worker can call.

`analyze()` READS the recipe's signal series at the decision bar; it never re-derives state (owner order R3). Inside a
backtest run (the engines' prefix cache) the series is computed ONCE on the whole frame and each WindowCursor prefix
reads its last bar from it, so a run costs one series, not one per bar (the square of the bar count). Outside a run
(live / PAPER), the series of the bars given is computed (one call per closed bar).

The adapter never calls BaseStrategy.build_signal (section 5: it forces targets of 1.5 / 2.5 R and a 1.2 R:R refusal
- rules the user never set). It builds the Signal directly: entry = the trigger close (a reference; the fill is the
deployment's), stop_loss = the template's invalidation, no targets, risk_reward None. Targets and exits are the user's
deployment settings (the next PRs: stop_source / target_rule and the TEMPLATE_EXIT plumbing, see
docs/design/STRATEGY_TEMPLATES_IMPL.md). `determine_exit_price` already accepts a missing target (R4), so both backtest
engines run a template signal that carries only its stop.

Step 2 wiring: a template strategy id (`app.strategy_templates.ids`, "template:<id>:<version>:...") resolves to this
adapter in `app.custom_strategies.resolver` (behind the flag `strategy_templates`), so the deployment and backtest routes
and the worker run it; the worker fetches `history_days` of candles (R6: the recipe's needs x the safety factor). The
provenance (template id + version, settings hash, setup instance) is on `Signal.provenance`, never in the reasons a user
reads (TPL-Q2).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from app.core.enums import SignalDirection, StrategyCategory, grade_from_score
from app.core.models import Signal
from app.indicators.prefix_cache import whole_frame
from app.screener.nodes import TF_MINUTES
from app.strategy_engine.base import BaseStrategy
from app.strategy_templates import engine, ids, library
from app.strategy_templates.compile import Settings, TemplateError, compile_recipe
from app.strategy_templates.schema import Template

# ScreenQL timeframe -> the backtest engines' / strategies' timeframe label (app/backtest/windows.bar_length)
ENGINE_LABEL: Dict[str, str] = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1d": "day"}
SESSION_MINUTES = 375           # one NSE session (09:15-15:30): intraday bars per session = this / the bar's minutes
DAYS_PER_SESSION = 7 / 5        # calendar days per trading session (weekends), before the holiday margin
HOLIDAY_MARGIN_DAYS = 7
MAX_HISTORY_DAYS = 400          # never more than the daily path's own lookback (app/market_data/service.py)
# A fixed score: a template signal is not graded (no quality claim); 70 is the grade VALID, as the Strategy Builder's
# own signals (OPEN_QUESTIONS TPL-Q1).
TEMPLATE_SCORE = 70
_NO_SIGNAL_TEXT = {engine.REASON_WARMUP: "Not enough history for this template yet",
                   engine.REASON_CONFLICT: "Both sides triggered on this bar (conflict): no signal",
                   engine.REASON_GEOMETRY: "Invalid geometry: the invalidation is not on the losing side of the close",
                   engine.REASON_NO_LEVEL: "No invalidation level on this bar"}


class TemplateStrategy(BaseStrategy):
    category = StrategyCategory.INDICATOR_BASED

    def __init__(self, template: Template, settings: Settings) -> None:
        if settings.timeframe not in ENGINE_LABEL:
            raise ValueError(f"timeframe {settings.timeframe} is not available to the backtest / worker (scans only)")
        self.template, self.settings = template, settings
        self.recipe = compile_recipe(template, settings)
        try:
            self.id = ids.encode(template, settings)          # the same id the deployment / order / trade records carry
        except TemplateError:
            self.id = f"{ids.PREFIX}{template.id}:{template.version}"
        self.name = template.name
        self.description = template.idea
        self.label = ENGINE_LABEL[settings.timeframe]
        self.timeframes = [self.label]
        self._cache: Optional[Tuple[pd.DataFrame, engine.SignalSeries]] = None
        self.computations = 0                                  # how many whole series were computed (R3 test)
        super().__init__()

    def min_history(self) -> Dict[str, int]:
        return {self.label: self.recipe.min_bars}

    def history_bars(self) -> int:
        """R6: the signal-timeframe bars a deployment fetches (the recipe's needs on every timeframe it reads, x the
        safety factor - the parity harness's own `fetch_bars`)."""
        from app.strategy_templates.parity import fetch_bars
        return fetch_bars(self.recipe)

    def history_days(self) -> int:
        """R6: the calendar days of candles that hold `history_bars` bars of the signal timeframe (sessions of
        SESSION_MINUTES, weekends and a holiday margin), capped at MAX_HISTORY_DAYS. The worker fetches this instead of its
        short intraday default (5 days), so swings and breakouts have their warm-up."""
        bars = self.history_bars()
        minutes = TF_MINUTES[self.settings.timeframe]
        sessions = bars if minutes >= SESSION_MINUTES else math.ceil(bars * minutes / SESSION_MINUTES)
        return min(MAX_HISTORY_DAYS, math.ceil(sessions * DAYS_PER_SESSION) + HOLIDAY_MARGIN_DAYS)

    def series(self, df: pd.DataFrame) -> Tuple[engine.SignalSeries, int]:
        """The signal series and the position of `df`'s last bar in it."""
        whole = whole_frame(df)
        if whole is not None:
            frame, length = whole
            if self._cache is None or self._cache[0] is not frame:
                self._cache = (frame, self._compute(frame))
            return self._cache[1], length - 1
        return self._compute(df), len(df) - 1

    def _compute(self, df: pd.DataFrame) -> engine.SignalSeries:
        self.computations += 1
        return engine.compute(self.recipe, {self.settings.timeframe: df})

    def analyze(self, data: Dict[str, pd.DataFrame], symbol: str) -> Signal:
        df = data.get(self.label)
        if df is None or len(df) == 0:
            return self.no_trade(symbol, pd.Timestamp.now(tz="UTC"), [_NO_SIGNAL_TEXT[engine.REASON_WARMUP]])
        timestamp = df.index[-1]
        if len(df) < self.recipe.min_bars:
            return self.no_trade(symbol, timestamp, [_NO_SIGNAL_TEXT[engine.REASON_WARMUP]])
        series, t = self.series(df)
        d = int(series.direction[t])
        if d == 0:
            why = str(series.reason[t])
            return self.no_trade(symbol, timestamp, [_NO_SIGNAL_TEXT.get(why, "No template signal on this bar")])
        side = "long" if d > 0 else "short"
        reasons = [library.signal_text(self.template, self.settings, side, self.recipe.context_timeframe)]
        if series.evidence.get(t):
            reasons.append("Evidence: " + ", ".join(series.evidence[t]))
        # TPL-Q2: the provenance stays off the reasons (users read those); the server keeps it on the signal
        provenance = {"template": self.template.id, "version": str(self.template.version), "settings": self.recipe.params_hash,
                      "setup": str(series.instance_id(t)), "strategy_id": self.id}
        return Signal(provenance=provenance, symbol=symbol, strategy_id=self.id, strategy_name=self.name,
                      direction=SignalDirection.LONG if d > 0 else SignalDirection.SHORT, timestamp=timestamp,
                      entry=float(df["close"].iloc[-1]), stop_loss=float(series.invalidation[t]), target1=None, target2=None,
                      risk_reward=None, score=TEMPLATE_SCORE, grade=grade_from_score(TEMPLATE_SCORE), reasons=reasons,
                      timeframe_combo=self.label)

    def exit_signal(self, data: Dict[str, pd.DataFrame], direction: Any) -> bool:
        """The template's exit signal on the last closed bar for an open idea of `direction` (R5 / S10: the engines and
        the position monitor call this in the next PR; it never blocks or delays any other exit)."""
        df = data.get(self.label)
        if df is None or len(df) == 0:
            return False
        series, t = self.series(df)
        is_long = direction in (SignalDirection.LONG, "LONG", "long")
        return bool(series.exit_long[t] if is_long else series.exit_short[t])


__all__ = ["TemplateStrategy", "ENGINE_LABEL", "TEMPLATE_SCORE", "MAX_HISTORY_DAYS"]
