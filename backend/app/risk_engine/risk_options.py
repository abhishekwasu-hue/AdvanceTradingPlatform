"""M99 P7 (owner order M99, flag `risk_options`, OFF by default): the trader's own risk and management options.

Every option is the trader's own switch on Settings > Risk limits (OFF until he switches it on) and every number in it is
his own (no value is pre-filled; reference values in the source study are never defaults). Nothing here is asked on an
exit: stops, targets, square-offs and Exit now always run (ADR-0004). Nothing here is AI (ADR-0006). PAPER and LIVE read
the same code: the entry check (`RiskManager.validate_and_size`) is the one every order path and both backtest engines
call, so a backtest behaves as the worker does.

* Daily loss lock (`daily_loss_lock`, on top of the existing "Daily loss limit" check): once today's loss reaches the
  limit, new entries stay refused for the rest of the Indian trading day even if the P&L recovers. "Reached" is any of:
  today's P&L now (closed + open marks, as the limit reads it), the lowest closed P&L of today (`day_trough`), or the mark
  the worker cycle stored when it saw the limit reached through open marks (`sweep`). It ends at 00:00 IST.
* Size step-down after losses in a row (`loss_step_down`): after `step_down_losses` losses in a row the size is
  `step_down_size_pct` % of the trader's own size (whole lots, rounded down, at least one lot); it never raises a size.
  Full size returns after `step_down_wins` wins in a row (reset "wins") or at the next trading day (reset "next_day").
  A loss is a closed trade with a P&L of zero or below (the losses-in-a-row pause's rule, loss_streak.is_loss).
* Stagnation flag (`stagnation_flag`): an open position whose last N closed bars since its entry stayed within X % of its
  entry price is shown as "not moving". Information only: it never closes, blocks or changes anything.

Storage: the organisation's `risk_settings` row (the M99 P7 columns) and the per-check switches in `limits_switches_json`.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.models import RiskConfig

FLAG = "risk_options"
LOCK_CHECK = "daily_loss_lock"
STEP_CHECK = "loss_step_down"
STAGNATION_CHECK = "stagnation_flag"
CHECK_IDS = frozenset({LOCK_CHECK, STEP_CHECK, STAGNATION_CHECK})
RESET_WINS = "wins"
RESET_NEXT_DAY = "next_day"
RESETS = (RESET_WINS, RESET_NEXT_DAY)
RESET_LABELS = {RESET_WINS: "After wins in a row", RESET_NEXT_DAY: "At the next trading day"}
# The bar sizes the stagnation flag reads (the store's native intraday sizes, app/market_data/intervals.DEFAULT_NATIVE).
BAR_MINUTES = (1, 3, 5, 10, 15, 30, 60)
# The RiskConfig fields of the options (all off = None / False).
OPTION_FIELDS = ("step_down_losses", "step_down_size_pct", "step_down_wins", "step_down_reset", "stagnation_bars",
                 "stagnation_move_pct", "stagnation_bar_minutes")
WHOLE_PCT = 100.0          # a share in % is at most the whole (a fact, not a setting)
# Enough closed trades for any streak the step-down reads (a reset by wins needs only the newest runs).
HISTORY_ROWS = 500

STEP_NEEDS_RESET = "Smaller size after losses in a row: pick when the full size returns."
STEP_NEEDS_WINS = "Smaller size after losses in a row: enter the wins in a row that bring the full size back."
LOCK_NEEDS_LIMIT = "Keep the daily loss lock all day: switch the daily loss limit on too."
STAGNATION_BAR_WORDS = "Flag a position that is not moving: the bar size must be one of " + ", ".join(str(m) for m in BAR_MINUTES) + " minutes."


# --- pure: on / off -----------------------------------------------------------------------------------------------------

def any_on(cfg: RiskConfig) -> bool:
    """Whether any option is set in this config (after the switches)."""
    return bool(cfg.daily_loss_lock) or any(getattr(cfg, k) is not None for k in OPTION_FIELDS)


def without_options(cfg: RiskConfig) -> RiskConfig:
    """The config with every option off - what the engine reads while the `risk_options` flag is OFF. Pure."""
    if not any_on(cfg):
        return cfg
    return cfg.model_copy(update={"daily_loss_lock": False, **{k: None for k in OPTION_FIELDS}})


# --- pure: the size step-down ---------------------------------------------------------------------------------------------

def is_loss(pnl: float | None) -> bool:
    """A closed trade that did not make money (zero or below) - the losses-in-a-row pause's rule."""
    return pnl is not None and pnl <= 0


def is_win(pnl: float | None) -> bool:
    return pnl is not None and pnl > 0


def _runs(pnls_newest_first: Iterable[float | None]) -> list[tuple[str, int]]:
    """The runs of wins / losses at the newest end, newest first ("win" / "loss", length). A trade with no P&L ends it."""
    runs: list[tuple[str, int]] = []
    for pnl in pnls_newest_first:
        if pnl is None:
            break
        kind = "loss" if is_loss(pnl) else "win"
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + 1)
        else:
            runs.append((kind, 1))
    return runs


def stepped_down(pnls_newest_first: Iterable[float | None], losses: int, wins: int | None) -> bool:
    """Whether the size is stepped down now. The rule as a state machine over the closed trades in order: `losses` losses
    in a row step the size down; `wins` wins in a row (None: no reset by wins) bring it back. Read newest first, the
    newest run that decides is a run of at least `losses` losses (down) or of at least `wins` wins (back). Pure."""
    if losses < 1:
        return False
    for kind, length in _runs(pnls_newest_first):
        if kind == "win" and wins is not None and wins >= 1 and length >= wins:
            return False
        if kind == "loss" and length >= losses:
            return True
    return False


def step_ready(cfg: RiskConfig) -> bool:
    """Whether the step-down is fully set: losses, size % and the reset (with its wins when the reset is "wins")."""
    if cfg.step_down_losses is None or cfg.step_down_size_pct is None or cfg.step_down_reset not in RESETS:
        return False
    if not (cfg.step_down_losses >= 1 and 0 < cfg.step_down_size_pct <= WHOLE_PCT):
        return False
    return cfg.step_down_reset != RESET_WINS or (cfg.step_down_wins is not None and cfg.step_down_wins >= 1)


def step_pct(cfg: RiskConfig, closed_newest_first: Sequence[float | None], today_newest_first: Sequence[float | None]) -> float | None:
    """The size now as % of the trader's own size when the step-down holds, else None (full size). "next_day" reads only
    today's closed trades (a new trading day starts at full size); "wins" reads every recent closed trade. Pure."""
    if not step_ready(cfg):
        return None
    if cfg.step_down_reset == RESET_NEXT_DAY:
        down = stepped_down(today_newest_first, int(cfg.step_down_losses or 0), None)
    else:
        down = stepped_down(closed_newest_first, int(cfg.step_down_losses or 0), cfg.step_down_wins)
    pct = cfg.step_down_size_pct
    return min(WHOLE_PCT, float(pct)) if down and pct is not None else None


def lots_text(n: float) -> str:
    n = int(n) if float(n).is_integer() else n
    return f"{n:g} lot" if n == 1 else f"{n:g} lots"


def apply_step(quantity: float, unit: float, pct: float | None) -> tuple[float, str | None]:
    """(the stepped size, the note) - `pct` % of `quantity` in whole lots of `unit`, rounded down, at least one lot.
    Never above `quantity` (the trader's own size). None / 100 %: unchanged, no note. Pure."""
    if pct is None or pct >= 100 or quantity <= 0:
        return quantity, None
    lot = float(unit) if unit and unit > 0 else 1.0
    held = quantity / lot
    lots = max(1, math.floor(held * pct / 100.0 + 1e-9))
    if lots >= held - 1e-9:
        return quantity, None
    stepped = round(lots * lot, 8)
    return stepped, (f"Smaller size after losses in a row: {lots_text(lots)} instead of {lots_text(round(held, 8))} "
                     f"({pct:g}% of your size, your own setting).")


# --- pure: the daily loss lock --------------------------------------------------------------------------------------------

def trough_after(trough: float, daily_pnl: float) -> float:
    """Today's lowest closed P&L after a close brought today's closed P&L to `daily_pnl`. Pure."""
    return min(trough, daily_pnl)


def note_closed(state: Any, pnl: float | None, track: bool) -> None:
    """One closed trade (or part) in a backtest, after its P&L is in `state.daily_pnl`: today's lowest closed P&L, and -
    `track` (an option is set) - the P&L newest first, bounded as the worker's read (HISTORY_ROWS). Both backtest
    engines call it, so a backtest reads the closed trades as the worker reads the stored ones."""
    state.day_trough_pnl = trough_after(state.day_trough_pnl, state.daily_pnl)
    if track:
        state.closed_pnls.insert(0, pnl)
        del state.closed_pnls[HISTORY_ROWS:]
        state.today_pnls.insert(0, pnl)


def lock_holds(cfg: RiskConfig, limit: float | None, *, daily_pnl: float, day_trough: float, marked: bool) -> bool:
    """Whether the daily loss lock holds now: switched on with a daily loss limit, and the limit was reached today -
    now, at today's lowest closed P&L, or as the worker cycle saw it (`marked`). Pure."""
    if not cfg.daily_loss_lock or limit is None:
        return False
    return marked or daily_pnl <= -limit or day_trough <= -limit


# --- pure: the stagnation flag ---------------------------------------------------------------------------------------------

def stagnation(highs: Sequence[float], lows: Sequence[float], *, entry: float, bars: int, move_pct: float) -> dict[str, Any] | None:
    """Over the last `bars` closed bars since the entry: their range (highest high - lowest low) as % of the entry price,
    and whether it stayed within `move_pct` ("not moving"). None while fewer bars have closed since the entry, or with no
    usable entry price. Information only. Pure."""
    if bars < 1 or len(highs) < bars or len(lows) < bars or not entry or entry <= 0:
        return None
    high, low = max(highs[-bars:]), min(lows[-bars:])
    moved = (high - low) / entry * 100.0
    return {"bars": bars, "moved_pct": round(moved, 2), "limit_pct": move_pct, "not_moving": moved <= move_pct + 1e-9}


# --- stored -----------------------------------------------------------------------------------------------------------------

def trading_day(now: datetime | None = None) -> date:
    from app.market_data.calendar import to_ist
    return to_ist(now).date()


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def marked_today(record: Any, now: datetime | None = None) -> bool:
    """Whether the worker cycle stored the daily loss limit as reached on today's trading day."""
    at = _aware(getattr(record, "daily_loss_locked_at", None)) if record is not None else None
    return at is not None and trading_day(at) == trading_day(now)


async def flag_on(session: Any, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def gate(session: Any, cfg: RiskConfig, tenant_id: int) -> RiskConfig:
    """The config as the engine reads it: the options only while the `risk_options` flag is ON (no flag read when no
    option is set)."""
    if not any_on(cfg) or await flag_on(session, tenant_id):
        return cfg
    return without_options(cfg)


@dataclass(frozen=True)
class EntryFacts:
    """What the entry check needs from the stored trades for the options (empty: the options are off)."""
    closed_newest_first: tuple[float | None, ...] = ()
    today_newest_first: tuple[float | None, ...] = ()
    day_trough: float = 0.0
    marked: bool = False


def _trough(today_oldest_first: Sequence[float | None]) -> float:
    running, low = 0.0, 0.0
    for pnl in today_oldest_first:
        running += float(pnl or 0.0)
        low = min(low, running)
    return low


async def entry_facts(session: Any, tenant_id: int, *, now: datetime | None = None) -> EntryFacts:
    """The facts for one entry check of this organisation (app/trading/persistence.build_trading_day_state). Reads
    only; nothing is read beyond the settings row while every option is off or the flag is OFF."""
    from sqlalchemy import select

    from app.db.models import RiskSettingsRecord, TradeRecord
    from app.market_data.calendar import trading_day_start
    from app.risk_engine.routes import _record_to_config
    record = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant_id))
    if record is None:
        return EntryFacts()
    cfg = _record_to_config(record)
    lock_on, step_on = bool(cfg.daily_loss_lock), step_ready(cfg)
    if not (lock_on or step_on) or not await flag_on(session, tenant_id):
        return EntryFacts()
    start = trading_day_start(now)
    today_rows = list(await session.execute(
        select(TradeRecord.pnl).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_not(None), TradeRecord.exit_time >= start)
        .order_by(TradeRecord.exit_time, TradeRecord.id)))
    today = [r[0] for r in today_rows]
    closed: list[float | None] = []
    if step_on and cfg.step_down_reset == RESET_WINS:
        rows = list(await session.execute(
            select(TradeRecord.pnl).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_not(None))
            .order_by(TradeRecord.exit_time.desc(), TradeRecord.id.desc()).limit(HISTORY_ROWS)))
        closed = [r[0] for r in rows]
    return EntryFacts(closed_newest_first=tuple(closed), today_newest_first=tuple(reversed(today)),
                      day_trough=_trough(today), marked=lock_on and marked_today(record, now))


async def sweep(session: Any, now: datetime | None = None) -> int:
    """The worker cycle: for every organisation with the daily loss lock on, store when today's P&L (closed + open marks)
    reached its daily loss limit - once a trading day, so the lock holds after the P&L recovers. Bookkeeping only: it
    never places, blocks or changes an order. Commits; returns how many organisations were read. The limit is the
    organisation's own (its saved capital for a %, or the rupee amount); an entry check also sees the limit reached now."""
    from sqlalchemy import or_, select, update

    from app.db.models import RiskSettingsRecord
    from app.market_data.calendar import trading_day_start
    from app.risk_engine.routes import _record_to_config
    from app.trading.persistence import day_pnl
    at = now or datetime.now(timezone.utc)
    rows = list(await session.scalars(select(RiskSettingsRecord).where(RiskSettingsRecord.limits_switches_json.contains(f'"{LOCK_CHECK}"'))))
    read = wrote = 0
    for record in rows:
        cfg = _record_to_config(record)
        limit = cfg.daily_loss_amount()
        if not cfg.daily_loss_lock or limit is None or marked_today(record, at) or not await flag_on(session, int(record.tenant_id)):
            continue
        read += 1
        if await day_pnl(session, int(record.tenant_id), now=at) > -limit:
            continue
        row = RiskSettingsRecord
        start = trading_day_start(at)
        await session.execute(update(row).where(row.tenant_id == record.tenant_id,
                                                or_(row.daily_loss_locked_at.is_(None), row.daily_loss_locked_at < start))
                              .values(daily_loss_locked_at=at, updated_at=row.updated_at).execution_options(synchronize_session=False))
        wrote += 1
    if wrote:
        await session.commit()
    return read


# --- the stagnation flag of open positions ----------------------------------------------------------------------------------

def stagnation_settings(cfg: RiskConfig) -> tuple[int, float, int] | None:
    """(bars, move %, bar minutes) when the flag is switched on with every value, else None."""
    bars, pct, minutes = cfg.stagnation_bars, cfg.stagnation_move_pct, cfg.stagnation_bar_minutes
    if bars is None or pct is None or minutes is None or bars < 1 or pct <= 0 or minutes not in BAR_MINUTES:
        return None
    return int(bars), float(pct), int(minutes)


async def stagnation_of_open(session: Any, user: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """The stagnation flag of the user's open single positions (his own, or every one of the organisation for its
    owner as the positions list shows them): per position the bars read, how far the price moved and whether it is
    "not moving". Bars are the stored ones (no broker call); a contract with none stored reads its underlying's (measured
    from the open of the first bar after the entry). A
    position with too few closed bars since its entry is listed as "not enough bars yet". Information only."""
    from sqlalchemy import select

    from app.db.models import TradeRecord
    from app.market_store.reader import read_bars
    from app.risk_engine.routes import get_tenant_risk_config
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    cfg = await gate(session, cfg, user.tenant_id)
    settings = stagnation_settings(cfg)
    if settings is None:
        return {"on": False, "positions": []}
    bars, pct, minutes = settings
    at = _aware(now) or datetime.now(timezone.utc)
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.exit_time.is_(None),
        TradeRecord.leg_group_id.is_(None)).order_by(TradeRecord.id)))
    out = []
    for trade in trades:
        entry_at = _aware(trade.entry_time) or at
        read_from = trade.symbol
        frame = None
        for symbol in dict.fromkeys(s for s in (trade.symbol, trade.underlying_symbol) if s):
            got = await read_bars(session, symbol, f"{minutes}min", entry_at, at, purpose="stagnation")
            if got.frame is not None and len(got.frame):
                frame, read_from = got.frame, symbol
                break
        item: dict[str, Any] = {"trade_id": trade.id, "symbol": trade.symbol, "read": read_from, "bar_minutes": minutes}
        if frame is None:
            out.append({**item, "state": "no_bars"})
            continue
        index = frame.index
        starts = [(_aware(ts.to_pydatetime()) if hasattr(ts, "to_pydatetime") else ts) for ts in index]
        closed = [i for i, ts in enumerate(starts) if ts is not None and ts >= entry_at and ts + timedelta(minutes=minutes) <= at]
        highs = [float(frame["high"].iloc[i]) for i in closed]
        lows = [float(frame["low"].iloc[i]) for i in closed]
        base = float(trade.entry_price) if read_from == trade.symbol else float(frame["open"].iloc[closed[0]]) if closed else 0.0
        result = stagnation(highs, lows, entry=base, bars=bars, move_pct=pct)
        if result is None:
            out.append({**item, "state": "too_few_bars", "closed_bars": len(closed)})
            continue
        out.append({**item, "state": "not_moving" if result["not_moving"] else "moving", **result})
    return {"on": True, "bars": bars, "move_pct": pct, "bar_minutes": minutes, "positions": out,
            "note": "It only shows: nothing is closed, blocked or changed by it."}


__all__ = ["FLAG", "LOCK_CHECK", "STEP_CHECK", "STAGNATION_CHECK", "CHECK_IDS", "RESETS", "RESET_LABELS", "BAR_MINUTES",
           "OPTION_FIELDS", "HISTORY_ROWS", "note_closed", "any_on", "without_options", "stepped_down", "step_ready", "step_pct", "apply_step", "trough_after",
           "lock_holds", "stagnation", "gate", "EntryFacts", "entry_facts", "sweep", "stagnation_settings", "stagnation_of_open"]
