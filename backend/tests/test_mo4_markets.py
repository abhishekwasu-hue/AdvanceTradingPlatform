"""MO4 slice 2 (ATP_SESSION_GROWTH_MASTER.md PART 2 items 4 and 6): the Sector & index view (app/markets) and the
Option chain "What the chain shows" summary (app/option_chain/dish.py), both behind `ui_v2` + `markets_view` (off).

Covers:
- the pure reads: sessions back per period, % change, breadth, the average, the move against the benchmark, the
  strongest / weakest stocks (a short list never names one stock twice);
- the overview and one list's stocks from seeded system lists and lake closes (index moves from the index's own
  closes, a sector's from its stocks' average, "I don't know" = null, never a made-up number);
- the flags (each one off is 404 and names itself), login required, an unknown list 404;
- the dish lines (PCR zone edges, OI walls, max pain against the spot, IV change since the session's first snapshot,
  the expected move, "I don't know" lines) and the analyses answer carrying the dish only with `markets_view` on;
- no advice wording anywhere.
"""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.brokers.models import OptionChain, OptionChainRow
from app.context_panel.service import index_key as real_index_key
from app.db.models import MdCandleRecord, SystemWatchlistRecord
from app.market_data.calendar import IST
from app.market_lake.bhavcopy_backfill import bar_end
from app.markets import service as M
from app.option_chain import analyses as A
from app.option_chain import dish as D
from app.platform import controls
from app.portfolio.risk_text import has_advice
from app.screener.taxonomy import advice_words
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_oc2_option_chain_analyses import _ChainBroker, _install, _ist_today, _store_snapshot, _v2_owner
from tests.test_phase_aa_market_data_api import _store_credentials
from tests.test_phase_k_commercial import _owner

UTC = timezone.utc
run = asyncio.run


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _clean(text: str) -> bool:
    return not has_advice(text) and not advice_words(text)


# --- pure ------------------------------------------------------------------------------------------------------------

def test_session_refs_take_one_five_and_twenty_one_sessions_back():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(30)]
    refs = M.session_refs(days)
    assert refs["as_of"] == days[-1] and refs["day"] == days[-2] and refs["week"] == days[-6] and refs["month"] == days[-22]
    short = M.session_refs(days[:4])
    assert short["day"] == days[2] and short["week"] is None and short["month"] is None
    assert M.session_refs([]) == {"as_of": None, "day": None, "week": None, "month": None}


def test_changes_breadth_average_and_relative():
    d0, d1, d2 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)
    refs = {"as_of": d2, "day": d1, "week": d0, "month": None}
    assert M.changes({d0: 100.0, d1: 110.0, d2: 121.0}, refs) == {"day": 10.0, "week": 21.0, "month": None}
    assert M.changes({d0: 100.0, d1: 110.0}, refs) == {"day": None, "week": None, "month": None}      # no close today
    assert M.pct_change(10.0, 0.0) is None and M.pct_change(None, 5.0) is None
    assert M.breadth([1.0, -2.0, 0.0, None, 3.0]) == {"advances": 2, "declines": 1, "unchanged": 1, "counted": 4, "missing": 1}
    assert M.average([1.0, None, 2.0]) == 1.5 and M.average([None]) is None
    assert M.relative({"day": 1.5, "week": None, "month": 2.0}, {"day": 0.5, "week": 1.0, "month": None}) == {"day": 1.0, "week": None, "month": None}


def test_extremes_rank_by_move_and_never_name_one_stock_twice():
    def m(symbol, day):
        return {"symbol": symbol, "exchange": "NSE", "name": symbol.title(), "change": {"day": day, "week": None, "month": None}}
    members = [m("AAA", 1.0), m("BBB", -3.0), m("CCC", 2.0), m("DDD", None), m("EEE", 2.0)]
    strongest, weakest = M.extremes(members, "day")
    assert [s["symbol"] for s in strongest] == ["CCC", "EEE", "AAA"]              # a tie goes by symbol
    assert [w["symbol"] for w in weakest] == ["BBB"]                               # the rest only; DDD has no move
    assert M.extremes(members, "week") == ([], [])


# --- seeded lists and closes -----------------------------------------------------------------------------------------

def _weekdays(n: int, end: date):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _latest_weekday() -> date:
    d = datetime.now(UTC).astimezone(IST).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _seed(key: str, closes_by_day):
    async def go():
        async with _session_factory() as session:
            for d, c in closes_by_day.items():
                await session.merge(MdCandleRecord(instrument_key=key, timeframe="day", ts=bar_end(d), source="test-mo4", version=5,
                                                   open=c, high=c, low=c, close=c, volume=1))
            await session.commit()
    run(go())


def _row(key, group, name, items, position=0):
    return SystemWatchlistRecord(key=key, group=group, name=name, position=position, items_json=json.dumps(items), count=len(items),
                                 checksum="x", changed_at=datetime.now(UTC), refreshed_at=datetime.now(UTC))


@pytest.fixture()
def world(monkeypatch):
    """System lists held in memory (other tests own the stored ones) and lake keys unique to this test."""
    tag = "T" + uuid.uuid4().hex[:6].upper()
    monkeypatch.setattr(M, "index_key", lambda name: f"{real_index_key(name)}{tag}")
    days = _weekdays(25, _latest_weekday())
    s = lambda x: f"{tag}{x}"  # noqa: E731
    bank = [{"symbol": s("BK1"), "exchange": "NSE", "name": "Bank One"}, {"symbol": s("BK2"), "exchange": "NSE", "name": "Bank Two"},
            {"symbol": s("BK3"), "exchange": "NSE", "name": "Bank Three"}]
    it = [{"symbol": s("IT1"), "exchange": "NSE", "name": "Soft One"}, {"symbol": s("IT2"), "exchange": "NSE", "name": "Soft Two"},
          {"symbol": s("NODATA"), "exchange": "NSE", "name": "No Data"}]
    rows = [
        _row("indices", "INDICES", "Indices", [{"symbol": "NIFTY 50", "exchange": "NSE", "name": "NIFTY 50"},
                                               {"symbol": "NIFTY BANK", "exchange": "NSE", "name": "NIFTY BANK"},
                                               {"symbol": "NIFTY IT", "exchange": "NSE", "name": "NIFTY IT"},
                                               {"symbol": "SENSEX", "exchange": "BSE", "name": "SENSEX"}]),
        _row("index-niftybank", "INDEX_STOCKS", "NIFTY BANK stocks (3)", bank),
        _row("sector-it", "SECTORS", "IT", it),
        _row("fno-stocks", "FNO", "F&O stocks (1)", bank[:1]),
    ]
    by_key = {r.key: r for r in rows}

    async def list_all(session):
        return rows

    async def get(session, key):
        return by_key.get(key)

    monkeypatch.setattr(M.system, "list_all", list_all)
    monkeypatch.setattr(M.system, "get", get)
    # Closes: the benchmark +1% on the day; NIFTY BANK +2% on the day and +5% over the week; BK1 +3, BK2 -1, BK3 flat on the
    # day; IT1 +4, IT2 -2 on the day; NODATA none; NIFTY IT nothing stored (I don't know).
    last, prev, week = days[-1], days[-2], days[-6]

    def path(base: float, week_v: float, prev_v: float, last_v: float):
        return {d: (last_v if d == last else prev_v if d == prev else week_v if d == week else base) for d in days}
    _seed(f"{real_index_key('NIFTY 50')}{tag}", path(100.0, 100.0, 100.0, 101.0))
    _seed(f"{real_index_key('NIFTY BANK')}{tag}", path(100.0, 100.0, 102.9411764706, 105.0))
    _seed(f"NSE:{s('BK1')}", path(100.0, 100.0, 100.0, 103.0))
    _seed(f"NSE:{s('BK2')}", path(100.0, 100.0, 100.0, 99.0))
    _seed(f"NSE:{s('BK3')}", path(100.0, 100.0, 100.0, 100.0))
    _seed(f"NSE:{s('IT1')}", path(100.0, 100.0, 100.0, 104.0))
    _seed(f"NSE:{s('IT2')}", path(100.0, 100.0, 100.0, 98.0))
    M.reset_cache()
    yield {"tag": tag, "s": s, "days": days}
    M.reset_cache()


def _overview():
    async def go():
        async with _session_factory() as session:
            return await M.overview(session, use_cache=False)
    return run(go())


def test_overview_moves_breadth_extremes_and_unknowns(world):
    s = world["s"]
    out = _overview()
    assert out["state"] == "ok" and out["as_of"] == world["days"][-1].isoformat()
    assert out["benchmark"]["change"]["day"] == 1.0 and out["benchmark"]["change"]["week"] == 1.0
    by = {g["name"]: g for g in out["indices"]}
    bank = by["NIFTY BANK"]
    assert bank["change"]["day"] == 2.0 and bank["change"]["week"] == 5.0 and bank["vs_benchmark"]["day"] == 1.0
    assert bank["list_key"] == "index-niftybank" and bank["family"] == "SECTORAL" and bank["count"] == 3
    assert bank["breadth"]["day"] == {"advances": 1, "declines": 1, "unchanged": 1, "counted": 3, "missing": 0}
    assert [x["symbol"] for x in bank["strongest"]["day"]] == [s("BK1"), s("BK3"), s("BK2")]
    assert bank["weakest"]["day"] == []                                    # three stocks: all are in the strongest
    assert bank["close"] == 105.0
    # NIFTY IT: nothing stored and no constituents list here -> not known, never zero.
    assert by["NIFTY IT"]["change"] == {"day": None, "week": None, "month": None} and by["NIFTY IT"]["known"] is False
    assert by["NIFTY IT"]["breadth"] is None and by["NIFTY IT"]["list_key"] is None
    # SENSEX (BSE): no BSE closes in the lake -> not known; the family is BSE and comes last.
    assert by["SENSEX"]["family"] == "BSE" and by["SENSEX"]["known"] is False and out["indices"][-1]["name"] == "SENSEX"
    assert [f["key"] for f in out["families"]] == ["BROAD", "SECTORAL", "BSE"]
    sector = out["sectors"][0]
    assert sector["name"] == "IT" and sector["kind"] == "sector" and sector["change"]["day"] == 1.0   # (4 - 2) / 2
    assert sector["breadth"]["day"]["missing"] == 1 and sector["vs_benchmark"]["day"] == 0.0
    assert [x["symbol"] for x in sector["strongest"]["day"]] == [s("IT1"), s("IT2")]
    assert sector["change"]["month"] == 1.0                                 # 25 sessions: a month back is stored
    assert all(_clean(t) for t in _strings(out) if not t[:1].isdigit())


def test_list_detail_sorts_by_day_move_and_names_the_index(world):
    s = world["s"]

    async def go(key):
        async with _session_factory() as session:
            return await M.list_detail(session, key)
    out = run(go("index-niftybank"))
    assert out["name"] == "NIFTY BANK" and out["kind"] == "index" and out["symbol"] == "NIFTY BANK"
    assert [m["symbol"] for m in out["members"]] == [s("BK1"), s("BK3"), s("BK2")]
    assert out["members"][0]["change"]["day"] == 3.0 and out["members"][0]["vs_benchmark"]["day"] == 2.0
    assert out["summary"]["change"]["day"] == 2.0
    it = run(go("sector-it"))
    assert it["kind"] == "sector" and it["members"][-1]["symbol"] == s("NODATA") and it["members"][-1]["change"]["day"] is None
    assert run(go("fno-stocks")) is None and run(go("indices")) is None and run(go("no-such-list")) is None


def test_no_lists_and_no_closes_are_plain_states(monkeypatch):
    async def none(session):
        return []
    monkeypatch.setattr(M.system, "list_all", none)
    out = _overview()
    assert out["state"] == "no_lists" and out["message"] == M.NO_LISTS and out["indices"] == []
    monkeypatch.setattr(M, "index_key", lambda name: f"NSE_INDEX:NOTHING{uuid.uuid4().hex[:6]}")

    async def one(session):
        return [_row("indices", "INDICES", "Indices", [{"symbol": "NIFTY 50", "exchange": "NSE", "name": "NIFTY 50"}])]
    monkeypatch.setattr(M.system, "list_all", one)
    out = _overview()
    assert out["state"] == "no_data" and out["message"].startswith("I don't know")


# --- API -------------------------------------------------------------------------------------------------------------

def test_flags_login_and_unknown_list(world):
    assert M.FLAG in controls.FEATURE_FLAGS and M.FLAG in controls.DEFAULT_OFF_FLAGS
    headers, me = _owner(f"mo4-{uuid.uuid4().hex[:8]}@example.com")
    r = client.get("/api/markets/overview", headers=headers)
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "ui_v2"
    _set_flag("ui_v2", me["tenant_id"], on=True)
    r = client.get("/api/markets/overview", headers=headers)
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "markets_view"
    _set_flag("markets_view", me["tenant_id"], on=True)
    r = client.get("/api/markets/overview", headers=headers)
    assert r.status_code == 200 and r.json()["state"] == "ok"
    assert {g["name"] for g in r.json()["indices"]} >= {"NIFTY BANK", "SENSEX"}
    assert client.get("/api/markets/lists/index-niftybank", headers=headers).json()["name"] == "NIFTY BANK"
    assert client.get("/api/markets/lists/no-such-list", headers=headers).status_code == 404
    assert client.get("/api/markets/lists/fno-stocks", headers=headers).status_code == 404
    assert client.get("/api/markets/overview").status_code == 401
    # Another organisation without the flag still gets 404 (the flag is per organisation).
    other_headers, _ = _owner(f"mo4-other-{uuid.uuid4().hex[:8]}@example.com")
    assert client.get("/api/markets/overview", headers=other_headers).status_code == 404


# --- the Option chain dish -------------------------------------------------------------------------------------------

def _r(strike, call_oi, put_oi, call_ltp=None, put_ltp=None, call_iv=None, put_iv=None):
    return OptionChainRow(strike=strike, call_oi=call_oi, put_oi=put_oi, call_ltp=call_ltp, put_ltp=put_ltp, call_iv=call_iv, put_iv=put_iv)


EXPIRY = "2026-10-27"
NOW = datetime(2026, 10, 21, 4, 30, tzinfo=UTC)               # 10:00 IST
CHAIN = OptionChain(underlying="DEMO", expiry=EXPIRY, underlying_ltp=118.0, rows=[
    _r(100, 10, 50), _r(110, 20, 40), _r(120, 40, 30, None, None, 14.0, 16.0), _r(130, 60, 10), _r(140, 30, 5)])
PRICED = OptionChain(underlying="DEMO", expiry=EXPIRY, underlying_ltp=118.0, rows=[
    _r(100, 10, 50), _r(110, 20, 40), _r(120, 40, 30, 6.0, 8.0), _r(130, 60, 10), _r(140, 30, 5)])
EARLIER = OptionChain(underlying="DEMO", expiry=EXPIRY, underlying_ltp=116.0, rows=[
    _r(100, 10, 50), _r(110, 20, 40), _r(120, 40, 30, None, None, 12.0, 14.0), _r(130, 60, 10), _r(140, 30, 5)])


def test_pcr_zones_follow_the_banner_band_edges():
    assert D.pcr_zone(None) is None
    assert D.pcr_zone(0.5)[:2] == ("very low", "under 0.70")
    assert D.pcr_zone(0.70)[0] == "low" and D.pcr_zone(0.89)[0] == "low"
    assert D.pcr_zone(0.90)[0] == "balanced" and D.pcr_zone(1.0)[0] == "balanced"
    assert D.pcr_zone(1.01)[:2] == ("high", "1.00 to 1.30") and D.pcr_zone(1.30)[0] == "high"
    assert D.pcr_zone(1.31)[:2] == ("very high", "above 1.30")


def test_dish_lines_from_the_analyses_and_the_first_snapshot():
    history = [A.Snapshot(datetime(2026, 10, 21, 3, 50, tzinfo=UTC), EARLIER)]                   # 09:20 IST
    results = A.compute_all(CHAIN, history, NOW)
    out = D.build(results, CHAIN, history)
    lines = {p["key"]: p for p in out["points"]}
    assert [p["key"] for p in out["points"]] == ["pcr_zone", "oi_walls", "max_pain", "iv_change", "expected_move"]
    assert lines["pcr_zone"]["text"] == ("PCR is 0.84, in the low zone (0.70 to 0.90): call open interest is larger than put open "
                                         "interest.")
    assert lines["oi_walls"]["text"] == ("The largest put open interest is at 100 and the largest call open interest is at 130; "
                                         "the spot 118.00 is between them.")
    assert lines["max_pain"]["text"] == "Max pain is 120, 2.00 points (1.69%) above the spot 118.00."
    assert lines["iv_change"]["text"] == "ATM IV is 15.00%, up 2.00 points since 09:20 IST (13.00% then)."
    assert lines["expected_move"]["state"] == "unknown"                     # the ATM options have no price here
    priced = {p["key"]: p for p in D.build(A.compute_all(PRICED, [], NOW), PRICED, [])["points"]}
    assert priced["expected_move"]["text"] == ("The ATM straddle prices a move of about 11.86% either way by expiry: 104.00 to "
                                               "132.00, with 6 days to expiry.")
    for text in _strings(out):
        assert _clean(text), text


def test_dish_says_i_dont_know_when_a_number_is_missing():
    empty = OptionChain(underlying="DEMO", expiry=EXPIRY, underlying_ltp=None, rows=[_r(100, 0, 0), _r(110, 0, 0)])
    out = D.build(A.compute_all(empty, [], NOW), empty, [])
    assert all(p["state"] == "unknown" and p["text"].startswith("I don't know") for p in out["points"])
    # No earlier snapshot: the IV line says when the change will show; the own-broker setting says why it is not shown.
    no_history = {p["key"]: p for p in D.build(A.compute_all(CHAIN, [], NOW), CHAIN, [])["points"]}
    assert no_history["iv_change"]["text"] == "ATM IV is 15.00%; its change shows once an earlier snapshot of this session is stored."
    byob = {p["key"]: p for p in D.build(A.compute_all(CHAIN, [], NOW, history_off=True), CHAIN, [], history_off=True)["points"]}
    assert "own-broker data setting" in byob["iv_change"]["text"]
    for text in _strings(out):
        assert _clean(text), text


def test_analyses_answer_carries_the_dish_only_with_markets_view(monkeypatch):
    name = "MOFOUR" + uuid.uuid4().hex[:5].upper()
    expiry = (_ist_today() + timedelta(days=7)).isoformat()
    current = OptionChain(underlying=name, expiry=expiry, underlying_ltp=118.0, rows=CHAIN.rows)
    _install(monkeypatch, _ChainBroker({name: current}))
    headers, me = _v2_owner("mo4-dish")
    _store_credentials(me)
    _store_snapshot(name, OptionChain(underlying=name, expiry=expiry, underlying_ltp=116.0, rows=EARLIER.rows),
                    datetime.now(UTC) - timedelta(minutes=30), me["tenant_id"])
    params = {"symbol": name, "expiry": expiry}
    body = client.get("/api/option-chain/analyses", headers=headers, params=params).json()
    assert body["state"] == "ok" and "dish" not in body
    _set_flag("markets_view", me["tenant_id"], on=True)
    body = client.get("/api/option-chain/analyses", headers=headers, params=params).json()
    assert body["dish"]["title"] == D.TITLE and len(body["dish"]["points"]) == 5
    iv = {p["key"]: p for p in body["dish"]["points"]}["iv_change"]["text"]
    assert iv.startswith("ATM IV is 15.00%, up 2.00 points since")
