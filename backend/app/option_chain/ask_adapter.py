"""OC3: the thin adapter between the option chain data and the analysis spec engine (app/option_chain/ask_spec.py).

The engine reads flat per-strike dicts (`ce_oi`, `pe_iv`, ...) and the session's earlier snapshots; this module is the
only place that knows where they come from:

* **The chain** is read through the caller's organisation's broker session exactly as `POST /api/market-data/option-chains`
  reads it (or, with the bring-your-own-broker data tier on, through the caller's own session inside its budget), and
  counted in the same `market_data_chains` usage. The route carries the same per-user rate limit.
* **Rows.** `flat_rows` takes rows in the OC1 view shape (`{"strike", "call": {...}, "put": {...}}`,
  app/option_chain/chain_view.py `build_view`). Until OC1 merges, `view_rows_from_chain` builds that shape from the
  broker's own values only (IV read as percent; Greeks only where the broker sends them). Once OC1 merges, point
  `view_rows_for` at `chain_view.build_view(...)["rows"]` and nothing else changes.
* **History** is the OI collector's stored slots for today (`oi_snapshots` / `strike_oi_snapshots`,
  app/option_chain/snapshots.py), kept only for the same expiry and read before now. With the own-broker tier on they
  are not used (the collector reads through a shared session), and a note says so.

Reads only: nothing is stored but the usage count, and no order path is reached (ADR-0006).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChain
from app.db.models import User
from app.option_chain.ask_spec import HistoryPoint

logger = logging.getLogger(__name__)

EXCHANGE = "NFO"            # an option chain is read in the F&O segment (as the option-chains route budgets it)
LEG_KEYS = ("oi", "oi_change", "ltp", "iv", "volume", "bid", "ask", "delta", "gamma", "theta", "vega")
FLAT_KEY = {"oi_change": "oi_chg"}

NO_BROKER = "Connect your broker and log in to it today to run an analysis. It uses your own broker's option chain."
UNAVAILABLE = "Your broker did not return the option chain just now. Try again in a moment."
NO_CHAIN = "Your broker returned no strikes for this symbol and expiry."
NO_HISTORY_BYOB = ("Earlier snapshots of the session are not used while your own-broker data setting is on, so values from "
                   "earlier in the session are missing.")


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def iv_percent(raw: Any) -> Optional[float]:
    """A broker's IV in percent: 13.2 stays 13.2, 0.132 becomes 13.2; zero, negative or missing is None (the same rule
    as OC1's `chain_view.iv_percent`)."""
    v = _finite(raw)
    if v is None or v <= 0:
        return None
    return v * 100.0 if v <= 3.0 else v


def view_rows_from_chain(chain: OptionChain) -> List[Dict[str, Any]]:
    """The OC1 view-row shape from the broker's own values (no solved IV, no computed Greeks: missing stays missing)."""
    out: List[Dict[str, Any]] = []
    for r in sorted(chain.rows, key=lambda x: x.strike):
        legs = {}
        for side, p in (("call", "call_"), ("put", "put_")):
            legs[side] = {"oi": _finite(getattr(r, p + "oi")), "oi_change": _finite(getattr(r, p + "change_oi")),
                          "ltp": _finite(getattr(r, p + "ltp")), "iv": iv_percent(getattr(r, p + "iv")),
                          "volume": _finite(getattr(r, p + "volume")), "bid": _finite(getattr(r, p + "bid")),
                          "ask": _finite(getattr(r, p + "ask")), "delta": _finite(getattr(r, p + "delta")),
                          "gamma": None, "theta": None, "vega": None}
        out.append({"strike": float(r.strike), **legs})
    return out


def view_rows_for(chain: OptionChain) -> List[Dict[str, Any]]:
    """The seam to OC1: today the broker-values stand-in; with OC1 merged, `chain_view.build_view(...)["rows"]`."""
    return view_rows_from_chain(chain)


def flat_rows(view_rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """OC1 view rows -> the engine's flat rows (`ce_oi`, `ce_oi_chg`, `pe_iv`, ...)."""
    out: List[Dict[str, Any]] = []
    for row in view_rows:
        strike = _finite(row.get("strike"))
        if strike is None:
            continue
        flat: Dict[str, Any] = {"strike": strike}
        for side, prefix in (("call", "ce"), ("put", "pe")):
            leg = row.get(side) or {}
            for key in LEG_KEYS:
                flat[f"{prefix}_{FLAT_KEY.get(key, key)}"] = _finite(leg.get(key)) if isinstance(leg, Mapping) else None
        out.append(flat)
    return out


def _expiry_key(text: Optional[str]) -> str:
    return (text or "")[:10]


def history_points(slots: Sequence[Any], expiry: Optional[str], now: datetime) -> List[HistoryPoint]:
    """Stored collector slots (`snapshots.SlotChain`) -> HistoryPoints: same expiry only, read before `now`."""
    want = _expiry_key(expiry)
    out: List[HistoryPoint] = []
    for slot in slots:
        chain: OptionChain = slot.chain
        if want and _expiry_key(chain.expiry) != want:
            continue
        if slot.captured_at >= now:
            continue
        rows: Dict[float, Dict[str, Optional[float]]] = {}
        for r in chain.rows:
            rows[float(r.strike)] = {"ce_oi": _finite(r.call_oi), "pe_oi": _finite(r.put_oi), "ce_ltp": _finite(r.call_ltp),
                                     "pe_ltp": _finite(r.put_ltp), "ce_iv": iv_percent(r.call_iv), "pe_iv": iv_percent(r.put_iv)}
        out.append(HistoryPoint(at=slot.captured_at, rows=rows))
    return sorted(out, key=lambda p: p.at)


@dataclass
class ChainRead:
    state: str                                          # ok | no_broker | unavailable | no_data
    message: Optional[str]
    symbol: str
    expiry: Optional[str] = None
    spot: Optional[float] = None
    rows: List[Dict[str, Any]] = field(default_factory=list)
    history: List[HistoryPoint] = field(default_factory=list)
    history_note: Optional[str] = None
    source: Optional[Dict[str, str]] = None


async def _record(session: AsyncSession, user: User, underlying: str) -> Tuple[Any, bool, Optional[str]]:
    """(credential, own-broker tier on, refusal sentence). Mirrors the option-chains route's choice of session."""
    from fastapi import HTTPException

    from app.market_data import candles_routes, entitlement
    from app.market_data.fanin import Priority
    if await candles_routes._byob(session, user):
        ents = await entitlement.resolve_many(session, user, [(underlying, EXCHANGE)], priority=Priority.CHART)
        ent = ents.get((underlying, EXCHANGE))
        if ent is None or not ent.live or ent.record is None:
            return None, True, (ent.reason if ent is not None and ent.reason else NO_BROKER)
        return ent.record, True, None
    try:
        return await candles_routes._pick_record(session, user.tenant_id, None, "primary"), False, None
    except HTTPException:
        return None, False, NO_BROKER


async def _read_chain(record: Any, underlying: str, expiry: Optional[Any]) -> Optional[OptionChain]:
    from app.market_data import candles_routes
    adapter = candles_routes.build_adapter(record)
    try:
        return await adapter.get_option_chain(underlying, expiry)
    except Exception as exc:  # noqa: BLE001 - any broker failure is the same plain state for the page (class name only)
        logger.info("OC3: chain read failed for %s via %s: %s", underlying, getattr(record, "broker_name", "?"), type(exc).__name__)
        return None


async def read(session: AsyncSession, user: User, underlying: str, expiry: Optional[Any], now: datetime) -> ChainRead:
    """The chain, its flat rows and the session's history for one underlying and expiry, or a plain state."""
    from app.billing.service import meter
    from app.option_chain import snapshots
    from app.option_chain.snapshots import IST
    base = ChainRead(state="ok", message=None, symbol=underlying, expiry=expiry.isoformat() if expiry is not None else None)
    record, byob, refusal = await _record(session, user, underlying)
    if record is None:
        base.state, base.message = "no_broker", refusal or NO_BROKER
        return base
    chain = await _read_chain(record, underlying, expiry)
    if chain is None:
        base.state, base.message = "unavailable", UNAVAILABLE
        return base
    base.expiry = _expiry_key(chain.expiry) or base.expiry
    if not chain.rows:
        base.state, base.message = "no_data", NO_CHAIN
        return base
    await meter(session, user.tenant_id, "market_data_chains", quantity=1, source="api",
                metadata={"broker": record.broker_name, "expiry": base.expiry, "use": "option_chain_ask"}, commit=True)
    base.spot = _finite(chain.underlying_ltp)
    base.rows = flat_rows(view_rows_for(chain))
    base.source = {"broker": str(record.broker_name), "account_label": str(record.account_label)}
    if byob:
        base.history_note = NO_HISTORY_BYOB
    else:
        slots = await snapshots.day_chains(session, underlying, now.astimezone(IST).date())
        base.history = history_points(slots, base.expiry, now)
    return base


__all__ = ["ChainRead", "read", "flat_rows", "view_rows_from_chain", "view_rows_for", "history_points", "iv_percent",
           "NO_BROKER", "UNAVAILABLE", "NO_CHAIN", "NO_HISTORY_BYOB"]
