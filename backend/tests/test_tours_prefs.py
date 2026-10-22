"""MO2 part C: the first-time guidance marks (`tours` namespace of `/api/me/prefs`) - defaults, the allow-listed page
ids, validation, compare-and-set, each user's own marks, and the frontend's list of tour pages kept equal to the server's."""
import json
import re
from pathlib import Path

import pytest

from app.me import prefs
from tests.test_ui2_4a1_prefs import BASE, _invite, _put, _who, client

DEFAULTS = {"welcome_done_at": None, "skip_all_at": None, "seen": {}}
WHEN = "2026-10-22T09:00:00Z"


def test_defaults_before_anything_is_saved():
    who = _who("tours-default")
    got = client.get(BASE, headers=who["headers"]).json()
    assert got["prefs"]["tours"] == DEFAULTS and got["versions"]["tours"] == 0
    assert prefs.defaults("tours") == DEFAULTS


def test_marks_round_trip():
    who = _who("tours-round")
    body = {"welcome_done_at": WHEN, "seen": {"dashboard": WHEN, "option-chain": WHEN}}
    saved = _put(who, body, namespace="tours")
    assert saved.status_code == 200, saved.text
    value = saved.json()["value"]
    assert value["welcome_done_at"].startswith("2026-10-22T09:00:00") and value["skip_all_at"] is None
    assert set(value["seen"]) == {"dashboard", "option-chain"}
    got = client.get(BASE, headers=who["headers"]).json()
    assert set(got["prefs"]["tours"]["seen"]) == {"dashboard", "option-chain"} and got["versions"]["tours"] == 1


def test_show_all_tips_again_clears_every_mark():
    who = _who("tours-reset")
    assert _put(who, {"welcome_done_at": WHEN, "skip_all_at": WHEN, "seen": {"risk": WHEN}}, namespace="tours").status_code == 200
    reset = _put(who, {}, namespace="tours", version=1)
    assert reset.status_code == 200 and reset.json()["value"] == DEFAULTS and reset.json()["version"] == 2


def test_a_stale_version_is_refused_with_the_current_marks():
    who = _who("tours-cas")
    assert _put(who, {"seen": {"charts": WHEN}}, namespace="tours", version=0).status_code == 200
    stale = _put(who, {"skip_all_at": WHEN}, namespace="tours", version=0)
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["version"] == 1 and set(detail["current"]["seen"]) == {"charts"}


@pytest.mark.parametrize("body", [
    {"seen": {"admin": WHEN}},                        # not a tour page
    {"seen": {"dashboard": None}},                    # a seen mark is a time
    {"seen": {"dashboard": "2026-10-22T09:00:00"}},   # without a time zone
    {"seen": {"dashboard": "2019-01-01T00:00:00Z"}},  # before 2020
    {"seen": ["dashboard"]},
    {"skip_all_at": True},
    {"welcome_done_at": WHEN, "steps": 3},            # unknown key
])
def test_bad_marks_are_refused(body):
    assert _put(_who("tours-bad"), body, namespace="tours").status_code == 422


def test_each_user_has_their_own_marks():
    owner = _who("tours-own")
    member = _invite(owner, "USER")
    assert _put(owner, {"skip_all_at": WHEN}, namespace="tours").status_code == 200
    assert client.get(BASE, headers=member["headers"]).json()["prefs"]["tours"] == DEFAULTS


def test_a_viewer_may_save_their_own_marks():
    owner = _who("tours-viewer")
    viewer = _invite(owner, "VIEWER")
    assert _put(viewer, {"seen": {"signals": WHEN}}, namespace="tours").status_code == 200


def test_a_stored_unknown_page_is_dropped_and_the_rest_kept():
    raw = json.dumps({"welcome_done_at": WHEN, "seen": {"dashboard": WHEN, "retired-page": WHEN}})
    value = prefs.stored_value("tours", raw)
    assert value["welcome_done_at"] is not None and set(value["seen"]) == {"dashboard"}


def test_the_frontend_lists_the_same_tour_pages():
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "tours" / "pages.ts").read_text(encoding="utf-8")
    block = re.search(r"TOUR_IDS\s*=\s*\[(.*?)\]", source, re.S)
    assert block, "frontend/src/tours/pages.ts must define TOUR_IDS"
    assert tuple(re.findall(r'"([a-z-]+)"', block.group(1))) == prefs.TOUR_IDS
