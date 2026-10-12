"""Z3-b / BT3 (beta spec, Phase 0): the per-user PAPER account and the progression ladder.

Scope decision: **per user**, not per organisation. A PAPER account is one person's practice record - the beta spec
says "full PAPER account per user", proposals reach "that user's own Telegram", and a team member's paper results are
not the owner's. Every read here filters on both `tenant_id` and `user_id` (isolation tests in
tests/test_z3b_paper_account.py). A position belongs to the user it was entered for: the person who approved the entry
proposal (Z3-a), the console user, or - for an organisation's automatic deployment - the worker's acting user. The risk
engine's daily budget stays organisation-wide (strictest wins, ADR-0003); in the beta every sign-up is its own
organisation, so the two coincide.

Ported from the Trade repo `paper/` (behaviour, English names, settings-driven):
* journal with charges and net P&L - `paper/journal.py` (charges here are ATP's dated India cost model, stored on each
  trade at close; net = gross - charges, as `on_exit` computes it);
* would-have records - `paper/journal.py::would_have`: an entry proposal that was rejected or expired is listed as
  "not taken" with its levels, nothing placed;
* PAPER-scope pause - `paper/pause.py`: stops this user's *new* PAPER entries and entry proposals only, never an exit,
  never LIVE; an unreadable state is not possible here (a row in the database), so there is no fail-closed branch.
Nothing in this module places, changes or closes an order.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.core import config as app_config
from app.db.models import AiActionRecord, PaperAccountRecord, TradeRecord, User
from app.market_data.calendar import IST

FLAG = "paper_account"
STAGE_PAPER = "PAPER"
STAGE_LIVE = "LIVE_APPROVAL"


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return _utc(dt).isoformat() if dt is not None else None


async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def get_account(session: AsyncSession, user: User) -> PaperAccountRecord:
    """The user's PAPER account, created on first use with the platform's starting capital (a new user starts in PAPER)."""
    row = await session.scalar(select(PaperAccountRecord).where(PaperAccountRecord.user_id == user.id))
    if row is not None:
        return row
    row = PaperAccountRecord(tenant_id=user.tenant_id, user_id=user.id, starting_capital=app_config.PAPER_STARTING_CAPITAL, paused=False)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:                                   # created concurrently: use that one
        row = await session.scalar(select(PaperAccountRecord).where(PaperAccountRecord.user_id == user.id))
    await session.commit()
    assert row is not None
    return row


class CapitalLocked(ValueError):
    """Review M7: the starting capital is fixed once the account has a PAPER trade."""


async def set_starting_capital(session: AsyncSession, user: User, amount: float) -> PaperAccountRecord:
    """Only before the first PAPER trade (review M7): changing it later would rewrite past balances and the drawdown the
    ladder measures."""
    if not app_config.PAPER_MIN_CAPITAL <= float(amount) <= app_config.PAPER_MAX_CAPITAL:
        raise ValueError(f"Starting capital must be between {app_config.PAPER_MIN_CAPITAL:,.0f} and {app_config.PAPER_MAX_CAPITAL:,.0f}")
    traded = await session.scalar(select(TradeRecord.id).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
                                                               TradeRecord.mode == "PAPER").limit(1))
    if traded is not None:
        raise CapitalLocked("The starting capital is fixed once your PAPER account has a trade (it is what your balance, drawdown and ladder are measured from)")
    row = await get_account(session, user)
    old = float(row.starting_capital)
    row.starting_capital = round(float(amount), 2)
    await write_audit_log(session, user.tenant_id, user.id, "paper_capital_set", f"{old:,.2f} -> {row.starting_capital:,.2f}")
    await session.commit()
    return row


async def set_paused(session: AsyncSession, user: User, paused: bool, *, via: str, reason: Optional[str] = None) -> PaperAccountRecord:
    """PAPER-scope pause (Trade `paper/pause.py`): only this user's new PAPER entries and entry proposals stop."""
    row = await get_account(session, user)
    row.paused = bool(paused)
    row.paused_reason = ((reason or "").strip()[:150] or f"paused via {via}") if paused else None
    row.paused_at = datetime.now(timezone.utc) if paused else None
    await write_audit_log(session, user.tenant_id, user.id, "paper_paused" if paused else "paper_resumed", f"via {via}: {row.paused_reason or ''}".strip())
    await session.commit()
    return row


async def paused_reason(session: AsyncSession, user_id: Optional[int]) -> Optional[str]:
    """Why this user's new PAPER entries are paused, or None. No account row = not paused."""
    if user_id is None:
        return None
    row = await session.scalar(select(PaperAccountRecord).where(PaperAccountRecord.user_id == user_id))
    if row is None or not row.paused:
        return None
    if not await enabled(session, row.tenant_id):            # review L6: the pause does not outlive the flag
        return None
    return f"PAPER entries paused for this user ({row.paused_reason or 'paused'}) - /resume to continue; exits keep running"


# --- the account -------------------------------------------------------------------------------------------------------

def _trade_row(t: TradeRecord) -> Dict:
    pnl = float(t.pnl) if t.pnl is not None else None
    charges = float(t.charges or 0.0)
    return {"id": t.id, "symbol": t.symbol, "direction": t.direction, "strategy_id": t.strategy_id, "quantity": float(t.quantity),
            "entry_time": _iso(t.entry_time), "entry_price": float(t.entry_price),
            "stop_loss": float(t.stop_loss), "target1": float(t.target1) if t.target1 is not None else None,
            "exit_time": _iso(t.exit_time), "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "exit_reason": t.exit_reason, "gross_pnl": round(pnl + charges, 2) if pnl is not None else None, "charges": round(charges, 2),
            "net_pnl": pnl, "mark_price": float(t.mark_price) if t.mark_price is not None else None,
            "deployment_id": t.deployment_id, "charges_source": t.charges_source}


def equity_drawdown(starting_capital: float, closed_pnls: List[float]) -> Dict[str, float]:
    """Largest peak-to-trough fall of the closed-trade equity curve (starting capital + cumulative net P&L), in money and
    as % of the peak it fell from. Pure."""
    equity = peak = float(starting_capital)
    worst, worst_pct = 0.0, 0.0
    for pnl in closed_pnls:
        equity += float(pnl)
        peak = max(peak, equity)
        fall = peak - equity
        if fall > worst:
            worst = fall
        if peak > 0 and fall / peak * 100.0 > worst_pct:
            worst_pct = fall / peak * 100.0
    return {"max_drawdown": round(worst, 2), "max_drawdown_pct": round(worst_pct, 2)}


async def _paper_trades(session: AsyncSession, user: User) -> List[TradeRecord]:
    return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
                                                                TradeRecord.mode == "PAPER").order_by(TradeRecord.id)))


def _counted(trade: TradeRecord) -> bool:
    """Review H2: a trade priced from anything but the platform's own quotes (`price_source`, e.g. "client" for a console
    entry priced from browser candles) is left out of the account and the ladder - they cannot be fed by made-up prices."""
    return not trade.price_source


async def _not_taken(session: AsyncSession, user: User, limit: int = 20) -> List[Dict]:
    """Would-have records: this user's own entry proposals (review L7) that were rejected or expired unanswered."""
    rows = await session.scalars(select(AiActionRecord).where(
        AiActionRecord.tenant_id == user.tenant_id, AiActionRecord.action == "ENTRY", AiActionRecord.owner_user_id == user.id,
        (AiActionRecord.status == "REJECTED") | ((AiActionRecord.status == "EXPIRED") & AiActionRecord.decided_by.is_(None)),
    ).order_by(AiActionRecord.id.desc()).limit(limit))
    out = []
    for row in rows:
        ev = json.loads(row.evidence_json or "{}")
        out.append({"proposal_id": row.id, "status": row.status, "symbol": ev.get("symbol"), "direction": ev.get("direction"),
                    "proposal_price": ev.get("proposal_price"), "stop_loss": ev.get("stop_loss"), "target1": ev.get("target1"),
                    "source": ev.get("source"), "created_at": _iso(row.created_at),
                    "note": row.decision_note or row.result})
    return out


async def _proposal_counts(session: AsyncSession, user: User) -> Dict[str, int]:
    """Review L7: the user's own entry proposals only. Z10-d: the four counts in one query (one round trip, not four:
    `/api/paper/account` was the slowest read of the load test's sessions)."""
    def count(*conditions: Any) -> Any:
        return func.coalesce(func.sum(case((and_(*conditions), 1), else_=0)), 0)
    row = (await session.execute(select(
        count(AiActionRecord.decided_by.is_not(None), AiActionRecord.status.in_(("APPROVED", "EXECUTED", "FAILED", "EXPIRED"))),
        count(AiActionRecord.status == "REJECTED"),
        count(AiActionRecord.status == "PROPOSED"),
        count(AiActionRecord.status == "EXPIRED", AiActionRecord.decided_by.is_(None)),
    ).where(AiActionRecord.tenant_id == user.tenant_id, AiActionRecord.action == "ENTRY", AiActionRecord.owner_user_id == user.id))).one()
    return {"approved_by_you": int(row[0]), "rejected_by_you": int(row[1]), "pending": int(row[2]), "expired_unanswered": int(row[3])}


async def summary(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict:
    """Balance, P&L (gross, charges, net), open positions, journal, would-have records and proposal counts - all from this
    user's own PAPER trades and proposals."""
    now = now or datetime.now(timezone.utc)
    account = await get_account(session, user)
    every = await _paper_trades(session, user)
    trades = [t for t in every if _counted(t)]
    closed = sorted((t for t in trades if t.exit_time is not None), key=lambda t: (_utc(t.exit_time or t.entry_time), t.id))
    open_ = [t for t in trades if t.exit_time is None]
    net = sum(float(t.pnl or 0.0) for t in closed)
    charges = sum(float(t.charges or 0.0) for t in closed)
    unrealised = 0.0
    for t in open_:
        if t.mark_price is not None:
            sign = 1.0 if t.direction == "LONG" else -1.0
            unrealised += sign * (float(t.mark_price) - float(t.entry_price)) * float(t.quantity)
    starting = float(account.starting_capital)
    wins = sum(1 for t in closed if (t.pnl or 0.0) > 0)
    sessions = {_utc(t.entry_time).astimezone(IST).date() for t in trades if t.entry_time is not None}
    return {
        "user_id": user.id, "mode": "PAPER", "starting_capital": round(starting, 2),
        "balance": round(starting + net, 2), "equity": round(starting + net + unrealised, 2),
        "realised": {"gross": round(net + charges, 2), "charges": round(charges, 2), "net": round(net, 2)},
        "unrealised": round(unrealised, 2),
        "closed_trades": len(closed), "open_positions": len(open_), "wins": wins, "losses": len(closed) - wins,
        "win_rate": round(wins / len(closed), 4) if closed else None, "sessions": len(sessions),
        **equity_drawdown(starting, [float(t.pnl or 0.0) for t in closed]),
        "paused": bool(account.paused), "paused_reason": account.paused_reason,
        "excluded_trades": len(every) - len(trades),
        "positions": [_trade_row(t) for t in open_],
        "journal": [_trade_row(t) for t in reversed(closed[-50:])],
        "not_taken": await _not_taken(session, user),
        "proposals": await _proposal_counts(session, user),
        "as_of": now.isoformat(),
        "note": "Figures from your own PAPER trades priced from platform quotes: net P&L is after the estimated charges of each trade (a contract "
                "note replaces the estimate). Trades priced from your own inputs (console candles) are left out.",
    }


async def ladder(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict:
    """The progression ladder card (P1 §1.4), PAPER stage only in Phase 0: each criterion with its value from the user's
    own PAPER trades and its threshold from settings. States facts; it never recommends trading or a stage change."""
    data = await summary(session, user, now)
    closed = data["closed_trades"]
    criteria = [
        {"key": "sessions", "label": "Sessions with a PAPER trade", "value": data["sessions"], "target": app_config.PAPER_LADDER_MIN_SESSIONS,
         "comparison": "at_least", "met": data["sessions"] >= app_config.PAPER_LADDER_MIN_SESSIONS},
        {"key": "closed_trades", "label": "Closed PAPER trades", "value": closed, "target": app_config.PAPER_LADDER_MIN_TRADES,
         "comparison": "at_least", "met": closed >= app_config.PAPER_LADDER_MIN_TRADES},
        {"key": "max_drawdown_pct", "label": "Largest drawdown, % of peak equity", "value": data["max_drawdown_pct"],
         "target": app_config.PAPER_LADDER_MAX_DRAWDOWN_PCT, "comparison": "at_most",
         "met": closed > 0 and data["max_drawdown_pct"] <= app_config.PAPER_LADDER_MAX_DRAWDOWN_PCT},
        {"key": "discipline_score", "label": "Discipline score", "value": None, "target": None, "comparison": None, "met": None,
         "note": "Not measured yet (journal behaviour tags come with P1-e)"},
    ]
    measured = [c for c in criteria if c["met"] is not None]
    return {"stage": STAGE_PAPER, "stages": [
                {"key": STAGE_PAPER, "label": "PAPER", "status": "current"},
                {"key": STAGE_LIVE, "label": "LIVE (approval-only)", "status": "locked", "note": "Not available in Phase 0"}],
            "criteria": criteria, "met": sum(1 for c in measured if c["met"]), "measured": len(measured),
            "summary": {k: data[k] for k in ("starting_capital", "balance", "equity", "closed_trades", "sessions", "max_drawdown_pct", "win_rate")},
            "as_of": data["as_of"]}
