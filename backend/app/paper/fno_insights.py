"""F&O workspace 8b, slice 1 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 8b): the read-only ATP-only reads.

Information only. Nothing here places, proposes, blocks or changes an order, and nothing here picks a strike or a trade
for the user: every figure is the user's OWN (his risk settings, his own deployments' strike rules).

* **risk_budget** (8b-d, the risk line): the caller's capital, risk per trade and daily loss limit from his own risk
  settings - the same effective configuration the order ticket checks against (organisation settings, the platform's
  ceilings, the user's own cap). The page compares an order's max loss with them in plain words. The line is a warning
  only: it never changes `can_place`, never refuses an order and never touches an exit (ADR-0004).
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
    from app.core.models import RiskConfig
    from app.platform.controls import clamp_config, risk_ceilings
    from app.risk_engine import by_default
    from app.risk_engine.routes import get_tenant_risk_config
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    cfg, _ = clamp_config(cfg, await risk_ceilings(session))
    if await by_default.enabled(session, user.tenant_id):
        cfg, _ = await by_default.apply_user_cap(session, user, cfg)
    capital = float(cfg.capital)
    return {
        "capital": round(capital, 2),
        "per_trade": {"amount": round(capital * cfg.risk_per_trade_pct / 100.0, 2), "pct": float(cfg.risk_per_trade_pct)},
        "daily": {"amount": round(capital * cfg.max_daily_loss_pct / 100.0, 2), "pct": float(cfg.max_daily_loss_pct)},
        "source": "Your risk settings",
        "blocks": False,                       # information only: the line never refuses an order or an exit
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
                "price": _num(ev.get("proposal_price")), "deployment": dep}
    hist = await session.get(SignalHistoryRecord, num)
    if hist is None or hist.tenant_id != user.tenant_id or hist.user_id != user.id:
        return None
    from app.signals_feed.service import strategy_name
    return {"symbol": hist.symbol, "direction": str(hist.direction or "").upper(), "setup": strategy_name(hist.strategy_id), "price": _num(hist.entry),
            "deployment": None}


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
        entry: Dict[str, Any] = {"id": d.id, "settings": strike_rules.describe(d, side)}
        entry.update(await strike_rules.deployment_legs(session, d, side, float(price), today))
        items.append(entry)
    return {**base, "items": items}
