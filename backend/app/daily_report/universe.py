"""M88-R S1: the report's symbols from the admin setting. "<index> constituents" is read from NSE's official list of
that index - the same file and parser the universe module uses (`app.universe.indices`, `NseArchiveSource`) - on each
run; the last good list is cached and used when NSE cannot be reached. Never typed into code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.daily_report import settings as S
from app.universe.indices import catalogue, parse_constituents, source_urls
from app.universe.parsers import LayoutError
from app.universe.sources import NseArchiveSource, ReferenceSource

SUFFIX = " CONSTITUENTS"


@dataclass(frozen=True)
class Item:
    symbol: str                 # the NSE symbol or the index name as the store knows it ("NIFTY 50")
    kind: str                   # INDEX | EQ
    name: str = ""
    sector: str | None = None   # NSE's industry from the index's own list


@dataclass
class Universe:
    items: list[Item]
    list_source: dict[str, str]          # index code -> "nse" (read now) | "cached" (NSE not reached)
    problems: list[str]


def _indices() -> dict[str, dict[str, Any]]:
    return {str(i["name"]).upper(): i for i in catalogue()}


def default_source() -> ReferenceSource:
    return NseArchiveSource(urls=source_urls())


async def _members(session: AsyncSession, code: str, source: ReferenceSource, cache: dict[str, Any]) -> tuple[list[Item], str | None]:
    fetched = await source.fetch(f"constituents:{code}")
    members: list[Item] = []
    if fetched is not None:
        try:
            members = [Item(c.symbol, "EQ", c.name, c.industry) for c in parse_constituents(fetched.content) if c.symbol]
        except LayoutError:
            members = []
    if members:
        cache[code] = {"fetched_at": datetime.now(UTC).isoformat(), "checksum": fetched.checksum if fetched else None,
                       "members": [{"symbol": m.symbol, "name": m.name, "sector": m.sector} for m in members]}
        return members, "nse"
    held = (cache.get(code) or {}).get("members") or []
    if held:
        return [Item(str(m["symbol"]), "EQ", str(m.get("name") or ""), m.get("sector")) for m in held], "cached"
    return [], None


async def expand(session: AsyncSession, entries: list[str], source: ReferenceSource | None = None) -> Universe:
    """The setting's entries as report items, in the setting's order (an index first, then its members)."""
    indices = _indices()
    cache = dict(await S.cached_lists(session))
    out: list[Item] = []
    seen: set[str] = set()
    used: dict[str, str] = {}
    problems: list[str] = []
    changed = False

    def add(item: Item) -> None:
        if item.symbol not in seen:
            seen.add(item.symbol)
            out.append(item)

    for entry in entries:
        if entry.endswith(SUFFIX) and entry[:-len(SUFFIX)].strip() in indices:
            idx = indices[entry[:-len(SUFFIX)].strip()]
            members, how = await _members(session, str(idx["index_code"]), source or default_source(), cache)
            if how is None:
                problems.append(f"The {idx['name']} list could not be read from NSE and no earlier list is kept.")
                continue
            used[str(idx["index_code"])] = how
            changed = changed or how == "nse"
            for m in members:
                add(m)
        elif entry in indices:
            add(Item(str(indices[entry].get("candle_symbol") or indices[entry]["name"]), "INDEX", str(indices[entry]["name"])))
        else:
            add(Item(entry, "EQ", entry))
    if changed:
        await S.cache_list(session, cache)
        await session.commit()
    return Universe(out, used, problems)
