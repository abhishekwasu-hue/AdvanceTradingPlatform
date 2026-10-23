from typing import Dict, Optional, Type

import httpx

from app.brokers.base import BrokerInterface
from app.brokers.models import BrokerCredentials
from app.brokers.shoonya import ShoonyaBroker
from app.brokers.angel_one import AngelOneBroker
from app.brokers.dhan import DhanBroker
from app.brokers.fyers import FyersBroker
from app.brokers.coindcx import CoinDCXBroker
from app.brokers.upstox import UpstoxBroker
from app.brokers.zerodha import ZerodhaBroker
from app.execution.paper_adapter import PaperExecutionAdapter

BROKER_ADAPTERS: Dict[str, Type[BrokerInterface]] = {
    ZerodhaBroker.name: ZerodhaBroker,
    UpstoxBroker.name: UpstoxBroker,
    ShoonyaBroker.name: ShoonyaBroker,
    AngelOneBroker.name: AngelOneBroker,
    FyersBroker.name: FyersBroker,
    DhanBroker.name: DhanBroker,
    CoinDCXBroker.name: CoinDCXBroker,
}


# TM-1: execution venues that are not a broker account - PAPER is a broker adapter (owner principle, Part 8) but it has
# no credentials, no login and no connect card, so it is not in BROKER_ADAPTERS (the list the connect pages show).
EXECUTION_ADAPTERS: Dict[str, Type[PaperExecutionAdapter]] = {
    PaperExecutionAdapter.name: PaperExecutionAdapter,
}


def get_execution_adapter(name: str, **kwargs) -> BrokerInterface:
    """A credential-less execution adapter by name ("paper"); kwargs go to its constructor."""
    try:
        adapter_cls = EXECUTION_ADAPTERS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown execution adapter '{name}'. Available: {', '.join(EXECUTION_ADAPTERS)}") from exc
    return adapter_cls(**kwargs)


def available_brokers() -> list[str]:
    return list(BROKER_ADAPTERS.keys())


def get_broker_adapter(
    name: str, credentials: BrokerCredentials, client: Optional[httpx.AsyncClient] = None
) -> BrokerInterface:
    try:
        adapter_cls = BROKER_ADAPTERS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown broker '{name}'. Available: {', '.join(available_brokers())}") from exc
    return adapter_cls(credentials, client)
