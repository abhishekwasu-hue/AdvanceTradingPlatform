"""REV-H3 (docs/design/REV1_PLAN.md M1; CLAUDE.md section 3, DEF-0): an option seller pack (F1 / F2) only gives the side.
On an option it trades only the construction the user chose - a structure, or an explicit buy / write of a single option.
The server never fills in a "buy at the money" for it: a single option with no side chosen is refused in plain words."""
import pytest

from app.strategy_templates import packs

PACK_ID = "template:F1:2:15m:both:,,,,,,,,,,,"


def test_the_pack_rule():
    assert packs.is_pack_id(PACK_ID) and packs.is_pack_id("template:F2:1:15m:both:,,")
    assert not packs.is_pack_id("template:D1:1:15m:both:,,") and not packs.is_pack_id("orb_15m_5m") and not packs.is_pack_id("")
    assert packs.contract_problem(PACK_ID, "OPTION", "SINGLE", None) == packs.PICK_STRUCTURE
    assert packs.contract_problem(PACK_ID, "OPTION", "SINGLE", "BUY") is None
    assert packs.contract_problem(PACK_ID, "OPTION", "SINGLE", "WRITE") is None
    assert packs.contract_problem(PACK_ID, "OPTION", "BULL_PUT_SPREAD", None) is None
    assert packs.contract_problem(PACK_ID, "FUTURE", "SINGLE", None) is None
    assert packs.contract_problem(PACK_ID, "UNDERLYING", "SINGLE", None) is None
    # Any other strategy keeps the API exactly as before.
    assert packs.contract_problem("orb_15m_5m", "OPTION", "SINGLE", None) is None


def test_the_rule_reads_the_api_enums_too():
    from app.core.enums import InstrumentKind, OptionStrategy
    assert packs.contract_problem(PACK_ID, InstrumentKind.OPTION, OptionStrategy.SINGLE, None) == packs.PICK_STRUCTURE
    assert packs.contract_problem(PACK_ID, InstrumentKind.OPTION, OptionStrategy.IRON_CONDOR, None) is None


@pytest.fixture()
def pack_org():
    from tests.test_contract_rules import _load_master
    from tests.test_deployments_api import _store_broker
    from tests.test_tpl2_routes import FLAG, _org, client
    _load_master()
    org = _org("rev-h3", FLAG)
    _store_broker(org["headers"])
    body = client.post("/api/strategy-templates/F1/adopt", headers=org["headers"], json={"timeframe": "15m"}).json()
    return client, org, body["deployment"]


@pytest.mark.real_option_defaults          # M68 PR-4: a user with no saved Option side (his saved side would be his own choice)
def test_a_pack_on_an_index_option_with_no_side_is_refused_and_nothing_is_created(pack_org):
    client, org, deployment = pack_org
    before = client.get("/api/deployments", headers=org["headers"]).json()
    res = client.post("/api/deployments", headers=org["headers"],
                      json={**deployment, "symbol": "NIFTY 50", "instrument_kind": "OPTION", "max_lots": 1})
    assert res.status_code == 400 and res.json()["detail"] == packs.PICK_STRUCTURE
    assert client.get("/api/deployments", headers=org["headers"]).json() == before


def test_a_pack_takes_the_users_own_single_option_side(pack_org):
    client, org, deployment = pack_org
    made = client.post("/api/deployments", headers=org["headers"],
                       json={**deployment, "symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_position": "WRITE", "max_lots": 1})
    assert made.status_code == 201, made.text
    assert made.json()["option_strategy"] == "SINGLE" and made.json()["option_position"] == "WRITE"
