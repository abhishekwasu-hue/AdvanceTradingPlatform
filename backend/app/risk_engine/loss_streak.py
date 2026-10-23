"""CP6-15: when the losses-in-a-row pause ends.

`RiskConfig.max_consecutive_losses` pauses new entries after that many losing trades in a row. Before CP6-15 the pause
never ended: the live / PAPER counter read the last closed trades of all time and the backtest counters reset only on a
win, and with no new trade there was no win to break the streak. The trader now picks when the pause ends
(`RiskConfig.loss_streak_pause`):

* ``next_session`` (the default): the streak counts only the losses of the current Indian trading session. Live and
  PAPER count closed trades whose exit is at or after `trading_day_start()` (00:00 IST, the same boundary as the daily
  loss and trades-per-day counters); a backtest resets the counter at the IST day roll, where it resets those counters.
* ``until_resume``: the streak counts the losses since the trader's last "Resume after losses in a row"
  (`risk_settings.loss_streak_resumed_at`, stamped by `POST /api/risk-settings/loss-streak/resume`). Nobody can resume
  a backtest, so there the pause holds to the end of the run, and the result says so (`BacktestResult.loss_streak`).

A losing trade is one whose P&L is zero or below, as before. The pause refuses new entries only: exits never pass
through `RiskManager.validate_and_size`, so they are never blocked (ADR-0004).

`BacktestLossStreak` is the one piece of logic both backtest engines call, so they behave identically; the live counter
(`app/trading/persistence.build_trading_day_state`) applies the same rule to the stored trades.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Literal, Optional

NEXT_SESSION = "next_session"
UNTIL_RESUME = "until_resume"
LossStreakPause = Literal["next_session", "until_resume"]
PAUSE_MODES = (NEXT_SESSION, UNTIL_RESUME)

# The words the Risk limits page and the answers use for each choice.
PAUSE_LABELS = {NEXT_SESSION: "Pause until the next session", UNTIL_RESUME: "Pause until I resume"}


def in_a_row(count: int) -> str:
    """ "1 loss in a row" / "3 losses in a row" - the words the notes use."""
    return f"{count} {'loss' if count == 1 else 'losses'} in a row"


def is_loss(pnl: Optional[float]) -> bool:
    """A closed trade that did not make money (zero or below) extends the streak; a winning one ends it."""
    return pnl is not None and pnl <= 0


def streak_of(pnls_newest_first: Iterable[Optional[float]]) -> int:
    """The losses in a row at the newest end of `pnls_newest_first` (a trade with no P&L yet ends the count, as before)."""
    count = 0
    for pnl in pnls_newest_first:
        if not is_loss(pnl):
            break
        count += 1
    return count


def counted_since(mode: str, *, session_start: datetime, resumed_at: Optional[datetime]) -> Optional[datetime]:
    """The earliest exit time that still counts towards the streak: the session's start, or the last resume (None =
    no resume yet, so every closed trade counts)."""
    if mode == UNTIL_RESUME:
        return resumed_at
    return session_start


def is_paused(losses_in_a_row: int, limit: int) -> bool:
    """The same test as `RiskManager.validate_and_size`."""
    return losses_in_a_row >= limit


class BacktestLossStreak:
    """The losses-in-a-row counter of one backtest run (`app/backtest/engine.py`, `app/backtest/options_engine.py`).

    The engine calls `new_session()` at every IST day roll and `closed()` for every closed trade; `report()` is what the
    result says about the pause (None when the limit was never reached)."""

    def __init__(self, mode: str, limit: int) -> None:
        self.mode = mode if mode in PAUSE_MODES else NEXT_SESSION
        self.limit = int(limit)
        self.count = 0
        self.pauses = 0
        self.first_paused_at: Any = None

    def new_session(self) -> int:
        """The IST day rolled: the next-session choice starts the count again. Returns the count to use."""
        if self.mode == NEXT_SESSION:
            self.count = 0
        return self.count

    def closed(self, pnl: Optional[float], when: Any) -> int:
        """One trade closed at `when`. Returns the count to use."""
        self.count = self.count + 1 if is_loss(pnl) else 0
        if self.count == self.limit:
            self.pauses += 1
            if self.first_paused_at is None:
                self.first_paused_at = when
        return self.count

    def report(self) -> Optional[Dict[str, Any]]:
        if not self.pauses:
            return None
        when = self.first_paused_at
        at = when.isoformat() if hasattr(when, "isoformat") else (str(when) if when is not None else None)
        if self.mode == UNTIL_RESUME:
            note = (f"After {in_a_row(self.limit)}, new entries paused and stayed paused to the end of this test: your setting "
                    "is \"Pause until I resume\", and nobody can resume a test. The test took no new trade after that point.")
        else:
            note = (f"New entries paused after {in_a_row(self.limit)} {self.pauses} "
                    f"{'time' if self.pauses == 1 else 'times'}; each pause ended at the next session.")
        return {"mode": self.mode, "limit": self.limit, "pauses": self.pauses, "first_paused_at": at,
                "held_to_end": self.mode == UNTIL_RESUME, "note": note}


__all__ = ["NEXT_SESSION", "UNTIL_RESUME", "LossStreakPause", "PAUSE_MODES", "PAUSE_LABELS", "in_a_row", "is_loss", "streak_of", "counted_since",
           "is_paused", "BacktestLossStreak"]
