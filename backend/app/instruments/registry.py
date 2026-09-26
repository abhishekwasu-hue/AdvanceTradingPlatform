"""Static reference registry of MCX commodity and crypto contract specs (see models.py for why
this exists - plain NSE/BSE equity & index options don't need it, since they already size off
the tenant's own RiskConfig.lot_size). Symbols are looked up case-insensitively.

Standard MCX lot sizes are public contract specifications set by the exchange (not fabricated
market data), but they do change from time to time - verify against MCX's current contract
specifications (https://www.mcxindia.com/products) before relying on these for anything beyond
paper trading. Crypto tick/quantity precision here mirrors common INR-pair conventions.
"""
from typing import Dict, List, Optional

from app.core.enums import AssetClass
from app.instruments.models import ContractSpec

_MCX_SPECS: Dict[str, ContractSpec] = {
    "GOLD": ContractSpec(
        symbol="GOLD", exchange="MCX", asset_class=AssetClass.COMMODITY,
        description="MCX Gold futures - standard lot", lot_size=100.0, tick_size=1.0,
    ),
    "GOLDM": ContractSpec(
        symbol="GOLDM", exchange="MCX", asset_class=AssetClass.COMMODITY,
        description="MCX Gold Mini futures", lot_size=10.0, tick_size=1.0,
    ),
    "SILVER": ContractSpec(
        symbol="SILVER", exchange="MCX", asset_class=AssetClass.COMMODITY,
        description="MCX Silver futures - standard lot (30 kg)", lot_size=30.0, tick_size=1.0,
    ),
    "SILVERM": ContractSpec(
        symbol="SILVERM", exchange="MCX", asset_class=AssetClass.COMMODITY,
        description="MCX Silver Mini futures (5 kg)", lot_size=5.0, tick_size=1.0,
    ),
    "CRUDEOIL": ContractSpec(
        symbol="CRUDEOIL", exchange="MCX", asset_class=AssetClass.COMMODITY,
        description="MCX Crude Oil futures (100 barrels/lot)", lot_size=100.0, tick_size=1.0,
    ),
    "NATURALGAS": ContractSpec(
        symbol="NATURALGAS", exchange="MCX", asset_class=AssetClass.COMMODITY,
        description="MCX Natural Gas futures (1250 mmBtu/lot)", lot_size=1250.0, tick_size=0.1,
    ),
    "COPPER": ContractSpec(
        symbol="COPPER", exchange="MCX", asset_class=AssetClass.COMMODITY,
        description="MCX Copper futures (2500 kg/lot)", lot_size=2500.0, tick_size=0.05,
    ),
}

_CRYPTO_SPECS: Dict[str, ContractSpec] = {
    "BTCINR": ContractSpec(
        symbol="BTCINR", exchange="CRYPTO", asset_class=AssetClass.CRYPTO,
        description="Bitcoin / Indian Rupee spot", lot_size=0.0001, tick_size=1.0, fractional=True,
    ),
    "ETHINR": ContractSpec(
        symbol="ETHINR", exchange="CRYPTO", asset_class=AssetClass.CRYPTO,
        description="Ethereum / Indian Rupee spot", lot_size=0.001, tick_size=0.5, fractional=True,
    ),
    "USDTINR_CRYPTO": ContractSpec(
        symbol="USDTINR_CRYPTO", exchange="CRYPTO", asset_class=AssetClass.CRYPTO,
        description="Tether / Indian Rupee spot", lot_size=1.0, tick_size=0.01, fractional=True,
    ),
}

CONTRACT_SPECS: Dict[str, ContractSpec] = {**_MCX_SPECS, **_CRYPTO_SPECS}


def get_contract_spec(symbol: str) -> Optional[ContractSpec]:
    """Returns None for anything not in the registry - callers must treat that as "size this the
    default equity way", never as an error, since the overwhelming majority of symbols traded on
    this platform are plain NSE/BSE equity & index options that were never meant to be here.
    """
    return CONTRACT_SPECS.get(symbol.upper())


def list_contract_specs() -> List[ContractSpec]:
    return list(CONTRACT_SPECS.values())
