"""Owner order (23 Oct) "the scan universe is never empty": the lists Find setups can scan, and how a larger one is run.

Routes (behind `screener_v2` like the rest of the screener; the page shows the picker only with `ui_v2` on as well):
- `GET /api/screener/universes`        the lists a scan can run on - the system watchlists' index-stock, sector and F&O
                                       lists (app/watchlists/system.py, shared reference data) - the default key
                                       ("index-nifty50") and the batch plan for this organisation's broker;
- `GET /api/screener/universes/{key}`  one list's NSE symbols (at most MAX_UNIVERSE; the answer says when it was cut).

NIFTY 50 when the system lists are not built yet: the current NIFTY 50 membership the universe sync stores
(`index_membership` + `securities`), when it has rows. With neither, the page uses its own names-only fallback list.

Batches (the run route stays at MAX_SYMBOLS a call): a larger universe is sent as consecutive `/run` calls with
`batch: true`. Every broker call of such a run takes a token from the credential's shared API budget
(app/market_data/api_budget.py - the same budget charts draw from, a share of app/brokers/rate_budget.py's conservative
limits), so a batch can never spend past it; a symbol that gets no token is reported "budget used" and the rest still
run. The plan here sizes a batch to what the per-second share fetches in about BATCH_SECONDS, and the page waits
between batches so their broker calls stay inside the per-minute share (`per_minute`).

Shared reference data and this organisation's broker name only: nothing of another organisation, nothing places an order.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import BrokerCredentialRecord, IndexMembershipRecord, SecurityRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/screener/universes", tags=["screener"])
FLAG = "screener_v2"
DEFAULT_KEY = "index-nifty50"
DEFAULT_INDEX_CODE = "NIFTY50"
DEFAULT_NAME = "NIFTY 50 stocks"
# The groups a scan runs on: stock lists. The "Indices" list (index instruments, NSE and BSE) is left out.
GROUPS = ("INDEX_STOCKS", "SECTORS", "FNO")
MAX_UNIVERSE = 500                 # NIFTY 500; a longer list (NIFTY TOTAL MARKET) is cut to its first 500, said so
BATCH_MAX = 50                     # = app.screener.routes.MAX_SYMBOLS (the run route's cap per call)
BATCH_MIN = 5
BATCH_SECONDS = 10                 # a batch is what the per-second share fetches in about this long


def batch_plan(broker: Optional[str]) -> Dict[str, Any]:
    """How a universe larger than one run is sent for this broker: the batch size and the API's per-minute share the
    page paces the batches by (a cached symbol costs no call; the page waits only for the calls a batch made)."""
    from app.market_data.api_budget import api_limits
    limits = api_limits(broker or "")
    size = max(BATCH_MIN, min(BATCH_MAX, int(limits.per_second * BATCH_SECONDS)))
    return {"size": size, "per_minute": limits.per_minute, "per_second": limits.per_second, "max_symbols": MAX_UNIVERSE}


def batch_wait_seconds(broker: str, symbols: int) -> float:
    """How long one batch run may wait for its tokens in all: the per-second share for this many symbols, plus room."""
    from app.market_data.api_budget import api_limits
    return float(math.ceil(symbols / max(1, api_limits(broker).per_second)) + 2)


async def _broker_name(session: AsyncSession, tenant_id: int, user: Optional[User] = None) -> Optional[str]:
    """The broker this organisation's runs read through. SCR-FIX A8: the credential a run itself picks - the user's own
    session with bring-your-own-broker on, else `_pick_record(..., "primary")` as `fetch_frames` does - not the first
    stored credential by name; that one only when no session is usable now (None when there is no credential at all)."""
    from app.market_data import live_access
    from app.market_data.candles_routes import _pick_record
    if user is not None and await live_access.byob_on(session, tenant_id):
        own = await live_access.own_session_record(session, user)
        if own is not None:
            return str(own.broker_name)
    else:
        try:
            if user is not None:                          # M71Q-2: the run reads through the reader's own credential only
                from app.market_data.reader_credential import credential_for_reader
                return str((await credential_for_reader(session, tenant_id, user.id)).broker_name)
            return str((await _pick_record(session, tenant_id, None, "primary")).broker_name)
        except HTTPException:
            pass                                          # no usable session now: the stored credential below
    return await session.scalar(select(BrokerCredentialRecord.broker_name).where(BrokerCredentialRecord.tenant_id == tenant_id)
                                .order_by(BrokerCredentialRecord.broker_name, BrokerCredentialRecord.account_label).limit(1))


async def _membership_symbols(session: AsyncSession, index_code: str) -> List[str]:
    """The index's current members as NSE symbols (largest weight first), from the universe sync's tables."""
    rows = (await session.execute(select(SecurityRecord.symbol, IndexMembershipRecord.weight)
                                  .join(SecurityRecord, SecurityRecord.isin == IndexMembershipRecord.isin)
                                  .where(IndexMembershipRecord.index_code == index_code, IndexMembershipRecord.valid_to.is_(None),
                                         SecurityRecord.status == "ACTIVE"))).all()
    ordered = sorted(rows, key=lambda r: (r[1] is None, -(r[1] or 0.0), r[0]))
    return list(dict.fromkeys(str(symbol).upper() for symbol, _ in ordered if symbol))


async def catalogue(session: AsyncSession) -> List[Dict[str, Any]]:
    """The groups of lists a scan can run on, in the system lists' display order; NIFTY 50 first among the index stocks.
    Without a built NIFTY 50 system list, the stored NIFTY 50 membership stands in (when the sync has it)."""
    from app.watchlists import system
    rows = [r for r in await system.list_all(session) if r.group in GROUPS]
    groups: List[Dict[str, Any]] = []
    for group in GROUPS:
        lists = [{"key": r.key, "name": r.name, "count": min(int(r.count or 0), MAX_UNIVERSE)} for r in rows if r.group == group]
        if group == "INDEX_STOCKS":
            if not any(item["key"] == DEFAULT_KEY for item in lists):
                members = await _membership_symbols(session, DEFAULT_INDEX_CODE)
                if members:
                    lists.insert(0, {"key": DEFAULT_KEY, "name": f"{DEFAULT_NAME} ({len(members)})", "count": len(members)})
            lists.sort(key=lambda item: item["key"] != DEFAULT_KEY)              # NIFTY 50 first; the rest keep their order
        if lists:
            groups.append({"group": group, "label": system.GROUP_LABELS[group], "lists": lists})
    return groups


async def symbols_of(session: AsyncSession, key: str) -> Optional[Dict[str, Any]]:
    """One list's NSE symbols ({key, name, symbols, truncated, source_count}), or None when there is no such list."""
    from app.watchlists import system
    row = await system.get(session, key)
    if row is not None and row.group in GROUPS:
        all_symbols = list(dict.fromkeys(i["symbol"].upper() for i in system.watch_items(row) if i["exchange"] == "NSE"))
        name = row.name
    elif key == DEFAULT_KEY:
        all_symbols = await _membership_symbols(session, DEFAULT_INDEX_CODE)
        if not all_symbols:
            return None
        name = f"{DEFAULT_NAME} ({len(all_symbols)})"
    else:
        return None
    return {"key": key, "name": name, "symbols": all_symbols[:MAX_UNIVERSE], "truncated": len(all_symbols) > MAX_UNIVERSE,
            "source_count": len(all_symbols), "exchange": "NSE"}


@router.get("")
async def list_universes(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The lists a scan can run on (shared reference data), the default key and this organisation's batch plan."""
    await require_flag(session, FLAG, user.tenant_id)
    groups = await catalogue(session)
    from app.core import config
    # M55 T3: with the daily universe sync off the lists are the stored fallback copy; the page says so in one line.
    return {"default_key": DEFAULT_KEY, "groups": groups, "batch": batch_plan(await _broker_name(session, user.tenant_id, user)),
            "fallback": not config.UNIVERSE_SYNC_ENABLED}


@router.get("/{key}")
async def get_universe(key: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    found = await symbols_of(session, key)
    if found is None:
        raise HTTPException(status_code=404, detail="This list is not available yet.")
    return found


__all__ = ["router", "DEFAULT_KEY", "MAX_UNIVERSE", "batch_plan", "batch_wait_seconds", "catalogue", "symbols_of"]
