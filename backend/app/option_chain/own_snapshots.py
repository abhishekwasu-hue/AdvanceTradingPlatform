"""B1 (bug lane, UI-H Part 1): with the own-broker data tier (`byob_data_tier`) on, the user's own option-chain reads
are HIS session snapshots.

Data terms (TM-7, RDY-6): a chain read through one person's broker session is that person's. Each chain the
Option chain analyses read through the user's OWN session is stored in the tenant-private chain history
(`md_tenant_chain_snapshots`, app/market_lake/chain_store.py) with the holder in the row's source
(`broker:<name>:u<id>`, HB2's rule). Only that user reads them back (`chain_store.holder_day`): never another member of
his organisation, never another organisation, never the shared OI collector's slots. Retention is the chain history's
own (`RETENTION_CHAIN_HISTORY_DAYS`, app/retention).

One stored chain per (underlying, expiry, OI_BANNER_SLOT_MINUTES slot) - the OI collector's cadence the analyses used
before; a second read in the same slot is not stored again. Nothing here reaches an order path (ADR-0006).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChain
from app.core import config
from app.db.models import BrokerCredentialRecord, User
from app.market_lake import chain_store, intraday_history
from app.option_chain.snapshots import IST, SlotChain

logger = logging.getLogger(__name__)


def _expiry(chain: OptionChain) -> Optional[date]:
    try:
        return date.fromisoformat((chain.expiry or "")[:10])
    except ValueError:
        return None


async def record(session: AsyncSession, user: User, credential: BrokerCredentialRecord, underlying: str, chain: OptionChain,
                 now: datetime) -> str:
    """Stores `chain` of `underlying`, just read through `credential`, as `user`'s session snapshot and commits. Only a credential the
    user linked himself is stored (his own token); anything else, or a chain without an expiry or strikes, is skipped.
    Returns the write status (OK / ALREADY_EXISTS / EMPTY / SKIPPED). A storage failure never fails the caller's read;
    callers call this LAST (a rollback expires the session's loaded rows)."""
    expiry = _expiry(chain)
    tenant_id = user.tenant_id                    # read before any rollback (it expires the loaded rows)
    if credential.user_id != user.id or credential.tenant_id != user.tenant_id or expiry is None or not chain.rows:
        return "SKIPPED"
    try:
        result = await chain_store.write(session, user.tenant_id, underlying, expiry, chain, now,
                                         slot_minutes=config.OI_BANNER_SLOT_MINUTES,
                                         source=intraday_history.holder_source(credential.broker_name, user.id))
        await session.commit()
        return result.status
    except Exception as exc:  # noqa: BLE001 - the page's answer does not depend on the snapshot being stored
        await session.rollback()
        logger.info("Own chain snapshot not stored for tenant %s: %s", tenant_id, type(exc).__name__)
        return "SKIPPED"


async def day(session: AsyncSession, user: User, underlying: str, on: date) -> List[chain_store.StoredChain]:
    """The user's own stored chains of `underlying` on `on` (every expiry), oldest first."""
    return await chain_store.holder_day(session, user.tenant_id, user.id, underlying, on)


def one_expiry(stored: List[chain_store.StoredChain], on: date) -> List[SlotChain]:
    """The OI banner's day view from the user's own chains: one chain per slot, all of ONE expiry (the nearest one on or
    after `on` that was stored; else the latest stored), oldest first."""
    if not stored:
        return []
    expiries = sorted({s.expiry for s in stored})
    ahead = [e for e in expiries if e >= on]
    chosen = ahead[0] if ahead else expiries[-1]
    by_slot: Dict[datetime, chain_store.StoredChain] = {}
    for s in stored:
        if s.expiry == chosen:
            by_slot[s.slot_start] = s
    return [SlotChain(k, by_slot[k].captured_at, by_slot[k].chain()) for k in sorted(by_slot)]


def baselines(slots: List[SlotChain]) -> Dict[float, Tuple[Optional[float], Optional[float]]]:
    """The first OI seen per strike in the user's own slots of the day (the OI-wall baseline)."""
    out: Dict[float, Tuple[Optional[float], Optional[float]]] = {}
    for s in slots:
        for r in s.chain.rows:
            out.setdefault(float(r.strike), (r.call_oi, r.put_oi))
    return out


def today(now: datetime) -> date:
    return now.astimezone(IST).date()


__all__ = ["record", "day", "one_expiry", "baselines", "today"]
