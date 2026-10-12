"""U9 TX4 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md §3 / §4): learn mode (past examples with evidence, as education),
provenance (version, change log, source note) and forking a pre-built screen into My screens (`app/screener/learn.py`;
flag `screener_tx4`, off). An example's signal is checked on its own bars only; its evidence is built as of its bar."""
import asyncio
import copy
import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from app.db.models import MdCorporateActionRecord, MdCandleRecord
from app.market_lake.bhavcopy_backfill import bar_end
from app.platform import controls
from app.screener import learn as ln
from app.screener import taxonomy as tx
from app.screener.parser import parse
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner

SOURCE = "test_tx4"
FLAGS = ("screener_v2", "screener_gallery", ln.FLAG)


def _run(coro):
    return asyncio.run(coro)


def _set_flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def tx4_on():
    _set_flags(**{f: True for f in FLAGS})
    yield
    _set_flags(**{f: False for f in FLAGS})


def _wave(n, step=1.0):
    t = np.arange(n)
    return 500 + step * t + 25 * np.sin(t / 6)


def _seed(symbol, closes, days):
    async def go():
        async with _session_factory() as session:
            for day, c in zip(days, closes):
                session.add(MdCandleRecord(instrument_key=f"NSE:{symbol}", timeframe="day", ts=bar_end(day), source=SOURCE, version=1,
                                           open=float(c) - 0.5, high=float(c) + 2, low=float(c) - 2, close=float(c), volume=1000,
                                           ingested_at=bar_end(day) + timedelta(hours=3)))
            await session.commit()
    _run(go())


def _split(symbol, ex_date):
    async def go():
        async with _session_factory() as session:
            session.add(MdCorporateActionRecord(symbol=symbol, exchange="NSE", ex_date=ex_date, action="SPLIT", ratio_new=2.0, ratio_old=1.0,
                                                source=SOURCE, version=1))
            await session.commit()
    _run(go())


@pytest.fixture
def lake():
    def clear():
        async def go():
            async with _session_factory() as session:
                await session.execute(delete(MdCandleRecord).where(MdCandleRecord.source == SOURCE))
                await session.execute(delete(MdCorporateActionRecord).where(MdCorporateActionRecord.source == SOURCE))
                await session.commit()
        _run(go())
    clear()
    yield
    clear()


# --- provenance in the taxonomy -----------------------------------------------------------------------------------------

def test_every_screen_has_a_version_a_change_log_and_a_source_note():
    assert tx.lint() == []
    for s in tx.load().screens:
        assert s.version >= 1 and s.changes[-1]["version"] == s.version and s.source_note
    assert tx.load().version == "tx4/1"


@pytest.mark.parametrize("mutate,problem", [
    (lambda r: r.pop("changes"), "changes must list at least the first version"),
    (lambda r: r.__setitem__("version", 2), "the last change (1) must be the version (2)"),
    (lambda r: r.__setitem__("version", "1"), "version must be a whole number"),
    (lambda r: r.__setitem__("changes", [{"version": 2, "note": "x"}]), "changes[0] must be"),
    (lambda r: r.__setitem__("source_note", ""), "source_note: empty"),
    (lambda r: r.__setitem__("source_note", "The best screen to buy with"), "advice wording"),
])
def test_broken_provenance_fails_the_lint(mutate, problem):
    data = json.loads(tx.DATA_FILE.read_text(encoding="utf-8"))
    broken = copy.deepcopy(data)
    mutate(broken["screens"][0])
    assert any(problem in p for p in tx.parse(broken)[1])


def test_provenance_names_the_source_and_hashes_the_text():
    import app.main  # noqa: F401 - registers the Legends source
    tax = tx.load()
    by_id = {s.id: s for s in tax.screens}
    starter = ln.provenance(by_id["starter.oversold"], "RSI(14) <= 30")
    assert starter["version"] == 1 and starter["changes"] == [{"version": 1, "note": "Filed in the library (U9 TX1)."}]
    assert starter["text_sha256"] == ln.text_sha256("RSI(14) <= 30") and starter["sources"] == []
    legend = ln.provenance(by_id["legends.turtle_channel_breakout"], "high > Max(high, 20)[1]")
    assert legend["source_note"].startswith("Source: ") and legend["sources"]
    method = ln.method_of(tax, by_id["starter.oversold"])
    assert method["method"]["id"] == "indicator" and method["page"] is None and ln.lint() == []


# --- past examples ------------------------------------------------------------------------------------------------------

def _daily(n=320, step=1.0, end="2026-09-30"):
    idx = pd.bdate_range(end=end, periods=n, tz="UTC")
    c = _wave(n, step)
    return pd.DataFrame({"open": c - 0.5, "high": c + 2, "low": c - 2, "close": c, "volume": 1000.0}, index=idx)


def test_matches_are_confirmed_on_their_own_bars_only():
    frame = _daily()
    ast = parse("CrossAbove(close, SMA(close, 20))")
    hits = ln.matches_of(ast, {"1d": frame}, "1d", "X")
    assert len(hits) >= 5
    from app.screener.runtime import SymbolData, evaluate
    for i in hits:
        prefix = frame.iloc[:i + 1]
        assert bool(evaluate(ast, SymbolData("X", {"1d": prefix}), base_tf="1d").iloc[-1])


def test_pick_takes_recent_complete_spaced_matches_outside_split_windows():
    frame = _daily(200)
    cands = [("A", i, frame) for i in (40, 100, 110, 150, 185)]                   # 185: fewer than 20 sessions after it
    got = ln.pick(cands, {})
    assert [e.bar for e in got] == [150, 110, 40]                                  # newest first; 100 is within 20 sessions of 110
    split_day = frame.index[105].to_pydatetime()
    assert [e.bar for e in ln.pick(cands, {"A": [(split_day, split_day)]})] == [40]   # a split inside the window drops it
    e = got[0]
    assert set(e.after) == {"5", "10", "20"}
    assert e.after["5"] == round((frame["close"].iloc[155] / frame["close"].iloc[150] - 1) * 100, 2)


def test_examples_from_the_shared_history_with_evidence_as_of_each_bar(lake):
    days = [d.date() for d in pd.bdate_range(end=date(2026, 9, 30), periods=320)]
    _seed("TXFOURA", _wave(320, 1.0), days)
    _seed("TXFOURB", _wave(320, -0.5) + 400, days)

    async def go():
        from app.screener import compile_screen
        ast, validated = compile_screen("CrossAbove(close, SMA(close, 20))", base_tf="1d")
        async with _session_factory() as session:
            return await ln.examples(session, ast, validated, "1d", ["TXFOURA", "TXFOURB", "TXNONE"], "Cross",
                                     now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    out = _run(go())
    assert len(out["examples"]) == ln.EXAMPLES and any(p.startswith("TXNONE:") for p in out["problems"]), out["problems"]
    assert all(e["pack"] for e in out["examples"]), out["problems"]
    dates = [e["date"] for e in out["examples"]]
    assert dates == sorted(dates, reverse=True)
    for e in out["examples"]:
        pack = e["pack"]
        assert pack is not None and pack["symbol"] == e["symbol"]
        assert pack["as_of"]["bar_start"][:10] <= e["date"]                         # no bar after the example's own
        assert set(e["after"]) == {"5", "10", "20"}
    assert tx.advice_words(out["note"]) == []


def test_a_split_in_the_window_drops_the_example(lake):
    days = [d.date() for d in pd.bdate_range(end=date(2026, 9, 30), periods=320)]
    _seed("TXFOURC", _wave(320, 1.0), days)
    for d in days[200:320:10]:
        _split("TXFOURC", d)

    async def go():
        from app.screener import compile_screen
        ast, validated = compile_screen("CrossAbove(close, SMA(close, 20))", base_tf="1d")
        async with _session_factory() as session:
            return await ln.examples(session, ast, validated, "1d", ["TXFOURC"], "Cross", now=datetime.now(timezone.utc))
    out = _run(go())
    assert out["examples"] and all(e["date"] < days[180].isoformat() for e in out["examples"])   # nothing whose window holds a split


def test_intraday_screens_have_no_end_of_day_examples():
    async def go():
        async with _session_factory() as session:
            return await ln.examples(session, parse("close > 1"), None, "15m", ["X"], "x")
    out = _run(go())
    assert out["examples"] == [] and "daily or weekly" in out["problems"][0]


# --- API ---------------------------------------------------------------------------------------------------------------

def test_the_routes_are_off_by_default():
    headers, _ = _owner("tx4-off@example.com")
    _set_flags(screener_v2=True, screener_gallery=True)
    try:
        for r in (client.get("/api/screener/library/screens/starter.oversold/learn", headers=headers),
                  client.post("/api/screener/library/screens/starter.oversold/examples", json={"symbols": ["X"]}, headers=headers),
                  client.post("/api/screener/library/fork", json={"screen_id": "starter.oversold"}, headers=headers)):
            assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == ln.FLAG
    finally:
        _set_flags(screener_v2=False, screener_gallery=False)
    assert ln.FLAG in controls.DEFAULT_OFF_FLAGS and ln.FLAG in controls.FEATURE_FLAGS


def test_learn_and_examples_endpoints(tx4_on, lake):
    headers, _ = _owner("tx4-learn@example.com")
    body = client.get("/api/screener/library/screens/starter.momentum/learn", headers=headers).json()
    assert body["provenance"]["version"] == 1 and body["provenance"]["text"] == "RSI(14) > 60 AND close > EMA(close, 20)"
    assert body["method"]["id"] == "indicator" and body["note"] == ln.NOTE
    assert client.get("/api/screener/library/screens/legends.crabel_nr7/learn", headers=headers).status_code == 404   # Legends off
    assert client.get("/api/screener/library/screens/starter.momentum.short/learn", headers=headers).status_code == 404  # TX2 off
    days = [d.date() for d in pd.bdate_range(end=(datetime.now(timezone.utc) - timedelta(days=40)).date(), periods=320)]
    _seed("TXFOURD", _wave(320, 1.0), days)
    r = client.post("/api/screener/library/screens/starter.daily_trend/examples", json={"symbols": ["txfourd"]}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["text"] == "close@1d > EMA(close, 50)@1d" and out["scanned"] == 1 and out["examples"]
    assert all(e["symbol"] == "TXFOURD" and e["pack"] for e in out["examples"])


def test_fork_copies_the_screen_with_its_provenance(tx4_on):
    headers, me = _owner("tx4-fork@example.com")
    r = client.post("/api/screener/library/fork", json={"screen_id": "starter.oversold"}, headers=headers)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["name"] == "Oversold (fork of v1)" and row["text"] == "RSI(14) <= 30"
    assert row["forked_from"] == "starter.oversold" and row["forked_version"] == 1
    mine = client.get("/api/screener/screens", headers=headers).json()
    assert any(s["id"] == row["id"] and s["forked_from"] == "starter.oversold" for s in mine)
    other, _ = _owner("tx4-fork-other@example.com")
    assert all(s["id"] != row["id"] for s in client.get("/api/screener/screens", headers=other).json())
    assert client.post("/api/screener/library/fork", json={"screen_id": "nope.x"}, headers=headers).status_code == 404
