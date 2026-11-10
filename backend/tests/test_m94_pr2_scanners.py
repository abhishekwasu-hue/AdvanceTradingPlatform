"""M94 PR-2 (docs/design/M94_INTRADAY_PACK.md section 3): the intraday scanners S1-S12 as the library source
`intraday` (app/screener/intraday.py), behind `intraday_pack` and `screener_breakouts` (both off by default).

* Each scanner runs through the production runtime (`run_screen`) on fixture sessions: a positive and a negative case.
* The short counterparts (New Day Low, Bottom Reversal, Turbo Breakdown) are the mirror rule's text of the long screen,
  checked on reflected bars; the new mirror rules (the session values, the pre-open levels, hammer / shooting star) are
  checked bar by bar in tests/test_tx2_library.py.
* Filing and lint: every scanner is filed in taxonomy.json, no advice wording, the footer is the order's sentence.
* With the flag off the gallery, the scanner list and the builder leave them out (nothing changes for users)."""
import asyncio
import json
import re
import warnings
from pathlib import Path

import pytest

from app.platform import controls
from app.quality.causal_cases import with_preopen
from app.screener import compile_screen
from app.screener import intraday as itd
from app.screener import library as lib
from app.screener import mirror as mr
from app.screener import taxonomy as tx
from app.screener.parser import parse
from app.screener.runtime import SymbolData, run_screen
from tests.breakout_fixtures import profile, quiet_day, sessions_frame
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner

IDS = ["intraday.gappers", "intraday.new_day_high", "intraday.new_day_low", "intraday.top_reversal", "intraday.bottom_reversal",
       "intraday.vwap_break", "intraday.turbo_breakup", "intraday.turbo_breakdown", "intraday.circuit_watch", "intraday.rsi_extreme",
       "intraday.volume_surge", "intraday.key_levels"]
GALLERY = ("screener_v2", "screener_gallery")
PACK = ("intraday_pack", "screener_breakouts")


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
def gallery_on():
    _set_flags(**{f: True for f in GALLERY})
    yield
    _set_flags(**{f: False for f in (*GALLERY, *PACK, "screener_builder_u2")})


def _match(sc, frame, **values):
    """The scanner's last closed bar through the production run path (validated, then run_screen)."""
    params = {**sc.defaults(), **values}
    ast, validated = compile_screen(sc.text, base_tf=sc.base_tf, params=params)
    assert validated.ok, [p.message for p in validated.problems]
    data = frame if isinstance(frame, SymbolData) else SymbolData("S", {sc.base_tf: frame})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        got = run_screen(ast, validated, [data], base_tf=sc.base_tf, params=params, check_history=False)
    return got[0]


# ------------------------------------------------------------------------------------------------------------ fixtures
HISTORY_5M = [quiet_day() for _ in range(21)]          # 21 quiet sessions: the time-of-day volume and the daily reads
HISTORY_1M = [quiet_day(375) for _ in range(21)]


def _day5(bars):
    return sessions_frame(HISTORY_5M + [bars])


def _day1(bars):
    return sessions_frame(HISTORY_1M + [bars], minutes=1)


def _gap_day(open_: float):
    return _day5([(open_, open_ + 0.25, open_ - 0.25, open_ + 0.1, profile(i)) for i in range(5)])


def _new_high_day(vol_mult: float):
    """Ten bars at 2.5x the time-of-day volume, then a close above the session's high on vol_mult x."""
    bars = [(o, h, lo, c, v * 2.5) for o, h, lo, c, v in quiet_day(10)]
    return _day5(bars + [(100.1, 101.2, 100.0, 101.0, profile(10) * vol_mult)])


def _top_day(pattern: bool):
    """A steady rise from the open, then a shooting star at the session's high (or a plain up bar there)."""
    bars, p = [], 100.0
    for i in range(20):
        bars.append((p, p + 0.15, p - 0.05, p + 0.1, profile(i)))
        p += 0.1
    last = (102.0, 102.5, 101.88, 101.9, profile(20)) if pattern else (102.0, 102.5, 101.9, 102.45, profile(20))
    return _day5(bars + [last])


def _vwap_day(vol_mult: float):
    bars = quiet_day(10) + [(99.5, 99.6, 99.4, 99.5, profile(10 + i)) for i in range(5)]
    return _day5(bars + [(99.5, 100.6, 99.5, 100.5, profile(15) * vol_mult)])


def _turbo_day(vol_mult: float):
    return _day1(quiet_day(30) + [(100.1, 100.7, 100.0, 100.6, profile(30) * vol_mult)])


def _rsi_day(rising: bool):
    """A steady 1-minute rise for 300 bars (5-minute RSI far above 90), or a quiet session."""
    if not rising:
        return _day1(quiet_day(320))
    bars, p = quiet_day(20), 100.0
    for _ in range(300):
        bars.append((p, p + 0.07, p - 0.01, p + 0.05, 1000.0))
        p += 0.05
    return _day1(bars)


def _surge_day(vol_mult: float):
    return _day5(quiet_day(10) + [(100.0, 100.25, 99.75, 100.1, profile(10) * vol_mult)])


def _reflected(frame):
    return mr.reflect_bars(frame, float(frame["high"].max() + frame["low"].min()))


def _band_day(ltp: float, quote: bool = True):
    """S9: quiet 5-minute bars and the broker quote of the last price with the day's band 90-110 (made-up numbers)."""
    from app.brokers.models import Quote
    d = SymbolData("S", {"5m": _day5(quiet_day(10))})
    if quote:
        d.quote = Quote(symbol="S", ltp=ltp, upper_circuit_limit=110.0, lower_circuit_limit=90.0)
    else:
        d.quote, d.quote_reason = None, "not available from your broker"
    return d


# (scanner, positive frame, negative frame, the values he sets for the run - the Not set ones and any he changes)
CASES = {
    "intraday.gappers": (itd.S1, lambda: _gap_day(103.0), lambda: _gap_day(100.5), dict(pre_vol=0, min_turnover=1, min_atr_pct=0.1)),
    "intraday.new_day_high": (itd.S2, lambda: _new_high_day(5), lambda: _new_high_day(2), dict(range_atr=1)),
    "intraday.new_day_low": (itd.S3, lambda: _reflected(_new_high_day(5)), lambda: _reflected(_new_high_day(2)), dict(range_atr=1)),
    "intraday.top_reversal": (itd.S4, lambda: _top_day(True), lambda: _top_day(False), dict(move_atr=2, near_atr=0.5)),
    "intraday.bottom_reversal": (itd.S5, lambda: _reflected(_top_day(True)), lambda: _reflected(_top_day(False)), dict(move_atr=2, near_atr=0.5)),
    "intraday.vwap_break": (itd.S6, lambda: _vwap_day(2), lambda: _vwap_day(1), {}),
    "intraday.turbo_breakup": (itd.S7, lambda: _turbo_day(3), lambda: _turbo_day(1), {}),
    "intraday.turbo_breakdown": (itd.S8, lambda: _reflected(_turbo_day(3)), lambda: _reflected(_turbo_day(1)), {}),
    "intraday.circuit_watch": (itd.S9, lambda: _band_day(109.0), lambda: _band_day(100.0), dict(near_pct=1.5)),
    "intraday.rsi_extreme": (itd.S10, lambda: _rsi_day(True), lambda: _rsi_day(False), dict(pmin=50, pmax=500, min_vol=0)),
    "intraday.volume_surge": (itd.S11, lambda: _surge_day(4), lambda: _surge_day(1), dict(v=3)),
    "intraday.key_levels": (itd.S12, lambda: _day5(quiet_day(10)),
                            lambda: _day5(quiet_day(10) + [(102.9, 103.1, 102.9, 103.0, 1000.0)]), dict(tol_pct=0.15)),
}


# ------------------------------------------------------------------------------------------------- the twelve scanners
def test_the_twelve_scanners_and_only_circuit_watch_reads_the_band():
    assert [s.id for s in itd.SCANNERS] == IDS and set(CASES) == set(IDS)
    assert [s.id for s in itd.SCANNERS if "Band" in s.text] == ["intraday.circuit_watch"]
    assert {s.base_tf for s in itd.SCANNERS} <= set(itd.INTRADAY_TFS)
    assert itd.S7.base_tf == itd.S8.base_tf == itd.S10.base_tf == "1m"


@pytest.mark.parametrize("ident", IDS)
def test_each_scanner_matches_its_positive_case_and_not_its_negative(ident):
    sc, positive, negative, values = CASES[ident]
    assert _match(sc, positive(), **values).matched, ident
    assert not _match(sc, negative(), **values).matched, ident


@pytest.mark.parametrize("ident", IDS)
def test_a_not_set_parameter_is_asked_for_before_the_run(ident):
    sc = itd.BY_ID[ident]
    _, validated = compile_screen(sc.text, base_tf=sc.base_tf, params=sc.defaults())
    missing = set(re.findall(r"parameter \$(\w+) has no value", " ".join(p.message for p in validated.problems)))
    assert missing == set(sc.unset())
    assert validated.ok == (not sc.unset())
    values = {name: 1 for name in sc.unset()}
    _, filled = compile_screen(sc.text, base_tf=sc.base_tf, params={**sc.defaults(), **values})
    assert filled.ok, (ident, [p.message for p in filled.problems])


def test_circuit_watch_reads_both_bands_and_says_not_checked_without_one():
    """S9: near the upper band and near the lower band both match; no band in the quote is "not checked" with the reason,
    never a value and never a silent no-match; `near_pct` is Not set (S9 (e))."""
    assert [(p.name, p.value) for p in itd.S9.params] == [("near_pct", None)]
    assert _match(itd.S9, _band_day(109.0), near_pct=1.5).matched                # (110 - 109) / 109 = 0.92 %
    assert _match(itd.S9, _band_day(90.5), near_pct=1.5).matched                 # (90.5 - 90) / 90.5 = 0.55 %
    assert not _match(itd.S9, _band_day(100.0), near_pct=1.5).matched
    got = _match(itd.S9, _band_day(109.0, quote=False), near_pct=1.5)
    assert not got.matched and "price band not available" in " ".join(str(v) for v in vars(got).values())


def test_circuit_watch_is_refused_where_bars_are_read_back_in_time():
    """BandDistancePct is last bar only (PR-3): history, the library's studies and template compile refuse it; the run
    path (band=True) accepts it."""
    from app.screener.routes import missing_data
    ast = parse(itd.S9.text)
    assert missing_data(ast, band=True) == []
    assert missing_data(ast) != []


def test_reference_values_carry_over_and_other_market_units_are_not_set():
    """M94-Q2 / Q3: the study's unit-free values are reference values; share counts, rupee amounts and prices are Not set;
    a value the study gives no number for is the ATP default (the breakout parameters') or Not set."""
    def by(sc):
        return {p.name: p for p in sc.params}
    s1, s2, s10, s12 = by(itd.S1), by(itd.S2), by(itd.S10), by(itd.S12)
    assert s1["gap_pct"].value == 2 and s1["gap_pct"].ref and all(s1[n].value is None for n in ("pre_vol", "min_turnover", "min_atr_pct"))
    assert (s2["v"].value, s2["v_max"].value, s2["cum_v"].value, s2["range_bars"].value) == (4, 20, 2, 3) and s2["range_atr"].value is None
    assert (s2["vwap"].value, s2["skip"].value, s2["k"].value) == (True, 15, 0.2) and not s2["skip"].ref
    assert all(p.value is None for p in itd.S4.params) and by(itd.S6)["v"].value == 1.5 and by(itd.S7)["v"].value == 1.5
    assert (s10["hi"].value, s10["lo"].value, s10["n"].value) == (90, 10, 14) and all(s10[n].value is None for n in ("pmin", "pmax", "min_vol"))
    assert by(itd.S11)["v"].value is None and s12["tol_pct"].value is None and by(itd.S9)["near_pct"].value is None
    assert [s12[n].value for n in ("sma_a", "sma_b", "ema_a", "ema_b")] == [50, 200, 9, 20]
    for sc in itd.SCANNERS:
        for p in sc.params:
            assert p.as_dict()["note"] == ("Not set" if p.value is None else itd.REF_LABEL if p.ref else "ATP default")


def test_a_zero_volume_setting_means_no_volume_condition():
    gap = _gap_day(103.0)
    values = dict(min_turnover=1, min_atr_pct=0.1)
    assert _match(itd.S1, gap, pre_vol=0, **values).matched
    # a pre-open volume condition without pre-open bars: never a false match, and the run says why (M94-Q4)
    got = _match(itd.S1, gap, pre_vol=10, **values)
    assert not got.matched and got.reason.code == "no_preopen"
    with_pre = with_preopen(gap)                                  # each date's first bar becomes a pre-open bar
    assert _match(itd.S1, with_pre, pre_vol=1, **values).matched
    assert not _match(itd.S1, with_pre, pre_vol=10 ** 9, **values).matched
    assert not _match(itd.S10, _rsi_day(True), pmin=50, pmax=500, min_vol=10 ** 9).matched


def test_the_price_range_and_the_tolerance_are_his_own_values():
    assert not _match(itd.S10, _rsi_day(True), pmin=500, pmax=900, min_vol=0).matched
    assert not _match(itd.S12, _day5(quiet_day(10)), tol_pct=0.0001).matched


def test_key_levels_names_each_level_in_its_own_branch():
    """M91 rows: one OR branch per level, so the legend says which level is near."""
    branches = parse(itd.S12.text).items
    assert len(branches) == len(itd.S12_LEVELS) == 13
    assert "PDH()" in itd.S12.text and "PreOpenHigh()" in itd.S12.text and "Max(high, 250)@1d" in itd.S12.text


# ------------------------------------------------------------------------------------------------------------ mirrors
def test_the_short_counterparts_are_the_mirror_rule_text_of_the_long_screens():
    for long, short in ((itd.S2, itd.S3), (itd.S4, itd.S5), (itd.S7, itd.S8)):
        assert short.mirror_of == long.id and short.params == long.params and short.base_tf == long.base_tf
        assert mr.mirror(parse(long.text)).text == short.text
        assert mr.mirror(parse(short.text)).text == long.text                                   # and back
    assert itd.S3.text == ('DayExtremeBreak("down", $v, $vwap, $skip, $k) AND RelVolumeTOD(20, "bar") <= $v_max AND '
                           'RelVolumeTOD(20, "cumulative") >= $cum_v AND Max(high, $range_bars) - Min(low, $range_bars) >= $range_atr * ATR(14)[1]')
    assert itd.S5.text == ('DayOpen() - SessionLow() >= $move_atr * ATR(14)[1] AND low - SessionLow() <= $near_atr * ATR(14)[1] AND '
                           '(Pattern("hammer") OR Pattern("bullish_engulfing") OR Pattern("morning_star"))')
    assert itd.S8.text == 'DayExtremeBreak("down", $v, $vwap, $skip, $k)'


@pytest.mark.parametrize("long,short,make,args,values", [
    (itd.S2, itd.S3, _new_high_day, (5, 2), dict(range_atr=1)), (itd.S4, itd.S5, _top_day, (True, False), dict(move_atr=2, near_atr=0.5)),
    (itd.S7, itd.S8, _turbo_day, (3, 1), {}),
])
def test_mirror_equality_on_reflected_sessions(long, short, make, args, values):
    """The short screen on the bars == the long screen on the reflected bars, both ways, on each fixture."""
    for arg in args:
        frame = make(arg)
        assert _match(short, frame, **values).matched == _match(long, _reflected(frame), **values).matched
        assert _match(short, _reflected(frame), **values).matched == _match(long, frame, **values).matched


def test_the_library_lists_the_generated_mirror_of_each_long_scanner():
    import app.main  # noqa: F401
    entries = {m["mirror_of"]: m for m in lib.mirrors(tx.load())}
    resolved = {s.id: lib.resolve(s)[0] for s in tx.load().screens if s.source == "intraday"}
    assert entries["intraday.new_day_high"]["text"] == lib.mirror_of(resolved["intraday.new_day_high"])["text"]
    assert entries["intraday.vwap_break"]["text"].startswith("CrossBelow(close, SessionVWAP())")
    assert "intraday.new_day_low" not in entries                                    # a short screen has no .short entry


# ----------------------------------------------------------------------------------------- filing, words and the footer
def test_every_scanner_is_filed_on_both_axes_as_intraday_with_your_broker_data():
    import app.main  # noqa: F401
    assert tx.lint() == [] and tx.coverage_problems() == []
    src = tx.sources()["intraday"]
    assert src.flag == "intraday_pack" and src.also == ("screener_breakouts",) and src.note == itd.FOOTER
    filed = {s.id: s for s in tx.load().screens if s.source == "intraday"}
    assert list(filed) == IDS
    for sc in itd.SCANNERS:
        s = filed[sc.id]
        assert (s.name, s.definition) == (sc.name, sc.definition)
        assert s.tags["timeframe_family"] == ["intraday"] and s.tags["data_tier"] == ["live_per_user"]
    assert {s.tags["direction"] for i, s in filed.items() if i in ("intraday.new_day_low", "intraday.top_reversal", "intraday.turbo_breakdown")} == {"short"}
    assert "intraday" in tx.default_hidden()


def test_no_advice_words_book_names_or_tickers():
    assert itd.FOOTER == "Information and education only, not investment advice."
    for sc in itd.SCANNERS:
        for text in (sc.name, sc.definition, *(p.doc for p in sc.params)):
            assert tx.advice_words(text) == [], (sc.id, text)
            assert not re.search(r"\b(stop|target|buy|sell)", text, re.IGNORECASE), (sc.id, text)
            assert text.isascii() and not re.search(r"\b[A-Z]{3,}\b(?<!ATR)(?<!RSI)(?<!VWAP)(?<!ATP)(?<!NSE)", text), (sc.id, text)


def test_the_resolved_texts_validate_with_his_values():
    import app.main  # noqa: F401
    for screen in tx.load().screens:
        if screen.source != "intraday":
            continue
        resolved, why = lib.resolve(screen)
        assert resolved is not None, why
        sc = itd.BY_ID[screen.id]
        assert resolved.text == sc.text and resolved.params == sc.defaults() and resolved.base_tf == sc.base_tf
        _, validated = compile_screen(resolved.text, base_tf=resolved.base_tf, params={**resolved.params, **{u: 1 for u in sc.unset()}})
        assert validated.ok, (screen.id, [p.message for p in validated.problems])


# ------------------------------------------------------------------------------------------------ the flag and the API
def test_the_gallery_leaves_them_out_while_the_flag_is_off(gallery_on):
    import app.main  # noqa: F401
    headers, _ = _owner("m94-pr2-off@example.com")
    off = client.get("/api/screener/taxonomy", headers=headers).json()
    assert not [s for s in off["screens"] if s["source"] == "intraday"] and "source_notes" not in off
    assert client.get("/api/screener/intraday", headers=headers).status_code == 503
    _set_flags(intraday_pack=True)                                     # still hidden without screener_breakouts
    assert not [s for s in client.get("/api/screener/taxonomy", headers=headers).json()["screens"] if s["source"] == "intraday"]
    _set_flags(screener_breakouts=True)
    on = client.get("/api/screener/taxonomy", headers=headers).json()
    assert [s["id"] for s in on["screens"] if s["source"] == "intraday"] == IDS
    assert on["source_notes"] == {"intraday": itd.FOOTER}
    body = client.get("/api/screener/intraday", headers=headers).json()
    assert [s["id"] for s in body["scanners"]] == IDS and body["footer"] == itd.FOOTER
    gappers = body["scanners"][0]
    assert gappers["unset"] == ["pre_vol", "min_turnover", "min_atr_pct"] and gappers["footer"] == itd.FOOTER


def test_the_builder_opens_a_scanner_with_its_not_set_values_still_asked(gallery_on):
    import app.main  # noqa: F401
    headers, _ = _owner("m94-pr2-stages@example.com")
    _set_flags(screener_builder_u2=True)
    r = client.post("/api/screener/stages", json={"library_id": "intraday.new_day_high"}, headers=headers)
    assert r.status_code == 404                                        # the family is off
    _set_flags(**{f: True for f in PACK})
    body = client.post("/api/screener/stages", json={"library_id": "intraday.new_day_high"}, headers=headers).json()
    assert body["base_tf"] == "5m" and body["unset"] == ["range_atr"] and "$range_atr" in body["text"]
    assert 'DayExtremeBreak("up", 4, TRUE, 15, 0.2)' in body["text"]
    full = client.post("/api/screener/stages", json={"library_id": "intraday.vwap_break"}, headers=headers).json()
    assert "unset" not in full and full["text"] == 'CrossAbove(close, SessionVWAP()) AND RelVolumeTOD(20, "bar") >= 1.5'


# ---------------------------------------------------------------------------------------------- the frontend samples
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "screener"


def test_the_frontend_samples_are_what_the_routes_serve(gallery_on):
    """src/screener/intradaySample.json and taxonomyIntradaySample.json feed the frontend tests and stories; regenerate
    them with `python -m app.screener.intraday > ...` and `python -m app.screener.intraday taxonomy > ...`."""
    import app.main  # noqa: F401
    headers, _ = _owner("m94-pr2-samples@example.com")
    _set_flags(**{f: True for f in PACK})
    assert client.get("/api/screener/intraday", headers=headers).json() == json.loads((FRONTEND / "intradaySample.json").read_text(encoding="utf-8"))
    served = client.get("/api/screener/taxonomy", headers=headers).json()
    assert served == json.loads((FRONTEND / "taxonomyIntradaySample.json").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(itd.taxonomy_sample())) == served


def test_the_texts_are_canonical():
    from app.screener import nodes
    for sc in itd.SCANNERS:
        assert nodes.to_text(parse(sc.text)) == sc.text, sc.id
