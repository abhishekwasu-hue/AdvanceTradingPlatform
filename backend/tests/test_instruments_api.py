from app.instruments.registry import get_contract_spec, list_contract_specs
from tests.test_auth_api import client


def test_registry_returns_none_for_a_plain_equity_symbol():
    # RELIANCE etc. are NOT in the registry on purpose - they size the default equity way,
    # off the tenant's own RiskConfig.lot_size, not a contract spec.
    assert get_contract_spec("RELIANCE") is None
    assert get_contract_spec("NIFTY") is None


def test_registry_lookup_is_case_insensitive():
    assert get_contract_spec("goldm") is not None
    assert get_contract_spec("GOLDM") is not None


def test_registry_includes_mcx_and_crypto_asset_classes():
    specs = list_contract_specs()
    asset_classes = {s.asset_class for s in specs}
    assert "COMMODITY" in asset_classes
    assert "CRYPTO" in asset_classes


def test_list_instruments_endpoint():
    response = client.get("/api/instruments")
    assert response.status_code == 200
    body = response.json()
    symbols = {row["symbol"] for row in body}
    assert "CRUDEOIL" in symbols
    assert "BTCINR" in symbols


def test_get_instrument_endpoint():
    found = client.get("/api/instruments/crudeoil")
    assert found.status_code == 200
    assert found.json()["asset_class"] == "COMMODITY"
    assert found.json()["lot_size"] == 100

    missing = client.get("/api/instruments/RELIANCE")
    assert missing.status_code == 404
