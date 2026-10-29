"""AUD-T4 (owner audit 28 Oct; flag `paper_report_card`, needs `ui_v2`): the paper report card - a forward test of the
user's OWN closed PAPER trades, per bot (deployment) and per strategy. Never a backtest, never a forecast.

Each card holds:

* closed trades and win % (a decision closed with a net gain; legs of one structure are one decision, as the journal
  counts them - `app.journal.service.review_trades`);
* average R: net P&L / initial risk (stop distance x quantity, a structure's max loss). A decision without a valid stop
  is counted in the closed trades but left out of the average, and the card says how many;
* credit kept % for written options: the realised net P&L over the premium received at entry (the positions dish's own
  "premium received" rule, `app.growth.dishes.premium_kept`), over the decisions that took a premium in;
* max drawdown: the largest fall of the cumulative net P&L from its running peak (starting at 0), decisions in the
  order they closed;
* plan followed %: from the journal's behaviour tags (`app.journal`, the score's `plan_adherence` component - FOMO entry,
  early exit) over the decisions the journal holds, counted the journal's way (`score.counted`). Only while the journal
  (`discipline_engine`) is on; otherwise nothing is shown and the reason is given;
* calibration (Brier score) of a shown confidence - only from PAPER_REPORT_CARD_MIN_TRADES_BRIER decisions and only when
  a confidence is stored with each trade. None is stored today (a trade carries no probability and no link to a scored
  signal), so the card says so in a plain sentence.

Self-scoped: the caller's own trades (tenant and user), PAPER only, closed only. Read-only: writes nothing, places,
changes or blocks no order. Trades priced from the browser's candles (`price_source = "client"`) are left out, as the
PAPER account and the ladder leave them out.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import CustomStrategyRecord, JournalEntryRecord, JournalTagRecord, StrategyDeploymentRecord, TradeRecord, User

FLAG = "paper_report_card"
MANUAL_KEY = "manual"
PLAN_TAGS_COMPONENT = "plan_adherence"

NO_CONFIDENCE = "No confidence is stored with your trades, so there is nothing to check its calibration against."
JOURNAL_OFF = "Plan followed comes from your journal's behaviour tags; the journal is off."
NOT_JOURNALED = "None of these trades is in your journal yet."


@dataclass(frozen=True)
class Decision:
    """One closed decision: a trade, or every leg of one structure together."""
    pnl: float                       # net of charges
    risk: float                      # initial risk in rupees; 0 = no valid stop (no R)
    closed_at: datetime
    received: Optional[float] = None  # premium received at entry (written options only)
    trade_ids: Tuple[int, ...] = ()

    @property
    def r(self) -> Optional[float]:
        return self.pnl / self.risk if self.risk > 0 else None


def _utc(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), places)


def max_drawdown(pnls: Sequence[float]) -> float:
    """The largest fall of the running total from its peak (the total starts at 0), as a positive number."""
    total = peak = worst = 0.0
    for p in pnls:
        total += float(p)
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return round(worst, 2)


def brier(pairs: Sequence[Tuple[float, int]], min_trades: int) -> Optional[float]:
    """Mean of (stated probability - outcome)^2 over (probability 0..1, outcome 0/1) pairs; None below `min_trades`."""
    if len(pairs) < max(1, min_trades):
        return None
    return round(sum((float(p) - float(o)) ** 2 for p, o in pairs) / len(pairs), 4)


def calibration(pairs: Sequence[Tuple[float, int]], min_trades: int) -> Dict[str, Any]:
    if not pairs:
        return {"shown": False, "brier": None, "trades": 0, "min_trades": min_trades, "note": NO_CONFIDENCE}
    score = brier(pairs, min_trades)
    if score is None:
        return {"shown": False, "brier": None, "trades": len(pairs), "min_trades": min_trades,
                "note": f"Calibration appears from {min_trades} closed trades with a confidence ({len(pairs)} so far)."}
    return {"shown": True, "brier": score, "trades": len(pairs), "min_trades": min_trades, "note": None}


def decisions(trades: Sequence[TradeRecord]) -> List[Decision]:
    """The closed decisions among `trades` (a structure only once every leg has closed)."""
    from app.growth.dishes import premium_kept
    from app.journal.service import review_trades
    by_id = {t.id: t for t in trades}
    out: List[Decision] = []
    for unit in review_trades(trades):
        if not unit.closed:
            continue
        legs = [by_id[i] for i in unit.rows if i in by_id]
        kept = premium_kept(legs)
        out.append(Decision(pnl=float(unit.pnl), risk=float(unit.risk), closed_at=max(_utc(leg.exit_time) for leg in legs),
                            received=kept["received"] if kept else None, trade_ids=tuple(unit.rows)))
    return sorted(out, key=lambda d: (d.closed_at, d.trade_ids))


def card(units: Sequence[Decision], *, flagged: Optional[set] = None, journaled: Optional[set] = None) -> Dict[str, Any]:
    """The figures of one card. `journaled` = trade ids the journal holds (None: the journal is off); `flagged` = trade
    ids carrying a counted plan-adherence tag."""
    pnls = [u.pnl for u in units]
    wins = sum(1 for p in pnls if p > 0)
    rs = [r for r in (u.r for u in units) if r is not None]
    credit = [u for u in units if u.received and u.received > 0]
    received = sum(float(u.received or 0.0) for u in credit)
    out: Dict[str, Any] = {
        "closed": len(units), "wins": wins,
        "win_pct": _round(100.0 * wins / len(units), 1) if units else None,
        "avg_r": _round(sum(rs) / len(rs)) if rs else None, "r_trades": len(rs), "no_stop_trades": len(units) - len(rs),
        "net_pnl": round(sum(pnls), 2), "max_drawdown": max_drawdown(pnls),
        "capture_pct": _round(100.0 * sum(u.pnl for u in credit) / received, 1) if received > 0 else None,
        "credit_trades": len(credit),
        "plan_pct": None, "plan_trades": 0, "plan_flagged": 0,
        "first_close": units[0].closed_at.isoformat() if units else None,
        "last_close": units[-1].closed_at.isoformat() if units else None,
    }
    if journaled is not None:
        held = [u for u in units if any(i in journaled for i in u.trade_ids)]
        bad = [u for u in held if any(i in (flagged or set()) for i in u.trade_ids)]
        out.update(plan_trades=len(held), plan_flagged=len(bad),
                   plan_pct=_round(100.0 * (len(held) - len(bad)) / len(held), 1) if held else None)
    return out


async def _closed_paper(session: AsyncSession, user: User) -> List[TradeRecord]:
    rows = await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == "PAPER",
        TradeRecord.exit_time.is_not(None), TradeRecord.pnl.is_not(None),
        or_(TradeRecord.price_source.is_(None), TradeRecord.price_source != "client"),
    ).order_by(TradeRecord.exit_time.desc(), TradeRecord.id.desc()).limit(config.PAPER_REPORT_CARD_MAX_TRADES))
    closed = list(reversed(list(rows)))
    groups = {t.leg_group_id for t in closed if t.leg_group_id}
    if not groups:
        return closed
    # A structure counts once every leg has closed: legs still open keep the whole structure out for now.
    still_open = set(await session.scalars(select(TradeRecord.leg_group_id).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.leg_group_id.in_(sorted(groups)),
        TradeRecord.exit_time.is_(None))))
    return [t for t in closed if t.leg_group_id not in still_open]


async def _journal(session: AsyncSession, user: User, trade_ids: Sequence[int]) -> Tuple[Optional[set], set]:
    """(trade ids the journal holds, trade ids with a counted plan-adherence tag); (None, empty) while the journal is off."""
    from app.journal import FLAG as JOURNAL_FLAG
    from app.journal import settings as journal_settings
    from app.journal.score import TagState, counted
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, JOURNAL_FLAG, user.tenant_id):
        return None, set()
    ids = list(trade_ids) or [-1]
    journaled = set(await session.scalars(select(JournalEntryRecord.trade_id).where(
        JournalEntryRecord.tenant_id == user.tenant_id, JournalEntryRecord.user_id == user.id, JournalEntryRecord.trade_id.in_(ids))))
    tags = await session.scalars(select(JournalTagRecord).where(
        JournalTagRecord.tenant_id == user.tenant_id, JournalTagRecord.user_id == user.id, JournalTagRecord.trade_id.in_(ids),
        JournalTagRecord.tag.in_(journal_settings.COMPONENT_TAGS[PLAN_TAGS_COMPONENT])))
    s = await journal_settings.load(session, user)
    flagged = {t.trade_id for t in counted([TagState(r.trade_id, r.tag, r.status) for r in tags], s)}
    return journaled, flagged


async def _names(session: AsyncSession, user: User, trades: Sequence[TradeRecord]) -> Tuple[Dict[int, Tuple[str, str]], Dict[str, str]]:
    """(deployment id -> (strategy id, symbol)) for the organisation's own bots, and strategy id -> display name."""
    from app.signals_feed.service import strategy_name
    dep_ids = sorted({t.deployment_id for t in trades if t.deployment_id})
    deps: Dict[int, Tuple[str, str]] = {}
    if dep_ids:
        for d in await session.scalars(select(StrategyDeploymentRecord).where(
                StrategyDeploymentRecord.tenant_id == user.tenant_id, StrategyDeploymentRecord.id.in_(dep_ids))):
            deps[d.id] = (d.strategy_id, d.symbol)
    sids = {t.strategy_id for t in trades} | {s for s, _ in deps.values()}
    custom_ids = [int(s.split(":", 1)[1]) for s in sids if s.startswith("custom:") and s.split(":", 1)[1].isdigit()]
    custom: Dict[int, str] = {}
    if custom_ids:
        for c in await session.scalars(select(CustomStrategyRecord).where(
                CustomStrategyRecord.tenant_id == user.tenant_id, CustomStrategyRecord.id.in_(custom_ids))):
            custom[c.id] = c.name
    names: Dict[str, str] = {}
    for s in sids:
        tail = s.split(":", 1)[1] if s.startswith("custom:") else ""
        names[s] = custom.get(int(tail), "My strategy") if tail.isdigit() else strategy_name(s)
    return deps, names


async def build(session: AsyncSession, user: User) -> Dict[str, Any]:
    trades = await _closed_paper(session, user)
    journaled, flagged = await _journal(session, user, [t.id for t in trades])
    deps, names = await _names(session, user, trades)

    by_bot: Dict[str, List[TradeRecord]] = defaultdict(list)
    by_strategy: Dict[str, List[TradeRecord]] = defaultdict(list)
    for t in trades:
        by_bot[str(t.deployment_id) if t.deployment_id in deps else MANUAL_KEY].append(t)
        by_strategy[t.strategy_id].append(t)

    def cards(groups: Dict[str, List[TradeRecord]], kind: str) -> List[Dict[str, Any]]:
        out = []
        for key, rows in groups.items():
            units = decisions(rows)
            if not units:
                continue
            if kind == "bot" and key != MANUAL_KEY:
                sid, symbol = deps[int(key)]
                head = {"key": key, "kind": kind, "name": names.get(sid, sid), "strategy_id": sid, "symbol": symbol}
            elif kind == "bot":
                head = {"key": key, "kind": kind, "name": "Trades without a bot", "strategy_id": None, "symbol": None}
            else:
                head = {"key": key, "kind": kind, "name": names.get(key, key), "strategy_id": key, "symbol": None}
            out.append({**head, **card(units, flagged=flagged, journaled=journaled)})
        return sorted(out, key=lambda c: (-c["closed"], c["name"], c["key"]))

    all_units = decisions(trades)
    plan_note = JOURNAL_OFF if journaled is None else (NOT_JOURNALED if all_units and not journaled else None)
    return {
        "bots": cards(by_bot, "bot"), "strategies": cards(by_strategy, "strategy"),
        "total": card(all_units, flagged=flagged, journaled=journaled),
        "plan_note": plan_note,
        # No probability is stored per trade or linked to it (see the module note): nothing to score.
        "calibration": calibration([], config.PAPER_REPORT_CARD_MIN_TRADES_BRIER),
    }
