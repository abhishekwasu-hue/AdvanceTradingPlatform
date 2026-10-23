"""FNO-8B-28 (owner decision 23 Oct): the user's own liquidity limits (`fno` namespace of `/api/me/prefs`) - OFF and empty
by default, strictly validated, each user's own, and never read by the server (information only on the confirm step: no
order path, worker or risk check imports it, so it can never hold back an order or an exit)."""
import json
from pathlib import Path

import pytest

from app.me import prefs
from tests.test_ui2_4a1_prefs import BASE, _invite, _put, _who, client

NS = "fno"
OFF = {"liquidity": {"on": False, "max_spread_pct": None, "min_oi": None}}


def test_off_and_empty_by_default():
    who = _who("fl-default")
    got = client.get(BASE, headers=who["headers"]).json()
    assert got["prefs"][NS] == OFF and got["versions"][NS] == 0
    assert prefs.defaults(NS) == OFF


def test_round_trip_turn_off_keeps_the_limits():
    who = _who("fl-round")
    saved = _put(who, {"liquidity": {"on": True, "max_spread_pct": 2.5, "min_oi": 5000}}, namespace=NS)
    assert saved.status_code == 200, saved.text
    assert saved.json()["value"] == {"liquidity": {"on": True, "max_spread_pct": 2.5, "min_oi": 5000}}
    off = _put(who, {"liquidity": {"on": False, "max_spread_pct": 2.5, "min_oi": 5000}}, namespace=NS, version=1)
    assert off.status_code == 200 and off.json()["value"]["liquidity"]["on"] is False
    assert off.json()["value"]["liquidity"]["max_spread_pct"] == 2.5 and off.json()["version"] == 2
    one = _put(who, {"liquidity": {"on": True, "min_oi": 100}}, namespace=NS)
    assert one.status_code == 200 and one.json()["value"]["liquidity"] == {"on": True, "max_spread_pct": None, "min_oi": 100}
    cleared = _put(who, {}, namespace=NS)
    assert cleared.status_code == 200 and cleared.json()["value"] == OFF


@pytest.mark.parametrize("body", [
    {"liquidity": {"on": True}},                                   # on needs at least one limit
    {"liquidity": {"on": True, "max_spread_pct": None, "min_oi": None}},
    {"liquidity": {"on": "true", "max_spread_pct": 2}},            # strict: a string is not a boolean
    {"liquidity": {"on": True, "max_spread_pct": 0}},
    {"liquidity": {"on": True, "max_spread_pct": -1}},
    {"liquidity": {"on": True, "max_spread_pct": 100.5}},
    {"liquidity": {"on": True, "max_spread_pct": "2"}},
    {"liquidity": {"on": True, "min_oi": 0}},
    {"liquidity": {"on": True, "min_oi": 2.5}},
    {"liquidity": {"on": True, "min_oi": prefs.MAX_MIN_OI + 1}},
    {"liquidity": {"on": True, "min_oi": 10, "block_orders": True}},   # unknown keys are refused: nothing can block
    {"block_orders": True},
    {"liquidity": None},
])
def test_bad_values_are_refused(body):
    who = _who("fl-bad")
    got = _put(who, body, namespace=NS)
    assert got.status_code == 422, (body, got.text)


def test_nan_and_infinity_are_refused():
    who = _who("fl-nan")
    for raw in ('{"liquidity": {"on": true, "max_spread_pct": NaN}}', '{"liquidity": {"on": true, "max_spread_pct": Infinity}}'):
        got = client.put(f"{BASE}/{NS}", headers={**who["headers"], "Content-Type": "application/json"}, content=raw)
        assert got.status_code == 422, raw


def test_only_the_two_limits_and_the_switch():
    assert set(prefs.FnoPrefs.model_fields) == {"liquidity"}
    assert set(prefs.LiquidityLimits.model_fields) == {"on", "max_spread_pct", "min_oi"}


def test_each_user_has_their_own_limits():
    owner = _who("fl-iso")
    member = _invite(owner, "USER")
    assert _put(owner, {"liquidity": {"on": True, "min_oi": 3}}, namespace=NS).status_code == 200
    assert client.get(BASE, headers=member["headers"]).json()["prefs"][NS] == OFF
    other_org = _who("fl-iso-2")
    assert client.get(BASE, headers=other_org["headers"]).json()["prefs"][NS] == OFF
    assert client.get(BASE, headers=owner["headers"]).json()["prefs"][NS]["liquidity"]["min_oi"] == 3


def test_a_stored_value_that_no_longer_validates_still_reads():
    # an unknown key is dropped; "on" without a limit cannot be read, so the limits fall back to OFF
    assert prefs.stored_value(NS, json.dumps({"liquidity": {"on": True}, "later": 1})) == OFF
    kept = prefs.stored_value(NS, json.dumps({"liquidity": {"on": True, "max_spread_pct": 3, "min_oi": 0}}))
    assert kept == {"liquidity": {"on": True, "max_spread_pct": 3, "min_oi": None}}


def test_the_server_never_reads_it():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = ("LiquidityLimits", "FnoPrefs", 'read_one("fno"', "read_one('fno'")
    readers = [p for p in app_dir.rglob("*.py") if p.name != "prefs.py" and any(n in p.read_text(encoding="utf-8") for n in names)]
    assert readers == [], readers
