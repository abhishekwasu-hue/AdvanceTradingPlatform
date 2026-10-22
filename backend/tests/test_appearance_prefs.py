"""Handover item 1: the text size (`appearance` namespace of `/api/me/prefs`) - default, the three sizes, validation,
and each user's own value."""
import pytest

from app.me import prefs
from tests.test_ui2_4a1_prefs import BASE, _invite, _put, _who, client


def test_default_is_normal():
    who = _who("appearance-default")
    got = client.get(BASE, headers=who["headers"]).json()
    assert got["prefs"]["appearance"] == {"text_size": "normal"} and got["versions"]["appearance"] == 0
    assert prefs.defaults("appearance") == {"text_size": "normal"}


@pytest.mark.parametrize("size", ["normal", "large", "xlarge"])
def test_each_size_round_trips(size):
    who = _who(f"appearance-{size}")
    saved = _put(who, {"text_size": size}, namespace="appearance")
    assert saved.status_code == 200, saved.text
    assert saved.json()["value"] == {"text_size": size}
    assert client.get(BASE, headers=who["headers"]).json()["prefs"]["appearance"]["text_size"] == size


@pytest.mark.parametrize("body", [{"text_size": "huge"}, {"text_size": 2}, {"text_size": "large", "zoom": 1.5}])
def test_bad_values_are_refused(body):
    assert _put(_who("appearance-bad"), body, namespace="appearance").status_code == 422


def test_each_user_has_their_own():
    owner = _who("appearance-own")
    member = _invite(owner, "USER")
    assert _put(owner, {"text_size": "xlarge"}, namespace="appearance").status_code == 200
    assert client.get(BASE, headers=member["headers"]).json()["prefs"]["appearance"]["text_size"] == "normal"


def test_a_stored_unknown_size_reads_as_normal():
    assert prefs.stored_value("appearance", '{"text_size": "giant"}') == {"text_size": "normal"}
