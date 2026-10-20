"""Z7a-2 golden parity: the ATP level-memory port (app.price_action.zones.memory) == Trade@eafc872 level_memory.py.

Two checks per section, as tests/test_zones_parity.py does for detection:
  * ATP output == the frozen Trade output (when the private fixtures store is mounted, ATP_PRIVATE_FIXTURES - the host
    or a private runner; the public CI skips it with the reason, tests/private_fixtures.py);
  * the frozen file == Trade run live (only when the read-only Trade checkout is on disk, TRADE_REPO_PATH or
    /home/user/trade; skipped with the reason otherwise).
The harness lives in tests/zones_memory_golden.py.
"""
import pytest

from tests import zones_golden as G
from tests import zones_memory_golden as MG

SECTIONS = sorted({"apply_memory", "last_touch_time", "merge_walks", "remember_dyn_sr", "zone_tolerance"})


@pytest.fixture(scope="module")
def golden():
    return MG.load_golden()                 # skips when the private fixtures store is not mounted


@pytest.fixture(scope="module")
def atp():
    return G.to_json(MG.atp_outputs())


@pytest.fixture(scope="module")
def trade_live():
    T = MG.import_trade()
    if T is None:
        pytest.skip(f"Trade checkout not on disk at {G.TRADE_PATH} (set TRADE_REPO_PATH) - the frozen golden file is checked instead")
    return G.to_json(MG.trade_outputs(T))


def test_golden_file_is_from_the_pinned_trade_commit_and_exercises_memory(golden):
    assert golden["trade_commit"] == "eafc872"
    assert set(golden) - {"trade_commit"} == set(SECTIONS)
    walks = golden["merge_walks"]
    levels = [z for steps in walks.values() for s in steps for z in s["levels"]]
    assert len(levels) > 500 and sum(not z["fresh"] for z in levels) > 100             # remembered levels carried
    assert any(len(s["levels"]) == 12 for s in walks["s15/default"])                   # the max_levels cap reached
    assert max(len(s["levels"]) for s in walks["bn/near_cap3"]) == 3
    # a carried level keeps its first formation time on later nights
    formed = {}
    for s in walks["s15/default"]:
        for z in s["levels"]:
            formed.setdefault(z["level"], set()).add(z["formed"])
    assert any(len(v) == 1 and n > 3 for v, n in ((v, sum(1 for s in walks["s15/default"] for z in s["levels"] if z["level"] == lv))
                                                   for lv, v in formed.items()))
    tol = golden["zone_tolerance"].values()
    assert len(set(tol)) > 100


@pytest.mark.parametrize("section", SECTIONS)
def test_atp_matches_frozen_trade_output(golden, atp, section):
    stats = {"exact": 0, "approx": 0}
    problems = G.diff(golden[section], atp[section], f"/{section}", stats=stats)
    assert not problems, "\n".join(problems[:40])
    assert stats["exact"] + stats["approx"] > 0


@pytest.mark.parametrize("section", SECTIONS)
def test_frozen_golden_equals_live_trade(golden, trade_live, section):
    problems = G.diff(trade_live[section], golden[section], f"/{section}")
    assert not problems, "the Trade checkout no longer produces the frozen output (re-sync deliberately: python -m tests.zones_memory_golden --write)\n" + \
        "\n".join(problems[:40])
