"""PW5: what the proposal card needs beyond the stored row - read-only facts, never an order.

* `mode` - PAPER or LIVE (the deployment's or the position's), so the card can say "Approve (PAPER)". The approval
  itself is unchanged: the approve endpoint still decides LIVE vs PAPER on its own and still asks for the
  authenticator on LIVE (`_action_is_live` in app/ai/routes.py).
* `ticket` - only with the `broker_app_links` flag on (off by default) and only for an EXIT_POSITION whose position is
  still open: the instrument, the closing side and the quantity, plus the broker the position belongs to. The browser
  turns it into a "Trade in your broker app" link (frontend/src/proposals/brokerLinks.ts) for brokers whose link
  format is documented in docs/BROKER_DATA_TERMS.md; the user confirms the order in the broker's own app. ATP sends
  nothing to any broker API here (Phase 0: no order from ATP through a broker API).

* `risk_check` - Z5 / P1-d, only with the `risk_by_default` flag on, only for a waiting ENTRY proposal and only for its
  owner (whose PAPER account the approved order goes to; others see none): what the owner's risk by default makes of it (`app/paper/ticket.py::assess`) - the quantity their cap allows at the
  proposal price, the risk at the stop, the cap that binds, the cost line (charges, slippage estimate, break-even move,
  reward:risk after costs), and why it would be refused now (daily loss lockout, entries per day, the guardian, the
  India VIX gate). Approve places the PAPER order sized the same way (the pipeline applies the same cap).
* `portfolio_impact` - N1 F3, only with the `portfolio_risk` flag on, only for a waiting ENTRY proposal and only for its
  owner: the owner's own PAPER book after this entry taken at its most (its bucket's exposure and open risk, all open
  risk, open positions, each against the owner's limit), with one line of text. Numbers only, never advice.
* `strike_breach` - Z5 (Trade `strike_breach_model.py`), with the same flag: each short strike's chance of a breach
  before expiry, when the proposal carries option legs (`evidence.strike_breach`, computed when it was raised).

Everything is scoped to the caller's organisation; a row of another organisation is never read.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import monitor
from app.core import config as app_config
from app.db.models import AiActionRecord, BrokerAccountRecord, BrokerCredentialRecord, StrategyDeploymentRecord, TradeRecord, User
from app.platform.controls import flag_enabled

FLAG = "broker_app_links"
RISK_FLAG = "risk_by_default"
TICKET_ACTIONS = ("EXIT_POSITION",)          # PAUSE / REDUCE / REVIEW concern no tradable instrument


def _side_to_close(direction: str) -> Optional[str]:
    return {"LONG": "SELL", "SHORT": "BUY"}.get((direction or "").upper())


def _whole(quantity: float) -> Optional[int]:
    return int(quantity) if quantity and quantity > 0 and float(quantity).is_integer() else None


async def _tenant_broker(session: AsyncSession, tenant_id: int) -> Optional[str]:
    """The organisation's single broker (its default account, else its only credential) - names only, no secrets."""
    account = await session.scalar(select(BrokerAccountRecord).where(BrokerAccountRecord.tenant_id == tenant_id, BrokerAccountRecord.is_default.is_(True))
                                   .order_by(BrokerAccountRecord.id))
    if account is not None:
        return account.broker_name
    names = set(await session.scalars(select(BrokerCredentialRecord.broker_name).where(BrokerCredentialRecord.tenant_id == tenant_id)))
    return next(iter(names)) if len(names) == 1 else None


async def _broker_of(session: AsyncSession, tenant_id: int, trade: TradeRecord, deployment: Optional[StrategyDeploymentRecord]) -> Optional[str]:
    if trade.broker_account_id is not None:
        account = await session.get(BrokerAccountRecord, trade.broker_account_id)
        if account is not None and account.tenant_id == tenant_id:
            return account.broker_name
    if deployment is not None and deployment.broker_name:
        return deployment.broker_name
    return await _tenant_broker(session, tenant_id)


def ticket_for(trade: TradeRecord, broker: Optional[str], publisher_api_key: str) -> Optional[dict]:
    """The facts of the closing order for an open position, or None when there is nothing tradable to pre-fill."""
    side, quantity = _side_to_close(trade.direction), _whole(trade.quantity)
    if trade.exit_time is not None or side is None or quantity is None:
        return None
    broker = (broker or "").lower() or None
    return {"broker": broker, "exchange": (trade.exchange or "NSE").upper(), "symbol": trade.symbol, "side": side, "quantity": quantity,
            "instrument_kind": trade.instrument_kind or "UNDERLYING",
            # The Kite Publisher key is a public, per-app identifier (it sits in the page's HTML form); never a secret.
            "publisher_api_key": (publisher_api_key or None) if broker == "zerodha" else None}


async def _risk_check(session: AsyncSession, user: User, evidence: dict) -> Optional[dict]:
    """The caller's risk by default applied to a waiting entry proposal, or None when its levels are incomplete."""
    from app.paper import ticket
    try:
        plan = ticket.Plan(symbol=str(evidence["symbol"]), direction=str(evidence["direction"]), setup="OTHER",
                           entry=float(evidence["proposal_price"]), stop_loss=float(evidence["stop_loss"]), target=float(evidence["target1"]))
    except (KeyError, TypeError, ValueError):
        return None
    view = await ticket.assess(session, user, plan)
    return {k: view[k] for k in ("sizing", "costs", "refusals", "warnings", "errors", "reward_risk", "min_reward_risk")}


async def enrich(session: AsyncSession, tenant_id: int, rows: Iterable[AiActionRecord], user: Optional[User] = None) -> List[dict]:
    rows = [r for r in rows if r.tenant_id == tenant_id]
    dep_ids: Set[int] = {r.deployment_id for r in rows if r.deployment_id is not None}
    trade_ids: Set[int] = {r.trade_id for r in rows if r.trade_id is not None}
    deployments: Dict[int, StrategyDeploymentRecord] = {d.id: d for d in await session.scalars(
        select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.id.in_(dep_ids)))} if dep_ids else {}
    trades: Dict[int, TradeRecord] = {t.id: t for t in await session.scalars(
        select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.id.in_(trade_ids)))} if trade_ids else {}
    links_on = any(r.action in TICKET_ACTIONS and r.status == "PROPOSED" for r in rows) and await flag_enabled(session, FLAG, tenant_id)
    key = (app_config.KITE_PUBLISHER_API_KEY or "").strip()
    risk_on = user is not None and user.tenant_id == tenant_id and any(r.action == "ENTRY" for r in rows) and await flag_enabled(session, RISK_FLAG, tenant_id)
    portfolio_on = (user is not None and user.tenant_id == tenant_id and any(r.action == "ENTRY" and r.status == "PROPOSED" for r in rows)
                    and await flag_enabled(session, "portfolio_risk", tenant_id))
    out: List[dict] = []
    for row in rows:
        item = monitor.as_dict(row)
        dep = deployments.get(row.deployment_id) if row.deployment_id is not None else None
        trade = trades.get(row.trade_id) if row.trade_id is not None else None
        live = (dep is not None and dep.mode == "LIVE") or (trade is not None and trade.mode == "LIVE")
        item["mode"] = "LIVE" if live else ("PAPER" if dep is not None or trade is not None else None)
        if row.action == "ENTRY":
            item["mode"] = "PAPER"           # Z3 / BT2: an approved entry proposal always places a PAPER order (Phase 0)
        item["ticket"] = None
        if links_on and row.action in TICKET_ACTIONS and row.status == "PROPOSED" and trade is not None:
            item["ticket"] = ticket_for(trade, await _broker_of(session, tenant_id, trade, dep), key)
        item["risk_check"] = None
        item["strike_breach"] = None
        item["portfolio_impact"] = None
        if portfolio_on and row.action == "ENTRY" and row.status == "PROPOSED" and user is not None \
                and getattr(row, "owner_user_id", None) in (None, user.id):
            from app.portfolio import risk_text
            ev = item["evidence"]
            item["portfolio_impact"] = await risk_text.proposal_impact(session, user, str(ev.get("symbol") or ""), ev.get("proposal_price"),
                                                                       ev.get("stop_loss"), quantity=(ev.get("ticket") or {}).get("max_quantity"))
        if risk_on and row.action == "ENTRY" and user is not None:
            item["strike_breach"] = item["evidence"].get("strike_breach")
            owner_id = getattr(row, "owner_user_id", None)
            if row.status == "PROPOSED" and (owner_id is None or owner_id == user.id):
                # The owner's own limits: the approved order is placed for (and sized by) the proposal's owner.
                item["risk_check"] = await _risk_check(session, user, item["evidence"])
        out.append(item)
    if risk_on:
        await session.commit()                 # a daily loss lockout seen for the first time is recorded
    return out
