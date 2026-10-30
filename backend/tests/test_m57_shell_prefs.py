"""M57 U7-1: the sidebar's show / hide choice is stored per user on the server (`/api/me/prefs/shell`), so it follows the
user to every device; never chosen = None (the frontend picks the default for the width). Strict schema, own user only."""
import uuid

from app.me import prefs
from tests.test_auth_api import client
from tests.test_phase_l_ai import _owner

BASE = "/api/me/prefs"


def _who(tag: str) -> dict:
    headers, _me = _owner(f"m57-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    return headers


def test_never_chosen_is_none():
    got = client.get(BASE, headers=_who("default")).json()
    assert got["prefs"]["shell"] == {"sidebar": None} and got["versions"]["shell"] == 0
    assert prefs.defaults("shell") == {"sidebar": None}


def test_round_trip_rail_then_expanded():
    who = _who("round")
    for mode, version in (("rail", 1), ("expanded", 2)):
        saved = client.put(f"{BASE}/shell", headers=who, json={"sidebar": mode})
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"namespace": "shell", "version": version, "value": {"sidebar": mode}}
    assert client.get(BASE, headers=who).json()["prefs"]["shell"] == {"sidebar": "expanded"}


def test_refuses_unknown_values_and_keys():
    who = _who("strict")
    for body in ({"sidebar": "hidden"}, {"sidebar": True}, {"sidebar": "rail", "width": 56}):
        assert client.put(f"{BASE}/shell", headers=who, json=body).status_code == 422, body
    assert client.get(BASE, headers=who).json()["versions"]["shell"] == 0


def test_each_user_has_their_own():
    one, two = _who("one"), _who("two")
    assert client.put(f"{BASE}/shell", headers=one, json={"sidebar": "rail"}).status_code == 200
    assert client.get(BASE, headers=two).json()["prefs"]["shell"] == {"sidebar": None}
