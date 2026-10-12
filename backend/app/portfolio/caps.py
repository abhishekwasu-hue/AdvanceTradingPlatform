"""N1 F2: the user's own portfolio caps on new PAPER entries (flag `portfolio_risk_caps`, which acts only while
`portfolio_risk` is on too). **Entry refusals only**: exits, stops, "Exit now" and the kill switch never call this
module (ADR-0004; `tests/test_n1_f2_portfolio_caps.py` walks the call graph).

Three settings in the user's P1-d risk settings (`by_default.RiskSettings`), each 0 = no limit:

- `max_open_positions`: at that many open positions (a multi-leg structure is one), no new entry (Trade's
  `max_open_positions`: `open >= max` refuses).
- `max_correlated_risk_pct`: the open risk at the stops in this entry's bucket plus this entry's max loss may not
  exceed that % of the capital basis (Trade's `check_portfolio_risk_cap`, per bucket: `projected > cap` refuses, so
  equal passes). Buckets as F1: every index together, else the NSE sector, else the name alone.
- `max_sector_pct`: the bucket's gross notional plus this entry's may not exceed that % of the capital basis.

This entry's size is not known before the pipeline sizes it, so it is taken at its most: a ticket's own quantity when
it has one, else the user's risk cap per trade (the largest loss the sizing allows) over the stop distance. A capital
basis of 0 or less refuses (Trade fails closed when it cannot read the capital); any read error refuses too, with the
reason - this is an entry, so refusing is the safe side.

Checked before a proposal is raised (`approval.propose_entry`) and again at Approve (`approval.execute_entry`, the
book may have changed in between). Nothing here places, changes or closes an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.portfolio import risk as R

FLAG = "portfolio_risk_caps"
BASE_FLAG = "portfolio_risk"


@dataclass(frozen=True)
class Candidate:
    """A new entry as the caps see it (prices on the traded instrument)."""
    symbol: str
    underlying: str
    entry: float
    stop: Optional[float]
    quantity: Optional[float] = None          # a ticket's quantity; None: sized at the user's risk cap


@dataclass(frozen=True)
class Limits:
    max_sector_pct: float = 0.0
    max_correlated_risk_pct: float = 0.0
    max_open_positions: int = 0

    @property
    def any(self) -> bool:
        return self.max_sector_pct > 0 or self.max_correlated_risk_pct > 0 or self.max_open_positions > 0


def sized(candidate: Candidate, risk_cap_amount: float) -> Dict[str, float]:
    """This entry's quantity, max loss and notional at its most (pure)."""
    distance = abs(candidate.entry - candidate.stop) if candidate.stop is not None else 0.0
    if candidate.quantity is not None and candidate.quantity > 0:
        qty = float(candidate.quantity)
    else:
        qty = risk_cap_amount / distance if distance > 0 else 0.0
    risk = qty * distance if distance > 0 else risk_cap_amount
    return {"quantity": qty, "risk": risk, "notional": abs(qty * candidate.entry)}


def refusals(positions: List[R.Position], buckets: Dict[str, str], candidate: Candidate, bucket: str, limits: Limits,
             capital: float, risk_cap_amount: float) -> List[str]:
    """Why the caps refuse this entry ([] = allowed). Pure: the F2 tests mirror Trade's rules on it."""
    if not limits.any:
        return []
    out: List[str] = []
    if capital <= 0:
        return [f"Portfolio caps: the capital basis is {capital:,.0f}, so the caps cannot be checked - no new entry"]
    if limits.max_open_positions > 0 and len(positions) >= limits.max_open_positions:
        out.append(f"Portfolio caps: {len(positions)} open positions already (your limit is {limits.max_open_positions})")
    size = sized(candidate, risk_cap_amount)
    in_bucket = [p for p in positions if (buckets.get(p.underlying) or f"{R.UNCLASSIFIED}:{p.underlying}") == bucket]
    if limits.max_correlated_risk_pct > 0:
        held = sum(p.risk for p in in_bucket)
        cap = capital * limits.max_correlated_risk_pct / 100.0
        projected = held + size["risk"]
        if projected > cap + 1e-9:
            out.append(f"Portfolio caps: open risk at the stops in {bucket} {held:,.0f} + this entry {size['risk']:,.0f} = "
                       f"{projected:,.0f}, above your {limits.max_correlated_risk_pct:g}% of capital ({cap:,.0f})")
    if limits.max_sector_pct > 0:
        held_n = sum(p.gross for p in in_bucket)
        cap_n = capital * limits.max_sector_pct / 100.0
        projected_n = held_n + size["notional"]
        if projected_n > cap_n + 1e-9:
            out.append(f"Portfolio caps: exposure in {bucket} {held_n:,.0f} + this entry {size['notional']:,.0f} = "
                       f"{projected_n:,.0f}, above your {limits.max_sector_pct:g}% of capital ({cap_n:,.0f})")
    return out


async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id) and await flag_enabled(session, BASE_FLAG, tenant_id)


async def entry_refusals(session: AsyncSession, user: Optional[User], candidate: Candidate, now: Optional[datetime] = None) -> List[str]:
    """The caps' refusals for this user's new PAPER entry ([] with the flags off, no limits set, or no user)."""
    if user is None or not await enabled(session, user.tenant_id):
        return []
    from app.portfolio import risk_data
    from app.risk_engine import by_default
    try:
        settings = await by_default.user_settings(session, user, now)
        limits = Limits(float(settings.max_sector_pct), float(settings.max_correlated_risk_pct), int(settings.max_open_positions))
        if not limits.any:
            return []
        day = risk_data._today(now)
        positions = R.positions_of(await risk_data.open_book(session, user))
        underlyings = sorted({p.underlying for p in positions} | {candidate.underlying})
        buckets = {u: await risk_data.bucket_of(session, u, day) for u in underlyings}
        capital = await by_default.capital_basis(session, user, now)
        cap_amount = float(by_default.risk_cap(capital, settings).get("amount") or 0.0)
        return refusals(positions, buckets, candidate, buckets[candidate.underlying], limits, capital, cap_amount)
    except Exception as exc:  # noqa: BLE001 - an entry: refuse, with the reason
        return [f"Portfolio caps could not be checked ({type(exc).__name__}) - no new entry"]


def candidate_of(symbol: str, entry: Any, stop: Any, *, underlying: Optional[str] = None, quantity: Any = None) -> Candidate:
    from app.instruments.master import underlying_of
    return Candidate(symbol=symbol, underlying=underlying_of(underlying or symbol), entry=float(entry or 0.0),
                     stop=float(stop) if stop is not None else None,
                     quantity=float(quantity) if quantity not in (None, "", 0) else None)
