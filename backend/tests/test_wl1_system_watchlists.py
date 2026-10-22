"""WL1 (handover item 4): the read-only system watchlists - Indices, Index stocks, Sectors and F&O stocks - built from the
U1 universe tables (fixture files, no network) and mapped to the instrument master; the reconstitution diff, the
unmapped symbols, the read-only rules (403), "Copy to my list", the admin's view and tenant isolation of the copies."""
import asyncio
import json
import pathlib
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, select

from app.core import config
from app.db.models import (ClassificationRecord, DataQualityEventRecord, FoMembershipRecord, IndexMembershipRecord, IndexRecord,
                           InstrumentRecord, SecurityRecord, SymbolHistoryRecord, SystemWatchlistRecord, SystemWatchlistRefreshRecord,
                           User, WatchlistRecord)
from app.universe import classification, fo_eod, indices, sources, sync
from app.watchlists import service, system
from tests.test_amw2_watchlists import _owner, _set_plan
from tests.test_auth_api import _session_factory, client
from tests.test_z3b_paper_account import _member
from tests.utils import enable_mfa

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "universe" / "wl1"
D0, D1 = date(2026, 10, 1), date(2026, 10, 8)
SYS = "/api/watchlists/system"
ADMIN = "/api/admin/system-watchlists"


def _run(coro):
    return asyncio.run(coro)


def _master_rows(equities=("ALPHAIND", "NEWCO", "GAMMA", "DELTA", "EPSILON", "ZETA")):
    rows = [InstrumentRecord(broker="upstox", exchange="NSE", segment="NSE_EQ", instrument_key=f"NSE_EQ|{s}", tradingsymbol=s,
                             name=f"{s.title()} Limited", underlying=s, instrument_type="EQ") for s in equities]
    for key, symbol, name, exchange in (("NSE_INDEX|Nifty 50", "NIFTY", "Nifty 50", "NSE"), ("NSE_INDEX|Nifty Bank", "BANKNIFTY", "Nifty Bank", "NSE"),
                                        ("NSE_INDEX|Nifty 500", "NIFTY 500", "Nifty 500", "NSE"),
                                        ("NSE_INDEX|Nifty Pvt Bank", "NIFTY PVT BANK", "Nifty Pvt Bank", "NSE"),
                                        ("BSE_INDEX|SENSEX", "SENSEX", "SENSEX", "BSE")):
        rows.append(InstrumentRecord(broker="upstox", exchange=exchange, segment=f"{exchange}_INDEX", instrument_key=key, tradingsymbol=symbol,
                                     name=name, underlying=symbol, instrument_type="INDEX"))
    return rows


async def _wipe(session):
    for model in (SystemWatchlistRecord, SystemWatchlistRefreshRecord, FoMembershipRecord, IndexMembershipRecord, ClassificationRecord,
                  IndexRecord, SymbolHistoryRecord, SecurityRecord, InstrumentRecord):
        await session.execute(delete(model))
    await session.execute(delete(DataQualityEventRecord).where(DataQualityEventRecord.source == "fixture"))


def _constituents(day: date, files: dict):
    async def go():
        async with _session_factory() as session:
            src = sources.StaticSource({f"constituents:{code}": content for code, content in files.items()}, source="fixture")
            return await indices.sync_indices(session, src, day, codes=list(files))
    return _run(go())


def _index_files(fifty: bytes = None):
    return {"NIFTY50": fifty or (FIX / "ind_test50list.csv").read_bytes(), "NIFTYBANK": (FIX / "ind_testbanklist.csv").read_bytes(),
            "NIFTY500": (FIX / "ind_test500list.csv").read_bytes()}


@pytest.fixture(autouse=True)
def universe():
    async def go():
        async with _session_factory() as session:
            await _wipe(session)
            await session.commit()
        async with _session_factory() as session:
            await sync.sync_equity_lists(session, sources.StaticSource({"equity_list": (FIX / "EQUITY_L.csv").read_bytes()}, source="fixture"), D0)
        async with _session_factory() as session:
            await fo_eod.sync_fo_and_index_eod(session, sources.StaticSource({"fo_lots": (FIX / "fo_mktlots.csv").read_bytes()}, source="fixture"), D0)
        async with _session_factory() as session:
            session.add_all(_master_rows())
            await session.commit()
    _run(go())
    _constituents(D0, _index_files())
    yield
    async def down():
        async with _session_factory() as session:
            await _wipe(session)
            await session.commit()
    _run(down())


def _refresh():
    async def go():
        async with _session_factory() as session:
            return await system.refresh(session)
    return _run(go())


def _lists():
    async def go():
        async with _session_factory() as session:
            return {r.key: system.as_dict(r) for r in await system.list_all(session)}
    return _run(go())


def _symbols(listed: dict, key: str):
    return [i["symbol"] for i in listed[key]["items"]]


# --- the three groups (and F&O) ---------------------------------------------------------------------------------------

def test_the_groups_are_built_from_the_universe_and_mapped_to_the_master():
    report = _refresh()
    listed = _lists()
    assert [v["group"] for v in listed.values()] == sorted((v["group"] for v in listed.values()), key=system.GROUPS.index)

    # a. Indices: ONE list of the indices themselves; the derivative indices first, SENSEX from the BSE master rows.
    assert [k for k, v in listed.items() if v["group"] == "INDICES"] == ["indices"]
    assert [(i["symbol"], i["exchange"]) for i in listed["indices"]["items"]] == [
        ("NIFTY 50", "NSE"), ("NIFTY BANK", "NSE"), ("SENSEX", "BSE"), ("NIFTY 500", "NSE"), ("NIFTY PVT BANK", "NSE")]

    # b. Index stocks: one list per index, "<INDEX> stocks (<count>)"; only what maps to the master is listed.
    stocks = {k: v["name"] for k, v in listed.items() if v["group"] == "INDEX_STOCKS"}
    assert stocks == {"index-nifty50": "NIFTY 50 stocks (4)", "index-niftybank": "NIFTY BANK stocks (2)", "index-nifty500": "NIFTY 500 stocks (6)"}
    assert _symbols(listed, "index-nifty50") == ["ALPHAIND", "GAMMA", "NEWCO", "ZETA"]
    assert listed["index-nifty50"]["count"] == 4 and listed["index-nifty50"]["read_only"] is True
    assert listed["index-nifty50"]["items"][1] == {"symbol": "GAMMA", "exchange": "NSE", "name": "Gamma Bank Limited"}

    # c. Sectors: every classified company of the sector, named for traders (sectors.json), in its order.
    sectors = [(v["name"], _symbols(listed, k)) for k, v in listed.items() if v["group"] == "SECTORS"]
    assert sectors == [("Financial Services", ["GAMMA", "NEWCO"]), ("IT", ["ZETA"]), ("Automobile", ["EPSILON"]),
                       ("Healthcare", ["DELTA"]), ("Capital Goods", ["ALPHAIND"])]
    assert "sector-banking" not in listed           # the industry level is not known from the constituent files alone

    # F&O: the stocks with derivatives (the index underlying is in Indices).
    assert listed["fno-stocks"]["name"] == "F&O stocks (2)" and _symbols(listed, "fno-stocks") == ["ALPHAIND", "GAMMA"]
    assert report.lists_total == len(listed) == 10 and report.lists_changed == 10


def test_unmapped_symbols_are_left_out_and_logged_for_the_admin(caplog):
    with caplog.at_level("WARNING", logger="app.watchlists.system"):
        report = _refresh()
    assert "not mapped to the instrument master" in caplog.text

    async def go():
        async with _session_factory() as session:
            return await session.get(SystemWatchlistRefreshRecord, report.refresh_id)
    record = _run(go())
    unmapped = json.loads(record.unmapped_json)
    assert record.unmapped_count == len(unmapped) == report.unmapped
    reasons = {(u["list"], u["symbol"]): u["reason"] for u in unmapped}
    assert reasons[("NIFTY 50 stocks", "OMEGA")] == "not an NSE equity in the instrument master"     # listed, no master row
    assert reasons[("NIFTY 50 stocks", "INE00U1A1094")] == "not in the exchange's equity list"       # SIGMA: ISIN unknown
    assert reasons[("Indices", "NIFTY NEXT 50")] == "no index row in the instrument master"
    assert reasons[("Indices", "BANKEX")] == "no index row in the instrument master"
    assert reasons[("F&O stocks", "OMEGA")] == "not an NSE equity in the instrument master"
    assert ("Power", "INE00U1A1094") in reasons and "sector-power" not in _lists()                     # never an empty list


def test_a_full_classification_file_adds_the_industry_lists():
    async def go():
        async with _session_factory() as session:
            src = sources.StaticSource({"classification": (FIX / "classification_full.csv").read_bytes()}, source="fixture")
            return await classification.sync_classification(session, src, D0)
    report = _run(go())
    assert report.rows == 4 and report.changed == 3 and report.unknown == ["UNKNOWNCO"]
    _refresh()
    listed = _lists()
    assert _symbols(listed, "sector-banking") == ["GAMMA"] and _symbols(listed, "sector-pharma") == ["DELTA"]
    assert _symbols(listed, "sector-financial-services") == ["GAMMA", "NEWCO"]        # the sector list keeps every company
    names = [v["name"] for v in listed.values() if v["group"] == "SECTORS"]
    assert names[:3] == ["Banking", "Pharma", "Financial Services"]

    # A constituent file's sector-only value does not overwrite the finer levels the full file gave.
    moved = (FIX / "ind_testbanklist.csv").read_bytes().replace(b"Gamma Bank Ltd.,Financial Services", b"Gamma Bank Ltd.,Capital Goods")
    _constituents(D1, {"NIFTYBANK": moved})

    async def gamma():
        async with _session_factory() as session:
            return await indices.classification_on(session, "INE00U1A1037", D1)
    assert _run(gamma()) == {"macro_sector": "Financial Services", "sector": "Financial Services", "industry": "Banks",
                             "basic_industry": "Private Sector Bank"}


def test_a_classification_file_without_a_known_layout_is_refused():
    async def go():
        async with _session_factory() as session:
            return await classification.sync_classification(session, sources.StaticSource({"classification": b"Foo,Bar\n1,2\n"}, source="fixture"), D0)
    assert "unexpected file layout" in _run(go()).refused


# --- refresh: idempotent, the reconstitution diff and the gates ---------------------------------------------------------

def test_refresh_is_idempotent_and_records_the_reconstitution_diff(monkeypatch):
    first = _refresh()
    assert all(c["kind"] == "created" for c in first.changes)
    again = _refresh()
    assert again.lists_changed == 0 and again.changes == []

    # The index provider's next file: ZETA leaves NIFTY 50, DELTA joins.
    monkeypatch.setattr(config, "UNIVERSE_MAX_CHURN_RATIO", 1.0)
    fifty = (FIX / "ind_test50list.csv").read_bytes().replace(b"Zeta Software Ltd.,Information Technology,ZETA,EQ,INE00U1A1078",
                                                              b"Delta Pharma Ltd.,Healthcare,DELTA,EQ,INE00U1A1052")
    [rebalance] = _constituents(D1, {"NIFTY50": fifty})
    assert rebalance.entered == ["INE00U1A1052"] and rebalance.exited == ["INE00U1A1078"]
    diff = _refresh()
    assert diff.changes == [{"key": "index-nifty50", "name": "NIFTY 50 stocks (4)", "kind": "changed", "added": ["DELTA"], "removed": ["ZETA"]}]
    assert _symbols(_lists(), "index-nifty50") == ["ALPHAIND", "DELTA", "GAMMA", "NEWCO"]
    assert _symbols(_lists(), "sector-it") == ["ZETA"]                                  # still an IT company, only not in the index


def test_a_list_that_would_shrink_too_far_is_held_and_an_empty_master_changes_nothing():
    _refresh()

    async def keep_only(symbols):
        async with _session_factory() as session:
            await session.execute(delete(InstrumentRecord).where(InstrumentRecord.instrument_type == "EQ",
                                                                 InstrumentRecord.tradingsymbol.not_in(symbols)))
            await session.commit()
    _run(keep_only(["ALPHAIND"]))                    # a half-synced master: most equities missing for a day
    held = _refresh()
    assert {c["key"] for c in held.changes if c["kind"] == "held"} >= {"index-nifty50", "index-nifty500"}
    assert _symbols(_lists(), "index-nifty50") == ["ALPHAIND", "GAMMA", "NEWCO", "ZETA"]
    assert {"sector-it", "sector-healthcare"} <= {c["key"] for c in held.changes if c["kind"] == "held"}   # mapped to nothing today
    assert _symbols(_lists(), "sector-it") == ["ZETA"] and held.lists_total == 10

    _run(keep_only([]))
    empty = _refresh()
    assert "no NSE equities" in empty.note and empty.changes == [] and len(_lists()) == 10


def test_a_list_whose_source_is_gone_is_dropped_with_its_symbols_in_the_diff():
    _refresh()

    async def no_fo():
        async with _session_factory() as session:
            await session.execute(delete(FoMembershipRecord))
            await session.commit()
    _run(no_fo())
    report = _refresh()
    assert {"key": "fno-stocks", "name": "F&O stocks (2)", "kind": "dropped", "added": [], "removed": ["ALPHAIND", "GAMMA"]} in report.changes
    assert "fno-stocks" not in _lists()


# --- the routes: read, read-only (403), copy, tenancy, flag -------------------------------------------------------------

def test_users_read_the_groups_and_a_list():
    _refresh()
    who = _owner("wl1-read")
    body = client.get(SYS, headers=who["headers"]).json()
    assert [g["label"] for g in body["groups"]] == ["Indices", "Index stocks", "Sectors", "F&O"]
    assert body["refreshed_at"] and all("items" not in item for g in body["groups"] for item in g["lists"])
    one = client.get(f"{SYS}/index-niftybank", headers=who["headers"])
    assert one.status_code == 200 and [i["symbol"] for i in one.json()["items"]] == ["GAMMA", "NEWCO"] and one.json()["read_only"]
    assert client.get(f"{SYS}/no-such-list", headers=who["headers"]).status_code == 404
    assert client.get(f"{SYS}/Bad Key!", headers=who["headers"]).status_code == 404
    # The user's own lists are unchanged by the system lists (still only their own rows).
    assert client.get("/api/watchlists", headers=who["headers"]).json() == []


def test_system_lists_are_read_only_for_everyone():
    _refresh()
    who = _owner("wl1-ro")
    before = _lists()
    for method in ("PUT", "DELETE"):
        response = client.request(method, f"{SYS}/indices", headers=who["headers"], json={"name": "Mine", "items": []})
        assert response.status_code == 403 and "read-only" in response.json()["detail"], (method, response.text)
    assert _lists() == before


def test_the_flag_off_hides_the_system_lists():
    _refresh()
    who = _owner("wl1-off", flag=False)
    for method, path in (("GET", SYS), ("GET", f"{SYS}/indices"), ("POST", f"{SYS}/indices/copy"), ("PUT", f"{SYS}/indices")):
        response = client.request(method, path, headers=who["headers"])
        assert response.status_code == 404 and response.headers.get("X-Feature-Disabled") == service.FLAG, (method, path)
    assert client.get(SYS).status_code in (401, 403)


def test_copy_to_my_list_makes_an_editable_list_of_the_callers_own():
    _refresh()
    who = _owner("wl1-copy")
    made = client.post(f"{SYS}/index-nifty50/copy", headers=who["headers"])
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["name"] == "NIFTY 50 stocks" and body["copied_from"] == "index-nifty50" and body["truncated"] is False
    assert [i["symbol"] for i in body["items"]] == ["ALPHAIND", "GAMMA", "NEWCO", "ZETA"] and body["symbols"] == ["ALPHAIND", "GAMMA", "NEWCO", "ZETA"]
    assert client.post(f"{SYS}/index-nifty50/copy", headers=who["headers"]).json()["name"] == "NIFTY 50 stocks 2"
    named = client.post(f"{SYS}/indices/copy", headers=who["headers"], json={"name": "My indices"})
    assert named.status_code == 201 and [i["exchange"] for i in named.json()["items"]][:3] == ["NSE", "NSE", "BSE"]
    assert client.post(f"{SYS}/indices/copy", headers=who["headers"], json={"name": "my INDICES"}).status_code == 409
    assert client.post(f"{SYS}/nope/copy", headers=who["headers"]).status_code == 404

    # The copy is the user's: editable, and editing it leaves the system list as it was.
    edited = client.put(f"/api/watchlists/{body['id']}", headers=who["headers"], json={"name": "Mine", "items": [{"symbol": "INFY", "exchange": "NSE"}]})
    assert edited.status_code == 200
    assert _symbols(_lists(), "index-nifty50") == ["ALPHAIND", "GAMMA", "NEWCO", "ZETA"]


def test_a_copy_of_a_longer_list_keeps_the_first_rows_and_says_so(monkeypatch):
    _refresh()
    monkeypatch.setattr(service, "MAX_SYMBOLS", 2)
    body = client.post(f"{SYS}/index-nifty500/copy", headers=_owner("wl1-long")["headers"]).json()
    assert body["truncated"] is True and body["source_count"] == 6 and len(body["items"]) == 2


def test_copy_respects_the_per_user_cap(monkeypatch):
    _refresh()
    monkeypatch.setattr(service, "MAX_PER_USER", 1)
    who = _owner("wl1-cap")
    assert client.post(f"{SYS}/indices/copy", headers=who["headers"]).status_code == 201
    assert client.post(f"{SYS}/fno-stocks/copy", headers=who["headers"]).status_code == 409


def test_copies_are_isolated_by_organisation_and_member():
    _refresh()
    a = _owner("wl1-ta")
    _set_plan(a["tenant_id"], "pro")                      # Free allows one member
    member = _member(a)
    b = _owner("wl1-tb")
    made = client.post(f"{SYS}/sector-it/copy", headers=a["headers"]).json()

    async def row():
        async with _session_factory() as session:
            r = await session.get(WatchlistRecord, made["id"])
            return r.tenant_id, r.user_id
    assert _run(row()) == (a["tenant_id"], a["user_id"])
    assert client.get("/api/watchlists", headers=b["headers"]).json() == []
    assert client.get("/api/watchlists", headers=member["headers"]).json() == []
    assert client.put(f"/api/watchlists/{made['id']}", headers=b["headers"], json={"name": "x", "items": []}).status_code == 404
    assert client.delete(f"/api/watchlists/{made['id']}", headers=member["headers"]).status_code == 404
    # Everyone reads the same system lists.
    assert client.get(f"{SYS}/sector-it", headers=b["headers"]).json()["items"] == client.get(f"{SYS}/sector-it", headers=a["headers"]).json()["items"]
    # b copies under the same name: its own list, no clash with a's.
    assert client.post(f"{SYS}/sector-it/copy", headers=b["headers"]).json()["name"] == "IT"


# --- the admin's view ----------------------------------------------------------------------------------------------------

def _admin():
    who = _owner(f"wl1-admin-{uuid.uuid4().hex[:4]}")

    async def promote():
        async with _session_factory() as session:
            user = await session.get(User, who["user_id"])
            user.role = "SUPER_ADMIN"
            await session.commit()
    _run(promote())
    enable_mfa(who["headers"])
    return who


def test_the_admin_sees_the_last_refresh_the_changes_and_the_unmapped_symbols():
    admin, owner = _admin(), _owner("wl1-notadmin")
    assert client.get(ADMIN, headers=owner["headers"]).status_code == 403
    assert client.post(f"{ADMIN}/refresh", headers=owner["headers"]).status_code == 403
    assert client.get(ADMIN).status_code in (401, 403)

    empty = client.get(ADMIN, headers=admin["headers"]).json()
    assert empty["last_refresh"] is None and empty["lists"] == 0

    ran = client.post(f"{ADMIN}/refresh", headers=admin["headers"])
    assert ran.status_code == 200 and ran.json()["lists_total"] == 10 and ran.json()["unmapped"] > 0
    view = client.get(ADMIN, headers=admin["headers"]).json()
    last = view["last_refresh"]
    assert last["finished_at"] and last["lists_changed"] == 10 and last["unmapped_count"] == len(last["unmapped"])
    assert {"list": "Indices", "symbol": "BANKEX", "reason": "no index row in the instrument master"} in last["unmapped"]
    assert {c["key"] for c in last["changes"]} == set(_lists())
    assert view["groups"] == {"INDICES": 1, "INDEX_STOCKS": 3, "SECTORS": 5, "FNO": 1}
    assert len(view["history"]) == 1 and "unmapped" not in view["history"][0]


def test_refresh_records_are_capped(monkeypatch):
    monkeypatch.setattr(system, "KEEP_REFRESHES", 3)
    for _ in range(5):
        _refresh()

    async def count():
        async with _session_factory() as session:
            return len(list(await session.scalars(select(SystemWatchlistRefreshRecord.id))))
    assert _run(count()) == 3


def test_names_and_keys():
    assert system.slug("Oil & Gas") == "oil-and-gas" and system.slug("F&O stocks") == "f-and-o-stocks"
    assert system._norm("NIFTY OIL & GAS") == system._norm("Nifty Oil and Gas") and system._norm("NIFTY HEALTHCARE INDEX") == system._norm("Nifty Healthcare")
    specs = system.sector_specs()
    assert len({s["name"] for s in specs}) == len(specs) and {"Banking", "IT", "Automobile", "Pharma"} <= {s["name"] for s in specs}
    assert all(s["level"] in ("sector", "industry") and s["values"] for s in specs)


def test_the_worker_refresh_gate_runs_at_start_and_every_evening():
    from datetime import date as _date
    today, yesterday = _date(2026, 10, 22), _date(2026, 10, 21)
    due = lambda **k: system.refresh_due(**{"nse_open": False, "hour": 8, "today": today, "sync_hour": 18, "last_day": None, "started": False, **k})  # noqa: E731
    assert due() == (True, False)                                          # a morning deploy: one start-up run, not the day's
    assert due(started=True) == (False, False)                             # ...and not again before the evening
    assert due(started=True, hour=18) == (True, True)                      # the evening run still follows the universe sync
    assert due(started=True, hour=19, last_day=today) == (False, False)    # once per day
    assert due(started=True, hour=19, last_day=yesterday) == (True, True)
    assert due(hour=20) == (True, True)                                    # an evening deploy: one run, it is the day's
    assert due(nse_open=True) == (False, False)                            # never while NSE is open
    assert due(nse_open=True, hour=19, started=True) == (False, False)
