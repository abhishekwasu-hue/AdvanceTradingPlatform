"""B2b proof tooling (owner, 24 Oct): the broker's own underlying spot in an option chain answer, next to the ATM strike
and our own last index close - as a few numbers only.

`GET /api/admin/option-chain/probe-chain?underlying=` (SUPER_ADMIN with a two-factor-checked session, read-only) and the
owner's `deploy/atp.sh probe-chain <UNDERLYING>` (which runs `python -m app.option_chain.probe` in the backend container
as the platform owner's account - the first SUPER_ADMIN_EMAILS user - through his OWN stored broker session) read one
chain for the nearest expiry directly from the broker (no shared copy) and report: the raw `underlying_ltp` the broker
sent, the ATM strike it gives, whether it equals a strike, our own last one-minute close of the index (and its bar time),
the market state and the `data_as_of` the pages now show. Nothing is written; no token, credential or other price.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.brokers.models import OptionChain
from app.db.models import User
from app.db.session import get_session
from app.market_data.calendar import IST, family_holidays
from app.option_chain import chain_view
from app.option_chain.as_of import as_of_for, strike_rounded

# MO3: SUPER_ADMIN with a two-factor-verified session, like every admin router.
admin_router = APIRouter(prefix="/api/admin/option-chain", tags=["admin"],
                         dependencies=[Depends(require_role()), Depends(require_mfa_session)])


_LONG_RUN = re.compile(r"[A-Za-z0-9_\-+/=.]{24,}")       # anything token-like in a broker's message


def scrub(text: str, limit: int = 160) -> str:
    """A broker's message, shortened, with anything token-like removed."""
    return _LONG_RUN.sub("[...]", " ".join(str(text).split()))[:limit]


async def _owner(session: AsyncSession) -> User:
    """The platform owner's account: the first SUPER_ADMIN of SUPER_ADMIN_EMAILS (any SUPER_ADMIN when that is unset)."""
    from sqlalchemy import select

    from app.core.config import SUPER_ADMIN_EMAILS
    from app.core.enums import UserRole
    query = select(User).where(User.role == UserRole.SUPER_ADMIN.value)
    if SUPER_ADMIN_EMAILS:
        query = query.where(User.email.in_(sorted(SUPER_ADMIN_EMAILS)))
    user = (await session.scalars(query.order_by(User.id).limit(1))).first()
    if user is None:
        raise SystemExit("no platform owner account (SUPER_ADMIN) found")
    return user


def _iso(ts: Optional[datetime]) -> Optional[str]:
    if ts is None:
        return None
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(IST).isoformat(timespec="minutes")


def summarise(underlying: str, chain: Optional[OptionChain], error: Optional[str], own: Optional[tuple], as_of: Any) -> Dict[str, Any]:
    """The probe's answer: counts and the three prices that decide B2b (broker spot, ATM strike, our last close)."""
    out: Dict[str, Any] = {"underlying": underlying, "market_closed": as_of.closed, "data_as_of": _iso(as_of.at),
                           "own_last_close": own[0] if own else None, "own_last_bar": _iso(own[1]) if own else None}
    if chain is None:
        return {**out, "expiry": None, "strikes": 0, "broker_spot": None, "atm_strike": None, "spot_is_a_strike": None,
                "reason": error or "the broker sent no chain"}
    raw = chain.underlying_ltp
    strikes = sorted(r.strike for r in chain.rows)
    return {**out, "expiry": (chain.expiry or "")[:10] or None, "strikes": len(strikes), "broker_spot": raw,
            "atm_strike": chain_view.atm_strike(strikes, raw if raw and raw > 0 else None),
            "spot_is_a_strike": bool(strikes) and strike_rounded(raw, chain),
            "reason": None if strikes else "the broker sent no strikes"}


async def run_chain_probe(session: AsyncSession, user: User, underlying: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One chain read (nearest expiry) and one candle read through the caller's own broker session and API budget."""
    from app.brokers.base import BrokerInterface
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data import api_budget
    from app.market_data.candles_routes import _pick_record
    from app.market_data.service import MarketDataService
    now = now or datetime.now(timezone.utc)
    record = await _pick_record(session, user.tenant_id, None, "primary")
    adapter = api_budget.BudgetedAdapter(build_adapter(record), record.id, record.broker_name)
    spot_symbol, spot_exchange = chain_view.spot_instrument(underlying)
    when = as_of_for(now, await family_holidays(session, spot_exchange), spot_exchange)
    chain: Optional[OptionChain] = None
    error: Optional[str] = None
    try:
        chain = await adapter.get_option_chain(underlying, None)
    except Exception as exc:  # noqa: BLE001 - the probe reports the broker's error, it never raises it
        error = scrub(f"{type(exc).__name__}: {exc}")
    own = None
    try:
        bars = await MarketDataService(cast(BrokerInterface, adapter), lookback_days=5).get_candles(spot_symbol, spot_exchange, "1min")
        if bars:
            own = (float(bars[-1].close), bars[-1].timestamp)
    except Exception:  # noqa: BLE001 - no own close: shown as "-"
        own = None
    result = summarise(underlying, chain, error, own, when)
    result["broker"] = record.broker_name
    return result


@admin_router.get("/probe-chain")
async def probe_chain_route(underlying: str = Query(min_length=1, max_length=30), user: User = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Read-only: the broker's own underlying spot next to the ATM strike and our last index close (B2b proof)."""
    from app.option_chain.snapshots import normalise_underlying
    try:
        name = normalise_underlying(underlying)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter an index or stock name, for example NIFTY.") from exc
    return await run_chain_probe(session, user, name)


def format_chain(r: Dict[str, Any]) -> List[str]:
    """The probe as a few short lines for the owner's terminal."""
    state = "closed" if r["market_closed"] else "open"
    lines = [f"probe-chain {r['underlying']} via {r.get('broker', '?')} (market {state}; data as of {r['data_as_of']})"]
    if r["reason"] and not r["strikes"]:
        lines.append(f"  chain: 0 strikes - {r['reason']}")
    else:
        lines.append(f"  chain: expiry {r['expiry']}, {r['strikes']} strikes")
        lines.append(f"  broker spot (raw underlying_ltp): {r['broker_spot'] if r['broker_spot'] is not None else 'missing'}")
        lines.append(f"  ATM strike: {r['atm_strike'] if r['atm_strike'] is not None else '-'}; spot equals a strike: "
                     f"{'yes' if r['spot_is_a_strike'] else 'no'}")
    own = r["own_last_close"]
    lines.append(f"  our last index close: {own if own is not None else '-'}" + (f" (bar {r['own_last_bar']})" if own is not None else ""))
    return lines


async def _main_async(underlying: str) -> int:
    from app.db.session import _session_factory
    from app.option_chain.snapshots import normalise_underlying
    name = normalise_underlying(underlying)
    async with _session_factory() as session:
        user = await _owner(session)
        try:
            result = await run_chain_probe(session, user, name)
        except HTTPException as exc:
            print(f"probe-chain: {exc.detail}")
            return 3
    print("\n".join(format_chain(result)))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="probe", description="B2b: the broker's spot in an option chain, as numbers")
    parser.add_argument("underlying")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_main_async(args.underlying))
    except ValueError:
        print("probe-chain: enter an index or stock name, for example NIFTY")
        return 2


if __name__ == "__main__":
    sys.exit(main())
