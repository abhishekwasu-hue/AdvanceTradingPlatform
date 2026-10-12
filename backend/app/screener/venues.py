"""N3 S8c (spec §2.4 category I): one screen engine per venue, with a field-availability matrix (flag `screener_venues`,
off by default; needs `screener_v2`).

Each venue says which bar fields and which data families (`Spec.needs`) its data carries. With the flag on, a screen run
on a venue that lacks a field or a family is refused with the reason (which venue has it) instead of quietly reading
NaN; `GET /api/screener/venues` gives the matrix to the builder. A venue's own session clock already drives the
session functions (U6 BK2). Nothing here reads data or places anything; the matrix is reference data.

What a venue lacks, by source (provisional, OPEN_QUESTIONS S8-7):
* open interest (`oi`): derivatives venues only (NFO, BFO, MCX, CDS);
* reference lists and classification (sector, industry, index membership, F&O list): the U1 masters cover NSE and BSE
  cash equities;
* shareholding patterns and the event calendar (`ownership`): companies are keyed by their NSE symbol - NSE only;
* option chains: the equity-derivatives venues (NFO, BFO) and MCX options;
* market zones and ranking systems: computed from bars, so wherever bars are.
Crypto (H) is listed for completeness: the screener does not run on it yet (no crypto exchange on the run path), and
funding, open interest and liquidations have no feed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from app.screener import nodes

FLAG = "screener_venues"
BAR_FIELDS = frozenset({"open", "high", "low", "close", "volume"})


@dataclass(frozen=True)
class Venue:
    code: str
    label: str
    exchanges: Tuple[str, ...]
    fields: FrozenSet[str]
    data: FrozenSet[str]                    # the `Spec.needs` families its data carries
    clock: str                              # app.market_data.calendar session family
    runnable: bool = True                   # on the screener's run path (RunBody.exchange)
    note: str = ""


VENUES: Tuple[Venue, ...] = (
    Venue("NSE", "NSE cash equities", ("NSE",), BAR_FIELDS, frozenset({"zones", "rankings", "reference", "ownership"}), "NSE"),
    Venue("BSE", "BSE cash equities", ("BSE",), BAR_FIELDS, frozenset({"zones", "rankings", "reference"}), "NSE",
          note="ownership and events are keyed by NSE symbol"),
    Venue("NFO", "NSE equity derivatives", ("NFO",), BAR_FIELDS | {"oi"}, frozenset({"zones", "rankings", "option_chain"}), "NSE"),
    Venue("BFO", "BSE equity derivatives", ("BFO",), BAR_FIELDS | {"oi"}, frozenset({"zones", "rankings", "option_chain"}), "NSE"),
    Venue("MCX", "MCX commodities", ("MCX",), BAR_FIELDS | {"oi"}, frozenset({"zones", "rankings", "option_chain"}), "MCX"),
    Venue("CDS", "Currency derivatives", ("CDS",), BAR_FIELDS | {"oi"}, frozenset({"zones", "rankings"}), "CDS"),
    Venue("CRYPTO", "Crypto (spot)", ("CRYPTO", "COINDCX", "BINANCE", "WAZIRX"), BAR_FIELDS, frozenset({"zones", "rankings"}), "CRYPTO",
          runnable=False, note="not on the screener's run path yet; funding, open interest and liquidations have no feed"),
)
BY_EXCHANGE: Dict[str, Venue] = {ex: v for v in VENUES for ex in v.exchanges}


def venue_of(exchange: Optional[str]) -> Optional[Venue]:
    return BY_EXCHANGE.get((exchange or "").strip().upper())


def _where(pred: Any) -> str:
    names = [v.code for v in VENUES if pred(v)]
    return ", ".join(names) if names else "no venue yet"


def unavailable(ast: Any, exchange: Optional[str]) -> List[str]:
    """Why a screen cannot run on that venue's data (empty: it can). Each field or function once."""
    from app.screener.registry import FIELDS, FUNCTIONS
    venue = venue_of(exchange)
    if venue is None or ast is None:
        return []
    out: List[str] = []
    seen = set()
    for node in nodes.walk(ast):
        if isinstance(node, nodes.Field) and node.name in FIELDS and node.name not in venue.fields and node.name not in seen:
            seen.add(node.name)
            out.append(f"{node.name}: {venue.label} data has no {node.name} (it is on {_where(lambda v, f=node.name: f in v.fields)})")
        elif isinstance(node, nodes.Call) and node.name in FUNCTIONS and node.name not in seen:
            missing = [need for need in FUNCTIONS[node.name].needs if need not in venue.data]
            if missing:
                seen.add(node.name)
                out.append(f"{node.name}(): {venue.label} data has no {missing[0].replace('_', ' ')} "
                           f"(it is on {_where(lambda v, m=missing[0]: m in v.data)})")
    return out


def matrix() -> Dict[str, Any]:
    """The matrix as data: per venue its fields, data families, and the registry entries it can run."""
    from app.screener.registry import FIELDS, FUNCTIONS
    rows = []
    for v in VENUES:
        fns = sorted(n for n, s in FUNCTIONS.items() if all(need in v.data for need in s.needs))
        rows.append({"code": v.code, "label": v.label, "exchanges": list(v.exchanges), "clock": v.clock, "runnable": v.runnable,
                     "fields": sorted(f for f in FIELDS if f in v.fields), "data": sorted(v.data), "functions": fns,
                     "unavailable": sorted(n for n in FUNCTIONS if n not in fns), "note": v.note})
    return {"venues": rows, "families": sorted({need for s in FUNCTIONS.values() for need in s.needs})}


async def enabled(session: Any, tenant_id: Any) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


__all__ = ["FLAG", "Venue", "VENUES", "venue_of", "unavailable", "matrix", "enabled"]
