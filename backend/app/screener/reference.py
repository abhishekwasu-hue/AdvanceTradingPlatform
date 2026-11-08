"""FN2-d: the reference data a screen run reads - Sector / Industry / IndexMember / IsFnO from the U1 tables
(app/universe: `symbol_history`, `classifications`, `index_membership` + `indices`, `fo_membership`).

No look-ahead: these functions answer the CURRENT classification (the ranges alive on the run's IST date), as the
registry says ("as of the run date"). A run and a bar-close alert decide on the bar that just closed, so today's
classification is the one known at that bar. Screen history, chart studies and the library's runs evaluate past bars
and do not load it: `routes.missing_data` keeps refusing these functions there (never a later classification read
back into an older bar).

Never guessed: a symbol without a held ISIN, classification, membership or F&O range keeps None / an empty set, and
the runtime never matches missing reference data (`Sector() == "IT"` is false, `IsFnO()` is false).
`McapBucket` (AMFI large / mid / small) has no U1 source and stays refused on every path.
Shared reference rows only: nothing of an organisation is read, nothing places an order.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import and_, or_, select

from app.screener import nodes

# The `needs=("reference",)` functions the run path loads (McapBucket has no source: not here)
LOADED = frozenset({"Sector", "Industry", "IndexMember", "IsFnO"})


def calls(ast: Any) -> List[str]:
    """The loaded reference functions the screen uses, in order of appearance."""
    out: List[str] = []
    if ast is None:
        return out
    for node in nodes.walk(ast):
        if isinstance(node, nodes.Call) and node.name in LOADED and node.name not in out:
            out.append(node.name)
    return out


def run_date(now: Optional[datetime] = None) -> date:
    """The run's exchange (IST) date - the day whose classification ranges a run reads."""
    from app.market_data.calendar import IST
    return (now or datetime.now(UTC)).astimezone(IST).date()


def _alive(model: Any, day: date) -> Any:
    return and_(model.valid_from <= day, or_(model.valid_to.is_(None), model.valid_to > day))


async def isins_on(session: Any, symbols: Sequence[str], day: date) -> Dict[str, str]:
    """{SYMBOL: ISIN} - the ISIN each symbol traded under on `day` (U1-a symbol_history; the securities master's current
    symbol when no history range covers it). A symbol with neither is left out."""
    from app.db.models import SecurityRecord, SymbolHistoryRecord
    wanted = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not wanted:
        return {}
    alive = and_(or_(SymbolHistoryRecord.valid_from.is_(None), SymbolHistoryRecord.valid_from <= day),
                 or_(SymbolHistoryRecord.valid_to.is_(None), SymbolHistoryRecord.valid_to > day))
    out: Dict[str, str] = {}
    for sym, isin in (await session.execute(select(SymbolHistoryRecord.symbol, SymbolHistoryRecord.isin)
                                            .where(SymbolHistoryRecord.symbol.in_(wanted), alive))).all():
        out.setdefault(sym.upper(), isin)
    rest = [s for s in wanted if s not in out]
    if rest:
        for sym, isin in (await session.execute(select(SecurityRecord.symbol, SecurityRecord.isin)
                                                .where(SecurityRecord.symbol.in_(rest), SecurityRecord.status != "DELISTED"))).all():
            out.setdefault(sym.upper(), isin)
    return out


async def attach(session: Any, universe: List[Any], day: Optional[date] = None) -> None:
    """Sets sector, industry, indices and is_fno on each SymbolData of `universe` from the ranges alive on `day` (the
    run date by default) - four batched reads, whatever the universe size. The current classification: see the module
    doc (no look-ahead)."""
    from app.db.models import ClassificationRecord, FoMembershipRecord, IndexMembershipRecord, IndexRecord
    if not universe:
        return
    day = day or run_date()
    isin_of = await isins_on(session, [d.symbol for d in universe], day)
    isins = sorted(set(isin_of.values()))
    classified: Dict[str, Any] = {}
    members: Dict[str, Set[str]] = {}
    if isins:
        for row in await session.scalars(select(ClassificationRecord).where(ClassificationRecord.isin.in_(isins), ClassificationRecord.scheme == "NSE",
                                                                            _alive(ClassificationRecord, day))
                                         .order_by(ClassificationRecord.valid_from)):
            classified[row.isin] = row                                   # the latest range alive on the day
        rows = (await session.execute(select(IndexMembershipRecord.isin, IndexMembershipRecord.index_code, IndexRecord.name)
                                      .join(IndexRecord, IndexRecord.index_code == IndexMembershipRecord.index_code, isouter=True)
                                      .where(IndexMembershipRecord.isin.in_(isins), _alive(IndexMembershipRecord, day)))).all()
        for isin, code, name in rows:
            # IndexMember("NIFTY 50") and IndexMember("NIFTY50"): the index's name and its code both answer
            members.setdefault(isin, set()).update(x for x in (code, name) if x)
    symbols = sorted({d.symbol.strip().upper() for d in universe})
    fo_isins: Set[str] = set()
    fo_symbols: Set[str] = set()
    fo_held = False
    for isin, underlying in (await session.execute(select(FoMembershipRecord.isin, FoMembershipRecord.underlying).where(
            FoMembershipRecord.is_index.is_(False), _alive(FoMembershipRecord, day),
            or_(FoMembershipRecord.isin.in_(isins or [""]), FoMembershipRecord.underlying.in_(symbols))))).all():
        fo_held = True
        if isin:
            fo_isins.add(isin)
        fo_symbols.add(underlying.upper())
    if not fo_held:
        fo_held = await session.scalar(select(FoMembershipRecord.id).where(_alive(FoMembershipRecord, day)).limit(1)) is not None
    for d in universe:
        sym = d.symbol.strip().upper()
        isin = isin_of.get(sym)
        row = classified.get(isin) if isin else None
        d.sector = row.sector if row is not None else None
        d.industry = row.industry if row is not None else None
        d.indices = set(members.get(isin, set())) if isin else set()
        # an F&O list held for the day: in it or not; none held at all: unknown (None - never matches either way)
        d.is_fno = ((isin in fo_isins) or (sym in fo_symbols)) if fo_held else None


__all__ = ["LOADED", "calls", "run_date", "isins_on", "attach"]
