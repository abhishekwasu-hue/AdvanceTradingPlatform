"""F&O workspace 8b, slice 3 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 8b-k): the auto journal of the user's
closed PAPER F&O trades.

When one of his PAPER F&O positions closes, its journal entry (`journal_entries`, the discipline engine's table: one row
per trade, `app/journal/capture.py`) is the draft: it is created from the trade row itself the first time the closed
trade is read here (the same `ensure_entries` rebuild the journal uses for anything a hook did not see), so it holds the
facts only - the contract, the entry and exit fills, the P&L, the exit reason as recorded - plus, when the trade came
from one of HIS OWN signals, that signal's setup name and its "why" lines in the signal language (structure words; a
line with a value or a call word is left out, never rewritten - app/signals/language.py). The user adds his own words
with the existing notes field (`PATCH /api/trades/{id}/journal`, `trades.notes`). Nothing here writes anything but the
journal rows: no trade, order, position, setting or exit is read for a decision or changed, and nothing places an order.

Where the signal comes from:
* "Trade this setup" (8b-b): the ticket / basket opened from his signal places as usual; afterwards the page asks
  `link` to note the signal on the journal entries of the trades it just placed. `link` only accepts his own signal
  (fno_insights._signal: his proposal or his generated signal) and his own PAPER F&O trades on the signal's underlying,
  entered within LINK_WINDOW, not linked yet. It writes `plan_json["from_signal"]` and nothing else.
* an approved proposal: the ENTRY proposal whose `trade_id` is the trade (his own, as above).
* otherwise none (the draft says "Placed by you" or names the strategy that placed it).

Legs placed together (one basket, one structure) are one draft, as the positions page groups them.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AiActionRecord, JournalEntryRecord, OrderRecord, TradeRecord, User
from app.instruments import master
from app.journal import capture
from app.paper import fno_insights
from app.paper.fno_contracts import basket_ref_of
from app.signals import language as signal_language

FNO_KINDS = ("OPTION", "FUTURE")
DRAFT_DAYS = 14             # closed in the last two weeks
MAX_TRADES = 60             # the newest closed legs read at most
MAX_LINK = 12               # a basket's limit (fno_basket.MAX_LEGS)
LINK_WINDOW = timedelta(minutes=30)
MAX_WORDS = 3
SIGNAL_KEY = "from_signal"

NOT_YOURS = "This signal is not one of yours, so nothing was noted."


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    v = _utc(value)
    return v.isoformat() if v else None


def signal_words(reasons: Any, symbol: str) -> List[str]:
    """The signal's "why" lines that are in the signal language (the user's own signal: his symbol may be named, an area
    as a range; no other number and no call word). A line that fails is left out, never rewritten."""
    if not isinstance(reasons, list):
        return []
    lines = [str(r)[:200] for r in reasons if isinstance(r, (str, int, float)) and str(r).strip()]
    return signal_language.shown_lines(lines, "signal_own", own_symbols=[symbol])[:MAX_WORDS]


def _setup_name(name: str, symbol: str) -> Optional[str]:
    """The setup's name when it passes the signal language too (a name with a price or a call word is not shown)."""
    text = (name or "").strip()[:80]
    if not text or signal_language.check(text, "signal_own", own_symbols=[symbol]):
        return None
    return text


# --- the link from "Trade this setup" ---------------------------------------------------------------------------------

async def link(session: AsyncSession, user: User, signal_id: str, trade_ids: Sequence[int], *,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    """Notes HIS signal on the journal entries of HIS trades just placed from it. Writes the journal rows only."""
    parsed = fno_insights.parse_signal_id(signal_id)
    found = await fno_insights._signal(session, user, *parsed) if parsed else None
    if found is None:
        return {"linked": [], "message": NOT_YOURS}
    dep = found["deployment"]
    symbol = str(dep.symbol if dep is not None else found["symbol"])
    underlying = master.underlying_of(symbol)
    now = now or datetime.now(timezone.utc)
    ids = sorted({int(t) for t in trade_ids})[:MAX_LINK]
    rows = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.id.in_(ids), TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
        TradeRecord.mode == "PAPER", TradeRecord.instrument_kind.in_(FNO_KINDS))))
    fits = [t for t in rows
            if master.underlying_of(t.underlying_symbol or t.symbol) == underlying
            and (_utc(t.entry_time) or now) >= now - LINK_WINDOW]
    if not fits:
        return {"linked": [], "message": None}
    entries = await capture.ensure_entries(session, fits)
    note = {"ref": signal_id, "setup": _setup_name(str(found["setup"] or ""), underlying),
            "words": signal_words(found.get("reasons"), underlying), "at": now.isoformat()}
    linked: List[int] = []
    for t in fits:
        entry = entries.get(t.id)
        if entry is None:
            continue
        plan = capture._loads(entry.plan_json, {})
        if plan.get(SIGNAL_KEY):                # noted once; a later link never overwrites it
            continue
        plan[SIGNAL_KEY] = note
        entry.plan_json = json.dumps(plan, sort_keys=True, default=str)
        linked.append(t.id)
    await session.commit()
    return {"linked": linked, "message": None}


# --- the drafts -------------------------------------------------------------------------------------------------------

async def _proposal_signals(session: AsyncSession, user: User, trades: Sequence[TradeRecord]) -> Dict[int, Dict[str, Any]]:
    """trade id -> {setup, words} from the approved ENTRY proposal that placed it, when that proposal is his own."""
    ids = [t.id for t in trades]
    if not ids:
        return {}
    rows = await session.scalars(select(AiActionRecord).where(
        AiActionRecord.tenant_id == user.tenant_id, AiActionRecord.action == fno_insights.ENTRY, AiActionRecord.trade_id.in_(ids)))
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        found = await fno_insights._signal(session, user, "action", row.id)
        if found is None or row.trade_id is None:
            continue
        under = master.underlying_of(str(found["symbol"] or ""))
        out[int(row.trade_id)] = {"setup": _setup_name(str(found["setup"] or ""), under), "words": signal_words(found.get("reasons"), under)}
    return out


async def _basket_refs(session: AsyncSession, user: User, ids: Sequence[int]) -> Dict[int, str]:
    if not ids:
        return {}
    orders = await session.execute(select(OrderRecord.trade_id, OrderRecord.idempotency_key).where(
        OrderRecord.tenant_id == user.tenant_id, OrderRecord.user_id == user.id, OrderRecord.trade_id.in_(list(ids))))
    out: Dict[int, str] = {}
    for trade_id, key in orders.all():
        ref = basket_ref_of(key, user.id)
        if ref and trade_id is not None:
            out.setdefault(int(trade_id), ref)
    return out


def _leg(t: TradeRecord) -> Dict[str, Any]:
    return {"trade_id": t.id, "symbol": t.symbol, "side": "short" if str(t.direction).upper() == "SHORT" else "long",
            "quantity": float(t.quantity), "lot_size": t.lot_size, "entry_price": float(t.entry_price), "entry_at": _iso(t.entry_time),
            "exit_price": float(t.exit_price) if t.exit_price is not None else None, "exit_at": _iso(t.exit_time),
            "exit_reason": t.exit_reason, "pnl": float(t.pnl) if t.pnl is not None else None, "charges": float(t.charges or 0.0)}


def _origin(t: TradeRecord) -> str:
    return "paper_bot" if t.deployment_id is not None else "you"


async def drafts(session: AsyncSession, user: User, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The journal drafts of his PAPER F&O trades closed in the last DRAFT_DAYS, newest first. Creating a missing
    journal entry from the trade row is the only write (the same rebuild the journal does)."""
    now = now or datetime.now(timezone.utc)
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == "PAPER",
        TradeRecord.instrument_kind.in_(FNO_KINDS), TradeRecord.exit_time.is_not(None),
        TradeRecord.exit_time >= now - timedelta(days=DRAFT_DAYS)).order_by(TradeRecord.exit_time.desc(), TradeRecord.id.desc())
        .limit(MAX_TRADES)))
    if not trades:
        return {"mode": "PAPER", "days": DRAFT_DAYS, "items": []}
    entries = await capture.ensure_entries(session, trades)
    await session.commit()
    proposals = await _proposal_signals(session, user, trades)
    baskets = await _basket_refs(session, user, [t.id for t in trades])
    groups: Dict[str, List[TradeRecord]] = {}
    for t in sorted(trades, key=lambda x: x.id):
        if t.id in baskets:
            key = f"basket:{baskets[t.id]}"
        elif t.leg_group_id:
            key = f"structure:{t.leg_group_id}"
        else:
            key = f"trade:{t.id}"
        groups.setdefault(key, []).append(t)
    items = []
    for key, legs in groups.items():
        items.append(_draft(key, legs, entries, proposals))
    items.sort(key=lambda d: (d["closed_at"] or "", d["key"]), reverse=True)
    return {"mode": "PAPER", "days": DRAFT_DAYS, "items": items}


def _draft(key: str, legs: List[TradeRecord], entries: Dict[int, JournalEntryRecord], proposals: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    first = legs[0]
    underlying = master.underlying_of(first.underlying_symbol or first.symbol)
    signal: Optional[Dict[str, Any]] = None
    for t in legs:
        entry = entries.get(t.id)
        noted = capture._loads(entry.plan_json, {}).get(SIGNAL_KEY) if entry is not None else None
        if isinstance(noted, dict):
            signal = {"setup": noted.get("setup"), "words": [str(w) for w in (noted.get("words") or [])][:MAX_WORDS]}
            break
        if t.id in proposals:
            signal = proposals[t.id]
            break
    pnls = [t.pnl for t in legs]
    total = round(sum(float(p) for p in pnls if p is not None), 2) if all(p is not None for p in pnls) else None
    notes = next((t.notes for t in legs if t.notes), None)
    kind = key.split(":", 1)[0]
    name = ("Basket" if kind == "basket" else (first.option_strategy or "Strategy").replace("_", " ").title() if kind == "structure" else first.symbol)
    from app.signals_feed.service import strategy_name
    placed_by = _origin(first)
    opened = [v for v in (_utc(t.entry_time) for t in legs) if v is not None]
    closed = [v for v in (_utc(t.exit_time) for t in legs) if v is not None]
    return {
        "key": key, "kind": kind, "title": name if len(legs) == 1 or kind == "trade" else f"{name} · {len(legs)} legs",
        "underlying": underlying, "trade_ids": [t.id for t in legs],
        "opened_at": _iso(min(opened, default=None)), "closed_at": _iso(max(closed, default=None)),
        "legs": [_leg(t) for t in legs], "pnl": total, "charges": round(sum(float(t.charges or 0.0) for t in legs), 2),
        "placed_by": placed_by,
        "strategy": _setup_name(strategy_name(first.strategy_id), underlying) if placed_by == "paper_bot" else None,
        "signal": signal, "notes": notes, "status": "noted" if notes else "draft",
    }
