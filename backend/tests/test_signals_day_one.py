"""Signals day one (owner order 23 Oct): the setup card and the adopted watch on Signals (app/signals_feed/day_one.py).

- the flags (ui_v2 + strategy_templates, 404 while either is off) and the card's choices: the ready strategies that are
  switched on, white box with every default setting; the lists, NIFTY 50 first; his own lists with `watchlists` on;
- adopting: PAPER and approval-only entries by construction (no request field can ask for LIVE or AUTO), nothing else is
  created (no deployment, no proposal, no order), a VIEWER cannot adopt, an audit row;
- the first-run evaluation: each symbol's state on its last closed Daily bar, in parity with the scan path and the
  worker path of the parity harness on the same bars; closed shared-lake bars only (no look-ahead, no broker rows);
- plain empty states when there is no data (never sample data), the outdated adoption, refresh and stop;
- per user and tenant: another member or organisation never sees or changes the watch, nor uses another's list;
- the signal language on every text the watch can show.
All data here is made up for the test."""
import asyncio
import json
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.db.models import (AiActionRecord, AuditLogRecord, MdCandleRecord, OrderRecord, StrategyDeploymentRecord, SystemWatchlistRecord,
                           UserPrefsRecord, WatchlistRecord)
from app.market_data.calendar import IST
from app.signals import language as L
from app.signals_feed import day_one as D
from app.strategy_templates import ids, library, parity
from app.strategy_templates.adapter import TemplateStrategy
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import _upgrade_plan
from tests.test_z3_members import member

URL = "/api/signals/day-one"
UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


def _owner(tag: str, *, flags=("ui_v2", "strategy_templates")) -> dict:
    headers = {"Authorization": f"Bearer {_register(f'sig1-{tag}-{uuid.uuid4().hex[:6]}@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    for f in flags:
        _set_flag(f, me["tenant_id"], on=True)
    return {"headers": headers, "tenant_id": me["tenant_id"], "user_id": me["id"]}


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
            return [getattr(r, "id", None) for r in rows]
    return _run(go())


def _count(model, *where) -> int:
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)
    return _run(go())


def _days(n: int, last: date) -> list:
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _last_session() -> date:
    """The last weekday strictly before today (IST): its Daily bar ended in the past."""
    d = datetime.now(UTC).astimezone(IST).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _bars(closes, *, spread=0.5) -> pd.DataFrame:
    days = _days(len(closes), _last_session())
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c, "volume": 1000.0},
                        index=pd.DatetimeIndex([D.bar_start(d) for d in days]))


def _seed(symbol: str, frame: pd.DataFrame, *, source="nse_bhav:cm") -> None:
    rows = []
    for ts, r in frame.iterrows():
        day = pd.Timestamp(ts).tz_convert(IST).date()
        end = datetime.combine(day, dtime(15, 30), IST).astimezone(UTC)
        rows.append(MdCandleRecord(instrument_key=f"NSE:{symbol}", timeframe="1d", ts=end, source=source, version=1, open=float(r["open"]),
                                   high=float(r["high"]), low=float(r["low"]), close=float(r["close"]), volume=int(r["volume"]),
                                   ingested_at=end))
    _add(*rows)


def _system_list(symbols, *, group="INDEX_STOCKS") -> str:
    key = f"index-demo{uuid.uuid4().hex[:8]}"
    items = [{"symbol": s, "exchange": "NSE", "name": f"Demo company {i}"} for i, s in enumerate(symbols)]
    _add(SystemWatchlistRecord(key=key, group=group, name=f"Demo index stocks ({len(items)})", position=0, items_json=json.dumps(items),
                               count=len(items), checksum=uuid.uuid4().hex))
    return key


def _sym() -> str:
    return f"DEMO{uuid.uuid4().hex[:6].upper()}"


UP = list(100 + np.arange(200) * 0.5)
PULLBACK = UP + [UP[-1] - k for k in range(1, 6)]           # D3: the pullback reached the fast average (armed)
FLAT = list(100 + np.sin(np.arange(200)) * 1.0)
BREAKOUT = FLAT + [106.0]                                     # B1: the close broke out above the range on the last bar


def _strategy(tid: str) -> TemplateStrategy:
    t = library.get(tid)
    _, s = ids.adopt(t, D.TIMEFRAME)
    return TemplateStrategy(t, s)


# --- flags and the card ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["ui_v2", "strategy_templates"])
def test_the_card_is_off_while_either_flag_is_off(missing):
    t = _owner("off", flags=tuple(f for f in ("ui_v2", "strategy_templates") if f != missing))
    for method, path in (("get", URL), ("post", URL + "/refresh"), ("delete", URL)):
        r = getattr(client, method)(path, headers=t["headers"])
        assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == missing
    r = client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": "index-nifty50"}})
    assert r.status_code == 404


def test_login_is_required():
    assert client.get(URL).status_code == 401
    assert client.post(URL + "/adopt", json={}).status_code == 401


def test_first_visit_offers_the_switched_on_ready_strategies_white_box_and_nifty_50_first(monkeypatch):
    t = _owner("card")
    other = _system_list([_sym()])
    default = _system_list([_sym(), _sym()])
    monkeypatch.setattr(D, "DEFAULT_LIST", default)
    body = client.get(URL, headers=t["headers"]).json()
    assert body["adopted"] is None and body["outdated"] is False and body["can_adopt"] is True
    # the breakout templates need their own screen flags: only D3 is offered while they are off
    assert [o["id"] for o in body["offers"]] == ["D3"]
    d3 = body["offers"][0]
    assert d3["name"] == "Moving-average pullback in trend" and d3["idea"] and d3["timeframe"] == "Daily"
    shown = {s["key"]: s["value"] for s in d3["settings"]}
    assert shown["ma_type"] == "Exponential" and shown["fast"] == "20" and shown["slow"] == "50"   # the template's own defaults, in words
    assert len(d3["settings"]) == len(library.get("D3").params) and d3["rules"] == ["The pullback average must be shorter than the trend average."]
    assert body["lists"][0]["key"] == default and body["lists"][0]["default"] is True
    assert body["default_list"] == {"kind": "system", "key": default}
    assert any(c["key"] == other for c in body["lists"])
    _set_flag("screener_breakouts", t["tenant_id"], on=True)
    _set_flag("screener_breakouts_bk4", t["tenant_id"], on=True)
    assert [o["id"] for o in client.get(URL, headers=t["headers"]).json()["offers"]] == ["A5", "B1", "D3"]


def test_his_own_lists_are_offered_with_the_watchlists_flag_on_only():
    t = _owner("own")
    _add(WatchlistRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], name="My demo list", symbols_json=json.dumps(["DEMOA"]),
                         items_json=json.dumps([{"symbol": "DEMOA", "exchange": "NSE"}])))
    assert not [c for c in client.get(URL, headers=t["headers"]).json()["lists"] if c["kind"] == "own"]
    _set_flag("watchlists", t["tenant_id"], on=True)
    own = [c for c in client.get(URL, headers=t["headers"]).json()["lists"] if c["kind"] == "own"]
    assert [(c["name"], c["count"], c["group"]) for c in own] == [("My demo list", 1, "My lists")]


# --- adopting: PAPER, approval-only, nothing else created --------------------------------------------------------------------

def test_adopt_is_paper_and_approval_only_by_construction_and_creates_no_bot_proposal_or_order():
    t = _owner("adopt")
    a, b = _sym(), _sym()
    _seed(a, _bars(PULLBACK))
    key = _system_list([a, b])
    for extra in ({"mode": "LIVE"}, {"entries": "AUTO"}, {"params": {"fast": 5}}, {"entry_mode": "AUTO"}):
        r = client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": key}, **extra})
        assert r.status_code == 422, extra                       # nothing in the body can choose the mode, the entries or settings
    r = client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": key}})
    assert r.status_code == 200, r.text
    watch = r.json()["adopted"]
    assert watch["mode"] == "PAPER" and watch["entries"] == "APPROVAL"
    assert watch["template"]["id"] == "D3" and watch["strategy_id"] == ids.adopt(library.get("D3"), "1d")[0]
    assert watch["list"]["key"] == key and watch["count"] == 2

    async def stored():
        async with _session_factory() as session:
            row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.user_id == t["user_id"], UserPrefsRecord.namespace == D.NAMESPACE))
            return json.loads(row.value_json)
    value = _run(stored())
    assert value["mode"] == "PAPER" and value["entries"] == "APPROVAL" and "LIVE" not in json.dumps(value)
    tenant = t["tenant_id"]
    assert _count(StrategyDeploymentRecord, StrategyDeploymentRecord.tenant_id == tenant) == 0
    assert _count(AiActionRecord, AiActionRecord.tenant_id == tenant) == 0
    assert _count(OrderRecord, OrderRecord.tenant_id == tenant) == 0
    assert _count(AuditLogRecord, AuditLogRecord.tenant_id == tenant, AuditLogRecord.event == "signals_day_one_adopted") == 1
    # the module itself has no path to a deployment, a proposal or an order
    import inspect
    from app.signals_feed import day_one_routes
    src = inspect.getsource(D) + inspect.getsource(day_one_routes)
    for word in ("StrategyDeploymentRecord", "AiActionRecord", "OrderRecord", "place_order", "submit(", "LIVE\""):
        assert word not in src, word


def test_a_template_not_offered_or_switched_off_and_a_bad_list_are_refused():
    t = _owner("refuse")
    key = _system_list([_sym()])
    assert client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D1", "list": {"kind": "system", "key": key}}).status_code == 404
    r = client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "B1", "list": {"kind": "system", "key": key}})
    assert r.status_code == 409                                    # B1 needs the breakout screens, off here
    assert client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": "index-nosuchlist"}}).status_code == 404
    indices = _system_list([_sym()], group="INDICES")
    assert client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": indices}}).status_code == 404


def test_a_viewer_cannot_adopt_refresh_or_stop():
    t = _owner("viewer")
    _upgrade_plan(t["tenant_id"])
    v = member(t, role="VIEWER")
    key = _system_list([_sym()])
    assert client.get(URL, headers=v["headers"]).json()["can_adopt"] is False
    assert client.post(URL + "/adopt", headers=v["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": key}}).status_code == 403
    assert client.post(URL + "/refresh", headers=v["headers"]).status_code == 403
    assert client.delete(URL, headers=v["headers"]).status_code == 403


# --- the first-run evaluation ------------------------------------------------------------------------------------------------

def test_after_adopting_the_page_shows_each_symbols_state_in_structure_words():
    t = _owner("states")
    _set_flag("screener_breakouts", t["tenant_id"], on=True)
    new, steady, empty = _sym(), _sym(), _sym()
    _seed(new, _bars(BREAKOUT))
    _seed(steady, _bars(FLAT))
    key = _system_list([steady, empty, new])
    watch = client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "B1", "list": {"kind": "system", "key": key}}).json()["adopted"]
    rows = {r["symbol"]: r for r in watch["rows"]}
    assert [r["symbol"] for r in watch["rows"]] == [new, steady, empty]          # the new setup first, then the list's order
    assert rows[new]["state"] == "new" and rows[new]["direction"] == "LONG"
    assert rows[new]["text"] == "The close broke out above the range on the Daily chart."
    assert rows[new]["area"].startswith("Buyer area: ") and rows[new]["setup_day"] == _last_session().isoformat()
    assert rows[steady]["state"] == "watching" and rows[steady]["text"] == D.WATCHING["B1"] and rows[steady]["area"] is None
    assert rows[empty]["state"] == "no_data" and rows[empty]["text"] == D.PLAIN["no_data"]
    assert watch["summary"]["new"] == 1 and watch["summary"]["watching"] == 1 and watch["summary"]["no_data"] == 1
    assert watch["bar_day"] == _last_session().isoformat() and watch["note"] is None
    for r in watch["rows"]:
        assert L.check(r["text"], "signal_own", own_symbols=[r["symbol"]]) == []
        if r["area"]:
            assert L.check(f"{r['symbol']} · {r['area']}", "signal_own", own_symbols=[r["symbol"]]) == []


def test_a_setup_waiting_for_its_trigger_reads_as_waiting_with_its_area():
    row = D.read_symbol(_strategy("D3"), "DEMOX", "NSE", _bars(PULLBACK))
    assert row["state"] == "armed" and row["direction"] == "LONG" and row["text"] == D.WAITING["D3"]["long"]
    assert row["area"].startswith("Buyer area: ")
    assert D.read_symbol(_strategy("D3"), "DEMOX", "NSE", _bars(UP))["state"] == "watching"
    assert D.read_symbol(_strategy("D3"), "DEMOX", "NSE", _bars(UP[:20]))["state"] == "warmup"
    recent = D.read_symbol(_strategy("B1"), "DEMOX", "NSE", _bars(BREAKOUT + [106.5, 107.0]))
    assert recent["state"] == "recent" and recent["direction"] == "LONG"


@pytest.mark.parametrize("tid,closes", [("B1", BREAKOUT), ("B1", FLAT), ("D3", PULLBACK), ("D3", UP), ("A5", list(100 + np.sin(np.arange(800) / 7) * 5))])
def test_the_first_run_matches_the_scan_and_the_worker_on_the_same_bars(tid, closes):
    """Parity: the watch reads the same series on the same window (the worker's `history_bars`) as the scan path and the
    worker path of the parity harness; every bar the watch could have described agrees."""
    strategy = _strategy(tid)
    frame = _bars(closes)
    window = frame.iloc[-strategy.history_bars():]
    scan = parity.scan_answers(strategy.recipe, window)
    last = pd.Timestamp(window.index[-1])
    worker = parity.worker_answers(strategy, window, "DEMOX", bars=[len(window) - 1])
    assert parity.compare(scan, worker, "worker") == []
    row = D.read_symbol(strategy, "DEMOX", "NSE", frame)
    want = scan[last].direction
    assert (row["state"] == "new") == (want != 0)
    if want:
        assert row["direction"] == ("LONG" if want > 0 else "SHORT")
    # no look-ahead: the state on bar k reads only bars up to k (adding later bars never changes it)
    for k in (len(frame) - 3, len(frame) - 2):
        early = D.read_symbol(strategy, "DEMOX", "NSE", frame.iloc[:k])
        sub = frame.iloc[:k].iloc[-strategy.history_bars():]
        assert (early["state"] == "new") == (parity.scan_answers(strategy.recipe, sub)[pd.Timestamp(sub.index[-1])].direction != 0)


def test_the_loader_reads_closed_shared_lake_bars_only():
    symbol = _sym()
    frame = _bars(UP[:30])
    _seed(symbol, frame)
    end = datetime.now(UTC) + timedelta(days=1)                   # a bar that has not ended yet: never read
    _add(MdCandleRecord(instrument_key=f"NSE:{symbol}", timeframe="1d", ts=end, source="nse_bhav:cm", version=1, open=1, high=1, low=1,
                        close=1, volume=1, ingested_at=datetime.now(UTC)))
    last_end = datetime.combine(_last_session(), dtime(15, 30), IST).astimezone(UTC)
    _add(MdCandleRecord(instrument_key=f"NSE:{symbol}", timeframe="1d", ts=last_end, source="broker:kite", version=9, open=1, high=1, low=1,
                        close=1, volume=1, ingested_at=last_end))           # one organisation's broker row: never read

    async def go():
        async with _session_factory() as session:
            return await D.load_daily(session, [{"symbol": symbol, "exchange": "NSE"}], datetime.now(UTC), 500)
    got = _run(go())[symbol]
    assert len(got) == 30 and got.index[-1] == frame.index[-1]
    assert np.allclose(got["close"].to_numpy(), frame["close"].to_numpy())
    assert (got["close"] != 1).all()


def test_with_no_history_every_row_says_so_in_plain_words_and_nothing_is_made_up():
    t = _owner("nodata")
    key = _system_list([_sym(), _sym()])
    watch = client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": key}}).json()["adopted"]
    assert {r["state"] for r in watch["rows"]} == {"no_data"} and watch["note"] == D.NO_DATA_NOTE
    assert all(r["area"] is None and r["direction"] is None for r in watch["rows"])


def test_an_empty_list_is_refused_and_a_long_list_is_cut_to_the_watch_size_in_words():
    t = _owner("sizes")
    _set_flag("watchlists", t["tenant_id"], on=True)
    (wid,) = _add(WatchlistRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], name="Empty demo", symbols_json="[]", items_json="[]"))
    assert client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "own", "id": wid}}).status_code == 422
    key = _system_list([f"DEMOL{i:03d}" for i in range(D.MAX_WATCH + 5)])
    watch = client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": key}}).json()["adopted"]
    assert watch["truncated"] is True and watch["count"] == D.MAX_WATCH and len(watch["rows"]) == D.MAX_WATCH


def test_the_time_box_leaves_the_rest_not_read_yet():
    ticks = iter(range(100))
    rows = D.evaluate(_strategy("D3"), [{"symbol": "DEMOA", "exchange": "NSE"}, {"symbol": "DEMOB", "exchange": "NSE"}], {},
                      budget=1.5, clock=lambda: next(ticks))
    assert [r["state"] for r in rows] == ["no_data", "not_read"]


# --- refresh, stale, outdated, stop ----------------------------------------------------------------------------------------------

def test_refresh_reads_again_at_most_once_a_minute_and_a_stale_watch_is_read_on_open(monkeypatch):
    t = _owner("refresh")
    symbol = _sym()
    key = _system_list([symbol])
    first = client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": key}}).json()["adopted"]
    assert first["rows"][0]["state"] == "no_data"
    _seed(symbol, _bars(UP))
    again = client.post(URL + "/refresh", headers=t["headers"]).json()["adopted"]
    assert again["evaluated_at"] == first["evaluated_at"] and again["rows"][0]["state"] == "no_data"   # within the minute
    monkeypatch.setattr(D, "MIN_REFRESH_SECONDS", -1)
    assert client.post(URL + "/refresh", headers=t["headers"]).json()["adopted"]["rows"][0]["state"] == "watching"
    monkeypatch.setattr(D, "STALE_SECONDS", -1)
    assert client.get(URL, headers=t["headers"]).json()["adopted"]["rows"][0]["state"] == "watching"
    assert not D.stale({"evaluated_at": datetime.now(UTC).isoformat()}, datetime.now(UTC), 60)
    assert D.stale(None, datetime.now(UTC)) and D.stale({"evaluated_at": "nonsense"}, datetime.now(UTC))


def test_an_outdated_adoption_asks_to_choose_again_and_stop_clears_it():
    t = _owner("outdated")
    key = _system_list([_sym()])
    client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": key}})

    async def spoil():
        async with _session_factory() as session:
            row = await session.scalar(select(UserPrefsRecord).where(UserPrefsRecord.user_id == t["user_id"], UserPrefsRecord.namespace == D.NAMESPACE))
            value = json.loads(row.value_json)
            value["strategy_id"] = "template:D3:99:1d:both:,,,,,,"
            row.value_json = json.dumps(value)
            await session.commit()
    _run(spoil())
    body = client.get(URL, headers=t["headers"]).json()
    assert body["adopted"] is None and body["outdated"] is True and body["offers"]
    assert client.post(URL + "/refresh", headers=t["headers"]).status_code == 404
    assert client.delete(URL, headers=t["headers"]).json()["adopted"] is None
    assert client.get(URL, headers=t["headers"]).json()["outdated"] is False
    assert _count(AuditLogRecord, AuditLogRecord.tenant_id == t["tenant_id"], AuditLogRecord.event == "signals_day_one_stopped") == 1


def test_the_prefs_routes_never_expose_the_internal_row():
    t = _owner("prefs")
    key = _system_list([_sym()])
    client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "system", "key": key}})
    assert D.NAMESPACE not in client.get("/api/me/prefs", headers=t["headers"]).json()["prefs"]
    assert client.put(f"/api/me/prefs/{D.NAMESPACE}", headers=t["headers"], json={"mode": "LIVE"}).status_code == 404


# --- tenancy -------------------------------------------------------------------------------------------------------------------

def test_another_member_or_organisation_never_sees_or_changes_his_watch_or_uses_his_list():
    t = _owner("tenA")
    _upgrade_plan(t["tenant_id"])
    _set_flag("watchlists", t["tenant_id"], on=True)
    mate = member(t)
    stranger = _owner("tenB")
    _set_flag("watchlists", stranger["tenant_id"], on=True)
    (wid,) = _add(WatchlistRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], name="Private demo", symbols_json=json.dumps(["DEMOP"]),
                                  items_json=json.dumps([{"symbol": "DEMOP", "exchange": "NSE"}])))
    assert client.post(URL + "/adopt", headers=t["headers"], json={"template_id": "D3", "list": {"kind": "own", "id": wid}}).status_code == 200
    for other in (mate, stranger):
        body = client.get(URL, headers=other["headers"]).json()
        assert body["adopted"] is None and not [c for c in body["lists"] if c["kind"] == "own" and c.get("id") == wid]
        assert client.post(URL + "/adopt", headers=other["headers"], json={"template_id": "D3", "list": {"kind": "own", "id": wid}}).status_code == 404
        assert client.post(URL + "/refresh", headers=other["headers"]).status_code == 404
        client.delete(URL, headers=other["headers"])
    assert client.get(URL, headers=t["headers"]).json()["adopted"]["list"] == {"kind": "own", "id": wid, "name": "Private demo"}


# --- the signal language -------------------------------------------------------------------------------------------------------

def test_every_text_the_watch_can_show_is_in_the_signal_language_of_his_own_signals():
    texts = [*D.WATCHING.values(), D.WATCHING_ANY, *D.PLAIN.values(), *D.WAITING_ANY.values(), D.NO_DATA_NOTE]
    texts += [w for sides in D.WAITING.values() for w in sides.values()]
    for tid in D.OFFERS:
        t = library.get(tid)
        _, s = ids.adopt(t, D.TIMEFRAME)
        texts += [library.signal_text(t, s, side) for side in ("long", "short")]
    for text in texts:
        assert L.check(f"DEMO1 {text}", "signal_own", own_symbols=["DEMO1"]) == [], text
        assert L.call_words(text) == [], text
    for tid in D.WAITING:
        assert tid in D.OFFERS
    assert set(D.WATCHING) == set(D.OFFERS)
    # a text that is not in the language is withheld, never shown
    assert D._shown("Buy above the range", "DEMO1") == L.WITHHELD
    assert L.check(L.area_label("buyer", 1234.5, 1250.0), "signal_own") == []
