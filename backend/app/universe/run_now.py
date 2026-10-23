"""WL1-3 / WL1-4 (owner decision 23 Oct): run the universe sync and the system-watchlists refresh NOW, on the server.

`python -m app.universe.run_now` (inside the worker image; `deploy/atp.sh lists-sync` runs it) does, in order:

1. refuses unless `UNIVERSE_SYNC_ENABLED` is true (the owner's switch in /opt/atp/.env);
2. downloads every published reference file ONCE and prints one HTTP status line per file (WL1-5: the file names are
   verified from the server itself) - the NSE equity / SME / ETF lists, the symbol changes, the F&O lots and ban list,
   each index's constituent file (`data/indices.json`) and the classification file when one is configured. The
   all-indices closing file is NOT fetched here: the lists hold names only, no prices;
3. refreshes the broker instrument master for `INSTRUMENT_SYNC_EXCHANGES` (Upstox's public file, no login);
4. runs the universe sync on the files just downloaded (the same functions and quality gates as the nightly job:
   short files, churn and layout changes are refused with a data-quality event; any time of day - names only);
5. rebuilds the system watchlists and prints each group (Indices / Index stocks / Sectors / F&O) with every list's row
   count and the symbols that did not map to the instrument master.

`--check` stops after step 2 (nothing is written). Output is plain lines; no secret is read or printed (the URLs are
the providers' public download links). Nothing here places, blocks or changes an order.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, TextIO, Tuple

from app.core import config

IST = timezone(timedelta(hours=5, minutes=30))
EQUITY_FILES = ("equity_list", "sme_equity_list", "etf_list", "symbol_changes")
FO_FILES = ("fo_lots", "fo_ban")
SHOWN_UNMAPPED = 50            # unmapped symbols printed per list (the full list is on the Admin console card)

# A fetcher: url -> (HTTP status or None, body or None, error text or ""). Tests pass a fake one; no network there.
Fetcher = Callable[[str], Awaitable[Tuple[Optional[int], Optional[bytes], str]]]
MasterSync = Callable[..., Awaitable[Dict[str, int]]]


@dataclass
class FileCheck:
    name: str
    url: str
    status: Optional[int]
    content: Optional[bytes]
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.content)


def file_urls() -> Dict[str, str]:
    """Every file the lists are built from, by the name the sync functions ask for. Names only - no closing prices."""
    urls = {k: v for k, v in config.UNIVERSE_FILE_URLS.items() if k in EQUITY_FILES + FO_FILES and v}
    from app.universe import classification, indices
    extra = config.UNIVERSE_FILE_URLS.get(classification.FILE)
    if extra:
        urls[classification.FILE] = extra
    urls.update(indices.source_urls())
    return urls


def http_fetcher(*, timeout: float = 30.0, pause_seconds: float = 1.0) -> Fetcher:
    import httpx
    headers = {"User-Agent": config.UNIVERSE_USER_AGENT, "Accept": "text/csv,application/octet-stream,*/*"}

    async def fetch(url: str) -> Tuple[Optional[int], Optional[bytes], str]:
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
                response = await client.get(url)
            return response.status_code, response.content, ""
        except Exception as exc:  # noqa: BLE001 - reported on its line; the other files still run
            return None, None, type(exc).__name__
        finally:
            await asyncio.sleep(pause_seconds)                 # polite: one file at a time, a pause between
    return fetch


def _rows(content: Optional[bytes]) -> int:
    if not content:
        return 0
    return max(0, sum(1 for line in content.decode("utf-8", "replace").splitlines() if line.strip()) - 1)


async def check_files(fetch: Fetcher, urls: Dict[str, str], out: TextIO) -> List[FileCheck]:
    out.write("Files (HTTP status as seen from this server)\n")
    checks: List[FileCheck] = []
    for name, url in urls.items():
        status, content, error = await fetch(url)
        check = FileCheck(name, url, status, content, error)
        checks.append(check)
        if check.ok:
            detail = f"{_rows(content)} rows"
        elif status == 200:
            detail = "empty file"
        else:
            detail = error or "not fetched"
        out.write(f"  {status if status is not None else '---'}  {name:32} {url.rsplit('/', 1)[-1]:44} {detail}\n")
    good = sum(1 for c in checks if c.ok)
    out.write(f"  {good} of {len(checks)} files fetched\n")
    return checks


def _group_of(list_label: str) -> str:
    if list_label == "Indices":
        return "INDICES"
    if list_label == "F&O stocks":
        return "FNO"
    if list_label.endswith(" stocks"):
        return "INDEX_STOCKS"
    return "SECTORS"


def print_lists(rows: Sequence, unmapped: Sequence[Dict[str, str]], out: TextIO) -> None:
    from app.watchlists import system
    by_list: Dict[str, List[Dict[str, str]]] = {}
    for u in unmapped:
        by_list.setdefault(str(u.get("list") or "?"), []).append(u)
    for group in system.GROUPS:
        mine = [r for r in rows if r.group == group]
        out.write(f"\n{system.GROUP_LABELS[group]} - {len(mine)} list(s)\n")
        if not mine:
            out.write("  (no list yet)\n")
        for r in mine:
            out.write(f"  {r.name:50} {r.count:5} rows\n")
        labels = sorted(label for label in by_list if _group_of(label) == group)
        missing = sum(len(by_list[label]) for label in labels)
        out.write(f"  unmapped: {missing}\n")
        for label in labels:
            items = by_list[label]
            out.write(f"    {label} ({len(items)}):\n")
            for u in items[:SHOWN_UNMAPPED]:
                out.write(f"      {u.get('symbol')} - {u.get('reason')}\n")
            if len(items) > SHOWN_UNMAPPED:
                out.write(f"      ... and {len(items) - SHOWN_UNMAPPED} more (Admin console > System watchlists)\n")


async def run(*, out: TextIO = sys.stdout, fetch: Optional[Fetcher] = None, master_sync: Optional[MasterSync] = None,
              session_factory: Optional[Callable[[], Any]] = None, today: Optional[date] = None, check_only: bool = False) -> int:
    if not config.UNIVERSE_SYNC_ENABLED:
        out.write("Refused: UNIVERSE_SYNC_ENABLED is not true in /opt/atp/.env - nothing was downloaded or changed.\n"
                  "  Set it with: /opt/atp/deploy/atp.sh env-set UNIVERSE_SYNC_ENABLED=true\n")
        return 2
    today = today or datetime.now(IST).date()
    checks = await check_files(fetch or http_fetcher(), file_urls(), out)
    if check_only:
        return 0 if all(c.ok for c in checks) else 1

    from app.db.models import SystemWatchlistRefreshRecord
    from app.universe import classification, fo_eod, indices, sync
    from app.universe.sources import StaticSource
    from app.watchlists import system
    if session_factory is None:
        from app.db.session import _session_factory
        session_factory = _session_factory
    if master_sync is None:
        from app.instruments.master import sync_upstox
        master_sync = sync_upstox

    files = {c.name: c.content for c in checks if c.ok and c.content is not None}
    source = StaticSource(files, source="nse_archives")
    refused: List[str] = []

    out.write(f"\nInstrument master ({', '.join(config.INSTRUMENT_SYNC_EXCHANGES) or 'none'})\n")
    async with session_factory() as session:
        try:
            counts = await master_sync(session, config.INSTRUMENT_SYNC_EXCHANGES)
            await session.commit()
            out.write("  " + ", ".join(f"{k}: {v} rows" for k, v in counts.items()) + "\n")
        except Exception as exc:  # noqa: BLE001 - the lists then map against the master already held
            await session.rollback()
            out.write(f"  not refreshed ({type(exc).__name__}); the lists map against the master already held\n")

    out.write(f"\nUniverse sync ({today.isoformat()})\n")
    async with session_factory() as session:
        eq = await sync.sync_equity_lists(session, source, today)
        out.write(f"  securities: {eq.inserted} new, {eq.updated} updated, {eq.seen} seen\n")
        refused += eq.refused
    async with session_factory() as session:
        idx = await indices.sync_indices(session, source, today)
        rebalanced = [r.index_code for r in idx if r.entered or r.exited]
        out.write(f"  index constituents: {len(idx)} files applied, {len(rebalanced)} with member changes\n")
        refused += [f"{r.index_code}: {r.refused}" for r in idx if r.refused]
    async with session_factory() as session:
        fo = await fo_eod.sync_fo_and_index_eod(session, source, today)
        out.write(f"  F&O list: {len(fo.fo_entered)} added, {len(fo.fo_exited)} removed, {len(fo.lot_changes)} lot changes\n")
        refused += fo.refused
    async with session_factory() as session:
        cls = await classification.sync_classification(session, source, today)
        out.write("  classification file: " + ("not configured (sectors from the index files)" if cls is None
                                                else f"{cls.changed} changed" + (f", refused: {cls.refused}" if cls.refused else "")) + "\n")
    for line in refused:
        out.write(f"  refused by a quality gate (kept as it was): {line}\n")

    report = None
    for attempt in range(2):                                  # the worker's own refresh may run at the same moment
        async with session_factory() as session:
            try:
                report = await system.refresh(session)
                break
            except Exception as exc:  # noqa: BLE001 - one retry, then reported
                await session.rollback()
                if attempt:
                    out.write(f"\nSystem watchlists: refresh failed ({type(exc).__name__})\n")
                    return 1
    if report is None:
        return 1
    async with session_factory() as session:
        rows = await system.list_all(session)
        record = await session.get(SystemWatchlistRefreshRecord, report.refresh_id) if report.refresh_id else None
        unmapped = json.loads(record.unmapped_json or "[]") if record is not None else []
    out.write(f"\nSystem watchlists: {report.lists_total} lists, {report.lists_changed} changed, {report.unmapped} unmapped symbol(s)\n")
    if report.note:
        out.write(f"  {report.note}\n")
    print_lists(rows, unmapped, out)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.universe.run_now", description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="only print the HTTP status of every file; change nothing")
    args = parser.parse_args(argv)
    return asyncio.run(run(check_only=args.check))


if __name__ == "__main__":
    sys.exit(main())
