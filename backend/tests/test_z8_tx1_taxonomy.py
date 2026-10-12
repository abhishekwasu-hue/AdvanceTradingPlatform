"""U9 TX1 (docs/specs/ATP_SCREENER_TAXONOMY_ADDENDUM.md): the screen library taxonomy - the data file passes its lint
rules, every pre-built screen is filed on both axes (the CI guard: a new template cannot ship untagged), template
sources register and hide behind their own flag, and the API (flag `screener_gallery`, off by default) returns both
axes, the full matrix and the screens."""
import asyncio
import json
from pathlib import Path

import pytest

from app.platform import controls
from app.screener import taxonomy as tx
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "screener"
STARTERS = FRONTEND / "starters.json"
SAMPLE = FRONTEND / "taxonomySample.json"


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
    _set_flags(screener_v2=True, screener_gallery=True)
    yield
    _set_flags(screener_v2=False, screener_gallery=False)


def _data():
    return json.loads(tx.DATA_FILE.read_text(encoding="utf-8"))


# --- the data file and its lint rules -------------------------------------------------------------------------------

def test_the_data_file_passes_every_lint_rule():
    assert tx.lint() == []
    tax = tx.load()
    assert [m.code for m in tax.methods] == ["A", "B", "C", "D", "E", "F"]
    assert [s.code for s in tax.setups] == [str(i) for i in range(1, 9)]
    assert [m.id for m in tax.methods if m.user_built] == ["my_screens"]
    assert set(tax.tags) == set(tx.TAG_KEYS)


def test_every_pre_built_screen_is_filed_on_both_axes_with_known_tags():
    tax = tx.load()
    methods, setups = {m.id for m in tax.methods}, {s.id for s in tax.setups}
    assert tax.screens, "the starters are filed"
    for s in tax.screens:
        assert s.method in methods and s.setup in setups and s.method != "my_screens"
        for key in tx.TAG_KEYS:
            allowed = {v["id"] for v in tax.tags[key]}
            values = s.tags[key] if isinstance(s.tags[key], list) else [s.tags[key]]
            assert values and set(values) <= allowed, (s.id, key)
        assert len(s.definition) <= tx.MAX_DEFINITION_CHARS and "\n" not in s.definition


@pytest.mark.parametrize("change, expect", [
    (lambda d: d["screens"][0].update(method="nope"), "not filed on the method axis"),
    (lambda d: d["screens"][0].pop("setup"), "not filed on the setup axis"),
    (lambda d: d["screens"][0].update(method="my_screens"), "cannot be filed under 'My screens'"),
    (lambda d: d["screens"][0].update(direction="sideways"), "direction 'sideways' not in the vocabulary"),
    (lambda d: d["screens"][0].update(timeframe_family="swing"), "timeframe_family must be a non-empty list"),
    (lambda d: d["screens"][0].update(difficulty=None), "difficulty must be one value"),
    (lambda d: d["screens"][0].update(definition="x" * 161), "at most 160 characters on one line"),
    (lambda d: d["screens"][0].update(definition="Line one\nline two"), "at most 160 characters on one line"),
    (lambda d: d["screens"][0].update(definition="Stocks to buy before the move"), "advice wording is not allowed (buy)"),
    (lambda d: d["screens"][0].update(name="Best momentum picks"), "advice wording is not allowed (best)"),
    (lambda d: d["screens"][0].update(definition="Guaranteed breakout with a clear target"), "advice wording is not allowed"),
    (lambda d: d["screens"][0].update(definition="Go long above 250 CE"), "a specific buy / sell call"),
    (lambda d: d["setups"][0].update(label="Recommended breakouts"), "advice wording is not allowed (recommended)"),
    (lambda d: d["screens"].append(dict(d["screens"][0])), "duplicate id"),
    (lambda d: d["screens"][0].update(id="breakouts.x"), "id must start with its source (starter.)"),
    (lambda d: d["screens"][0].update(extra=1), "unknown fields extra"),
    (lambda d: d["screens"][0].update(evidence_ready="yes"), "evidence_ready must be true or false"),
    (lambda d: d["tags"].pop("difficulty"), "tags.difficulty: missing or empty"),
    (lambda d: d["tags"].update(colour=[{"id": "red", "label": "Red"}]), "unknown tag family"),
    (lambda d: d.update(methods=[]), "methods: none defined"),
])
def test_the_lint_rules_refuse_a_broken_file(change, expect):
    data = _data()
    change(data)
    _, problems = tx.parse(data)
    assert any(expect in p for p in problems), problems


def test_load_refuses_a_file_that_fails_lint(tmp_path):
    data = _data()
    data["screens"][0]["setup"] = "everything"
    bad = tmp_path / "taxonomy.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(tx.TaxonomyError) as err:
        tx.load(bad)
    assert any("setup axis" in p for p in err.value.problems)


def test_negated_advice_words_and_neutral_trading_words_pass():
    assert tx.advice_words("Trapped buyers at a failed breakout; premium decay candidates for sellers.") == []
    assert tx.advice_words("Close above EMA(close, 50) on daily bars.") == []
    assert tx.advice_words("You should buy now") != []


# --- the CI guard: every template of every source is filed ----------------------------------------------------------

def test_every_frontend_starter_template_is_filed_by_id_and_name():
    """The empty canvas's starters (frontend/src/screener/starters.json) - a new starter must be filed here."""
    starters = json.loads(STARTERS.read_text(encoding="utf-8"))["starters"]
    filed = {s.id: s for s in tx.load().screens if s.source == "starter"}
    assert sorted(t["id"] for t in starters) == sorted(filed), "file every starter in app/screener/data/taxonomy.json"
    for t in starters:
        assert filed[t["id"]].name == t["name"]


def test_every_registered_backend_template_source_is_fully_filed():
    """Template modules register at import (register_source); loading the app imports every router and so every
    template module. Nothing may be unfiled, orphaned or filed under a source nobody registered."""
    import app.main  # noqa: F401 - registers every template source
    assert tx.coverage_problems() == []


def test_the_guard_catches_an_unfiled_template_an_orphan_and_an_unregistered_source():
    base = _data()
    base["screens"] = [s for s in base["screens"] if s["source"] not in ("breakouts", "legends")]   # the filed U6 / U7 templates aside
    tax, _ = tx.parse(base)
    fake = {"breakouts": tx.Source("breakouts", lambda: ["breakouts.daily_52w_high"], "screener_breakouts")}
    problems = tx.coverage_problems(tax, fake)
    assert problems == ["template 'breakouts.daily_52w_high' from source 'breakouts' is not filed in the taxonomy "
                        "(add it to app/screener/data/taxonomy.json)"]
    data = base
    entry = dict(data["screens"][0], id="breakouts.daily_52w_high", source="breakouts", name="Daily 52-week high breakout",
                 definition="Close above the prior 52-week high on volume.", method="price_action", setup="breakout")
    data["screens"].append(entry)
    filed, lint = tx.parse(data)
    assert lint == [] and tx.coverage_problems(filed, fake) == []                          # filed: the guard is satisfied
    assert tx.coverage_problems(filed, {}) == ["taxonomy source 'breakouts' is not registered (register_source) - its screens would never show"]
    gone = {"breakouts": tx.Source("breakouts", lambda: [], None)}
    assert tx.coverage_problems(filed, gone) == ["taxonomy entry 'breakouts.daily_52w_high' names a template that source 'breakouts' does not have"]


def test_register_source_refuses_the_frontend_source_name_and_bad_names():
    for name in ("starter", "Bad Name", ""):
        with pytest.raises(ValueError):
            tx.register_source(name, lambda: [])


# --- the matrix and the API ----------------------------------------------------------------------------------------

def test_the_matrix_has_every_cell_and_counts_the_filed_screens():
    tax = tx.load()
    m = tx.matrix(tax, tax.screens)
    assert set(m) == {x.id for x in tax.methods} and all(set(row) == {s.id for s in tax.setups} for row in m.values())
    assert sum(sum(r.values()) for r in m.values()) == len(tax.screens)
    assert m["indicator"]["momentum"] == 1 and m["indicator"]["reversal"] == 1 and m["indicator"]["pullback"] == 1
    assert m["fundamental"]["breakout"] == 0                                               # an empty cell stays, as 0
    assert m["price_action"]["breakout"] == 10 and m["indicator"]["breakout"] == 1 and m["volume_flow"]["breakout"] == 1   # U6 BK1 / BK2


def test_the_frontend_sample_is_the_server_payload():
    """src/screener/taxonomySample.json feeds the frontend tests and stories; regenerate it with
    `python -m app.screener.taxonomy > ../frontend/src/screener/taxonomySample.json` after changing the data file."""
    import app.main  # noqa: F401 - registers every template source
    hidden = tx.default_hidden()                                       # flagged-off sources (U6 breakouts) as a new org sees it
    assert "breakouts" in hidden
    assert json.loads(SAMPLE.read_text(encoding="utf-8")) == json.loads(json.dumps(tx.payload(tx.load(), hidden_sources=hidden)))


def test_the_api_is_off_by_default_and_needs_both_flags():
    headers, _ = _owner("tx1-off@example.com")
    assert "screener_gallery" in controls.FEATURE_FLAGS and "screener_gallery" in controls.DEFAULT_OFF_FLAGS
    r = client.get("/api/screener/taxonomy", headers=headers)
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_v2"
    _set_flags(screener_v2=True)
    try:
        r = client.get("/api/screener/taxonomy", headers=headers)
        assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_gallery"
    finally:
        _set_flags(screener_v2=False)
    assert client.get("/api/screener/taxonomy").status_code == 401


def test_the_api_shape(gallery_on):
    headers, _ = _owner("tx1-shape@example.com")
    body = client.get("/api/screener/taxonomy", headers=headers).json()
    assert set(body) == {"version", "methods", "setups", "tags", "screens", "matrix", "total", "note"}
    assert body["version"] == "tx1/1" and body["total"] == len(body["screens"]) == 3
    assert [m["id"] for m in body["methods"]] == ["price_action", "indicator", "volume_flow", "fundamental", "legends", "my_screens"]
    assert set(body["methods"][0]) == {"id", "code", "label", "definition", "user_built"}
    assert len(body["setups"]) == 8 and all(len(row) == 8 for row in body["matrix"].values())
    screen = body["screens"][0]
    assert set(screen) == {"id", "source", "name", "definition", "method", "setup", "tags", "evidence_ready", "fitness_available"}
    assert set(screen["tags"]) == set(tx.TAG_KEYS)
    assert tx.advice_words(json.dumps({k: body[k] for k in ("methods", "setups", "screens", "note")})) == []


def test_the_api_narrows_the_list_not_the_matrix(gallery_on):
    headers, _ = _owner("tx1-filter@example.com")
    one = client.get("/api/screener/taxonomy?method=indicator&setup=reversal", headers=headers).json()
    assert [s["id"] for s in one["screens"]] == ["starter.oversold"] and one["total"] == 3
    assert one["matrix"]["indicator"]["momentum"] == 1
    empty = client.get("/api/screener/taxonomy?method=price_action", headers=headers).json()
    assert empty["screens"] == [] and empty["total"] == 3
    assert client.get("/api/screener/taxonomy?method=astrology", headers=headers).status_code == 422
    assert client.get("/api/screener/taxonomy?setup=everything", headers=headers).status_code == 422


def test_a_source_behind_its_own_flag_is_hidden_while_the_flag_is_off(gallery_on, monkeypatch):
    """How the breakouts (U6 BK1, flag `screener_breakouts`) join: registered with their flag, hidden until it is on."""
    data = _data()
    data["screens"].append(dict(data["screens"][0], id="flagged.one", source="flagged", name="Flagged screen",
                                definition="A screen from a flagged source.", method="price_action", setup="breakout"))
    filed, problems = tx.parse(data)
    assert problems == []
    monkeypatch.setattr(tx, "load", lambda path=tx.DATA_FILE: filed)
    tx.register_source("flagged", lambda: ["flagged.one"], flag="screener_intrabar")     # a default-off flag stands in
    try:
        headers, _ = _owner("tx1-flagged@example.com")
        hidden = client.get("/api/screener/taxonomy", headers=headers).json()
        assert "flagged.one" not in [s["id"] for s in hidden["screens"]] and hidden["matrix"]["price_action"]["breakout"] == 0
        _set_flags(screener_intrabar=True)
        shown = client.get("/api/screener/taxonomy", headers=headers).json()
        assert "flagged.one" in [s["id"] for s in shown["screens"]] and shown["matrix"]["price_action"]["breakout"] == 1
    finally:
        _set_flags(screener_intrabar=False)
        tx.unregister_source("flagged")
