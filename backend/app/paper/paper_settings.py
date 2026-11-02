"""M68 PR-7 (owner order M68, TS-1 rows TS1-064 to 071, owner decision D4 - provisional default M68Q-4): the user's own
Paper account settings - the starting capital and the charges model - read for every paper fill, cost line and backtest.

The rule (M68, binding): ATP never pre-chooses a starting capital, a brokerage or a slippage. Each is the user's own:

* **Starting capital** - his paper account's own (`paper_accounts.starting_capital`, with `capital_saved` = he chose it),
  else his Settings > Trade settings > Paper account > Starting capital (the value a new account starts with). Not set:
  a new paper account has no capital; the first paper order ASKS for it in the ticket ("Save as my default" next to the
  field, TS-3) and every automated paper entry places nothing and records "Needs your setting: Starting capital". A
  figure measured as a share of the capital (a % risk cap, a % daily loss, a drawdown %) says "needs your capital"
  instead of using a basis nobody chose. Existing accounts keep their stored capital (TS-6), so every balance stays.
* **Charges** - Not set = statutory charges only (STT, exchange, stamp duty, GST, SEBI fee: set by law, a fact), no
  brokerage and no slippage, labelled so on the screen. "Statutory charges + my brokerage" adds his own brokerage per
  order; his own slippage (each side) moves a paper fill when he saved one. Practising for a broker account keeps that
  broker's stated brokerage (app/brokers/broker_charges.py, a broker fact).

A closed trade keeps the charges stored at its close (`trades.charges`), so a past P&L never changes with a setting.
Nothing here places, changes or closes an order. Exits are never blocked (ADR-0004): no exit path reads the capital.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaperAccountRecord, User, UserPrefsRecord
from app.execution.paper_charges import BROKERAGE_KEY, CHARGES_KEY, SLIPPAGE_KEY, STATUTORY_ONLY, WHERE, WITH_BROKERAGE, PaperCharges, charges_from
from app.execution.paper_charges import number as _number
from app.me import trade_settings

SECTION = "paper_account"
CAPITAL_KEY = f"{SECTION}.capital"
NEEDS_CAPITAL_WORDS = "needs your capital"


def needs_capital() -> str:
    """The plain-words refusal / skip reason for a paper entry while the starting capital is Not set."""
    from app.execution.order_defaults import NEEDS_PREFIX
    return f"{NEEDS_PREFIX}{trade_settings.BY_KEY[CAPITAL_KEY].label} ({WHERE})"


def is_capital_refusal(reason: str) -> bool:
    return reason.startswith(needs_capital().split(" (")[0])


def capital_from(values: dict[str, Any]) -> float | None:
    capital = _number(values.get(CAPITAL_KEY))
    return capital if capital is not None and capital > 0 else None


async def values_of(session: AsyncSession, tenant_id: int | None, user_id: int | None) -> dict[str, Any]:
    """The user's saved Trade settings (cleaned), {} for no user. Reads only."""
    if tenant_id is None or user_id is None:
        return {}
    raw = await session.scalar(select(UserPrefsRecord.value_json).where(
        UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id,
        UserPrefsRecord.namespace == trade_settings.NAMESPACE))
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return trade_settings.clean(data.get("values")) if isinstance(data, dict) else {}


async def charges_for(session: AsyncSession, user: User | None) -> PaperCharges:
    if user is None:
        return STATUTORY_ONLY
    return charges_from(await values_of(session, user.tenant_id, getattr(user, "id", None)))


async def charges_of_ids(session: AsyncSession, tenant_id: int | None, user_id: int | None) -> PaperCharges:
    return charges_from(await values_of(session, tenant_id, user_id))


async def saved_capital(session: AsyncSession, user: User) -> float | None:
    """His Trade settings starting capital (the value a new paper account starts with), None = Not set."""
    return capital_from(await values_of(session, user.tenant_id, user.id))


async def capital_of(session: AsyncSession, user: User | None) -> float | None:
    """The capital his paper figures are measured from: his paper account's own, else his saved Trade settings value;
    None = Not set (never a value ATP chose). Reads only - no account is created."""
    if user is None or getattr(user, "id", None) is None:
        return None
    row = await session.scalar(select(PaperAccountRecord.starting_capital).where(
        PaperAccountRecord.tenant_id == user.tenant_id, PaperAccountRecord.user_id == user.id))
    if row is not None and float(row) > 0:
        return float(row)
    return await saved_capital(session, user)


async def capital_refusal(session: AsyncSession, user: User | None) -> str | None:
    """Why a NEW paper entry waits for the starting capital (None: it does not). Only while the organisation has the paper
    account (flag `paper_account`): the balance, the drawdown and the ladder are measured from this capital. Exits
    never come here (ADR-0004)."""
    if user is None or getattr(user, "id", None) is None:
        return None
    from app.paper.service import enabled
    if not await enabled(session, user.tenant_id):
        return None
    return None if await capital_of(session, user) is not None else needs_capital()


__all__ = ["PaperCharges", "STATUTORY_ONLY", "charges_from", "capital_from", "charges_for", "charges_of_ids", "saved_capital",
           "capital_of", "capital_refusal", "backtest_inputs", "needs_capital", "is_capital_refusal", "values_of", "SECTION", "WHERE",
           "CAPITAL_KEY", "CHARGES_KEY", "BROKERAGE_KEY", "SLIPPAGE_KEY", "WITH_BROKERAGE", "NEEDS_CAPITAL_WORDS"]


async def backtest_inputs(session: AsyncSession, user: User | None, risk: Any) -> tuple[Any, PaperCharges]:
    """TS1-066 (parity: backtest = live paper): a backtest's risk config with HIS capital (his own `risk.capital` first,
    else his paper account's / saved Trade settings value; Not set stays None - never a capital ATP chose) and his paper
    charges (Not set: statutory charges only)."""
    return risk.with_capital(await capital_of(session, user)), await charges_for(session, user)
