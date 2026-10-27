"""M45 MA-3 (docs/design/M45_MULTI_ACCOUNT.md): multi-account orders on PAPER, through simulated accounts (MB-7).

The user's simulated PAPER accounts (`paper_sub_accounts`) carry a role, a group, an on / off switch and a sizing
setting, as the LIVE registry does. A ticket order sent "multi-account" is:
1. resolved and sized by `fanout_plan` (ENTRY -> Primary only by default, or the per-order override; lots per account,
   rounded down, under one lot skipped with a reason);
2. placed as ONE child order per account through the ordinary ticket path (`fno_ticket.place`: every check, the Phase 0
   PAPER assertion, the user's own risk settings - each child counts as its own entry), each with its own client
   reference (MB-5); the child trade records its simulated account;
3. Secondaries only after the Primary was accepted (MB-5 default for entries): a refused Primary leaves the Secondaries
   unplaced, each with that reason; no automatic rollback of a child that was placed.
Every account gets a result row (placed / refused / skipped) and the decision is audited (MB-8). PAPER only: this module
never reaches a broker. Exits are not sent from here: every child is an ordinary PAPER position whose exit runs as today
(MB-4 - the position's own account).
"""
from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import fanout_plan as fp
from app.audit.log import write_audit_log
from app.db.models import PaperSubAccountRecord, TradeRecord, User

_LABEL = re.compile(r"^[A-Za-z0-9 _-]{1,50}$")


class FanoutRefused(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


async def sub_accounts(session: AsyncSession, user: User) -> List[PaperSubAccountRecord]:
    return list(await session.scalars(select(PaperSubAccountRecord).where(
        PaperSubAccountRecord.tenant_id == user.tenant_id, PaperSubAccountRecord.user_id == user.id)
        .order_by(PaperSubAccountRecord.id)))


def describe(a: PaperSubAccountRecord) -> Dict[str, Any]:
    return {"id": a.id, "label": a.label, "capital": float(a.capital), "role": a.role, "group_name": a.group_name,
            "enabled": a.enabled, "sizing_mode": a.sizing_mode, "sizing_value": a.sizing_value}


async def create(session: AsyncSession, user: User, label: str, capital: float) -> PaperSubAccountRecord:
    label = " ".join((label or "").split())
    if not _LABEL.match(label):
        raise FanoutRefused("An account name is 1-50 letters, digits, spaces, '-' or '_'.", "bad_label")
    if not capital or capital <= 0:
        raise FanoutRefused("The capital is above zero.", "bad_capital")
    if any(a.label.lower() == label.lower() for a in await sub_accounts(session, user)):
        raise FanoutRefused("You already have a simulated account with this name.", "duplicate")
    row = PaperSubAccountRecord(tenant_id=user.tenant_id, user_id=user.id, label=label, capital=capital)
    session.add(row)
    await session.flush()
    await write_audit_log(session, user.tenant_id, user.id, "paper_sub_account_created", f"#{row.id} {label} capital {capital:.2f}")
    await session.commit()
    return row


async def update(session: AsyncSession, user: User, row: PaperSubAccountRecord, *, role: Optional[str] = None,
                 clear_role: bool = False, group_name: Optional[str] = None, enabled: Optional[bool] = None,
                 sizing_mode: Optional[str] = None, sizing_value: Optional[float] = None, confirm: bool = False) -> PaperSubAccountRecord:
    """Role (exactly one PRIMARY per user, moving it needs `confirm`), group (Secondaries only), switch and sizing."""
    from app.accounts.multi import clean_group
    changes: List[str] = []
    if clear_role:
        row.role, row.group_name = None, None
        changes.append("role none")
    elif role is not None:
        role = role.upper()
        if role not in (fp.PRIMARY, fp.SECONDARY):
            raise FanoutRefused("The role is Primary or Secondary.", "bad_role")
        if role == fp.PRIMARY:
            other = next((a for a in await sub_accounts(session, user) if a.role == fp.PRIMARY and a.id != row.id), None)
            if other is not None and not confirm:
                raise FanoutRefused(f"{other.label} is your Primary simulated account. Confirm to make this one Primary; "
                                    f"{other.label} becomes Secondary.", "confirm_primary")
            if other is not None:
                other.role = fp.SECONDARY
                changes.append(f"#{other.id} -> {fp.SECONDARY}")
            row.group_name = None
        row.role = role
        changes.append(f"role {role}")
    if group_name is not None:
        try:
            group = clean_group(group_name)
        except ValueError as exc:
            raise FanoutRefused(str(exc), "bad_group") from None
        if group is not None and row.role != fp.SECONDARY:
            raise FanoutRefused("Only a Secondary account belongs to a group.", "not_secondary")
        row.group_name = group
        changes.append(f"group {group or 'none'}")
    if enabled is not None:
        row.enabled = enabled
        changes.append("on" if enabled else "off")
    if sizing_mode is not None or sizing_value is not None:
        mode = (sizing_mode or row.sizing_mode).upper()
        value = row.sizing_value if sizing_value is None else float(sizing_value)
        if mode not in fp.SIZING_MODES or value <= 0:
            raise FanoutRefused("Sizing is FIXED lots, MULTIPLIER of the Primary's lots or PCT_CAPITAL, with a value above zero.", "bad_sizing")
        row.sizing_mode, row.sizing_value = mode, value
        changes.append(f"sizing {mode} {value:g}")
    await write_audit_log(session, user.tenant_id, user.id, "paper_sub_account_changed", f"#{row.id} {row.label}: {', '.join(changes) or 'no change'}")
    await session.commit()
    return row


def _plan_accounts(rows: List[PaperSubAccountRecord]) -> List[fp.Account]:
    return [fp.Account(id=r.id, role=r.role, group=r.group_name, enabled=r.enabled, capital=float(r.capital)) for r in rows]


async def place(session: AsyncSession, user: User, ask: Any, *, override: Optional[str], price_lookup: Any,
                origin: str) -> Dict[str, Any]:
    """Place one ticket order on the user's simulated PAPER accounts (see the module docstring). `ask`: an
    `fno_ticket.OrderAsk` whose `lots` are the Primary's lots."""
    from app.paper import fno_ticket
    if getattr(ask, "rest_if_waiting", False):
        raise FanoutRefused("A waiting (resting) order goes to one account; send it without multi-account.", "resting_not_supported")
    if not ask.client_ref:
        raise FanoutRefused("A multi-account order needs the ticket's own reference.", "client_ref_required")
    rows = await sub_accounts(session, user)
    by_id = {r.id: r for r in rows}
    try:
        route = fp.resolve(fp.ENTRY, _plan_accounts(rows), override=override)
    except ValueError as exc:
        raise FanoutRefused(str(exc), "bad_override") from None
    if not route.accounts and not route.skipped:
        raise FanoutRefused("No simulated account matches: give one account the Primary role first.", "no_accounts")
    sizing = {r.id: fp.Sizing(r.sizing_mode, r.sizing_value) for r in rows}
    margin_per_lot: Optional[float] = None
    if any(sizing[a.id].mode == fp.PCT_CAPITAL for a in route.accounts):
        # % of capital needs one lot's margin: the ticket's own estimate (none for a sold option or a future on PAPER,
        # where the broker's SPAN is not known - those accounts are then skipped with the reason, never guessed).
        one = await fno_ticket.assess(session, user, dataclasses.replace(ask, lots=1), price_lookup=price_lookup, origin=origin)
        value = (one.get("margin") or {}).get("value")
        margin_per_lot = float(value) if value else None
    plan = fp.size(route, sizing, primary_lots=int(ask.lots), lot_size=1, margin_per_lot=margin_per_lot)
    rows_out: List[Dict[str, Any]] = [{"account_id": s.account_id, "label": by_id[s.account_id].label, "status": "SKIPPED",
                                       "reasons": [s.reason], "trade_id": None, "lots": 0} for s in plan.skipped]
    # The Primary first (MB-5: Secondaries only after the Primary was accepted, for entries).
    legs = sorted(plan.legs, key=lambda leg: by_id[leg.account_id].role != fp.PRIMARY)
    primary_ok: Optional[bool] = None
    for leg in legs:
        row = by_id[leg.account_id]
        if row.role != fp.PRIMARY and primary_ok is False:
            rows_out.append({"account_id": row.id, "label": row.label, "status": "SKIPPED", "lots": leg.lots, "trade_id": None,
                             "reasons": ["the Primary account's order was not accepted"]})
            continue
        child = dataclasses.replace(ask, lots=leg.lots, client_ref=f"{ask.client_ref}-a{row.id}"[:64])
        result = await fno_ticket.place(session, user, child, price_lookup=price_lookup, origin=origin)
        placed = result.get("status") == "PLACED"
        if row.role == fp.PRIMARY:
            primary_ok = placed
        if placed and result.get("trade_id"):
            trade = await session.get(TradeRecord, result["trade_id"])
            if trade is not None and trade.user_id == user.id:
                trade.paper_sub_account_id = row.id
                trade.paper_fanout_ref = ask.client_ref
        rows_out.append({"account_id": row.id, "label": row.label, "status": result.get("status"), "lots": leg.lots,
                         "trade_id": result.get("trade_id"), "reasons": list(result.get("reasons") or [])})
    summary = {"why": route.why, "override": override, "results": rows_out}
    await write_audit_log(session, user.tenant_id, user.id, "paper_fanout_order",
                          json.dumps({"ref": ask.client_ref, "why": route.why,
                                      "results": [(r["account_id"], r["status"], r["lots"]) for r in rows_out]})[:2000])
    await session.commit()
    return summary


async def exit_all(session: AsyncSession, user: User, ref: str) -> Dict[str, Any]:
    """MA-4 / MB-4: close every open child of one multi-account order, on every account that holds it - sold legs first,
    each through the ordinary "Exit now" path; one account's failure never stops the others (ADR-0004). A row per child."""
    from app.execution import approval
    trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.paper_fanout_ref == ref,
        TradeRecord.mode == "PAPER", TradeRecord.exit_time.is_(None))))
    if not trades:
        raise FanoutRefused("No open position of this multi-account order.", "nothing_open")
    trades.sort(key=lambda t: (t.option_position not in ("SELL", "WRITE"), t.id))
    labels = {a.id: a.label for a in await sub_accounts(session, user)}
    rows: List[Dict[str, Any]] = []
    for trade in trades:
        row: Dict[str, Any] = {"trade_id": trade.id, "account_id": trade.paper_sub_account_id,
                               "label": labels.get(trade.paper_sub_account_id or 0), "status": "CLOSED", "reasons": []}
        try:
            outcomes = await approval.exit_now(session, trade, user, via="multi_account")
            if not any(o.closed for o in outcomes):
                row["status"] = "NOT_CLOSED"
                row["reasons"] = [w for o in outcomes for w in o.warnings] or ["could not be closed"]
        except Exception as exc:  # noqa: BLE001 - one account's failure never stops the others' exits
            await session.rollback()
            row["status"], row["reasons"] = "NOT_CLOSED", [str(exc) or type(exc).__name__]
        rows.append(row)
    await write_audit_log(session, user.tenant_id, user.id, "paper_fanout_exit",
                          json.dumps({"ref": ref, "results": [(r["trade_id"], r["status"]) for r in rows]})[:2000])
    await session.commit()
    return {"ref": ref, "results": rows}


__all__ = ["FanoutRefused", "create", "describe", "exit_all", "place", "sub_accounts", "update"]
