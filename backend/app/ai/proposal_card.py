"""PW5: what the proposal card needs beyond the stored row - read-only facts, never an order.

* `mode` - PAPER or LIVE (the deployment's or the position's), so the card can say "Approve (PAPER)". The approval
  itself is unchanged: the approve endpoint still decides LIVE vs PAPER on its own and still asks for the
  authenticator on LIVE (`_action_is_live` in app/ai/routes.py).
* `ticket` - only with the `broker_app_links` flag on (off by default) and only for an EXIT_POSITION whose position is
  still open: the instrument, the closing side and the quantity, plus the broker the position belongs to. The browser
  turns it into a "Trade in your broker app" link (frontend/src/proposals/brokerLinks.ts) for brokers whose link
  format is documented in docs/BROKER_DATA_TERMS.md; the user confirms the order in the broker's own app. ATP sends
  nothing to any broker API here (Phase 0: no order from ATP through a broker API).

Everything is scoped to the caller's organisation; a row of another organisation is never read.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import monitor
from app.core import config as app_config
from app.db.models import AiActionRecord, BrokerAccountRecord, BrokerCredentialRecord, StrategyDeploymentRecord, TradeRecord
from app.platform.controls import flag_enabled

FLAG = "broker_app_links"
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


async def enrich(session: AsyncSession, tenant_id: int, rows: Iterable[AiActionRecord]) -> List[dict]:
    rows = [r for r in rows if r.tenant_id == tenant_id]
    dep_ids: Set[int] = {r.deployment_id for r in rows if r.deployment_id is not None}
    trade_ids: Set[int] = {r.trade_id for r in rows if r.trade_id is not None}
    deployments: Dict[int, StrategyDeploymentRecord] = {d.id: d for d in await session.scalars(
        select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.id.in_(dep_ids)))} if dep_ids else {}
    trades: Dict[int, TradeRecord] = {t.id: t for t in await session.scalars(
        select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.id.in_(trade_ids)))} if trade_ids else {}
    links_on = any(r.action in TICKET_ACTIONS and r.status == "PROPOSED" for r in rows) and await flag_enabled(session, FLAG, tenant_id)
    key = (app_config.KITE_PUBLISHER_API_KEY or "").strip()
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
        out.append(item)
    return out
