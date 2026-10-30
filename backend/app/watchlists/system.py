"""WL1 (handover item 4, overrides OPEN_QUESTIONS UI4C-1): read-only SYSTEM watchlists every user sees first.

Groups, in display order:
1. **Indices** - one list of the indices themselves: every index of the U1 catalogue (`universe/data/indices.json`:
   broad, sectoral, thematic, strategy) plus SENSEX and BANKEX, each where the instrument master has it.
2. **Index stocks** - one list per index with its current constituents, named "<INDEX> stocks (<count>)".
3. **Sectors** - one list per sector with every classified NSE company of that sector (NSE's industry classification,
   `classifications` scheme NSE), named for traders ("Banking", "IT", "Automobile", "Pharma", ...; `data/sectors.json`).
4. **F&O** - "F&O stocks (<count>)": the stocks with derivatives (`fo_membership`).

Where the data comes from: nothing here downloads. The lists are rebuilt from tables the universe sync already fills
(U1-a securities, U1-b index membership and classification, U1-c F&O membership) and mapped to the broker instrument
master (`instruments`): a stock maps when the master lists its NSE symbol as an equity, an index when the master has an
index row under its upstox key, name, candle symbol or one of the catalogue's `master_names`. Anything that does not map
is left out of the list and reported (the refresh record's `unmapped`), never guessed.

The refresh (`refresh`) runs once a day after the universe sync (and on the admin's request). Per list it records the
reconstitution diff - the symbols added and removed - in `system_watchlist_refreshes`, with the time. Gates, so a bad
day cannot wipe the lists: with no equity in the instrument master the refresh changes nothing; a list that would shrink
below `UNIVERSE_MIN_ROWS_RATIO` of what it holds is kept as it was and reported as "held".

System lists are platform reference data (no tenant): users read them and copy them into their own lists; no route
changes them (app/watchlists/routes.py answers 403). A watchlist is a list of names: nothing here places an order.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import (
    ClassificationRecord,
    FoMembershipRecord,
    IndexMembershipRecord,
    InstrumentRecord,
    SecurityRecord,
    SystemWatchlistRecord,
    SystemWatchlistRefreshRecord,
)
from app.universe.indices import catalogue
from app.watchlists.service import ITEM_SYMBOL_RE

log = logging.getLogger(__name__)

GROUPS: Tuple[str, ...] = ("INDICES", "INDEX_STOCKS", "SECTORS", "FNO")
GROUP_LABELS: Dict[str, str] = {"INDICES": "Indices", "INDEX_STOCKS": "Index stocks", "SECTORS": "Sectors", "FNO": "F&O"}
KEY_RE = re.compile(r"^[a-z0-9-]{1,80}$")
NAME_MAX = 60
SECTORS_FILE = Path(__file__).resolve().parents[1] / "universe" / "data" / "sectors.json"
# The BSE indices the brokers list (not in the NSE catalogue): (display symbol, names the masters use).
BSE_INDICES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (("SENSEX", ("SENSEX", "BSE SENSEX", "S&P BSE SENSEX")),
                                                          ("BANKEX", ("BANKEX", "BSE BANKEX", "S&P BSE BANKEX")))
KEEP_REFRESHES = 90          # refresh records kept (one a day: about three months of reconstitutions)
UNMAPPED_CAP = 2000          # unmapped entries stored per refresh (the count is always exact)


def _norm(name: str) -> str:
    """A loose spelling key for index names: upper case, & = AND, a trailing INDEX dropped, only letters and digits."""
    text = " ".join((name or "").upper().replace("&", " AND ").split())
    text = re.sub(r"\s+INDEX$", "", text)
    return re.sub(r"[^A-Z0-9]", "", text)


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").lower().replace("&", " and "))).strip("-")[:70] or "list"


def item_key(item: Dict[str, str]) -> str:
    return f"{item['exchange']}:{item['symbol']}"


def sector_specs() -> List[dict]:
    return json.loads(SECTORS_FILE.read_text(encoding="utf-8"))["lists"]


@dataclass
class BuiltList:
    key: str
    group: str
    name: str
    items: List[Dict[str, str]]


@dataclass
class Built:
    lists: List[BuiltList] = field(default_factory=list)
    unmapped: List[Dict[str, str]] = field(default_factory=list)
    master_equities: int = 0
    # Keys whose source still has members before mapping: such a list that maps to nothing today is held, not dropped.
    sourced: Set[str] = field(default_factory=set)


@dataclass
class RefreshReport:
    refresh_id: Optional[int] = None
    lists_total: int = 0
    lists_changed: int = 0
    changes: List[Dict[str, Any]] = field(default_factory=list)
    unmapped: int = 0
    note: str = ""


# --- build (reads only) -------------------------------------------------------------------------------------------------

async def _master(session: AsyncSession) -> Tuple[Dict[str, str], List[Tuple[str, str, str, Optional[str]]]]:
    """NSE equities {symbol: name} and the index rows [(exchange, instrument_key, tradingsymbol, name)] of the master."""
    equities: Dict[str, str] = {}
    index_rows: List[Tuple[str, str, str, Optional[str]]] = []
    rows = await session.execute(select(InstrumentRecord.exchange, InstrumentRecord.instrument_type, InstrumentRecord.instrument_key,
                                        InstrumentRecord.tradingsymbol, InstrumentRecord.name)
                                 .where(InstrumentRecord.instrument_type.in_(("EQ", "INDEX")), InstrumentRecord.exchange.in_(("NSE", "BSE"))))
    for exchange, itype, key, symbol, name in rows.all():
        if itype == "EQ" and exchange == "NSE":
            equities.setdefault(symbol.upper(), name or "")
        elif itype == "INDEX":
            index_rows.append((exchange, key, symbol.upper(), name))
    return equities, index_rows


def _find_index(index_rows, exchange: str, names: Iterable[str], key: Optional[str]) -> Optional[Tuple[str, str, str, Optional[str]]]:
    wanted = {_norm(n) for n in names if n}
    for row in index_rows:
        if key and row[1] == key:
            return row
    for row in index_rows:
        if row[0] == exchange and (_norm(row[3] or "") in wanted or _norm(row[2]) in wanted):
            return row
    return None


async def build(session: AsyncSession) -> Built:
    """Every system list as it should be now, from the universe tables and the instrument master. Reads only."""
    out = Built()
    equities, index_rows = await _master(session)
    out.master_equities = len(equities)
    securities = {isin: (symbol, name, is_etf) for isin, symbol, name, is_etf in (await session.execute(
        select(SecurityRecord.isin, SecurityRecord.symbol, SecurityRecord.name, SecurityRecord.is_etf)
        .where(SecurityRecord.status == "ACTIVE"))).all()}

    def stock(isin: Optional[str], symbol: Optional[str], list_name: str) -> Optional[Dict[str, str]]:
        """The NSE equity item for a company, or None (reported as unmapped)."""
        sec = securities.get(isin) if isin else None
        sym = (sec[0] if sec else symbol or "").upper()
        if not sym:
            out.unmapped.append({"list": list_name, "symbol": isin or "?", "reason": "not in the exchange's equity list"})
            return None
        if sym not in equities:
            out.unmapped.append({"list": list_name, "symbol": sym, "reason": "not an NSE equity in the instrument master"})
            return None
        return {"symbol": sym, "exchange": "NSE", "name": (sec[1] if sec else "") or equities[sym]}

    # 1. Indices: the derivative indices first (the ones traders open most), then the catalogue's order.
    cat = catalogue()
    indices: List[Tuple[bool, int, Dict[str, str]]] = []
    for order, entry in enumerate(cat):
        display = entry.get("candle_symbol") or entry["name"]
        names = [entry["name"], display, *(entry.get("master_names") or [])]
        row = _find_index(index_rows, "NSE", names, (entry.get("broker_symbols") or {}).get("upstox"))
        if row is None:
            out.unmapped.append({"list": "Indices", "symbol": entry["name"], "reason": "no index row in the instrument master"})
            continue
        symbol = (entry.get("candle_symbol") or " ".join((row[3] or row[2]).split())).upper()
        indices.append((not entry.get("has_derivatives"), order, {"symbol": symbol, "exchange": row[0], "name": entry["name"]}))
    for order, (symbol, bse_names) in enumerate(BSE_INDICES, start=len(cat)):
        row = _find_index(index_rows, "BSE", bse_names, None)
        if row is None:
            out.unmapped.append({"list": "Indices", "symbol": symbol, "reason": "no index row in the instrument master"})
            continue
        indices.append((False, order, {"symbol": symbol, "exchange": "BSE", "name": symbol}))
    index_items = [i for _, _, i in sorted(indices, key=lambda t: (t[0], t[1]))]
    out.sourced.add("indices")
    if index_items:
        out.lists.append(BuiltList("indices", "INDICES", "Indices", _unique(index_items)))

    # 2. Index stocks: one list per index with members, by weight (largest first) when published, else by symbol.
    members: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    for code, isin, weight in (await session.execute(select(IndexMembershipRecord.index_code, IndexMembershipRecord.isin,
                                                            IndexMembershipRecord.weight)
                                                     .where(IndexMembershipRecord.valid_to.is_(None)))).all():
        members.setdefault(code, []).append((isin, weight))
    for entry in cat:
        rows = members.get(entry["index_code"])
        if not rows:
            continue
        out.sourced.add(f"index-{entry['index_code'].lower()}")
        label = f"{entry['name']} stocks"
        found = [(w, item) for isin, w in rows if (item := stock(isin, None, label)) is not None]
        found.sort(key=lambda t: (t[0] is None, -(t[0] or 0.0), t[1]["symbol"]))
        items = _unique(item for _, item in found)
        if items:
            out.lists.append(BuiltList(f"index-{entry['index_code'].lower()}", "INDEX_STOCKS", _name(f"{label} ({len(items)})"), items))

    # 3. Sectors: NSE's classification (open ranges) -> the named lists of sectors.json; an unnamed sector keeps NSE's name.
    specs = sector_specs()
    by_value: Dict[Tuple[str, str], str] = {(s["level"], v.lower()): s["name"] for s in specs for v in s["values"]}
    sector_members: Dict[str, List[Dict[str, str]]] = {}
    classified = (await session.execute(select(ClassificationRecord.isin, ClassificationRecord.sector, ClassificationRecord.industry)
                                        .where(ClassificationRecord.scheme == "NSE", ClassificationRecord.valid_to.is_(None)))).all()
    for isin, sector, industry in classified:
        sec = securities.get(isin)
        if sec is not None and sec[2]:
            continue                                                  # an ETF is not a company of the sector
        names = []
        if industry and ("industry", industry.lower()) in by_value:
            names.append(by_value[("industry", industry.lower())])
        if sector:
            names.append(by_value.get(("sector", sector.lower())) or " ".join(sector.split())[:NAME_MAX])
        if not names:
            continue
        out.sourced.update(f"sector-{slug(n)}" for n in names)
        item = stock(isin, None, names[-1])
        if item is None:
            continue
        for name in names:
            sector_members.setdefault(name, []).append(item)
    spec_order = {s["name"]: i for i, s in enumerate(specs)}
    for name in sorted(sector_members, key=lambda n: (spec_order.get(n, len(spec_order)), n)):
        items = _unique(sorted(sector_members[name], key=lambda i: i["symbol"]))
        out.lists.append(BuiltList(f"sector-{slug(name)}", "SECTORS", _name(name), items))

    # 4. F&O stocks (the index underlyings are in "Indices").
    fo = (await session.execute(select(FoMembershipRecord.underlying, FoMembershipRecord.isin)
                                .where(FoMembershipRecord.valid_to.is_(None), FoMembershipRecord.is_index.is_(False)))).all()
    if fo:
        out.sourced.add("fno-stocks")
    fo_items = _unique(sorted((i for u, isin in fo if (i := stock(isin, u, "F&O stocks")) is not None), key=lambda i: i["symbol"]))
    if fo_items:
        out.lists.append(BuiltList("fno-stocks", "FNO", _name(f"F&O stocks ({len(fo_items)})"), fo_items))
    return out


def _unique(items: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    """Each (exchange, symbol) once, in order; a symbol the watchlists would refuse (service.ITEM_SYMBOL_RE) is left out."""
    seen, out = set(), []
    for i in items:
        if item_key(i) not in seen and ITEM_SYMBOL_RE.match(i["symbol"]):
            seen.add(item_key(i))
            out.append(i)
    return out


def _name(text: str) -> str:
    return text if len(text) <= NAME_MAX else text[:NAME_MAX - 1] + "…"


def _checksum(name: str, items: List[Dict[str, str]]) -> str:
    return hashlib.sha256(json.dumps([name, items], sort_keys=True).encode()).hexdigest()


# --- refresh (writes) ---------------------------------------------------------------------------------------------------

async def refresh(session: AsyncSession, *, now: Optional[datetime] = None, commit: bool = True) -> RefreshReport:
    """Rebuilds the system lists and records the run: per list the symbols added / removed, and the unmapped symbols."""
    now = now or datetime.now(timezone.utc)
    report = RefreshReport()
    built = await build(session)
    record = SystemWatchlistRefreshRecord(started_at=now)
    existing = {r.key: r for r in await session.scalars(select(SystemWatchlistRecord))}
    if built.master_equities == 0:
        report.note = "The instrument master has no NSE equities yet; the system lists were left as they were."
    else:
        wanted = {b.key: b for b in built.lists}
        positions = {b.key: i for i, b in enumerate(sorted(built.lists, key=lambda b: GROUPS.index(b.group)))}
        for key, b in wanted.items():
            row = existing.get(key)
            checksum = _checksum(b.name, b.items)
            if row is None:
                session.add(SystemWatchlistRecord(key=key, group=b.group, name=b.name, position=positions[key], items_json=json.dumps(b.items),
                                                  count=len(b.items), checksum=checksum, changed_at=now, refreshed_at=now))
                report.changes.append({"key": key, "name": b.name, "kind": "created", "added": [i["symbol"] for i in b.items], "removed": []})
                continue
            row.refreshed_at, row.position, row.group = now, positions[key], b.group
            if row.checksum == checksum:
                continue
            held = _items(row)
            if held and len(b.items) < config.UNIVERSE_MIN_ROWS_RATIO * len(held):
                report.changes.append({"key": key, "name": row.name, "kind": "held", "added": [], "removed": [],
                                       "reason": f"{len(b.items)} symbols against {len(held)} held; kept as it was"})
                continue
            before, after = {item_key(i): i for i in held}, {item_key(i): i for i in b.items}
            added = [after[k]["symbol"] for k in after if k not in before]
            removed = [before[k]["symbol"] for k in before if k not in after]
            if added or removed or row.name != b.name:
                report.changes.append({"key": key, "name": b.name, "kind": "changed", "added": added, "removed": removed,
                                       **({"renamed_from": row.name} if row.name != b.name else {})})
            row.name, row.items_json, row.count, row.checksum, row.changed_at = b.name, json.dumps(b.items), len(b.items), checksum, now
        for key, row in existing.items():
            if key in wanted:
                continue
            if key in built.sourced:                 # its source still has members; none maps today (a half-synced master)
                report.changes.append({"key": key, "name": row.name, "kind": "held", "added": [], "removed": [],
                                       "reason": "no symbol of it maps to the instrument master today; kept as it was"})
                row.refreshed_at = now
            else:
                report.changes.append({"key": key, "name": row.name, "kind": "dropped", "added": [], "removed": [i["symbol"] for i in _items(row)]})
                await session.delete(row)
        report.lists_total = len(wanted) + sum(1 for c in report.changes if c["kind"] == "held" and c["key"] not in wanted)
    report.lists_changed = sum(1 for c in report.changes if c["kind"] != "held")
    report.unmapped = len(built.unmapped)
    record.finished_at = datetime.now(timezone.utc)
    record.lists_total = report.lists_total or len(existing)
    record.lists_changed = report.lists_changed
    record.changes_json = json.dumps(report.changes)
    record.unmapped_json = json.dumps(built.unmapped[:UNMAPPED_CAP])
    record.unmapped_count = report.unmapped
    record.note = report.note
    session.add(record)
    await session.flush()
    report.refresh_id = record.id
    keep = list(await session.scalars(select(SystemWatchlistRefreshRecord.id).order_by(SystemWatchlistRefreshRecord.id.desc()).limit(KEEP_REFRESHES)))
    if len(keep) == KEEP_REFRESHES:
        await session.execute(delete(SystemWatchlistRefreshRecord).where(SystemWatchlistRefreshRecord.id < min(keep)))
    if commit:
        await session.commit()
    if built.unmapped:
        sample = ", ".join(f"{u['symbol']} ({u['list']})" for u in built.unmapped[:10])
        log.warning("System watchlists: %s source symbol(s) not mapped to the instrument master, e.g. %s", len(built.unmapped), sample)
    log.info("System watchlists refreshed: %s lists, %s changed%s", report.lists_total, report.lists_changed,
             f" - {report.note}" if report.note else "")
    return report


# --- reads ----------------------------------------------------------------------------------------------------------------

def _items(row: SystemWatchlistRecord) -> List[Dict[str, str]]:
    try:
        value = json.loads(row.items_json or "[]")
    except (TypeError, ValueError):
        return []
    return [{"symbol": str(v["symbol"]), "exchange": str(v["exchange"]), "name": str(v.get("name") or "")}
            for v in value if isinstance(v, dict) and v.get("symbol") and v.get("exchange")] if isinstance(value, list) else []


def summary(row: SystemWatchlistRecord) -> Dict[str, Any]:
    return {"key": row.key, "group": row.group, "group_label": GROUP_LABELS.get(row.group, row.group), "name": row.name,
            "count": row.count, "read_only": True, "position": row.position,
            "refreshed_at": row.refreshed_at.isoformat() if row.refreshed_at else None,
            "changed_at": row.changed_at.isoformat() if row.changed_at else None}


def as_dict(row: SystemWatchlistRecord) -> Dict[str, Any]:
    return {**summary(row), "items": _items(row)}


async def list_all(session: AsyncSession) -> List[SystemWatchlistRecord]:
    rows = list(await session.scalars(select(SystemWatchlistRecord)))
    return sorted(rows, key=lambda r: (GROUPS.index(r.group) if r.group in GROUPS else len(GROUPS), r.position, r.name))


async def get(session: AsyncSession, key: str) -> Optional[SystemWatchlistRecord]:
    if not KEY_RE.match(key or ""):
        return None
    return await session.scalar(select(SystemWatchlistRecord).where(SystemWatchlistRecord.key == key))


def watch_items(row: SystemWatchlistRecord) -> List[Dict[str, str]]:
    """The list's rows in the user-watchlist shape ({symbol, exchange}) - what "Copy to my list" saves."""
    return [{"symbol": i["symbol"], "exchange": i["exchange"]} for i in _items(row)]


async def status(session: AsyncSession, *, history: int = 10) -> Dict[str, Any]:
    """The admin's view: the last refresh (time, the changes, the unmapped symbols) and the recent runs' diffs."""
    runs = list(await session.scalars(select(SystemWatchlistRefreshRecord).order_by(SystemWatchlistRefreshRecord.id.desc()).limit(history)))

    def run(r: SystemWatchlistRefreshRecord, *, unmapped: bool) -> Dict[str, Any]:
        out = {"id": r.id, "started_at": r.started_at.isoformat() if r.started_at else None,
               "finished_at": r.finished_at.isoformat() if r.finished_at else None, "lists_total": r.lists_total,
               "lists_changed": r.lists_changed, "changes": json.loads(r.changes_json or "[]"), "unmapped_count": r.unmapped_count,
               "note": r.note}
        if unmapped:
            out["unmapped"] = json.loads(r.unmapped_json or "[]")
        return out

    lists = await list_all(session)
    groups = {g: sum(1 for r in lists if r.group == g) for g in GROUPS}
    return {"last_refresh": run(runs[0], unmapped=True) if runs else None, "history": [run(r, unmapped=False) for r in runs],
            "lists": len(lists), "groups": groups, "universe_sync_enabled": config.UNIVERSE_SYNC_ENABLED,
            "refresh_hour_ist": config.UNIVERSE_SYNC_HOUR_IST}


def refresh_due(*, nse_open: bool, hour: int, today: date, sync_hour: int, last_day: Optional[date], started: bool) -> Tuple[bool, bool]:
    """The worker's gate: (run now, it is the day's evening run). With NSE closed: once per IST day from `sync_hour` (after
    the universe sync), and once when the worker starts (a deploy) - that start-up run is not the day's run, so the
    evening one still follows the sync. Never while NSE is open."""
    if nse_open:
        return False, False
    evening = hour >= sync_hour and last_day != today
    return evening or not started, evening


__all__ = ["GROUPS", "refresh_due", "GROUP_LABELS", "build", "refresh", "list_all", "get", "summary", "as_dict", "watch_items", "status", "slug"]
