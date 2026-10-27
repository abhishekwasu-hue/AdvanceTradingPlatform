"""WL1-3 / WL1-4 / WL1-5: `python -m app.universe.run_now` - the server's one-off universe sync + system-watchlists refresh.

Fake downloads only (fixture files, no network). Pinned: it refuses unless UNIVERSE_SYNC_ENABLED; every file gets one HTTP
status line before anything is written; `--check` writes nothing; the closing-price file is never fetched (names only);
the sync's quality gates still apply; each group is printed with its lists' row counts and the unmapped symbols."""
import asyncio
import io
import pathlib
from datetime import date

import pytest
from sqlalchemy import func, select

from app.core import config
from app.db.models import SecurityRecord, SystemWatchlistRecord
from app.universe import indices, run_now
from tests.test_auth_api import _session_factory
from tests.test_wl1_system_watchlists import _master_rows, _wipe

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "universe" / "wl1"
DAY = date(2026, 10, 23)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE_SYNC_ENABLED", True)
    monkeypatch.setattr(config, "INSTRUMENT_SYNC_EXCHANGES", ["NSE", "BSE"])

    async def wipe():
        async with _session_factory() as session:
            await _wipe(session)
            await session.commit()
    _run(wipe())
    yield
    _run(wipe())


def _served():
    urls = run_now.file_urls()
    by_code = {"NIFTY50": "ind_test50list.csv", "NIFTYBANK": "ind_testbanklist.csv", "NIFTY500": "ind_test500list.csv"}
    files = {urls["equity_list"]: (FIX / "EQUITY_L.csv").read_bytes(), urls["fo_lots"]: (FIX / "fo_mktlots.csv").read_bytes()}
    files.update({urls[f"constituents:{code}"]: (FIX / name).read_bytes() for code, name in by_code.items()})
    return files


def _fake_fetch(files, asked):
    async def fetch(url):
        asked.append(url)
        if url in files:
            return 200, files[url], ""
        return 404, b"<html>not found</html>", ""
    return fetch


async def _fake_master(session, exchanges):
    session.add_all(_master_rows())
    await session.flush()
    return {e: 7 for e in exchanges}


def _go(**kw):
    out, asked = io.StringIO(), []
    rc = _run(run_now.run(out=out, fetch=_fake_fetch(_served(), asked), master_sync=_fake_master, session_factory=_session_factory,
                          today=DAY, **kw))
    return rc, out.getvalue(), asked


def _count(model):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(model))
    return _run(go())


def test_refuses_unless_the_owner_switched_the_sync_on(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE_SYNC_ENABLED", False)
    rc, text, asked = _go()
    assert rc == 2 and "UNIVERSE_SYNC_ENABLED is not true" in text
    assert asked == [] and _count(SecurityRecord) == 0 and _count(SystemWatchlistRecord) == 0


def test_the_file_list_is_names_only_and_holds_every_catalogue_index():
    urls = run_now.file_urls()
    assert "index_close_all" not in urls                                       # closing prices are not fetched here
    assert all(k in urls for k in ("equity_list", "sme_equity_list", "etf_list", "symbol_changes", "fo_lots", "fo_ban"))
    codes = [i["index_code"] for i in indices.catalogue()]
    assert len(codes) == 37 and all(f"constituents:{c}" in urls for c in codes)


def test_check_prints_one_status_line_per_file_and_writes_nothing():
    rc, text, asked = _go(check_only=True)
    urls = run_now.file_urls()
    assert rc == 1                                                             # some files answer 404
    assert sorted(asked) == sorted(urls.values())                              # each file asked for exactly once
    lines = [ln for ln in text.splitlines() if ln.startswith("  200 ") or ln.startswith("  404 ")]
    assert len(lines) == len(urls)
    assert any(ln.startswith("  200 ") and "constituents:NIFTY50" in ln and "rows" in ln for ln in lines)
    assert any(ln.startswith("  404 ") and "constituents:NIFTYCPSE" in ln and "ind_niftycpselist.csv" in ln for ln in lines)
    assert _count(SecurityRecord) == 0 and _count(SystemWatchlistRecord) == 0


def test_a_full_run_syncs_once_and_prints_every_group_with_counts_and_unmapped():
    rc, text, asked = _go()
    assert rc == 0, text
    assert len(asked) == len(set(asked)) == len(run_now.file_urls())          # downloaded once; the sync reuses the bytes
    assert not any("ind_close_all" in u for u in asked)
    assert "NSE: 7 rows, BSE: 7 rows" in text
    for heading in ("Indices - 1 list(s)", "Index stocks - ", "Sectors - ", "F&O - 1 list(s)"):
        assert heading in text
    async def lists():
        async with _session_factory() as session:
            return {r.key: (r.name, r.count) for r in await session.scalars(select(SystemWatchlistRecord))}
    held = _run(lists())
    for name, count in held.values():
        assert any(name in ln and f"{count} rows" in ln for ln in text.splitlines()), (name, count)
    assert {k for k in held if k.startswith("index-")} == {"index-nifty50", "index-niftybank", "index-nifty500"}
    # unmapped: indices the fake master does not have, listed under their group with the reason
    assert "no index row in the instrument master" in text and "unmapped: " in text
    assert "System watchlists: " in text and "unmapped symbol(s)" in text


def test_a_second_run_changes_nothing_and_a_short_file_is_refused_by_the_gate():
    assert _go()[0] == 0
    before = _count(SecurityRecord)
    rc, text, _ = _go()
    assert rc == 0 and "0 changed" in text.split("System watchlists:")[1].splitlines()[0]
    assert _count(SecurityRecord) == before

    short = _served()
    eq_url = run_now.file_urls()["equity_list"]
    short[eq_url] = b"\n".join(short[eq_url].splitlines()[:2]) + b"\n"         # a truncated download
    out = io.StringIO()
    rc = _run(run_now.run(out=out, fetch=_fake_fetch(short, []), master_sync=_fake_master, session_factory=_session_factory, today=DAY))
    assert rc == 0 and "refused by a quality gate" in out.getvalue() and _count(SecurityRecord) == before


def test_a_failed_master_download_keeps_the_master_held_and_still_builds():
    async def broken(session, exchanges):
        raise ConnectionError("down")
    out = io.StringIO()
    rc = _run(run_now.run(out=out, fetch=_fake_fetch(_served(), []), master_sync=broken, session_factory=_session_factory, today=DAY))
    text = out.getvalue()
    assert rc == 0 and "not refreshed (ConnectionError)" in text
    assert "The instrument master has no NSE equities yet" in text            # the refresh's own gate: nothing wiped


def test_the_corrected_wl1_5_file_names():
    urls = {i["index_code"]: i["constituents_url"] for i in indices.catalogue()}
    assert urls["NIFTY50VALUE20"].endswith("/ind_Nifty50_Value20.csv")
    assert urls["NIFTYCHEMICALS"].endswith("/ind_niftyChemicals_list.csv")
    # Owner 25 Oct: niftyindices.com answered 302 -> 404 / 502 from the server; the same files are on the NSE archives host.
    assert all(u.startswith("https://nsearchives.nseindia.com/content/indices/ind_") and u.endswith(".csv") for u in urls.values())
    assert len(set(urls.values())) == len(urls)
