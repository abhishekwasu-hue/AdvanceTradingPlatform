"""F&O workspace 8b, slice 1 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 8b): the read-only ATP-only reads.

Information only. Nothing here places, proposes, blocks or changes an order, and nothing here picks a strike or a trade
for the user: every figure is the user's OWN (his risk settings, his own deployments' strike rules).

* **risk_budget** (8b-d, the risk line): the caller's capital, risk per trade and daily loss limit from his own risk
  settings - the same effective configuration the order ticket checks against (organisation settings, the platform's
  ceilings, the user's own cap). The page compares an order's max loss with them in plain words. The line is a warning
  only: it never changes `can_place`, never refuses an order and never touches an exit (ADR-0004).
* **paper_balance** (8b-d, slice 4): the PAPER balance the margin estimate is set beside - starting capital plus the net
  P&L of his own closed PAPER trades priced from platform quotes (the account summary's rule), read only (no account is
  created by reading it). The margin his open positions already use is not taken off: ATP does not track it, and the
  line says so.
* **event_risk** (8b-d, slice 4): the scheduled events of the events calendar ATP already keeps - the market events
  (budget, RBI policy, expiry, results, ... - global ones and his organisation's own, the Risk Guardian's table) and,
  for a stock, the company's calendar events (results, board meeting, ... - shared ones and his organisation's own)
  - from today to the contract's expiry. When the calendar holds nothing at all from today on, it says so plainly
  instead of "no events". Each event says what his own event rule does that day (no new entries / a smaller size); the
  line itself never refuses anything.
* **setup_strikes** (8b-a, the setup-aware chain): when the user opens the option chain from one of HIS OWN signals,
  the strikes his own option deployment's rules (strike rule, offset, wing width, expiry rule) work out to at the
  price on screen - the Analyze-a-chart rule (app/chart_analyze/strikes.py), so both say the same. The chain marks them;
  the choice stays his. A teammate's signal or deployment is never used. Text in structure words only (main strike,
  wing); no buy / sell / target / stop wording.
"""
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chart_analyze import strikes as strike_rules
from app.core.enums import InstrumentKind, OptionStrategy
from app.db.models import AiActionRecord, SignalHistoryRecord, StrategyDeploymentRecord, User
from app.instruments import master

SOURCE = "your_settings"
ENTRY = "ENTRY"
MAX_ITEMS = 3
SIDE_OF = {"LONG": "bull_put", "SHORT": "bear_call"}

NOT_YOURS = "This signal is not one of yours, so nothing is marked on the chain."
NO_DIRECTION = "This signal has no side yet, so there are no strikes to mark."
NO_PRICE = "The price is not known yet, so the strikes cannot be worked out."
NONE_FIT = ("None of your option deployments on this symbol fits this signal's side, so nothing is marked. "
            "Strike settings live on the Deployments page.")
NOT_OPTION = "The deployment behind this signal trades the stock or future itself, so it has no strike settings to mark."


# --- 8b-d: the risk line ---------------------------------------------------------------------------------------------

async def risk_budget(session: AsyncSession, user: User) -> Dict[str, Any]:
    """The caller's own capital, risk per trade and daily loss limit (the effective configuration the ticket checks)."""
    from app.platform.controls import clamp_config, risk_ceilings
    from app.risk_engine import by_default
    from app.risk_engine.routes import user_risk_config
    # DEF-0: the trader's own limits only - a limit not set is None (the line says "not set"); the capital is his own,
    # else his PAPER balance.
    cfg = await user_risk_config(session, user)
    cfg, _ = clamp_config(cfg, await risk_ceilings(session))
    if await by_default.enabled(session, user.tenant_id):
        cfg, _ = await by_default.apply_user_cap(session, user, cfg)
    per_trade, daily = cfg.risk_amount(), cfg.daily_loss_amount()
    return {
        "capital": round(float(cfg.capital), 2) if cfg.capital is not None else None,
        "per_trade": ({"amount": round(per_trade, 2), "pct": round(per_trade / float(cfg.capital) * 100.0, 4)}
                      if per_trade is not None and cfg.capital else None),
        "daily": {"amount": round(daily, 2), "pct": float(cfg.max_daily_loss_pct or 0.0)} if daily is not None else None,
        "source": "Your risk settings",
        "paper_balance": await paper_balance(session, user),
        "blocks": False,                       # information only: the line never refuses an order or an exit
    }


async def paper_balance(session: AsyncSession, user: User) -> Dict[str, Any]:
    """His PAPER balance (starting capital + the net P&L of his closed, counted PAPER trades), read only."""
    from sqlalchemy import func

    from app.db.models import TradeRecord
    from app.paper import paper_settings
    start = await paper_settings.capital_of(session, user)      # M68 PR-7 (TS1-065): his own; None = Not set
    if start is None:
        return {"amount": None, "source": "Set your paper starting capital (Settings › Trade settings › Paper account)",
                "open_margin_counted": False, "needs_capital": True}
    net = await session.scalar(select(func.coalesce(func.sum(TradeRecord.pnl), 0.0)).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == "PAPER",
        TradeRecord.exit_time.is_not(None), TradeRecord.price_source.is_(None)))
    return {"amount": round(start + float(net or 0.0), 2), "source": "Your PAPER balance: starting capital plus your closed PAPER trades",
            "open_margin_counted": False}


# --- 8b-d, slice 4: the events between today and the expiry ---------------------------------------------------------

EVENT_DAYS_MAX = 120                       # a contract further out than this is read up to this many days ahead
MAX_EVENTS = 8
MARKET_EVENT_NAMES = {"BUDGET": "Budget", "RBI_POLICY": "RBI policy", "EXPIRY": "Expiry day", "RESULTS": "Results",
                      "FED": "US Fed decision", "ELECTION": "Election", "OTHER": "Market event"}
COMPANY_EVENT_NAMES = {"RESULTS": "Results", "AGM": "AGM", "BOARD_MEETING": "Board meeting", "DIVIDEND": "Dividend", "BONUS": "Bonus",
                       "SPLIT": "Split", "BUYBACK": "Buyback", "RECORD_DATE": "Record date", "INVESTOR_DAY": "Investor day",
                       "PRODUCT_LAUNCH": "Product launch", "REGULATORY_DECISION": "Regulatory decision"}
EVENTS_SOURCE = "Your events calendar"
EMPTY_CALENDAR = ("The events calendar has no entries from today on, so results, RBI policy or budget days are not checked "
                  "here. Events are added on the Risk page.")


def _scope_applies(scope: Optional[str], underlying: str) -> bool:
    from app.risk_engine import guardian
    s = (scope or "").strip().upper()
    if not s or s == "*":
        return True
    if s == guardian.INDEX_BUCKET:
        return guardian.bucket_for(underlying) == guardian.INDEX_BUCKET
    return master.underlying_of(s) == master.underlying_of(underlying)


def _scope_text(scope: Optional[str]) -> str:
    s = (scope or "").strip().upper()
    if not s or s == "*":
        return "all symbols"
    return "all indices" if s == "INDEX" else s


def _rule_text(action: Optional[str], size_cut_pct: Optional[float]) -> str:
    if (action or "").upper() == "BLOCK":
        return "Your event rule that day: no new entries."
    if size_cut_pct is not None:
        return f"Your event rule that day: a smaller size ({size_cut_pct:g}% cut)."
    return "Your event rule that day: a smaller size."


async def event_risk(session: AsyncSession, user: User, underlying: str, until: date, *, today: Optional[date] = None) -> Dict[str, Any]:
    """The calendar's events from today to `until` (the contract's expiry) that apply to this underlying. Reads only."""
    from datetime import timedelta

    from app.db.models import CompanyRecord, EarningsCalendarEventRecord, MarketEventRecord
    from app.fundamentals.visibility import visible
    from app.market_data.calendar import IST
    start = today or datetime.now(IST).date()
    name = master.underlying_of(underlying)
    end = min(max(until, start), start + timedelta(days=EVENT_DAYS_MAX))
    own = (MarketEventRecord.tenant_id.is_(None)) | (MarketEventRecord.tenant_id == user.tenant_id)
    rows = list(await session.scalars(select(MarketEventRecord).where(
        MarketEventRecord.event_date >= start, MarketEventRecord.event_date <= end, own).order_by(MarketEventRecord.event_date, MarketEventRecord.id)))
    events: List[Dict[str, Any]] = []
    for e in rows:
        if not _scope_applies(e.underlying, name):
            continue
        events.append({"date": e.event_date.isoformat(), "kind": e.kind, "name": MARKET_EVENT_NAMES.get((e.kind or "").upper(), "Market event"),
                       "scope": _scope_text(e.underlying), "rule": _rule_text(e.action, e.size_cut_pct), "source": "market"})
    company_ids = list(await session.scalars(select(CompanyRecord.id).where(
        CompanyRecord.symbol == name, visible(CompanyRecord, user.tenant_id))))
    if company_ids:
        for c in await session.scalars(select(EarningsCalendarEventRecord).where(
                EarningsCalendarEventRecord.company_id.in_(company_ids), EarningsCalendarEventRecord.event_date >= start,
                EarningsCalendarEventRecord.event_date <= end, visible(EarningsCalendarEventRecord, user.tenant_id),
        ).order_by(EarningsCalendarEventRecord.event_date, EarningsCalendarEventRecord.id)):
            kind = (c.event_type or "").upper()
            events.append({"date": c.event_date.isoformat(), "kind": kind, "name": COMPANY_EVENT_NAMES.get(kind, "Company event"),
                           "scope": name, "rule": None, "source": "company"})
    events.sort(key=lambda ev: (ev["date"], ev["source"] != "market"))
    calendar_rows = await session.scalar(select(MarketEventRecord.id).where(MarketEventRecord.event_date >= start, own).limit(1))
    if calendar_rows is None and company_ids:
        calendar_rows = await session.scalar(select(EarningsCalendarEventRecord.id).where(
            EarningsCalendarEventRecord.company_id.in_(company_ids), EarningsCalendarEventRecord.event_date >= start,
            visible(EarningsCalendarEventRecord, user.tenant_id)).limit(1))
    return {
        "underlying": name, "from": start.isoformat(), "until": end.isoformat(), "clipped": end < until,
        "events": events[:MAX_EVENTS], "more": max(0, len(events) - MAX_EVENTS),
        "calendar_empty": calendar_rows is None,
        "message": EMPTY_CALENDAR if calendar_rows is None else None,
        "source": EVENTS_SOURCE, "blocks": False,
    }


# --- 8b-a: the setup-aware chain -------------------------------------------------------------------------------------

def parse_signal_id(text: str) -> Optional[Tuple[str, int]]:
    """'action:12' / 'history:7' (the Signals inbox's row ids) -> (kind, id); None for anything else."""
    kind, _, num = (text or "").partition(":")
    if kind not in ("action", "history") or not num.isdigit() or len(num) > 12:
        return None
    return kind, int(num)


def _json(text: Optional[str]) -> Dict[str, Any]:
    try:
        out = json.loads(text or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _json_list(text: Optional[str]) -> List[Any]:
    try:
        out = json.loads(text or "[]")
    except ValueError:
        return []
    return out if isinstance(out, list) else []


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _own_deployment(dep: Optional[StrategyDeploymentRecord], user: User) -> bool:
    """The caller's own deployment: his organisation's, created by him or organisation-wide (no creator) - never a
    teammate's (the ready strategies' rule, FNO-2-9)."""
    return dep is not None and dep.tenant_id == user.tenant_id and dep.created_by in (None, user.id)


async def _signal(session: AsyncSession, user: User, kind: str, num: int) -> Optional[Dict[str, Any]]:
    """The caller's OWN signal (symbol, direction, setup, price, deployment) or None. A proposal counts when it is his
    (`owner_user_id`) or organisation-wide with his own deployment behind it; a generated signal when it is his."""
    if kind == "action":
        row = await session.get(AiActionRecord, num)
        if row is None or row.tenant_id != user.tenant_id or row.action != ENTRY:
            return None
        dep = await session.get(StrategyDeploymentRecord, row.deployment_id) if row.deployment_id is not None else None
        if row.owner_user_id is not None and row.owner_user_id != user.id:
            return None
        if row.deployment_id is not None and not _own_deployment(dep, user):
            return None
        if row.owner_user_id is None and dep is None:
            return None
        ev = _json(row.evidence_json)
        signal = ev.get("signal") if isinstance(ev.get("signal"), dict) else {}
        return {"symbol": str(ev.get("symbol") or ""), "direction": str(ev.get("direction") or "").upper(),
                "setup": str((signal or {}).get("strategy_name") or ev.get("strategy_id") or "Your signal")[:80],
                "price": _num(ev.get("proposal_price")), "deployment": dep, "reasons": (signal or {}).get("reasons")}
    hist = await session.get(SignalHistoryRecord, num)
    if hist is None or hist.tenant_id != user.tenant_id or hist.user_id != user.id:
        return None
    from app.signals_feed.service import strategy_name
    return {"symbol": hist.symbol, "direction": str(hist.direction or "").upper(), "setup": strategy_name(hist.strategy_id), "price": _num(hist.entry),
            "deployment": None, "reasons": _json_list(hist.reasons_json)}


async def _candidates(session: AsyncSession, user: User, underlying: str, side: str,
                      dep: Optional[StrategyDeploymentRecord]) -> List[StrategyDeploymentRecord]:
    """The deployment behind the signal when it is an option one; else the caller's own option deployments on this
    underlying whose structure fits the signal's side (the Analyze-a-chart rule). Never a teammate's."""
    if dep is not None:
        return [dep] if dep.instrument_kind == InstrumentKind.OPTION.value else []
    rows = (await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == user.tenant_id,
        StrategyDeploymentRecord.instrument_kind == InstrumentKind.OPTION.value,
        StrategyDeploymentRecord.status.in_(strike_rules.LIVE_STATUSES),
        or_(StrategyDeploymentRecord.created_by == user.id, StrategyDeploymentRecord.created_by.is_(None)),
    ).order_by(StrategyDeploymentRecord.id))).all()
    out = []
    for row in rows:
        if master.underlying_of(row.symbol) != underlying:
            continue
        try:
            strategy = OptionStrategy(row.option_strategy or "SINGLE")
        except ValueError:
            continue
        if strategy in strike_rules.FITS[side]:
            out.append(row)
    return out[:MAX_ITEMS]


async def setup_strikes(session: AsyncSession, user: User, signal_id: str, *, spot: Optional[float],
                        today: Optional[date] = None) -> Dict[str, Any]:
    """The strikes the caller's own deployment rules work out to for one of HIS signals, at `spot` (the chain's price;
    else the signal's own price). Reads only."""
    base: Dict[str, Any] = {"source": SOURCE, "signal": signal_id, "underlying": None, "setup": None, "side": "none",
                            "reference": None, "items": [], "message": None}
    parsed = parse_signal_id(signal_id)
    found = await _signal(session, user, *parsed) if parsed else None
    if found is None:
        return {**base, "message": NOT_YOURS}
    dep: Optional[StrategyDeploymentRecord] = found["deployment"]
    underlying = master.underlying_of(dep.symbol if dep is not None else found["symbol"])
    base.update(underlying=underlying, setup=found["setup"])
    side = SIDE_OF.get(found["direction"])
    if side is None:
        return {**base, "message": NO_DIRECTION}
    base["side"] = side
    if dep is not None and dep.instrument_kind != InstrumentKind.OPTION.value:
        return {**base, "message": NOT_OPTION}
    price, text = (spot, "the current price") if spot and spot > 0 else (found["price"], "the signal's price")
    if price is None:
        return {**base, "message": NO_PRICE}
    base["reference"] = {"text": text, "price": round(float(price), 2)}
    deps = await _candidates(session, user, underlying, side, dep)
    if not deps:
        return {**base, "message": NONE_FIT}
    from app.market_data.calendar import IST
    today = today or datetime.now(IST).date()
    items = []
    for d in deps:
        # 8b-b "Trade this setup": his own lots and sold-leg premium stop from the same deployment (None = not set: the
        # ticket / basket then starts from his own defaults or asks him; nothing is made up here).
        entry: Dict[str, Any] = {"id": d.id, "settings": strike_rules.describe(d, side),
                                 "lots": int(d.max_lots) if d.max_lots else None,
                                 "stop_pct": float(d.premium_stop_pct) if d.premium_stop_pct is not None else None}
        entry.update(await strike_rules.deployment_legs(session, d, side, float(price), today))
        items.append(entry)
    return {**base, "items": items}
