"""M97 PR-2 (docs/design/M97_SUPPLY_DEMAND.md, Z4): the Supply & Demand scanners as the library source `supply_demand`
(app/screener/sd_scanners.py), behind `sd_pack` (off by default), as M94 PR-2 built the intraday source.

* Each scanner runs through the production runtime (`run_screen`) on hand-built bars: a positive and a negative case,
  on a daily and on a 5-minute frame (any timeframe).
* Every detector number is a $parameter with the M97-Q2 reference value; the near distance is Not set (asked at the run).
* Filing and lint: every scanner is filed in taxonomy.json, no advice wording, the footer is the order's sentence.
* With the flag off the gallery, the scanner list and the builder leave them out (nothing changes for users)."""
from __future__ import annotations

import asyncio
import json
import re
import warnings
from pathlib import Path

import pandas as pd
import pytest

from app.platform import controls
from app.price_action import sd_zones as sd
from app.screener import compile_screen, nodes
from app.screener import library as lib
from app.screener import sd_scanners as sds
from app.screener import taxonomy as tx
from app.screener.parser import parse
from app.screener.runtime import SymbolData, run_screen
from tests.test_auth_api import _session_factory, client
from tests.test_m97_sd_zones import BASE, FLAT, FLAT_OUT, LEG_DOWN, OUT_UP, STATE_PATH, _frame, _mirror
from tests.test_phase_l_ai import _owner

IDS = ["supply_demand.demand_enter", "supply_demand.supply_enter", "supply_demand.zone_formed", "supply_demand.zone_violated",
       "supply_demand.near_fresh_zone"]
GALLERY = ("screener_v2", "screener_gallery")
PACK = ("sd_pack",)
WEAK_OUT = (97.1, 99.4, 97.2, 99.2)             # range 2.2 < 1.5 x the average: no zone


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


def _bars(candles, tf: str = "1d") -> pd.DataFrame:
    """The PR-1 hand-built candles (21 neutral candles first) on a daily or a 5-minute index."""
    df = _frame(candles)
    if tf == "1d":
        df.index = pd.date_range("2025-01-01 10:00", periods=len(df), freq="D", tz="UTC")
    return df


def _match(sc, candles, tf: str = "1d", **values):
    """The scanner's last closed bar through the production run path (validated, then run_screen) on `tf`."""
    params = {**sc.defaults(), **values}
    ast, validated = compile_screen(sc.text, base_tf=tf, params=params)
    assert validated.ok, [p.message for p in validated.problems]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        got = run_screen(ast, validated, [SymbolData("S", {tf: _bars(candles, tf)})], base_tf=tf, params=params, check_history=False)
    return got[0]


FORMED = [LEG_DOWN, *BASE, OUT_UP]
# (scanner, positive candles, negative candles, the values he sets for the run - the Not set ones)
CASES = {
    "supply_demand.demand_enter": (sds.Z1, STATE_PATH[:6], STATE_PATH[:5], {}),        # the first visit / still away
    "supply_demand.supply_enter": (sds.Z2, _mirror(STATE_PATH[:6]), _mirror(STATE_PATH[:5]), {}),
    "supply_demand.zone_formed": (sds.Z3, FORMED, [LEG_DOWN, *BASE, WEAK_OUT], {}),
    "supply_demand.zone_violated": (sds.Z4, STATE_PATH, STATE_PATH[:-1], {}),           # the close below the distal line
    "supply_demand.near_fresh_zone": (sds.Z5, STATE_PATH[:5], STATE_PATH[:5], None),    # within 4 % / within 3 %
}


# ----------------------------------------------------------------------------------------------- the five scanners
def test_the_five_scanners():
    assert [s.id for s in sds.SCANNERS] == IDS and set(CASES) == set(IDS)
    assert {s.base_tf for s in sds.SCANNERS} == {"1d"}
    assert [s.unset() for s in sds.SCANNERS] == [[], [], [], [], ["within_pct"]]


@pytest.mark.parametrize("ident", IDS)
@pytest.mark.parametrize("tf", ["1d", "5m"])
def test_each_scanner_matches_its_positive_case_and_not_its_negative(ident, tf):
    sc, positive, negative, values = CASES[ident]
    if values is None:                                     # the near scanner: his own distance decides
        assert _match(sc, positive, tf, within_pct=4).matched                    # (101.5 - 97.7) / 101.5 = 3.74 %
        assert not _match(sc, negative, tf, within_pct=3).matched
        return
    assert _match(sc, positive, tf, **values).matched, ident
    assert not _match(sc, negative, tf, **values).matched, ident


def test_entering_reads_only_a_fresh_zone_and_only_its_side():
    assert not _match(sds.Z1, STATE_PATH[:9]).matched                  # the second visit: the zone is tested, not fresh
    assert not _match(sds.Z2, STATE_PATH[:6]).matched                  # a demand visit is not a supply one
    assert not _match(sds.Z1, _mirror(STATE_PATH[:6])).matched


def test_the_formation_is_a_parameter():
    assert _match(sds.Z3, FORMED, formation="DBR").matched
    for other in ("RBR", "RBD", "DBD"):
        assert not _match(sds.Z3, FORMED, formation=other).matched, other
    assert _match(sds.Z3, _mirror(FORMED), formation="RBD").matched and _match(sds.Z3, _mirror(FORMED)).matched
    assert sds.Z3.params[0].name == "formation" and sds.Z3.params[0].value == "any" and not sds.Z3.params[0].ref
    _, bad = compile_screen(sds.Z3.text, base_tf="1d", params={**sds.Z3.defaults(), "formation": "XYZ"})
    assert not bad.ok


def test_flat_bars_are_no_base_unless_his_setting_says_so():
    flat = [LEG_DOWN, FLAT, FLAT, FLAT_OUT]                                  # M98-P1 Q2: two flat candles, then a leg-out
    assert not _match(sds.Z3, flat).matched                                  # the reference: skip_flat_bars on
    assert _match(sds.Z3, flat, skip_flat_bars=False).matched                # his setting off: the old two-candle base
    assert _match(sds.Z3, flat, skip_flat_bars=False, formation="DBR").matched


def test_violated_reads_both_sides_and_names_each_in_its_branch():
    assert _match(sds.Z4, _mirror(STATE_PATH)).matched and not _match(sds.Z4, _mirror(STATE_PATH[:-1])).matched
    branches = parse(sds.Z4.text).items
    assert [b.args[0].value for b in branches] == ["demand", "supply"]


def test_near_reads_fresh_zones_only_and_his_distance_decides():
    assert not _match(sds.Z5, STATE_PATH[:7], within_pct=50).matched           # visited: no fresh zone left
    closer = STATE_PATH[:5] + [(101.0, 101.2, 98.0, 99.0)]                     # low 98.0 stays above 97.7: no visit
    assert _match(sds.Z5, closer, within_pct=1.5).matched                     # (99.0 - 97.7) / 99.0 = 1.31 %
    assert not _match(sds.Z5, closer, within_pct=1.0).matched
    assert _match(sds.Z5, _mirror(STATE_PATH[:5]), within_pct=4).matched      # the supply side
    _, validated = compile_screen(sds.Z5.text, base_tf="1d", params=sds.Z5.defaults())
    assert not validated.ok and "parameter $within_pct has no value" in " ".join(p.message for p in validated.problems)


def test_the_detector_settings_are_his_parameters_with_reference_values():
    """M97-Q2 / Q3: every detector number is a $parameter with the reference value, labelled; the leg-out reference is the
    function default; the values and their order are sd_zones.SETTINGS_TABLE's."""
    assert [(p.name, p.value) for p in sds.SETTINGS] == [(row[0], row[2]) for row in sd.SETTINGS_TABLE]
    assert [p.name for p in sds.SETTINGS if not p.ref] == ["out_ref"]
    for sc in sds.SCANNERS:
        assert sc.params[-len(sds.SETTINGS):] == sds.SETTINGS
        for p in sds.SETTINGS:
            assert f"{p.name}=${p.name}" in sc.text
        for p in sc.params:
            assert p.as_dict()["note"] == ("Not set" if p.value is None else sds.REF_LABEL if p.ref else "ATP default")
    assert sds.REF_LABEL == "Reference value - edit before use"
    # his own value changes the read: a weak leg-out is a zone once he lowers out_mult
    assert _match(sds.Z3, [LEG_DOWN, *BASE, WEAK_OUT], out_mult=1.0).matched
    _, bounded = compile_screen(sds.Z1.text, base_tf="1d", params={**sds.Z1.defaults(), "leg_mult": 9})
    assert not bounded.ok


# ----------------------------------------------------------------------------------------- filing, words and the footer
def test_every_scanner_is_filed_on_both_axes():
    import app.main  # noqa: F401
    assert tx.lint() == [] and tx.coverage_problems() == []
    src = tx.sources()["supply_demand"]
    assert src.flag == "sd_pack" and src.also == () and src.note == sds.FOOTER
    filed = {s.id: s for s in tx.load().screens if s.source == "supply_demand"}
    assert list(filed) == IDS
    for sc in sds.SCANNERS:
        s = filed[sc.id]
        assert (s.name, s.definition, s.method) == (sc.name, sc.definition, "price_action")
    assert {i: s.tags["direction"] for i, s in filed.items()} == {
        IDS[0]: "long", IDS[1]: "short", IDS[2]: "both", IDS[3]: "both", IDS[4]: "both"}
    assert "supply_demand" in tx.default_hidden()


def test_no_advice_words_and_the_footer():
    assert sds.FOOTER == "Information and education only, not investment advice."
    for sc in sds.SCANNERS:
        for text in (sc.name, sc.definition, *(p.doc for p in sc.params)):
            assert tx.advice_words(text) == [], (sc.id, text)
            assert not re.search(r"\b(stop|target|buy|sell|entry)", text, re.IGNORECASE), (sc.id, text)
            assert text.isascii(), (sc.id, text)
        assert sc.as_dict()["footer"] == sds.FOOTER


def test_the_resolved_texts_validate_and_are_canonical():
    import app.main  # noqa: F401
    for screen in tx.load().screens:
        if screen.source != "supply_demand":
            continue
        resolved, why = lib.resolve(screen)
        assert resolved is not None, why
        sc = sds.BY_ID[screen.id]
        assert resolved.text == sc.text and resolved.params == sc.defaults() and resolved.base_tf == sc.base_tf
        _, validated = compile_screen(resolved.text, base_tf=resolved.base_tf, params={**resolved.params, **{u: 1 for u in sc.unset()}})
        assert validated.ok, (screen.id, [p.message for p in validated.problems])
        assert nodes.to_text(parse(sc.text)) == sc.text


# ------------------------------------------------------------------------------------------------ the flag and the API
def test_the_gallery_leaves_them_out_while_the_flag_is_off(gallery_on):
    import app.main  # noqa: F401
    headers, _ = _owner("m97-pr2-off@example.com")
    off = client.get("/api/screener/taxonomy", headers=headers).json()
    assert not [s for s in off["screens"] if s["source"] == "supply_demand"]
    assert "supply_demand" not in off.get("source_notes", {})
    assert client.get("/api/screener/supply-demand", headers=headers).status_code == 503
    _set_flags(sd_pack=True)
    on = client.get("/api/screener/taxonomy", headers=headers).json()
    assert [s["id"] for s in on["screens"] if s["source"] == "supply_demand"] == IDS
    assert on["source_notes"]["supply_demand"] == sds.FOOTER
    body = client.get("/api/screener/supply-demand", headers=headers).json()
    assert [s["id"] for s in body["scanners"]] == IDS and body["footer"] == sds.FOOTER
    assert body["scanners"][4]["unset"] == ["within_pct"]


def test_the_builder_opens_a_scanner_with_its_not_set_value_still_asked(gallery_on):
    import app.main  # noqa: F401
    headers, _ = _owner("m97-pr2-stages@example.com")
    _set_flags(screener_builder_u2=True)
    r = client.post("/api/screener/stages", json={"library_id": "supply_demand.near_fresh_zone"}, headers=headers)
    assert r.status_code == 404                                        # the family is off
    _set_flags(sd_pack=True)
    body = client.post("/api/screener/stages", json={"library_id": "supply_demand.near_fresh_zone"}, headers=headers).json()
    assert body["base_tf"] == "1d" and body["unset"] == ["within_pct"] and "$within_pct" in body["text"]
    assert 'SDZoneDistancePct("demand", "fresh", avg_n=20, leg_mult=1, base_mult=0.7' in body["text"]   # M98-P1 Q1
    assert 'leg_body_pct=50, base_body_pct=50' in body["text"] and 'skip_flat_bars=TRUE' in body["text"]   # M98-P1 Q1 / Q2
    full = client.post("/api/screener/stages", json={"library_id": "supply_demand.demand_enter"}, headers=headers).json()
    assert "unset" not in full and full["text"].startswith('SDZoneEnter("demand", avg_n=20, leg_mult=1, base_mult=0.7')


# ---------------------------------------------------------------------------------------------- the frontend samples
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "screener"


def test_the_frontend_samples_are_what_the_routes_serve(gallery_on):
    """src/screener/sdSample.json and taxonomySdSample.json feed the frontend tests; regenerate them with
    `python -m app.screener.sd_scanners > ...` and `python -m app.screener.sd_scanners taxonomy > ...`."""
    import app.main  # noqa: F401
    headers, _ = _owner("m97-pr2-samples@example.com")
    _set_flags(sd_pack=True)
    assert client.get("/api/screener/supply-demand", headers=headers).json() == json.loads((FRONTEND / "sdSample.json").read_text(encoding="utf-8"))
    served = client.get("/api/screener/taxonomy", headers=headers).json()
    assert served == json.loads((FRONTEND / "taxonomySdSample.json").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(sds.taxonomy_sample())) == served


def test_the_module_is_english_and_public_safe():
    root = Path(__file__).resolve().parents[1]
    assert (root / "app/screener/sd_scanners.py").read_text(encoding="utf-8").isascii()
