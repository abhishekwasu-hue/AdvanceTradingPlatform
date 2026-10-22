"""Idea library slice 1 (owner order ATP_SESSION_IDEA_LIBRARY.md, T1-T8): the content, its guard, the scans against the
ScreenQL registry and the read-only API behind the `idea_library` flag (off: 404, nothing there)."""
import asyncio
import json
import re
import uuid

import pytest

from app.core import rate_limit
from app.idea_library import content as c
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO
from app.screener import nodes
from app.screener import stages as st
from app.screener.parser import parse
from app.screener.registry import FUNCTIONS
from tests.test_auth_api import _register, _session_factory, client

LIB = c.load()
SCANS = [(idea, scan) for idea in LIB.ideas for scan in idea.scans]
SCAN_FLAGS = ("screener_v2", "screener_builder_u2", "screener_breakouts", "screener_breakouts_bk4", "market_zones",
              "screenql_amw_functions")


def _run(coro):
    return asyncio.run(coro)


def _set_flag(name: str, tenant_id=None, on: bool = True) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = dict(flags.get(name) or {"on": False, "tenants": []})
            if tenant_id is None:
                entry["on"] = on
            else:
                tenants = set(entry.get("tenants") or [])
                (tenants.add if on else tenants.discard)(tenant_id)
                entry["tenants"] = sorted(tenants)
            flags[name] = entry
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _org(tag: str, *, ideas: bool = True) -> dict:
    headers = {"Authorization": f"Bearer {_register(f'ideas-{tag}-{uuid.uuid4().hex[:6]}@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    if ideas:
        _set_flag(c.FLAG, me["tenant_id"])
    return {"headers": headers, "tenant_id": me["tenant_id"]}


@pytest.fixture(autouse=True)
def _fresh_limits():
    rate_limit.reset()
    yield
    rate_limit.reset()


# ------------------------------------------------------------------------------------------------------------ content
def test_the_library_holds_t1_to_t8_in_the_orders_groups():
    assert [i.id for i in LIB.ideas] == ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
    assert [g.name for g in LIB.groups] == ["Trend", "Range", "Reversal at areas", "Patterns", "Options sellers"]
    groups = {i.id: i.group for i in LIB.ideas}
    assert groups["T1"] == "trend" and groups["T2"] == "range" and groups["T7"] == "patterns" and groups["T8"] == "options"
    assert {groups["T3"], groups["T4"]} == {"reversal"}
    assert not any("opening range" in i.name.lower() for i in LIB.ideas)        # dropped by the owner (clock-based)
    assert all(i.difficulty in ("Beginner", "Intermediate") for i in LIB.ideas)


def test_every_card_is_a_white_box_with_honest_provenance_and_the_users_own_decisions():
    for idea in LIB.ideas:
        assert idea.plain and idea.structure_words and idea.your_decisions and idea.logic.evidence
        assert "no results are claimed" in idea.provenance.lower(), idea.id
        assert any("your own" in d for d in idea.your_decisions), idea.id
    frame = " ".join(LIB.header.frame).lower()
    assert "education, not advice" in frame and "no results are claimed" in frame and "on paper" in frame


def test_the_content_guard_finds_nothing_in_the_library():
    assert c.problems(LIB) == []
    assert len(c.readable_lines(LIB)) > 150          # the guard reads every human-readable line, not a sample


@pytest.mark.parametrize("text, why", [
    ("Buy the dip at the area.", "forbidden word"),
    ("Sell when price reaches the edge.", "forbidden word"),
    ("Keep a stop-loss below the area.", "forbidden"),
    ("The target is the next swing.", "forbidden word"),
    ("Book profit at the edge.", "forbidden word"),
    ("This idea has a high win rate.", "win rate"),
    ("Works well on Nifty.", "a security name"),
    ("A demand area on RELIANCE.", "all-capitals"),
    ("Seller area 23,000 to 23,100.", "a number"),
    ("An area near ₹ five hundred.", "currency"),
])
def test_the_guard_refuses_names_prices_advice_and_claims(text, why):
    assert any(why in p for p in c.guard(text)), (text, c.guard(text))


def test_the_guard_allows_structure_words_timeframe_words_and_idea_ids():
    for ok in ("Uptrend; the pullback reached a demand area; a handover is forming.", "Daily for a 15-minute chart.",
               "A broken channel can then become a breakout and retest (T5).", "RSI divergence at the edge."):
        assert c.guard(ok) == [], ok


# --------------------------------------------------------------------------------------------- scans and the registry
@pytest.mark.parametrize("idea, scan", SCANS, ids=[f"{i.id}.{s.id}" for i, s in SCANS])
def test_each_scan_builds_and_validates_against_the_registry(idea, scan):
    assert c.check_scan(idea, scan) == []
    ast = parse(scan.text)
    for node in nodes.walk(ast):
        if isinstance(node, nodes.Call):
            assert node.name in FUNCTIONS                    # only the existing registry: no new indicator semantics
    stages = st.split(st.inline(ast, c.scan_params(idea, scan)))     # what "Try as a scan" opens in the builder
    assert stages and all("$" not in s["text"] for s in stages)


@pytest.mark.parametrize("idea, scan", SCANS, ids=[f"{i.id}.{s.id}" for i, s in SCANS])
def test_each_scan_validates_at_every_edge_of_its_settings(idea, scan):
    used = c.scan_params(idea, scan)
    for setting in idea.settings:
        if setting.key not in used:
            continue
        for value in setting.values_to_check():
            assert c.check_scan(idea, scan, {setting.key: value}) == [], (setting.key, value)


def test_every_number_in_a_scan_is_a_users_setting():
    for idea, scan in SCANS:
        bare = re.sub(r"@\d+[mhdwM]\b", "", scan.text)                # a timeframe is not a value
        bare = re.sub(r'"[^"]*"', "", bare)
        assert not re.search(r"\d", bare), f"{idea.id}.{scan.id}: a fixed number in {scan.text!r}"


def test_a_value_outside_the_registrys_range_is_refused():
    t2 = LIB.idea("T2")
    assert t2 is not None
    assert c.check_scan(t2, t2.scans[0], {"swing_size": 4})           # degree 0..3 only


def test_the_options_pack_is_t1_and_t2_reduced_to_direction():
    t1, t2, t8 = (LIB.idea(i) for i in ("T1", "T2", "T8"))
    assert t1 and t2 and t8
    assert [s.text for s in t8.scans] == [s.text for s in t1.scans] + [s.text for s in t2.scans]
    assert [s.label for s in t8.scans] == ["Bullish continuation", "Bearish continuation", "Range upper edge", "Range lower edge"]


def test_ideas_without_the_building_blocks_say_coming_later_instead_of_inventing_rules():
    for idea_id in ("T6", "T7"):
        idea = LIB.idea(idea_id)
        assert idea is not None and idea.scans == () and idea.scan_gaps and "comes later" in idea.scan_gaps


def test_scan_flags_name_the_screener_and_each_functions_flag():
    t5 = LIB.idea("T5")
    assert t5 is not None
    assert c.scan_flags(t5.scans[0]) == ["screener_v2", "screener_builder_u2", "screener_breakouts", "screener_breakouts_bk4"]
    assert set(c.needed_flags(LIB)) == set(SCAN_FLAGS)


def test_no_idea_can_be_adopted_yet_and_nothing_names_a_mode():
    data = c.payload(LIB, set(SCAN_FLAGS))
    assert all(i["adopt"] == {"available": False} for i in data["ideas"])
    text = str(data).lower()
    assert "live" not in re.findall(r"[a-z]+", text)


def test_the_schema_refuses_an_unknown_setting_and_an_unread_setting():
    raw = c.load().model_dump(by_alias=True, mode="json")
    t2 = raw["ideas"][1]
    t2["scans"][0]["text"] = 'DowTrend($nope) == "RANGE"'
    with pytest.raises(ValueError, match="unknown setting"):
        c.Library.model_validate_json(json.dumps(raw))
    raw = c.load().model_dump(by_alias=True, mode="json")
    raw["ideas"][1]["settings"].append({"key": "spare", "label": "Spare", "help": "Unused.", "type": "number", "default": 1,
                                         "min": 0, "max": 2, "step": 1})
    with pytest.raises(ValueError, match="no scan reads"):
        c.Library.model_validate_json(json.dumps(raw))


def test_the_beginner_path_only_names_library_ideas():
    ids = {i.id for i in LIB.ideas}
    assert len(LIB.beginner.questions) == 3
    for q in LIB.beginner.questions:
        for o in q.options:
            assert set(o.ideas) <= ids


# ------------------------------------------------------------------------------------------------------------- flag
def test_the_flag_is_registered_off_by_default_with_a_catalog_entry():
    assert c.FLAG in controls.FEATURE_FLAGS and c.FLAG in controls.DEFAULT_OFF_FLAGS
    assert FLAG_INFO[c.FLAG].depends_on == ("ui_v2",)


# -------------------------------------------------------------------------------------------------------------- API
def test_flag_off_means_nothing_is_there():
    org = _org("off", ideas=False)
    assert client.get("/api/ideas", headers=org["headers"]).status_code == 404
    assert client.get("/api/ideas/T1", headers=org["headers"]).status_code == 404


def test_signed_out_is_refused():
    assert client.get("/api/ideas").status_code == 401


def test_the_library_per_organisation():
    on, off = _org("on"), _org("other", ideas=False)
    r = client.get("/api/ideas", headers=on["headers"])
    assert r.status_code == 200
    data = r.json()
    assert [i["id"] for i in data["ideas"]] == list(c.IDEA_IDS)
    assert data["header"]["frame"] and data["beginner"]["questions"]
    assert client.get("/api/ideas", headers=off["headers"]).status_code == 404
    # no scan can open while the Screener and its functions are off for this organisation
    assert not any(s["available"] for i in data["ideas"] for s in i["scans"])
    assert "screener_v2" not in r.text and "market_zones" not in r.text       # flag names are never sent


def test_a_scan_is_available_once_every_flag_it_needs_is_on():
    org = _org("flags")
    for flag in ("screener_v2", "screener_builder_u2", "screener_breakouts", "screener_breakouts_bk4"):
        _set_flag(flag, org["tenant_id"])
    data = client.get("/api/ideas", headers=org["headers"]).json()
    by_id = {i["id"]: i for i in data["ideas"]}
    assert all(s["available"] for s in by_id["T5"]["scans"])          # breakouts + bk4: on
    assert all(s["available"] for s in by_id["T2"]["scans"])          # breakouts: on
    assert not any(s["available"] for s in by_id["T1"]["scans"])      # market zones: still off
    one = client.get("/api/ideas/T5", headers=org["headers"])
    assert one.status_code == 200 and one.json()["scans"][0]["params"] == {"retest_of": "swing", "retest_max_bars": 10}
    assert client.get("/api/ideas/T9", headers=org["headers"]).status_code == 404


def test_try_as_a_scan_opens_in_the_screener_builder():
    org = _org("stages")
    for flag in ("screener_v2", "screener_builder_u2", "screener_breakouts", "screener_breakouts_bk4"):
        _set_flag(flag, org["tenant_id"])
    t5 = client.get("/api/ideas/T5", headers=org["headers"]).json()
    scan = t5["scans"][0]
    r = client.post("/api/screener/stages", headers=org["headers"],
                    json={"source": scan["text"], "base_tf": scan["base_tf"], "params": scan["params"]})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == 'RetestEntry("up", "swing", 10, TRUE)'


def test_the_frontend_fixture_is_the_servers_own_payload():
    """frontend/e2e/fixtures/ideas_v2.json (vitest + e2e) is this payload with the Screener, its builder and the breakout
    families on and market zones / the AMW functions off - regenerate it when the content changes."""
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "frontend" / "e2e" / "fixtures" / "ideas_v2.json"
    on = {"screener_v2", "screener_builder_u2", "screener_breakouts", "screener_breakouts_bk4"}
    assert json.loads(path.read_text(encoding="utf-8")) == c.payload(LIB, on)


# ------------------------------------------------------------------------------- slice 2: adopt as a paper strategy
def _with_mapping(idea_id: str, adopt: "c.Adopt") -> "c.Library":
    ideas = tuple(i.model_copy(update={"adopt": adopt}) if i.id == idea_id else i for i in LIB.ideas)
    return LIB.model_copy(update={"ideas": ideas})


def test_every_idea_is_either_mapped_honestly_or_says_why_not():
    for idea in LIB.ideas:
        assert (idea.adopt is None) == (idea.id in c.ADOPT_REVIEW), idea.id
        assert c.check_adopt(idea) == [], idea.id
    assert set(c.ADOPT_REVIEW) <= set(c.IDEA_IDS) and all(len(v) > 20 for v in c.ADOPT_REVIEW.values())
    # 22 Oct: no T idea shares its structure with D1 / D2 / B1 / B3, so none can be adopted yet (IDEA-1, IDEA-10)
    assert c.adopt_templates(LIB) == []


def test_a_mapping_is_checked_against_the_real_template_library():
    t1 = LIB.idea("T1")
    assert t1 is not None
    good = t1.model_copy(update={"adopt": c.Adopt(template="D1", timeframe="15m", params={"period": 12})})
    assert c.check_adopt(good) == []
    assert c.check_adopt(t1.model_copy(update={"adopt": c.Adopt(template="G9")})) == ["T1: unknown template 'G9'"]
    bad = c.check_adopt(t1.model_copy(update={"adopt": c.Adopt(template="D1", params={"period": 0})}))
    assert bad and all(p.startswith("T1: ") for p in bad)
    assert c.check_adopt(t1.model_copy(update={"adopt": c.Adopt(template="D1", params={"nope": 1})}))
    with pytest.raises(ValueError):
        c.Adopt(template="T1")                          # only strategy template ids (families A-G)


def test_adopt_is_offered_only_when_the_mapped_template_can_be_adopted():
    lib = _with_mapping("T5", c.Adopt(template="D1", timeframe="15m", params={"period": 12}))
    t5 = lib.idea("T5")
    assert t5 is not None and c.adopt_templates(lib) == ["D1"]
    assert c.adopt_payload(t5) == {"available": False}
    assert c.adopt_payload(t5, frozenset({"D1"})) == {"available": True, "template": "D1", "timeframe": "15m", "params": {"period": 12}}
    data = c.payload(lib, set(), frozenset({"D1"}))
    assert [i["id"] for i in data["ideas"] if i["adopt"]["available"]] == ["T5"]
    assert all("adopt" in i and set(i["adopt"]) == {"available"} for i in data["ideas"] if i["id"] != "T5")


def test_the_route_offers_adopt_only_with_strategy_templates_on(monkeypatch):
    from app.strategy_templates.routes import FLAG as TEMPLATES_FLAG
    lib = _with_mapping("T5", c.Adopt(template="D1", timeframe="15m", params={"period": 12}))
    monkeypatch.setattr(c, "load", lambda: lib)
    org = _org("adopt")
    t5 = client.get("/api/ideas/T5", headers=org["headers"]).json()
    assert t5["adopt"] == {"available": False}                       # strategy_templates off
    _set_flag(TEMPLATES_FLAG, org["tenant_id"])
    r = client.get("/api/ideas", headers=org["headers"])
    by_id = {i["id"]: i for i in r.json()["ideas"]}
    assert by_id["T5"]["adopt"] == {"available": True, "template": "D1", "timeframe": "15m", "params": {"period": 12}}
    assert not any(by_id[i]["adopt"]["available"] for i in by_id if i != "T5")
    # the same settings are what the template's own adopt route turns into the paper bot pre-fill (nothing is created)
    pre = client.post("/api/strategy-templates/D1/adopt", headers=org["headers"],
                      json={"timeframe": "15m", "params": {"period": 12}})
    assert pre.status_code == 200, pre.text
    assert pre.json()["deployment"]["mode"] == "PAPER" and pre.json()["settings"]["params"]["period"] == 12
