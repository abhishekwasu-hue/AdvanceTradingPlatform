"""N3 S7a (spec §2.5): ranking systems - weighted factor trees ranking a universe 0-100 (flag screener_rankings, off).

- the tree's rules (weights per level, depth, factor count, a daily number of one symbol's bars) and the platform systems;
- the scoring on hand-built values: percentiles, lower-is-better, sector-neutral groups, a missing factor re-weighted,
  the composite re-percentiled, every component explained;
- no look-ahead: the rank at each date (the S6 history read) equals ranking bars cut at that date, and rewriting every
  bar after a date leaves that date's rank unchanged;
- RankScore("...") in a screen, and its refusals (flag off, unknown system, history, alerts);
- the routes: tenant-scoped saved systems, a run on server bars with its components, the backtest's rank_system."""
import asyncio
import json
import time
import types
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.models import OHLCVBar
from app.db.models import ClassificationRecord, ResearchTrialRecord
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.screener import compile_screen
from app.screener import history as H
from app.screener import ranking as RK
from app.screener.runtime import SymbolData, run_screen
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_n3_s6_history import _lake_rows, _seed, _universe
from tests.test_trading_worker import _tenant

TREE = {"nodes": [
    {"name": "Momentum", "weight": 60, "children": [
        {"name": "20d return", "factor": "PctChange(close, 20)", "weight": 50},
        {"name": "Near the high", "factor": "close / Max(high, 60)", "weight": 50, "neutral": "sector"}]},
    {"name": "Calm", "factor": "ATR(14) / close", "weight": 40, "higher_is_better": False}]}


def _run(coro):
    return asyncio.run(coro)


def _email(tag):
    return f"n3s7a-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _system(tree=TREE, name="test"):
    return RK.parse(name, tree)


# --- the tree --------------------------------------------------------------------------------------------------------------------

def test_the_flag_is_off_by_default():
    assert RK.FLAG in DEFAULT_OFF_FLAGS


@pytest.mark.parametrize("tree, why", [
    ({"nodes": [{"name": "a", "factor": "close", "weight": 60}]}, "add up to 100"),
    ({"nodes": [{"name": "a", "factor": "close > 5", "weight": 100}]}, "is a number"),
    ({"nodes": [{"name": "a", "factor": "Rank(close)", "weight": 100}]}, "no Rank"),
    ({"nodes": [{"name": "a", "factor": "SMA(close, 5)@1w", "weight": 100}]}, "daily bars only"),
    ({"nodes": [{"name": "a", "factor": "close", "weight": 100, "children": []}]}, "not both"),
    ({"nodes": [{"name": "a", "factor": "close", "weight": 100, "neutral": "industry"}]}, "neutral"),
    ({"nodes": [{"name": "a", "factor": "close", "weight": 50}, {"name": "a", "factor": "open", "weight": 50}]}, "repeat"),
    ({"nodes": [{"name": "a", "weight": 100, "children": [{"name": "b", "weight": 100, "children": [
        {"name": "c", "weight": 100, "children": [{"name": "d", "factor": "close", "weight": 100}]}]}]}]}, "at most 3 levels"),
    ({"nodes": [{"name": f"f{i}", "factor": "close", "weight": 100 / 31} for i in range(31)]}, "at most 30"),
    ({"nodes": []}, "non-empty"),
])
def test_a_tree_the_platform_does_not_accept_is_refused_with_the_reason(tree, why):
    with pytest.raises(RK.RankingError, match=why):
        _system(tree)


def test_the_platform_systems_compile_and_document_their_formulas():
    for name, spec in RK.PLATFORM.items():
        system = RK.platform_system(name)
        assert system.name == f"platform:{name}" and spec["description"]
        assert all(leaf.factor and leaf.ast is not None for leaf in system.leaves())
        assert system.tree() == {"nodes": [dict({"higher_is_better": True, "neutral": None}, **n) for n in spec["tree"]["nodes"]]}
    assert _system().lookback == 60 and _system().sector_neutral


# --- the scoring -----------------------------------------------------------------------------------------------------------------

def test_scoring_on_hand_built_values():
    system = _system({"nodes": [{"name": "up", "factor": "close", "weight": 75},
                                {"name": "low", "factor": "volume", "weight": 25, "higher_is_better": False}]})
    values = {"/up": pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}),
              "/low": pd.Series({"A": 10.0, "B": 40.0, "C": 30.0, "D": np.nan})}
    rank, parts = RK.score(system, values)
    assert parts["/up"].to_dict() == {"A": 25.0, "B": 50.0, "C": 75.0, "D": 100.0}
    assert parts["/low"].round(4).to_dict() == pytest.approx({"A": 100.0, "B": 33.3333, "C": 66.6667, "D": np.nan}, nan_ok=True)
    composite = parts["composite"]
    assert composite["A"] == pytest.approx(0.75 * 25 + 0.25 * 100)
    assert composite["D"] == pytest.approx(100.0)                    # its missing factor is left out, the rest re-weighted
    assert rank.to_dict() == {"A": 25.0, "B": 50.0, "C": 75.0, "D": 100.0}                 # the composite re-percentiled
    explained = RK.components(system, values, parts, "B")
    assert [c["path"] for c in explained] == ["/up", "/low"] and explained[1]["value"] == 40.0 and explained[1]["higher_is_better"] is False


def test_a_sector_neutral_factor_is_a_percentile_within_the_sector():
    system = _system({"nodes": [{"name": "x", "factor": "close", "weight": 100, "neutral": "sector"}]})
    values = {"/x": pd.Series({"A": 1.0, "B": 2.0, "C": 100.0, "D": 200.0, "E": 5.0})}
    _, parts = RK.score(system, values, {"A": "IT", "B": "IT", "C": "BANK", "D": "BANK", "E": None})
    assert parts["/x"].to_dict() == {"A": 50.0, "B": 100.0, "C": 50.0, "D": 100.0, "E": 100.0}   # no sector: its own group
    assert RK.score(system, values)[1]["/x"]["B"] == 40.0                                       # not neutral: the whole universe


def test_sector_on_reads_the_range_alive_that_day():
    ranges = [(date(2025, 1, 1), date(2025, 6, 1), "IT"), (date(2025, 6, 1), None, "SERVICES")]
    assert RK.sector_on(ranges, date(2025, 5, 31)) == "IT" and RK.sector_on(ranges, date(2025, 6, 1)) == "SERVICES"
    assert RK.sector_on(ranges, date(2024, 12, 31)) is None


# --- no look-ahead ---------------------------------------------------------------------------------------------------------------

def _cut_rank(system, frames, d, sectors=None):
    universe = []
    for s, f in frames.items():
        upto = H.cut(f, d)
        if upto is not None and not upto.empty and H.session_dates(upto.index[-1:])[0] == d:
            universe.append(SymbolData(s, {"1d": upto}, sector=(sectors or {}).get(s)))
    rank, _ = RK.score(system, RK.live_values(system, universe), {u.symbol: u.sector for u in universe} if system.sector_neutral else None)
    return rank


def test_the_rank_at_each_date_equals_ranking_bars_cut_there():
    frames, days = _universe(n=10, bars=200)
    frames["LATE"] = frames.pop("S09").iloc[120:]                    # listed later: too little history early on
    sectors = {s: ("IT" if i % 2 else "BANK") for i, s in enumerate(sorted(frames))}
    system = _system()
    dates = days[70::15]
    table = RK.rank_at_dates(system, frames, dates, {d: sectors for d in dates})
    for d in dates:
        cut = _cut_rank(system, frames, d, sectors)
        got = table.loc[d].dropna()
        assert sorted(got.index) == sorted(cut.dropna().index)
        assert got.to_dict() == pytest.approx(cut.dropna().to_dict())
    assert np.isnan(table.loc[dates[0], "LATE"])


def test_nothing_after_a_date_changes_its_rank():
    frames, days = _universe(n=8, bars=180)
    system = _system()
    d = days[120]
    before = RK.rank_at_dates(system, frames, [d], {d: {}}).loc[d]
    changed = {}
    for i, (s, f) in enumerate(frames.items()):
        g = f.copy()
        later = g.index > H.bar_index([d])[0]
        g.loc[later, ["open", "high", "low", "close"]] *= (5.0 if i % 2 else 0.2)
        changed[s] = g
    after = RK.rank_at_dates(system, changed, [d], {d: {}}).loc[d]
    assert after.to_dict() == pytest.approx(before.to_dict())


def test_rankscore_in_a_screen_reads_the_run_s_systems():
    frames, days = _universe(n=8, bars=120)
    universe = [SymbolData(s, {"1d": f}) for s, f in frames.items()]
    system = RK.platform_system("Low volatility")
    ast, validated = compile_screen('RankScore("platform:Low volatility") >= 50')
    assert validated.ok and validated.cross_sectional
    matched = {m.symbol for m in RK.within({system.name: system}, run_screen, ast, validated, universe, base_tf="1d") if m.matched}
    rank, _ = RK.score(system, RK.live_values(system, universe))
    assert matched == {s for s, r in rank.items() if r >= 50} and 0 < len(matched) < len(universe)
    assert not any(m.matched for m in run_screen(ast, validated, universe, base_tf="1d"))    # no systems loaded: never matches
    by_param, v2 = compile_screen("RankScore($sys) >= 50", params={"sys": "platform:Low volatility"})
    assert RK.names_in(by_param, {"sys": "platform:Low volatility"}) == ["platform:Low volatility"]
    assert {m.symbol for m in RK.within({system.name: system}, run_screen, by_param, v2, universe, base_tf="1d",
                                        params={"sys": "platform:Low volatility"}) if m.matched} == matched
    for bad, why in [('RankScore("x") > 1', "@1d bars only"), ('SMA(RankScore("x"), 5) > 1', "last bar only")]:
        _, v = compile_screen(bad, base_tf="15m" if "@1d" in why else "1d")
        assert any(why in p.message for p in v.problems)


def test_ranking_fifty_symbols_over_sixty_dates_is_quick():
    frames, days = _universe(n=50, bars=320)
    dates = days[260:320]
    started = time.monotonic()
    table = RK.rank_at_dates(RK.platform_system("Momentum"), frames, dates)
    assert table.notna().all().all() and time.monotonic() - started < 10.0      # about 1.5 s here


# --- the routes ------------------------------------------------------------------------------------------------------------------

def _org(tag, on=True):
    t = _tenant(_email(tag), with_credentials=False)
    for name in ("screener_v2",) + ((RK.FLAG,) if on else ()):
        _flag(name, t["tenant_id"])
    return t


def test_the_routes_are_off_by_default_and_rankscore_is_refused():
    t = _org("off", on=False)
    for method, path in [("get", "/api/screener/rankings"), ("post", "/api/screener/rankings/run")]:
        r = getattr(client, method)(path, headers=t["headers"], **({"json": {"system": "platform:Momentum", "symbols": ["X"]}} if method == "post" else {}))
        assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == RK.FLAG
    r = client.post("/api/screener/validate", headers=t["headers"], json={"source": 'RankScore("platform:Momentum") > 80'})
    assert r.status_code == 200 and not r.json()["ok"] and RK.FLAG in json.dumps(r.json()["problems"])


def test_saved_systems_are_the_organisation_s_own():
    t, other = _org("own"), _org("other")
    made = client.post("/api/screener/rankings", headers=t["headers"], json={"name": "Quality-ish", "tree": TREE})
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    assert made.json()["tree"]["nodes"][1]["higher_is_better"] is False
    listed = client.get("/api/screener/rankings", headers=t["headers"]).json()
    assert [s["name"] for s in listed["systems"]] == ["Quality-ish"] and {p["name"] for p in listed["platform"]} == {
        f"platform:{n}" for n in RK.PLATFORM}
    assert client.get("/api/screener/rankings", headers=other["headers"]).json()["systems"] == []
    assert client.put(f"/api/screener/rankings/{sid}", headers=other["headers"], json={"name": "x", "tree": TREE}).status_code == 404
    assert client.post(f"/api/screener/rankings/{sid}/archive", headers=other["headers"]).status_code == 404
    assert client.post("/api/screener/rankings", headers=t["headers"], json={"name": "Quality-ish", "tree": TREE}).status_code == 409
    assert client.post("/api/screener/rankings", headers=t["headers"], json={"name": "platform:Mine", "tree": TREE}).status_code == 422
    bad = client.post("/api/screener/rankings", headers=t["headers"], json={"name": "bad", "tree": {"nodes": [{"name": "a", "factor": "close", "weight": 5}]}})
    assert bad.status_code == 422 and "add up to 100" in bad.text
    # another organisation's screen cannot name it; the owner's can, until it is archived
    other_check = client.post("/api/screener/validate", headers=other["headers"], json={"source": 'RankScore("Quality-ish") > 80'})
    assert other_check.status_code == 200 and not other_check.json()["ok"]                       # (its flag is on; the name is unknown)
    assert client.post("/api/screener/validate", headers=t["headers"], json={"source": 'RankScore("Quality-ish") > 80'}).json()["ok"]
    renamed = client.put(f"/api/screener/rankings/{sid}", headers=t["headers"], json={"name": "Renamed", "description": "d", "tree": TREE})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed"
    assert client.post(f"/api/screener/rankings/{sid}/archive", headers=t["headers"]).json()["archived"] is True
    gone = client.post("/api/screener/validate", headers=t["headers"], json={"source": 'RankScore("Renamed") > 80'}).json()
    assert not gone["ok"] and "no ranking system named" in json.dumps(gone["problems"])


def _bars(step, days=300):
    idx = pd.date_range("2025-06-01", periods=days, freq="1D", tz="UTC")
    rng = np.random.default_rng(int(abs(step) * 100) + 7)
    closes = 100 * np.cumprod(1 + step / 1000 + rng.normal(0, 0.01, days))
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=float(c), high=float(c) * 1.01, low=float(c) * 0.99, close=float(c), volume=1000.0)
            for t, c in zip(idx, closes)]


def _broker(monkeypatch, steps):
    from app.brokers import token_lifecycle
    from app.market_data import candles_routes, service

    async def pick(session, tenant_id, broker, label):
        return types.SimpleNamespace(broker_name="fake")

    async def candles(self, symbol, exchange="NSE", interval="1min", now=None):
        if symbol not in steps:
            raise RuntimeError("unknown symbol")
        return _bars(steps[symbol])
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    monkeypatch.setattr(token_lifecycle, "build_adapter", lambda record: object())
    monkeypatch.setattr(service.MarketDataService, "get_candles", candles)


def test_a_run_ranks_on_server_bars_and_explains_every_rank(monkeypatch):
    t = _org("run")
    steps = {"UP": 3.0, "FLAT": 0.0, "DOWN": -3.0, "MILD": 1.0}
    _broker(monkeypatch, steps)
    r = client.post("/api/screener/rankings/run", headers=t["headers"],
                    json={"system": "platform:Momentum", "symbols": ["up", "FLAT", "DOWN", "MILD", "NOPE"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [x["symbol"] for x in out["ranked"]][0] == "UP" and out["ranked"][-1]["symbol"] == "DOWN"
    assert out["ranked"][0]["rank"] == 100.0 and "NOPE" in out["not_ranked"] and out["data_source"] == "broker:fake"
    first = out["ranked"][0]["components"]
    assert [c["name"] for c in first] == [n["name"] for n in RK.PLATFORM["Momentum"]["tree"]["nodes"]]
    assert all(c["value"] is not None and c["score"] is not None for c in first)
    assert "not a recommendation" in out["disclaimer"] and "percentile" in out["method"]
    tried = client.post("/api/screener/rankings/run", headers=t["headers"], json={"tree": TREE, "symbols": list(steps)})
    assert tried.status_code == 200 and len(tried.json()["ranked"]) == 4
    assert client.post("/api/screener/rankings/run", headers=t["headers"],
                       json={"system": "platform:Nope", "symbols": ["UP"]}).status_code == 422
    # the screen run: RankScore reads the same system
    screen = client.post("/api/screener/run", headers=t["headers"], json={"source": 'RankScore("platform:Momentum") > 60', "symbols": list(steps)})
    assert screen.status_code == 200, screen.text
    expected = {x["symbol"] for x in out["ranked"] if x["rank"] > 60}
    assert set(screen.json()["matched"]) == expected and expected


def test_history_refuses_rankscore_and_alerts_refuse_it():
    t = _org("hist")
    _flag(H.FLAG, t["tenant_id"])
    r = client.post("/api/screener/history/asof", headers=t["headers"],
                    json={"source": 'RankScore("platform:Momentum") > 80', "date": "2025-12-01", "universe": {"symbols": ["X"]}})
    assert r.status_code == 422 and "rank_system" in r.text
    from app.alerts.rules_routes import _ranking_check
    from fastapi import HTTPException
    ast, _ = compile_screen('RankScore("platform:Momentum") > 80')
    with pytest.raises(HTTPException, match="422"):
        _ranking_check(ast)


def test_the_backtest_ranks_by_a_system_with_sectors_as_of_each_date():
    from app.screener import backtest as B
    t = _org("bt")
    for name in (H.FLAG, B.FLAG):
        _flag(name, t["tenant_id"])
    stem = f"R{uuid.uuid4().hex[:4].upper()}"
    frames, days = _universe(n=6, bars=200, seed=31)
    renamed = {f"{stem}{i}": f for i, f in enumerate(frames.values())}
    for s, f in renamed.items():
        _seed(*_lake_rows(s, f))
    body = {"source": "close > 0", "start": str(days[110]), "end": str(days[190]), "every": "weekly",
            "universe": {"symbols": list(renamed)}, "top_n": 2, "rank_system": "platform:Trend quality"}
    r = client.post("/api/screener/history/backtest", headers=t["headers"], json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["rank_system"] == "platform:Trend quality" and out["rebalances"] >= 10
    dates = H.rebalance_dates(sorted({d for f in renamed.values() for d in H.session_dates(f.index)}), days[110], days[190], H.EVERY["weekly"])
    table = RK.rank_at_dates(RK.platform_system("Trend quality"), renamed, dates)
    assert out["rebalance_log"]
    for entry in out["rebalance_log"]:                               # every match passes (close > 0): the held are the top 2
        d = date.fromisoformat(entry["decided"])
        ranked = table.loc[d].dropna()
        top = sorted(ranked.index, key=lambda s: (-ranked[s], s))[:2]
        assert entry["held"] == sorted(top)

    async def trial():
        async with _session_factory() as session:
            from sqlalchemy import select
            return list(await session.scalars(select(ResearchTrialRecord).where(ResearchTrialRecord.tenant_id == t["tenant_id"])))
    [row] = _run(trial())
    assert json.loads(row.dsl_json)["rank_system"]["tree"]["nodes"][0]["factor"] == "ADX(14)"
    both = client.post("/api/screener/history/backtest", headers=t["headers"], json={**body, "rank_by": "close"})
    assert both.status_code == 422
    unknown = client.post("/api/screener/history/backtest", headers=t["headers"], json={**body, "rank_system": "Nope"})
    assert unknown.status_code == 422 and "no ranking system" in unknown.text


def test_sector_ranges_read_the_classification_history():
    isin = f"INE{uuid.uuid4().hex[:7].upper()}01"
    _seed(ClassificationRecord(isin=isin, scheme="NSE", sector="IT", valid_from=date(2025, 1, 1), valid_to=date(2025, 7, 1), source="t", checksum="x"),
          ClassificationRecord(isin=isin, scheme="NSE", sector="SERVICES", valid_from=date(2025, 7, 1), valid_to=None, source="t", checksum="y"))

    async def go():
        async with _session_factory() as session:
            return await RK.sector_ranges(session, [isin], date(2025, 3, 1), date(2025, 9, 1))
    ranges = _run(go())[isin]
    assert RK.sector_on(ranges, date(2025, 6, 30)) == "IT" and RK.sector_on(ranges, date(2025, 7, 1)) == "SERVICES"
    assert RK.sector_on(ranges, date(2025, 7, 1) - timedelta(days=200)) is None
