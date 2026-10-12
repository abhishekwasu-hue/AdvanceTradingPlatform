"""U8 EV3 + EV4: evidence-chart context (flag `evidence_context`) and the coverage lint.

EV3
* option-chain conditions (PCR, ChainBias, MaxPainDistancePct) draw the chain's call / put OI walls (or max pain) on the
  price pane, with the chain's numbers from the same `analyze_option_chain` the runtime calls; a pack as of an earlier
  bar never reads a chain (a chain is a snapshot of its own time);
* the multi-timeframe strip: the engine's own DowTrend tag on each timeframe at or above the screen's, as of the matched
  bar (equal to `runtime.evaluate` of the same expression on the same bars), and the conditions that read it;
* no look-ahead: a pack with the context, built over all bars as of bar n, equals the pack over bars[:n], at many n;
* replay (GET /api/screener/evidence/{id}/replay?at=): the stored pack's screen recomputed from the shared lake's bars
  that ended by `at` - the builder's own answer on those bars, unchanged by later or non-shared (stream:) rows; refused
  outside the pack's window and where it could not be the screen's own computation; flag off -> 404; another
  organisation -> 404.
EV4
* every ScreenQL registry entry declares how it shows in a pack (`evidence.drawn_as`), and the declaration is what the
  builder really produces on the Z10-a harness expressions;
* every Legends card and every breakout template with a ScreenQL text builds a pack in which each condition is drawn or
  says why not.
Synthetic bars and chains only - no broker, no network, no NIFTY data. Nothing here places an order.
"""
import asyncio
import json
import uuid
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import delete

from app.brokers.models import OptionChain, OptionChainRow
from app.db.models import MdCandleRecord, ScreenEvidenceRecord
from app.platform import controls
from app.quality.causal_cases import SCREENQL_SERIES
from app.screener import compile_screen, parse
from app.screener import evidence as ev
from app.screener.evidence_routes import replay_start
from app.screener.evidence_store import _record, canonical
from app.screener.registry import BOOL, CAT, FUNCTIONS
from app.screener.runtime import SymbolData, _last, evaluate, resample
from tests import zones_golden as G
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

IST = "Asia/Kolkata"


def _run(coro):
    return asyncio.run(coro)


def _frame(name: str) -> pd.DataFrame:
    df = G.load(name)
    idx = pd.DatetimeIndex(df["timestamp"]).tz_localize(IST).tz_convert("UTC")
    out = pd.DataFrame({k: df[k].astype(float).to_numpy() for k in ("open", "high", "low", "close")}, index=idx)
    out["volume"] = 1000.0 + (np.arange(len(out)) % 11) * 150.0
    return out


DAILY = _frame("bn_daily")                     # 400 sessions (start-labelled at the session's date)
M5 = _frame("market_5m")                       # 750 x 5m


def _chain(ltp=30_500.0) -> OptionChain:
    strikes = range(29_000, 32_001, 250)
    rows = [OptionChainRow(strike=float(s), call_oi=900.0 + (4_000.0 if s == 31_250 else 0.0) + (s - 29_000) / 10,
                           put_oi=1_200.0 + (5_000.0 if s == 29_750 else 0.0), call_ltp=40.0, put_ltp=35.0) for s in strikes]
    return OptionChain(underlying="BNTEST", expiry="2026-11-26", underlying_ltp=ltp, rows=rows)


def _pack(src, data, tf="1d", **kw):
    ast, validated = compile_screen(src, base_tf=tf)
    assert validated.ok, validated.problems
    return ast, validated, ev.build_pack(ast, validated, data, base_tf=tf, **kw)


# ---------------------------------------------------------------------------------------------------------- EV3 chain
def test_chain_conditions_draw_the_oi_walls_from_the_engines_own_analysis():
    data = SymbolData("BNTEST", {"1d": DAILY}, option_chain=_chain())
    _a, _v, pack = _pack('PCR() > 0.1 AND MaxPainDistancePct() < 50 AND ChainBias() != "CONFLICTING"', data, debug=True, context=True)
    from app.option_chain.analysis import analyze_option_chain
    a = analyze_option_chain(_chain())
    by_row = {d["row"]: d for d in pack["drawables"]}
    pcr, pain, bias = pack["rows"]
    assert [lv["value"] for lv in by_row[pcr["id"]]["levels"]] == [a.call_resistance_strikes[0], a.put_support_strikes[0]] == [31_250.0, 29_750.0]
    assert by_row[pcr["id"]]["pane"] == "price" and by_row[pcr["id"]]["chain"] == {"underlying": "BNTEST", "expiry": "2026-11-26"}
    assert [lv["label"] for lv in by_row[pain["id"]]["levels"]] == ["max pain", "underlying"] and by_row[pain["id"]]["levels"][0]["value"] == a.max_pain
    assert bias["id"] in by_row and "call OI wall 31,250.00" in bias["note"].replace("31250", "31,250")
    assert pcr["terms"][0]["value"] == pytest.approx(float(a.pcr), abs=1e-4)                 # the engine's own PCR (rounded)
    # without the flag: packs as before (EV1 / EV2), no chain drawing
    _a, _v, plain = _pack("PCR() > 0.1", data, debug=True)
    assert not any("chain" in d for d in plain["drawables"]) and "context" not in plain
    # as of an earlier bar the chain is not read: it is a snapshot of the newest bar's time
    _a, _v, earlier = _pack("PCR() > 0.1", data, debug=True, context=True, as_of=DAILY.index[-3].to_pydatetime())
    assert earlier["rows"][0]["drawable"] is None and "snapshot of its own time" in earlier["rows"][0]["note"]
    assert earlier["matched"] is None or earlier["rows"][0]["passed"] is not True


# ---------------------------------------------------------------------------------------------------------- EV3 strip
def test_the_trend_strip_is_the_engines_dowtrend_on_each_timeframe():
    data = SymbolData("BNTEST", {"1d": DAILY})
    src = 'close > SMA(close, 20) AND DowTrend(0)@1w != "DOWN"'
    _a, _v, pack = _pack(src, data, debug=True, context=True)
    strip = {c["tf"]: c for c in pack["context"]}
    assert list(strip) == ["1d", "1w"]                                           # at or above the screen's own timeframe
    for tf, c in strip.items():
        expr = "DowTrend(0)" + ("" if tf == "1d" else f"@{tf}")                   # the screen's own degree
        assert c["expr"] == expr
        want = _last(evaluate(parse(expr), data, base_tf="1d"))
        assert c["tag"] == (None if want is None or (isinstance(want, float) and np.isnan(want)) else str(want))
    assert any(c["tag"] for c in strip.values())
    assert strip["1w"]["used_by"] == [r["id"] for r in pack["rows"] if "@1w" in r["text"]]
    assert pd.Timestamp(strip["1w"]["bar_close"]) <= pd.Timestamp(pack["as_of"]["bar_close"])
    assert pack["caption"][-2].startswith("Trend: ")
    intraday = _pack("close > SMA(close, 20)", SymbolData("X", {"5m": M5}), tf="5m", context=True)[2]
    assert [c["tf"] for c in intraday["context"]] == ["5m", "1h", "1d", "1w"]


def test_no_lookahead_the_pack_with_context_as_of_bar_n_equals_the_pack_over_bars_up_to_n():
    src = 'close > SMA(close, 20) AND RSI(14) > 0 AND DowTrend(1)@1w != "DOWN" AND PCR() > 0'
    ast, validated = compile_screen(src, base_tf="1d")
    for n_ in range(150, len(DAILY), 37):
        cut = DAILY.iloc[:n_]
        a = ev.build_pack(ast, validated, SymbolData("BNTEST", {"1d": cut}), base_tf="1d", debug=True, context=True)
        later = DAILY.copy()
        later.iloc[n_:, :4] = later.iloc[n_:, :4].to_numpy() * 1.07
        as_of = (cut.index[-1] + pd.Timedelta(days=1)).to_pydatetime()
        b = ev.build_pack(ast, validated, SymbolData("BNTEST", {"1d": later}, option_chain=_chain()), base_tf="1d", debug=True,
                          context=True, as_of=as_of)
        assert canonical(a)[0] == canonical(b)[0], n_


# ---------------------------------------------------------------------------------------------------------- EV4 lint
def _condition(name: str, expr: str) -> str:
    spec = FUNCTIONS[name]
    if spec.returns == BOOL:
        return expr
    if spec.returns == CAT:
        return f'{expr} != "zz"'
    return f"{expr} > -1000000000"


def test_every_registry_entry_declares_how_it_shows_in_a_pack():
    missing = [name for name in FUNCTIONS if ev.drawn_as(name) is None]
    assert not missing, f"declare these in app/screener/evidence.py DRAWN_AS (or give them a type drawn_as knows): {missing}"
    assert set(ev.DRAWN_AS) <= set(FUNCTIONS), set(ev.DRAWN_AS) - set(FUNCTIONS)


ZONE_OR_DATA = {"NearZone", "ZoneRole", "ZoneState", "ZoneStrength"}            # need stored zones (EV1 tests draw their bands)


def test_each_declaration_is_what_the_builder_produces():
    """For every registry function with a Z10-a expression on the base timeframe: build a pack of one condition on it
    (5m bars, a chain supplied) and check the drawing kind against the declaration."""
    data = SymbolData("BNTEST", {"5m": M5}, option_chain=_chain(24_000.0))
    wrong = []
    checked = 0
    for name, exprs in SCREENQL_SERIES.items():
        if name in ZONE_OR_DATA or name not in FUNCTIONS:
            continue
        expr = next((e for e in exprs if "@" not in e), None)
        if expr is None:
            continue
        ast, validated = compile_screen(_condition(name, expr), base_tf="5m")
        if not validated.ok:
            continue
        pack = ev.build_pack(ast, validated, data, base_tf="5m", debug=True, context=True)
        row = pack["rows"][0]
        d = next((x for x in pack["drawables"] if x["row"] == row["id"]), None)
        kind = ev.drawn_as(name)
        checked += 1
        ok = {
            "series": d is not None and d["kind"] in ("overlay", "pane"),
            "box": d is None or d["kind"] == "box",                                  # a base only where the engine found one
            "bars": (d is not None and d["kind"] == "bars") or (name in ev.BREAKOUT_ARGS and d is None and bool(row.get("note"))),
            "overlay": d is None or d["kind"] == "overlay",
            "levels": d is not None and "chain" in d,
            "strip": d is None and bool(row.get("note")),
        }[kind]
        if not ok:
            wrong.append((name, kind, None if d is None else d["kind"], row.get("note")))
    assert checked >= 40 and not wrong, wrong
    for name in FUNCTIONS:
        if ev.drawn_as(name) == "strip":
            assert name not in ev.DRAWN_AS or ev.DRAWN_AS[name] == "strip"


def _data_for(tf: str) -> SymbolData:
    if tf in ("1d", "1w", "1M"):
        return SymbolData("BNTEST", {"1d": DAILY} if tf == "1d" else {tf: resample(DAILY, "1d", tf), "1d": DAILY})
    base = M5 if tf == "5m" else resample(M5, "5m", tf)
    return SymbolData("BNTEST", {tf: base, "1d": resample(M5, "5m", "1d")})


def test_every_legends_card_and_breakout_template_has_its_conditions_drawn_or_explained():
    from app.screener import legends
    from app.screener.breakouts import templates
    screens = []
    for card in legends.load().cards:
        params = {p.name: p.default for p in card.params}
        screens.append((f"legends:{card.id}", card.source, card.base_tf, params))
    for t in templates.TEMPLATES:
        ast, why = templates.screenql_ast(t.screen, t.signal_tf)
        if ast is not None:
            from app.screener import nodes
            screens.append((f"breakouts:{t.id}", nodes.to_text(ast), t.signal_tf, {}))
    assert len(screens) >= 10
    problems = []
    for sid, src, tf, params in screens:
        ast, validated = compile_screen(src, base_tf=tf, params=params)
        if ast is None or not validated.ok:
            problems.append((sid, "invalid", [p.message for p in validated.problems][:2]))
            continue
        try:
            pack = ev.build_pack(ast, validated, _data_for(tf), base_tf=tf, params=params, debug=True, context=True)
        except Exception as exc:  # noqa: BLE001 - reported below
            problems.append((sid, "raised", repr(exc)[:200]))
            continue
        for row in pack["rows"]:
            if row["drawable"] is None and not row.get("note"):
                problems.append((sid, row["text"], "neither drawn nor explained"))
        assert "context" in pack and json.dumps(pack)
    assert not problems, problems


# ---------------------------------------------------------------------------------------------------------- EV3 replay
KEY = "NSE:EVREPLAY"
URL = "/api/screener/evidence/{}/replay"


def _lake_rows(f: pd.DataFrame, source="nse_bhav:cm", version=1):
    ends = (f.index.tz_convert(IST).normalize() + pd.Timedelta(hours=15, minutes=30)).tz_convert("UTC")
    return [MdCandleRecord(instrument_key=KEY, timeframe="1d", ts=e.to_pydatetime(), source=source, version=version, open=float(r.open),
                           high=float(r.high), low=float(r.low), close=float(r.close), volume=int(r.volume),
                           ingested_at=e.to_pydatetime() + timedelta(hours=4)) for e, r in zip(ends, f.itertuples())]


async def _put(rows):
    async with _session_factory() as session:
        session.add_all(rows)
        await session.commit()


async def _drop(cond=None):
    async with _session_factory() as session:
        q = delete(MdCandleRecord).where(MdCandleRecord.instrument_key == KEY)
        await session.execute(q if cond is None else q.where(cond))
        await session.commit()


def _flags(**flags):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            for name, on in flags.items():
                stored[name] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


def _daily_start_labelled(f: pd.DataFrame) -> pd.DataFrame:
    """The lake's daily bars as the CH3 loader serves them (start = the session open in UTC) - the frame a stored pack
    of this symbol would have been built on."""
    out = f.copy()
    out.index = (f.index.tz_convert(IST).normalize() + pd.Timedelta(hours=9, minutes=15)).tz_convert("UTC")
    return out


@pytest.fixture
def stored():
    from app.core import rate_limit
    rate_limit.reset("evidence_replay")
    _run(_drop())
    _run(_put(_lake_rows(DAILY)))
    _flags(screener_v2=True, evidence_charts=True, evidence_context=True)
    headers, me = _owner(f"ev3-{uuid.uuid4().hex[:10]}@example.com")
    src = "close > SMA(close, 20) AND RSI(14) > 0"
    ast, validated = compile_screen(src, base_tf="1d")
    frame = _daily_start_labelled(DAILY)
    pack = ev.build_pack(ast, validated, SymbolData("EVREPLAY", {"1d": frame}, exchange="NSE"), base_tf="1d", debug=True, context=True,
                         exchange="NSE", screen_name="replay test")

    async def go():
        async with _session_factory() as session:
            row = _record(pack, tenant_id=me["tenant_id"], user_id=me["id"])
            session.add(row)
            await session.commit()
            return row.id
    rid = _run(go())
    yield headers, me, rid, pack, frame, (ast, validated)
    _flags(screener_v2=False, evidence_charts=False, evidence_context=False)
    _run(_drop())

    async def clean():
        async with _session_factory() as session:
            await session.execute(delete(ScreenEvidenceRecord).where(ScreenEvidenceRecord.id == rid))
            await session.commit()
    _run(clean())


def _close_of(frame: pd.DataFrame, i: int) -> pd.Timestamp:
    from app.screener.runtime import bar_close
    return bar_close(pd.DatetimeIndex([frame.index[i]]), "1d")[0]


async def _first_loaded(start, at):
    from app.charts import price_action_layer as PAL
    async with _session_factory() as session:
        f, _src = await PAL.load_frame(session, KEY, "1d", start, at)
    return pd.Timestamp(f["timestamp"].iloc[0]).tz_convert("UTC") if f["timestamp"].iloc[0].tzinfo else pd.Timestamp(f["timestamp"].iloc[0], tz="UTC")


def test_replay_is_the_builder_on_the_shared_lake_as_of_the_bar(stored):
    headers, _me, rid, pack, frame, (ast, validated) = stored
    for back in (1, 5, 30):
        at = _close_of(frame, len(frame) - 1 - back)
        r = client.get(URL.format(rid), params={"at": at.isoformat(), "debug": "true"}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["replay"]["source"] == "lake:1d" and body["replay"]["stored_as_of"] == pack["as_of"] and body["can_send"] is False
        start = replay_start(validated, "1d", len(pack["bars"]["t"]), at.to_pydatetime(), KEY)
        first = _run(_first_loaded(start, at.to_pydatetime()))
        bars = frame[(frame.index >= first)].iloc[:len(frame) - back - int((frame.index < first).sum())]
        want = ev.build_pack(ast, validated, SymbolData("EVREPLAY", {"1d": bars}, exchange="NSE"), base_tf="1d",
                             debug=True, context=True, exchange="NSE", window=len(pack["bars"]["t"]), data_source="replay:lake:1d",
                             screen_name="replay test")
        assert body["as_of"] == want["as_of"] and body["rows"] == [r_ for r_ in want["rows"]]
        assert body["bars"]["t"][-1] == want["bars"]["t"][-1] and body["context"] == want["context"]
        assert pd.Timestamp(body["as_of"]["bar_close"]) <= at


def test_replay_never_reads_a_later_or_a_private_row(stored):
    headers, _me, rid, _pack, frame, _ = stored
    at = _close_of(frame, len(frame) - 20)
    before = client.get(URL.format(rid), params={"at": at.isoformat()}, headers=headers).json()
    later = DAILY.iloc[-19:]
    early = DAILY.iloc[-60:-25]
    _run(_put(_lake_rows(later.assign(close=later["close"] * 1.3), version=2) + _lake_rows(early.assign(high=early["high"] * 1.2), source="stream:upstox", version=9)))
    try:
        assert client.get(URL.format(rid), params={"at": at.isoformat()}, headers=headers).json() == before
    finally:
        _run(_drop((MdCandleRecord.version > 1)))


def test_replay_refusals_flag_and_tenancy(stored):
    headers, _me, rid, pack, frame, _ = stored
    first = _close_of(frame, len(frame) - len(pack["bars"]["t"]))
    assert client.get(URL.format(rid), params={"at": (first - pd.Timedelta(days=3)).isoformat()}, headers=headers).status_code == 422
    after = pd.Timestamp(pack["as_of"]["bar_close"]) + pd.Timedelta(days=2)
    assert client.get(URL.format(rid), params={"at": after.isoformat()}, headers=headers).status_code == 422
    naive = _close_of(frame, len(frame) - 2).tz_localize(None).isoformat()
    assert client.get(URL.format(rid), params={"at": naive}, headers=headers).status_code == 422
    other, _ = _owner(f"ev3-other-{uuid.uuid4().hex[:10]}@example.com")
    ok_at = _close_of(frame, len(frame) - 2).isoformat()
    assert client.get(URL.format(rid), params={"at": ok_at}, headers=other).status_code == 404
    _flags(evidence_context=False)
    assert client.get(URL.format(rid), params={"at": ok_at}, headers=headers).status_code == 404
    assert client.get(f"/api/screener/evidence/{rid}", headers=headers).status_code == 200      # the pack itself stays readable
    assert controls.DEFAULT_OFF_FLAGS >= {"evidence_context"} and "places no orders" in controls.FEATURE_FLAGS["evidence_context"]


def test_replay_refuses_what_would_not_be_the_screens_own_computation(stored):
    headers, me, _rid, _pack, frame, _ = stored
    for src, why in (("PercentileRank(close) > 50", "cross-sectional"), ('NearZone("demand", 0.5)', "market-zone")):
        ast, validated = compile_screen(src, base_tf="1d")
        pack = ev.build_pack(ast, validated, SymbolData("EVREPLAY", {"1d": frame}, exchange="NSE"), base_tf="1d", debug=True, exchange="NSE")

        async def go():
            async with _session_factory() as session:
                row = _record(pack, tenant_id=me["tenant_id"], user_id=me["id"])
                session.add(row)
                await session.commit()
                return row.id
        rid = _run(go())
        r = client.get(URL.format(rid), params={"at": _close_of(frame, len(frame) - 2).isoformat()}, headers=headers)
        assert r.status_code == 422 and why in r.text
