"""M45 MA-1 (docs/design/M45_MULTI_ACCOUNT.md): the user's own broker accounts as Primary / Secondary.

MB-0 / G1: only accounts the asking user owns (`broker_accounts.user_id`) take part - never an account another member of
the organisation linked. MB-1 / M45-1: exactly one PRIMARY per user across all brokers; making another account PRIMARY
needs an explicit confirmation and turns the old one SECONDARY. Secondaries may carry a user-named group.

M45-3 / G3: no account gets a role while the user still holds an open LIVE trade with no recorded account (its exit
could not be routed to a known account once several accounts trade); the open trades are listed for him. Exits are
never blocked by this - only the registry change waits.

The registry only: nothing here routes, sizes or places an order (MA-2 .. MA-6). Every change is written to the audit log.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import as_dict, credential_for_account, list_accounts
from app.audit.log import write_audit_log
from app.db.models import BrokerAccountRecord, TradeRecord, User

FLAG = "multi_account"
PAGE_FLAG = "ui_v2"
PRIMARY = "PRIMARY"
SECONDARY = "SECONDARY"
ROLES = (PRIMARY, SECONDARY)
_GROUP = re.compile(r"^[A-Za-z0-9 _-]{1,50}$")


class RegistryRefused(ValueError):
    """A registry change the rules refuse; `code` names the rule for the client, `extra` carries what it needs."""

    def __init__(self, message: str, code: str, extra: Optional[Dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra or {}


async def own_accounts(session: AsyncSession, user: User) -> List[BrokerAccountRecord]:
    """The caller's own accounts (G1), Primary first."""
    mine = [a for a in await list_accounts(session, user.tenant_id) if a.user_id == user.id]
    return sorted(mine, key=lambda a: (a.role != PRIMARY, a.role != SECONDARY, a.broker_name, a.id))


async def unaccounted_live_trades(session: AsyncSession, user: User) -> List[TradeRecord]:
    """The user's open LIVE trades with no recorded broker account (M45-3)."""
    return list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.mode == "LIVE",
        TradeRecord.exit_time.is_(None), TradeRecord.broker_account_id.is_(None)).order_by(TradeRecord.id)))


def clean_group(name: Optional[str]) -> Optional[str]:
    if name is None or not name.strip():
        return None
    name = " ".join(name.split())
    if not _GROUP.match(name):
        raise RegistryRefused("A group name is 1-50 letters, digits, spaces, '-' or '_'.", "bad_group")
    return name


async def set_role(session: AsyncSession, user: User, account: BrokerAccountRecord, role: Optional[str], *,
                   confirm: bool = False) -> BrokerAccountRecord:
    """Give one of the user's own accounts a role (or clear it with None)."""
    if account.user_id != user.id:
        raise RegistryRefused("This account was linked by another member; only your own accounts can take a role.", "not_owner")
    if role is not None and role not in ROLES:
        raise RegistryRefused("The role is Primary or Secondary.", "bad_role")
    if role is not None:
        blocking = await unaccounted_live_trades(session, user)
        if blocking:
            raise RegistryRefused(
                "You hold open LIVE positions with no broker account recorded. Close or reconcile them first; "
                "their exits keep running as today.", "unaccounted_live_trades",
                {"trades": [{"id": t.id, "symbol": t.symbol} for t in blocking]})
    old_primary: Optional[BrokerAccountRecord] = None
    if role == PRIMARY:
        old_primary = next((a for a in await own_accounts(session, user) if a.role == PRIMARY and a.id != account.id), None)
        if old_primary is not None and not confirm:
            raise RegistryRefused("You already have a Primary account. Confirm to make this one Primary instead; "
                                  "the current one becomes Secondary.", "confirm_primary",
                                  {"current_primary": {"id": old_primary.id, "name": _name(old_primary)}})
        if old_primary is not None:
            old_primary.role = SECONDARY
            await write_audit_log(session, user.tenant_id, user.id, "broker_account_role",
                                  f"#{old_primary.id} {_name(old_primary)}: {PRIMARY} -> {SECONDARY} (Primary moved to #{account.id})")
    previous = account.role
    account.role = role
    if role != SECONDARY:
        account.group_name = None
    await write_audit_log(session, user.tenant_id, user.id, "broker_account_role",
                          f"#{account.id} {_name(account)}: {previous or 'none'} -> {role or 'none'}")
    await session.commit()
    return account


async def set_group(session: AsyncSession, user: User, account: BrokerAccountRecord, name: Optional[str]) -> BrokerAccountRecord:
    """Name (or clear) the group of one of the user's own Secondary accounts."""
    if account.user_id != user.id:
        raise RegistryRefused("This account was linked by another member; only your own accounts can be grouped.", "not_owner")
    group = clean_group(name)
    if group is not None and account.role != SECONDARY:
        raise RegistryRefused("Only a Secondary account belongs to a group.", "not_secondary")
    previous = account.group_name
    account.group_name = group
    await write_audit_log(session, user.tenant_id, user.id, "broker_account_group",
                          f"#{account.id} {_name(account)}: {previous or 'none'} -> {group or 'none'}")
    await session.commit()
    return account


async def describe(session: AsyncSession, account: BrokerAccountRecord) -> dict:
    """One registry row: the account, its role / group, and its broker session state (MB-1: token expiry, re-login)."""
    record = await credential_for_account(session, account)
    row = as_dict(account, token_status=record.token_status if record is not None else "MISSING")
    row.update({
        "role": account.role, "group_name": account.group_name, "enabled": account.status == "ACTIVE",
        "token_expires_at": record.token_expires_at.isoformat() if record is not None and record.token_expires_at else None,
    })
    return row


def _name(account: BrokerAccountRecord) -> str:
    return account.display_name or f"{account.broker_name}/{account.account_label}"


__all__ = ["FLAG", "PAGE_FLAG", "PRIMARY", "ROLES", "RegistryRefused", "SECONDARY", "clean_group", "describe",
           "own_accounts", "set_group", "set_role", "unaccounted_live_trades"]
