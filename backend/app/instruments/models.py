"""Contract specs for asset classes beyond plain NSE/BSE equity & index options, which already
work today through the tenant's own `RiskConfig.lot_size` and the broker adapters' free-form
`exchange` string. MCX commodities and crypto pairs need real per-instrument sizing information
instead - a single tenant-wide lot size makes no sense once "one lot" might mean 100 barrels of
crude oil on one symbol and a fraction of one Bitcoin on another.

These are static, publicly known contract specifications (standard MCX lot sizes, common crypto
tick sizes) - not live prices, and not fabricated. Exchanges revise contract specs periodically,
so treat this as a reference default to override per-deployment, not a live feed.
"""
from pydantic import BaseModel, Field

from app.core.enums import AssetClass


class ContractSpec(BaseModel):
    symbol: str
    exchange: str
    asset_class: AssetClass
    description: str
    # The smallest tradable increment. For a non-fractional instrument (MCX futures) this is the
    # exchange's lot size (e.g. 100 barrels/lot for crude oil). For a fractional instrument
    # (crypto) this is the smallest quantity increment (e.g. 0.0001 BTC), and quantity is never
    # floored to a whole multiple of it the way a lot size is.
    lot_size: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    fractional: bool = False


def default_contract_spec(symbol: str, exchange: str, asset_class: AssetClass) -> ContractSpec:
    return ContractSpec(
        symbol=symbol, exchange=exchange, asset_class=asset_class,
        description=f"{symbol} ({exchange})", lot_size=1.0, tick_size=0.01,
        fractional=asset_class == AssetClass.CRYPTO,
    )
