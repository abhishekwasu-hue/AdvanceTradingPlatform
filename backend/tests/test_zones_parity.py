"""Z7a-1 golden parity: the ATP market-zones port == Trade@eafc872 on the fixtures in tests/fixtures/zones.

Two checks per section:
  * ATP output == the frozen Trade output (always runs - CI has no Trade checkout);
  * the frozen file == Trade run live (only when the read-only Trade checkout is on disk, TRADE_REPO_PATH or
    /home/user/trade; skipped with the reason otherwise) - proves the golden file is what Trade really produces.
The harness and the normalisation of the deliberate differences live in tests/zones_golden.py.
"""
import pytest

from tests import zones_golden as G

GOLDEN = G.load_golden()
SECTIONS = sorted(k for k in GOLDEN if k != "trade_commit")


@pytest.fixture(scope="module")
def atp():
    return G.to_json(G.atp_outputs())


@pytest.fixture(scope="module")
def trade_live():
    T = G.import_trade()
    if T is None:
        pytest.skip(f"Trade checkout not on disk at {G.TRADE_PATH} (set TRADE_REPO_PATH) - the frozen golden file is checked instead")
    return G.to_json(G.trade_outputs(T))


def test_golden_file_is_from_the_pinned_trade_commit_and_covers_every_family():
    assert GOLDEN["trade_commit"] == "eafc872"
    assert set(SECTIONS) == {"all_zones", "confluence", "demand_supply", "dynamic_sr", "first_touch", "intraday", "order_block_retest",
                             "order_blocks", "overnight_gaps", "role", "sr_levels", "sr_v3", "sr_v3_flip", "sr_v3_gaps", "swings"}
    # the fixtures really exercise every family (not trivially empty)
    assert sum(len(v) for v in GOLDEN["order_blocks"].values()) > 50 and sum(len(v) for v in GOLDEN["demand_supply"].values()) > 30
    assert sum(len(v) for v in GOLDEN["overnight_gaps"].values()) > 5
    states = {r["state"] for rows in GOLDEN["all_zones"].values() for r in rows}
    assert states == {"fresh", "tested"}
    assert any(z["gap_kind"] for case in GOLDEN["sr_v3"].values() for z in case["levels"])
    assert any(z["flipped"] for case in GOLDEN["sr_v3"].values() for z in case["levels"])


@pytest.mark.parametrize("section", SECTIONS)
def test_atp_matches_frozen_trade_output(atp, section):
    stats = {"exact": 0, "approx": 0}
    problems = G.diff(GOLDEN[section], atp[section], f"/{section}", stats=stats)
    assert not problems, "\n".join(problems[:40])
    assert stats["exact"] + stats["approx"] > 0


@pytest.mark.parametrize("section", SECTIONS)
def test_frozen_golden_equals_live_trade(trade_live, section):
    problems = G.diff(trade_live[section], GOLDEN[section], f"/{section}")
    assert not problems, "the Trade checkout no longer produces the frozen output (re-sync deliberately: python -m tests.zones_golden --write)\n" + \
        "\n".join(problems[:40])
