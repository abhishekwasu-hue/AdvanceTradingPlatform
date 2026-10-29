"""AUD-T6 (BR-M3 / BR-R1): similar-setup recall and "what the context said vs what happened" - from what ATP already
stored at entry, never from a new reading of the chart.

Similar (BR-M3, structured features first): the user's OWN past decisions whose context stored at entry is EXACTLY the
same on every field of MATCH_FIELDS:

* `setup`     - the strategy / template the trade was taken on (`trades.strategy_id`; a proposal's `strategy_id`);
* `side`      - long or short on the instrument the setup read (`underlying_direction` for an option or future taken on
                an underlying's signal, else `direction`; a proposal's `direction`);
* `holding`   - INTRADAY or SWING (`trades.holding`; a proposal's `holding`);
* `timeframe` - the bot's timeframe for a trade or proposal raised by a bot (`strategy_deployments.timeframe`, fixed when
                the bot was made); "not recorded" for one entered by hand, a webhook or the API - and "not recorded"
                matches only "not recorded".

Left out on purpose: the indicator regime stored on bot trades (`regime_at_entry`) - an admin diagnostic only by the
owner's order (app/ai/regime.py, handover item 5 C), never user-facing structure; and the symbol (the same setup on
another symbol is still the same setup).

No look-ahead: only decisions CLOSED strictly before the subject's own entry (a trade's entry time, a proposal's time
raised) are matched; a structure counts once and only when every leg closed before then. The subject itself (and the
other legs of its structure) is never a match.

What happened (BR-R1): for a closed trade, the context as captured then (the fields above, the plan's reward to risk,
and the reasons the signal carried when it was proposed, when it came through an approval) next to the outcome (how it
closed, R, the most it moved in his favour / against him while open from the journal's marks, Z6 `on_marks`). Plain
words, no buy / sell / target / stop-loss wording, nothing re-read in hindsight.

Self-scoped (tenant AND user), read-only: SELECTs only, never a commit, never an order path.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import AbstractSet, Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import AiActionRecord, JournalEntryRecord, StrategyDeploymentRecord, TradeRecord, User

MATCH_FIELDS: Tuple[str, ...] = ("setup", "side", "holding", "timeframe")

NO_MATCHES = "No similar closed trades of yours yet."
OPEN_TRADE = "Shown once this trade is closed."
MAX_REASONS = 5


@dataclass(frozen=True)
class Context:
    """The context of one decision as stored at entry (the match key)."""
    setup: str
    side: str
    holding: str
    timeframe: Optional[str]

    def to_dict(self) -> Dict[str, Optional[str]]:
        return asdict(self)


@dataclass(frozen=True)
class Unit:
    """One decision of the user: a trade, or every leg of one structure together (counted once)."""
    trade_id: int                    # the first leg - the journal row it links to
    trade_ids: Tuple[int, ...]
    context: Context
    entered_at: Optional[datetime]
    closed_at: Optional[datetime]    # None while any leg is open
    pnl: Optional[float]             # net, None while open
    risk: float                      # initial risk in rupees; 0 = no valid stop (no R)
    exit_reason: Optional[str]
    mode: str

    @property
    def r(self) -> Optional[float]:
        return round(self.pnl / self.risk, 2) if self.pnl is not None and self.risk > 0 else None


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _upper(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper()
    return text or default


def trade_context(trade: TradeRecord, timeframes: Dict[int, str]) -> Context:
    side = _upper(trade.underlying_direction) or _upper(trade.direction)
    timeframe = timeframes.get(trade.deployment_id) if trade.deployment_id is not None else None
    return Context(setup=trade.strategy_id or "", side=side, holding=_upper(trade.holding, "INTRADAY"), timeframe=timeframe or None)


def evidence_of(action: AiActionRecord) -> Dict[str, Any]:
    try:
        value = json.loads(action.evidence_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def proposal_context(action: AiActionRecord, timeframe: Optional[str]) -> Context:
    ev = evidence_of(action)
    return Context(setup=str(ev.get("strategy_id") or ""), side=_upper(ev.get("direction")), holding=_upper(ev.get("holding"), "INTRADAY"),
                   timeframe=timeframe or None)


# --- plain words --------------------------------------------------------------------------------------------------------

def _trader_exit(text: str) -> bool:
    from app.journal.detectors import TRADER_EXIT_MARKERS
    return any(m in text for m in TRADER_EXIT_MARKERS)


def exit_words(reason: Optional[str]) -> str:
    """How a decision closed, in plain words without stop / target wording (the user's own levels by their neutral names)."""
    text = (reason or "").lower()
    if not text:
        return "Closed"
    if _trader_exit(text):
        return "You closed it yourself"
    for markers, words in (
        (("trail",), "Closed at your trailing exit level"),
        (("breakeven", "break-even", "break even"), "Closed at your break-even level"),
        (("premium floor", "premium ceiling"), "Closed at your premium limit"),
        (("stop",), "Closed at your exit-if-wrong level"),
        (("target",), "Closed at your exit-at-profit level"),
        (("time", "square", "end of day", "eod"), "Closed by your time exit"),
        (("expir",), "Closed at expiry"),
        (("structure", "invalidat"), "Closed when the structure changed"),
        (("emergency", "kill", "lockout"), "Closed by a safety switch"),
        (("reconcil",), "Closed at the broker"),
    ):
        if any(m in text for m in markers):
            return words
    return "Closed by a platform rule"


def result_of(pnl: Optional[float]) -> Optional[str]:
    if pnl is None:
        return None
    return "gain" if pnl > 0 else "loss" if pnl < 0 else "flat"


def r_text(r: Optional[float]) -> str:
    return f"{r:+.2f} R"


def outcome_words(pnl: Optional[float], r: Optional[float]) -> str:
    result = result_of(pnl)
    if result is None:
        return "Still open"
    base = {"gain": "a gain", "loss": "a loss", "flat": "no gain or loss"}[result]
    return f"{r_text(r)}, {base}" if r is not None else base[0].upper() + base[1:]


# --- similar-setup recall (pure) ----------------------------------------------------------------------------------------

def build_units(trades: Sequence[TradeRecord], timeframes: Dict[int, str]) -> List[Unit]:
    """The user's decisions among `trades` (a structure's legs together, as the journal counts them)."""
    from app.journal.service import review_trades
    by_id = {t.id: t for t in trades}
    out: List[Unit] = []
    for unit in review_trades(trades):
        legs = sorted((by_id[i] for i in unit.rows if i in by_id), key=lambda leg: leg.id)
        if not legs:
            continue
        first = legs[0]
        closed = unit.pnl is not None
        exits = [x for x in (_utc(leg.exit_time) for leg in legs) if x is not None]
        entries = [x for x in (_utc(leg.entry_time) for leg in legs) if x is not None]
        out.append(Unit(trade_id=first.id, trade_ids=tuple(leg.id for leg in legs), context=trade_context(first, timeframes),
                        entered_at=min(entries) if entries else None, closed_at=max(exits) if closed and exits else None,
                        pnl=float(unit.pnl) if closed else None, risk=float(unit.risk or 0.0), exit_reason=first.exit_reason,
                        mode=first.mode or "PAPER"))
    return out


def pick(subject: Context, cutoff: datetime, units: Iterable[Unit], exclude: AbstractSet[int] = frozenset()) -> List[Unit]:
    """The units that match `subject` exactly on MATCH_FIELDS and closed strictly before `cutoff` (newest first)."""
    limit = _utc(cutoff) or cutoff
    found = [u for u in units if u.closed_at is not None and u.closed_at < limit and u.context == subject and not (set(u.trade_ids) & exclude)]
    return sorted(found, key=lambda u: (u.closed_at, u.trade_id), reverse=True)


def summary(matches: Sequence[Unit], min_sample: int) -> Dict[str, Any]:
    """The aggregate words - only from `min_sample` matches (never extrapolated below it)."""
    n = len(matches)
    if n == 0:
        return {"enough": False, "line": NO_MATCHES, "wins": None, "losses": None, "flat": None, "avg_r": None, "r_trades": 0,
                "common_loss_exit": None}
    if n < min_sample:
        return {"enough": False, "line": f"Not enough history yet: {n} of {min_sample} similar closed trades.", "wins": None, "losses": None,
                "flat": None, "avg_r": None, "r_trades": 0, "common_loss_exit": None}
    wins = sum(1 for u in matches if (u.pnl or 0) > 0)
    losses = sum(1 for u in matches if (u.pnl or 0) < 0)
    rs = [u.r for u in matches if u.r is not None]
    avg_r = round(sum(rs) / len(rs), 2) if rs else None
    loss_exits = Counter(exit_words(u.exit_reason) for u in matches if (u.pnl or 0) < 0)
    common = sorted(loss_exits.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if loss_exits else None
    line = f"{n} similar closed trades: {wins} closed with a gain, {losses} with a loss"
    if avg_r is not None:
        line += f", average {r_text(avg_r)}" + (f" (over {len(rs)} with a set exit-if-wrong level)" if len(rs) < n else "")
    line += "."
    if common:
        line += f" The losses most often: {common[0].lower() + common[1:]}."
    return {"enough": True, "line": line, "wins": wins, "losses": losses, "flat": n - wins - losses, "avg_r": avg_r, "r_trades": len(rs),
            "common_loss_exit": common}


def match_dict(u: Unit) -> Dict[str, Any]:
    from app.journal.service import week_bounds
    week = week_bounds(None, now=u.entered_at)[0] if u.entered_at else None
    return {"trade_id": u.trade_id, "entered_at": u.entered_at.isoformat() if u.entered_at else None,
            "closed_at": u.closed_at.isoformat() if u.closed_at else None, "journal_week": week, "mode": u.mode,
            "r": u.r, "result": result_of(u.pnl), "outcome": outcome_words(u.pnl, u.r), "how_closed": exit_words(u.exit_reason)}


def recall(subject: Context, cutoff: datetime, units: Iterable[Unit], exclude: AbstractSet[int] = frozenset(), *,
           min_sample: Optional[int] = None, max_listed: Optional[int] = None) -> Dict[str, Any]:
    min_sample = config.SETUP_RECALL_MIN_SAMPLE if min_sample is None else min_sample
    max_listed = config.SETUP_RECALL_MAX_LISTED if max_listed is None else max_listed
    matches = pick(subject, cutoff, units, exclude)
    return {"context": subject.to_dict(), "fields": list(MATCH_FIELDS), "as_of": (_utc(cutoff) or cutoff).isoformat(), "count": len(matches),
            "min_sample": min_sample, **summary(matches, min_sample), "matches": [match_dict(u) for u in matches[:max_listed]]}


# --- what the context said vs what happened (pure) ----------------------------------------------------------------------

def clean_reasons(reasons: Any) -> List[str]:
    """The signal's own reasons as stored when it was proposed - only those in clean words (no call word)."""
    from app.signals.language import is_clean
    if not isinstance(reasons, list):
        return []
    out: List[str] = []
    for r in reasons:
        text = str(r or "").strip()
        if text and is_clean(text) and text not in out:
            out.append(text[:300])
    return out[:MAX_REASONS]


def reflection(trade: TradeRecord, unit: Unit, *, planned_rr: Optional[float], excursions: Dict[str, Optional[float]],
               reasons: Sequence[str], source: Optional[str]) -> Dict[str, Any]:
    ctx = unit.context
    said = {**ctx.to_dict(), "planned_rr": planned_rr, "reasons": list(reasons), "source": source,
            "captured_at": unit.entered_at.isoformat() if unit.entered_at else None}
    if unit.closed_at is None:
        return {"closed": False, "said": said, "happened": None, "line": OPEN_TRADE}
    mae_r, mfe_r = excursions.get("mae_r"), excursions.get("mfe_r")
    how = exit_words(unit.exit_reason)
    outcome = outcome_words(unit.pnl, unit.r)
    happened = {"how_closed": how, "r": unit.r, "result": result_of(unit.pnl), "outcome": outcome, "mfe_r": mfe_r, "mae_r": mae_r}
    side = {"LONG": "long", "SHORT": "short"}.get(ctx.side, ctx.side.lower() or "recorded")
    line = f"Entered on the {side} side" + (f" of the {ctx.timeframe} chart" if ctx.timeframe else "")
    if planned_rr is not None:
        line += f", planned reward to risk {planned_rr:g}"
    line += f"; {how[0].lower() + how[1:]}, {outcome[0].lower() + outcome[1:]}"
    if mfe_r is not None and mae_r is not None:
        line += f"; while open it moved up to {mfe_r:g} R in your favour and {mae_r:g} R against you"
    return {"closed": True, "said": said, "happened": happened, "line": line + "."}


# --- database reads (self-scoped, read-only) ----------------------------------------------------------------------------

async def _timeframes(session: AsyncSession, tenant_id: int, ids: Iterable[Optional[int]]) -> Dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await session.execute(select(StrategyDeploymentRecord.id, StrategyDeploymentRecord.timeframe).where(
        StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.id.in_(wanted)))
    return {int(i): str(tf) for i, tf in rows.all() if tf}


async def _closed_before(session: AsyncSession, user: User, setup: str, cutoff: datetime) -> List[TradeRecord]:
    """The user's own trades with this setup closed before `cutoff`, plus every leg of their structures (so a structure
    with a leg still open, or closed later, is never counted as closed)."""
    own = (TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id)
    rows = list(await session.scalars(select(TradeRecord).where(
        *own, TradeRecord.strategy_id == setup, TradeRecord.exit_time.is_not(None), TradeRecord.exit_time < cutoff)
        .order_by(TradeRecord.exit_time.desc(), TradeRecord.id.desc()).limit(config.SETUP_RECALL_MAX_SCAN)))
    groups = {r.leg_group_id for r in rows if r.leg_group_id}
    if groups:
        have = {r.id for r in rows}
        rows += [r for r in await session.scalars(select(TradeRecord).where(*own, TradeRecord.leg_group_id.in_(groups))) if r.id not in have]
    return rows


async def own_trade(session: AsyncSession, user: User, trade_id: int) -> Optional[TradeRecord]:
    row = await session.get(TradeRecord, trade_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        return None
    return row


async def own_proposal(session: AsyncSession, user: User, action_id: int) -> Optional[AiActionRecord]:
    """An ENTRY proposal the caller sees in Signals: his own, or an organisation-wide one (no owner) of his organisation."""
    from app.execution.approval import ACTION_ENTRY
    row = await session.get(AiActionRecord, action_id)
    if row is None or row.tenant_id != user.tenant_id or row.action != ACTION_ENTRY:
        return None
    if row.owner_user_id is not None and row.owner_user_id != user.id:
        return None
    return row


async def _structure_legs(session: AsyncSession, user: User, trade: TradeRecord) -> List[TradeRecord]:
    if not trade.leg_group_id:
        return [trade]
    return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
                                                                TradeRecord.leg_group_id == trade.leg_group_id)))


async def for_trade(session: AsyncSession, user: User, trade: TradeRecord) -> Dict[str, Any]:
    """Recall + reflection for one of the user's own journal rows (open or closed)."""
    from app.journal import capture
    from app.journal.service import excursions
    legs = await _structure_legs(session, user, trade)
    cutoff = min(x for x in (_utc(leg.entry_time) for leg in legs) if x is not None)
    candidates = await _closed_before(session, user, trade.strategy_id, cutoff)
    timeframes = await _timeframes(session, user.tenant_id, [t.deployment_id for t in candidates + legs])
    subject_unit = next(u for u in build_units(legs, timeframes) if trade.id in u.trade_ids)
    out = {"recall": recall(subject_unit.context, cutoff, build_units(candidates, timeframes), set(subject_unit.trade_ids))}
    entry = await session.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == trade.id, JournalEntryRecord.user_id == user.id,
                                                                  JournalEntryRecord.tenant_id == user.tenant_id))
    plan = capture._loads(entry.plan_json, {}) if entry is not None else {}
    planned_rr = (plan or capture.plan_of(trade)).get("rr")
    action = await session.scalar(select(AiActionRecord).where(AiActionRecord.tenant_id == user.tenant_id, AiActionRecord.action == "ENTRY",
                                                               AiActionRecord.trade_id.in_([leg.id for leg in legs]),
                                                               or_(AiActionRecord.owner_user_id == user.id, AiActionRecord.owner_user_id.is_(None)))
                                  .order_by(AiActionRecord.id).limit(1))
    reasons = clean_reasons(((evidence_of(action).get("signal") or {}).get("reasons")) if action is not None else None)
    source = (entry.source if entry is not None and entry.source else capture.derive_source(trade))
    out["reflection"] = reflection(trade, subject_unit, planned_rr=planned_rr if isinstance(planned_rr, (int, float)) else None,
                                   excursions=excursions(trade, entry), reasons=reasons, source=source)
    return out


async def for_proposal(session: AsyncSession, user: User, action: AiActionRecord) -> Dict[str, Any]:
    """Recall for an entry proposal (its time raised is the cut-off): the caller's own past closed trades."""
    timeframe = (await _timeframes(session, user.tenant_id, [action.deployment_id])).get(action.deployment_id) if action.deployment_id else None
    subject = proposal_context(action, timeframe)
    cutoff = _utc(action.created_at) or datetime.now(timezone.utc)
    candidates = await _closed_before(session, user, subject.setup, cutoff)
    timeframes = await _timeframes(session, user.tenant_id, [t.deployment_id for t in candidates])
    exclude = {action.trade_id} if action.trade_id else set()
    return {"recall": recall(subject, cutoff, build_units(candidates, timeframes), exclude)}


__all__ = ["MATCH_FIELDS", "Context", "Unit", "build_units", "pick", "summary", "recall", "reflection", "exit_words",
           "trade_context", "proposal_context", "for_trade", "for_proposal", "own_trade", "own_proposal"]
